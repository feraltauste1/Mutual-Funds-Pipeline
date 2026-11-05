from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pandas as pd
import requests
from pypdf import PdfReader

from .common import ASSET_TYPES, config, csv_write, fingerprint, json_read, json_write, today, utc_now
from .tr_public import fetch_public_catalog

TR_URL = "https://assets.traderepublic.com/assets/files/ES/Instrument_Universe_ES_en.pdf"
CATALOG_COLUMNS = ["isin", "name", "asset_type", "source", "verified_at"]


def valid_isin(isin: str) -> bool:
    if not re.fullmatch(r"[A-Z]{2}[A-Z0-9]{9}[0-9]", str(isin)):
        return False
    digits = "".join(str(int(c, 36)) for c in isin)
    total = 0
    for i, digit in enumerate(reversed(digits)):
        n = int(digit) * (2 if i % 2 else 1)
        total += n // 10 + n % 10
    return total % 10 == 0


def parse_pdf(path: Path) -> tuple[pd.DataFrame, list]:
    rows, rejected = [], []
    kind = None
    # Se conserva el nombre multilínea incluso si cruza una página.
    for page in PdfReader(path).pages:
        for raw in (page.extract_text() or "").splitlines():
            line = raw.strip()
            if line == "Stocks":
                kind = "stock"
                continue
            if line in {"ETF", "ETFs"}:
                kind = "etf"
                continue
            match = re.match(r"^([A-Z]{2}[A-Z0-9]{9}[0-9])\s+(.+)$", line)
            if match and kind:
                isin, name = match.groups()
                row = {"isin": isin, "name": name, "asset_type": kind,
                       "source": TR_URL, "verified_at": str(today().date())}
                if valid_isin(isin):
                    rows.append(row)
                else:
                    rejected.append({**row, "reason": "ISIN no supera checksum"})
            elif rows and line and not line.startswith(("TRADING UNIVERSE", "ISIN", "___")):
                if not re.fullmatch(r"\d+", line):
                    rows[-1]["name"] += " " + line
    frame = pd.DataFrame(rows, columns=CATALOG_COLUMNS)
    if set(frame.asset_type) != {"stock", "etf"}:
        raise ValueError("Formato del PDF cambiado: no se identifican Stocks y ETF.")
    return frame, rejected


def validate_catalog(frame: pd.DataFrame) -> pd.DataFrame:
    missing = set(CATALOG_COLUMNS) - set(frame.columns)
    if missing:
        raise ValueError(f"Faltan columnas del catálogo: {sorted(missing)}")
    frame = frame.fillna("").copy()
    frame["isin"] = frame["isin"].str.strip().str.upper()
    if not frame["isin"].map(valid_isin).all():
        raise ValueError("Catálogo con ISIN inválidos.")
    if not frame.asset_type.isin(ASSET_TYPES).all():
        raise ValueError(f"asset_type debe ser uno de {ASSET_TYPES}")
    if frame["isin"].duplicated().any():
        raise ValueError("Cada ISIN debe aparecer una sola vez en el catálogo.")
    if (frame[["name", "source", "verified_at"]] == "").any().any():
        raise ValueError("Cada ISIN necesita nombre, fuente y fecha de verificación.")
    dates = pd.to_datetime(frame.verified_at, errors="coerce")
    if dates.isna().any() or (dates > today()).any():
        raise ValueError("Fechas de verificación inválidas o futuras.")
    return frame.sort_values(["asset_type", "isin"]).reset_index(drop=True)


def build_catalog(root: Path, refresh: bool = False, offline: bool = False) -> tuple[pd.DataFrame, dict]:
    cfg = config(root)
    folder = root / "data/catalog"
    folder.mkdir(parents=True, exist_ok=True)
    rejected = []
    complete_by_type, expected_counts = {}, {}
    selection_info = {}
    if cfg.get("authoritative_catalog"):
        path = root / cfg["authoritative_catalog"]
        frame = validate_catalog(pd.read_csv(path, dtype=str))
        proof = json_read(path.with_suffix(".json"))
        age = (today() - pd.Timestamp(proof["as_of"])).days
        complete = (proof.get("complete") is True and proof.get("country") == cfg["country"]
                    and 0 <= age <= cfg["catalog_max_age_days"] and bool(proof.get("source")))
        note = "Catálogo completo declarado por la fuente aportada." if complete else "Catálogo sin acreditación vigente."
        source_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    elif cfg.get("catalog_source", "public") == "public":
        frame, public_manifest = fetch_public_catalog(root, cfg, refresh=refresh, offline=offline)
        frame = validate_catalog(frame)
        complete = public_manifest["complete"]
        expected_counts = public_manifest["expected_counts"]
        complete_by_type = public_manifest.get("complete_by_type", {
            k: public_manifest["counts"].get(k, 0) == expected_counts.get(k, 0) for k in ASSET_TYPES})
        note = public_manifest["note"]
        selection_info = {k: public_manifest[k] for k in (
            "selection_cap", "selection_method", "selection_complete_by_type") if k in public_manifest}
        source_hash = fingerprint(frame.to_dict("records"))
        manual = root / "data/manual/universe.csv"
        if manual.exists():
            extra = pd.read_csv(manual, dtype=str).fillna("")
            if not extra.empty:
                frame = validate_catalog(pd.concat([frame[~frame["isin"].isin(extra["isin"])], extra], ignore_index=True))
                complete = False
                complete_by_type = {k: False for k in ASSET_TYPES}
                selection_info["selection_complete_by_type"] = {k: False for k in ASSET_TYPES}
                note += " Incluye aportaciones manuales: cobertura exacta pendiente de acreditar."
    elif cfg.get("catalog_source") == "pdf":
        if cfg["country"] != "ES":
            raise ValueError("El PDF configurado es de España. Aporta authoritative_catalog para otro país.")
        pdf = folder / "tr_es.pdf"
        download_meta = folder / "download.json"
        if refresh or not pdf.exists():
            if offline:
                raise ValueError("No hay PDF del catálogo en caché.")
            response = requests.get(TR_URL, timeout=60)
            response.raise_for_status()
            if not response.content.startswith(b"%PDF"):
                raise ValueError("Trade Republic no ha devuelto un PDF.")
            pdf.write_bytes(response.content)
            json_write(download_meta, {"downloaded_at": utc_now(), "source": TR_URL,
                                       "last_modified": response.headers.get("Last-Modified")})
        frame, rejected = parse_pdf(pdf)
        # Fecha de descarga, no fecha de publicación (el PDF no la especifica).
        downloaded = (json_read(download_meta)["downloaded_at"][:10] if download_meta.exists()
                      else pd.Timestamp(pdf.stat().st_mtime, unit="s").strftime("%Y-%m-%d"))
        frame["verified_at"] = downloaded
        manual = root / "data/manual/universe.csv"
        if manual.exists():
            extra = pd.read_csv(manual, dtype=str).fillna("")
            # La aportación manual puede actualizar una fila del PDF.
            frame = pd.concat([frame[~frame["isin"].isin(extra["isin"])], extra], ignore_index=True)
        # Duplicados idénticos del PDF se colapsan; conflictos requieren revisión.
        frame = frame.drop_duplicates(subset=CATALOG_COLUMNS)
        frame = validate_catalog(frame)
        complete = False
        note = ("Cobertura parcial: PDF público sin fondos tradicionales ni garantía de vigencia por cuenta. "
                "No equivale a todos los ISIN disponibles hoy en Trade Republic.")
        source_hash = hashlib.sha256(pdf.read_bytes()).hexdigest()
    else:
        raise ValueError("catalog_source debe ser public o pdf.")
    manifest = {"built_at": utc_now(), "country": cfg["country"], "complete": complete,
                "complete_by_type": complete_by_type or {k: complete for k in ASSET_TYPES},
                "expected_counts": expected_counts or frame.asset_type.value_counts().to_dict(),
                "note": note, "source_sha256": source_hash,
                "counts": frame.asset_type.value_counts().to_dict(), "rejected_rows": len(rejected),
                "catalog_hash": fingerprint(frame.to_dict("records")), **selection_info}
    csv_write(folder / "universe.csv", frame)
    json_write(folder / "manifest.json", manifest)
    json_write(folder / "rejected.json", rejected)
    if cfg["require_complete_universe"] and not complete:
        raise ValueError(note + " Configura authoritative_catalog con su manifest.")
    return frame, manifest

