"""Catálogo del explorador PÚBLICO de TR. No inicia sesión ni usa credenciales.

El protocolo no está documentado como API estable por TR. Se observó en los módulos
del explorador https://app.traderepublic.com/browse/ el 2026-11-05. Si cambia,
se falla explícitamente; nunca se declara completa una descarga truncada.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pandas as pd
from websockets.sync.client import connect

from .common import csv_write, json_read, json_write, utc_now

BROWSE = "https://app.traderepublic.com/browse/"
TR_TYPES = {"stock": "stock", "etf": "fund", "mutual_fund": "mutualFund"}
RELEVANCE_METHOD = "Orden predeterminado del explorador público TR (proxy de relevancia; fórmula no publicada)"


def collect_relevant(query, filters: list, cap: int) -> tuple[list, int, bool]:
    """Primeras apariciones en el orden global de TR; nunca se reordena por ISIN.

    No particionar por gestora/país: destruiría el orden global. Se colapsan
    repeticiones entre páginas y se detectan conteos que cambian durante la consulta.
    """
    found, total, stable = {}, None, True
    page = 1
    while total is None or (len(found) < min(cap, total) and page <= (total + 99) // 100):
        body = query({"type": "neonSearch", "data": {
            "q": "", "page": page, "pageSize": 100, "filter": filters}})
        count = int(body["resultCount"])
        if total is None:
            total = count
        stable = stable and count == total
        for item in body["results"]:
            if item["isin"] not in found and len(found) < min(cap, total):
                found[item["isin"]] = {**item, "relevance_rank": len(found) + 1}
        page += 1
    return list(found.values()), total, stable


def request_topic(ws, subscription: int, topic: dict) -> dict:
    ws.send(f"sub {subscription} " + json.dumps(topic))
    try:
        for _ in range(20):
            message = ws.recv(timeout=30)
            if not isinstance(message, str):
                raise ValueError("TR ha cambiado a un formato binario; revisar el proveedor público.")
            if message.startswith("echo"):
                continue
            parts = message.split(" ", 2)
            if len(parts) != 3 or parts[0] != str(subscription):
                continue
            if parts[1] != "A":
                raise ValueError(f"TR rechaza la consulta pública: {parts[2][:200]}")
            return json.loads(parts[2])
        raise ValueError("TR no responde a la suscripción pública.")
    finally:
        ws.send(f"unsub {subscription}")


def collect_instruments(query, filters: list, dimensions: tuple) -> tuple[list, int, bool]:
    """Particiona por etiquetas para evitar empates en páginas de grandes catálogos."""
    def page(number):
        return query({"type": "neonSearch", "data": {
            "q": "", "page": number, "pageSize": 100, "filter": filters}})
    first = page(1)
    total = int(first["resultCount"])
    found = {item["isin"]: item for item in first["results"]}
    stable = True
    if len(found) >= total:
        return list(found.values()), total, stable
    if dimensions:
        aggregations = query({"type": "neonSearchAggregations", "data": {"q": "", "filter": filters}})
        dimension, *remaining = dimensions
        groups = [g for g in aggregations["results"] if g["type"] == dimension]
        for group in groups:
            rows, _, child_stable = collect_instruments(
                query, filters + [{"key": dimension, "value": group["id"]}], tuple(remaining))
            found.update({r["isin"]: r for r in rows})
            stable = stable and child_stable
    # Captura también activos sin etiquetas. Las repeticiones se colapsan por ISIN.
    if len(found) < total:
        for _ in range(3):
            for number in range(2, (total + 99) // 100 + 1):
                body = page(number)
                stable = stable and int(body["resultCount"]) == total
                found.update({r["isin"]: r for r in body["results"]})
            if len(found) >= total:
                break
    return list(found.values()), total, stable


def fetch_public_catalog(root: Path, cfg: dict, refresh=False, offline=False) -> tuple[pd.DataFrame, dict]:
    folder = root / "data/catalog"
    cache = folder / "public_universe.csv"
    meta_path = folder / "public_manifest.json"
    cap = cfg.get("max_assets_per_type")
    if cache.exists() and meta_path.exists() and not refresh:
        meta = json_read(meta_path)
        age = (pd.Timestamp.now(tz="UTC") - pd.Timestamp(meta["downloaded_at"])).total_seconds() / 3600
        if ((offline or age < cfg["cache_hours"]) and meta["country"] == cfg["country"]
                and meta.get("selection_cap") == cap):
            if age > cfg["catalog_max_age_days"] * 24:
                meta["complete"] = False
                meta["complete_by_type"] = {k: False for k in TR_TYPES}
                meta["selection_complete_by_type"] = {k: False for k in TR_TYPES}
                meta["note"] += " Caché de catálogo antigua: actualizar conectado."
            return pd.read_csv(cache, dtype=str).fillna(""), meta
    if offline:
        raise ValueError("No hay catálogo público en caché para esta jurisdicción y límite; ejecuta catalog conectado.")
    all_rows, counts, expected_counts, complete_by_type = [], {}, {}, {}
    selection_complete_by_type = {}
    all_stable = True
    with connect("wss://api.traderepublic.com", origin="https://app.traderepublic.com",
                 open_timeout=20, close_timeout=5, max_size=8 * 1024 * 1024) as ws:
        ws.send("connect 34 " + json.dumps({"locale": "en", "platformId": "webtrading",
                 "platformVersion": "python", "clientId": "app.traderepublic.com", "clientVersion": "1.2639.8"}))
        if ws.recv(timeout=30) != "connected":
            raise ValueError("No se puede conectar al catálogo público de TR.")
        subscription = 0
        def query(topic):
            nonlocal subscription
            subscription += 1
            body = request_topic(ws, subscription, topic)
            if subscription % 50 == 0:
                print(f"Catálogo: {subscription} consultas públicas completadas", flush=True)
            time.sleep(cfg["request_pause_seconds"])
            return body
        for kind, tr_type in TR_TYPES.items():
            print(f"Consultando catálogo público: {kind}", flush=True)
            filters = [{"key": "type", "value": tr_type}, {"key": "jurisdiction", "value": cfg["country"]}]
            dimensions = ("country", "sector") if kind == "stock" else ("issuer",)
            items, total, stable = (collect_relevant(query, filters, cap) if cap is not None
                                    else collect_instruments(query, filters, dimensions))
            all_stable = all_stable and stable
            for item in items:
                if item.get("instrumentType") != tr_type:
                    raise ValueError(f"Tipo inesperado en {kind}: {item['isin']}")
                tags = item.get("tags", [])
                issuer = next((t["name"] for t in tags if t["type"] == "issuer"), "")
                sector = next((t["name"] for t in tags if t["type"] == "sector"), "")
                source = f"{BROWSE}{tr_type}?jurisdiction={cfg['country']}"
                all_rows.append({"isin": item["isin"], "name": item["name"], "asset_type": kind,
                                 "source": source, "verified_at": utc_now()[:10], "issuer": issuer,
                                 "catalog_sector": sector, "availability": "Catálogo público TR",
                                 **({"relevance_rank": item["relevance_rank"], "relevance_source": RELEVANCE_METHOD}
                                    if cap is not None else {})})
            counts[kind] = len(items)
            expected_counts[kind] = total
            complete_by_type[kind] = len(items) == total and stable
            selection_complete_by_type[kind] = len(items) == min(cap or total, total) and stable
            print(f"Catálogo {kind}: {len(items)}/{total}", flush=True)
    frame = pd.DataFrame(all_rows)
    complete = counts == expected_counts and all_stable
    manifest = {"downloaded_at": utc_now(), "country": cfg["country"], "complete": complete,
                "counts": counts, "expected_counts": expected_counts,
                "complete_by_type": complete_by_type,
                "selection_cap": cap, "selection_method": RELEVANCE_METHOD if cap is not None else "",
                "selection_complete_by_type": selection_complete_by_type,
                "source": BROWSE, "protocol": "public websocket v34 neonSearch",
                "note": "Enumeración completa del explorador público para la jurisdicción indicada; no verifica restricciones de una cuenta concreta."}
    if cap is not None:
        manifest["note"] = (f"Selección de hasta {cap} ISIN por tipo: {RELEVANCE_METHOD}. "
                            f"Recuperados: {counts}; universo publicado: {expected_counts}. "
                            "No equivale a todo Trade Republic ni acredita mayor patrimonio o calidad.")
        if not all(selection_complete_by_type.values()):
            manifest["note"] += " Selección incompleta o conteos cambiantes: revisar/actualizar."
    elif not complete:
        manifest["note"] = (f"Catálogo público parcial por empates en la paginación: {counts}; "
                            f"conteos publicados: {expected_counts}. No equivale a todo Trade Republic.")
    csv_write(cache, frame)
    json_write(meta_path, manifest)
    return frame, manifest


def public_metadata(root: Path, isin: str, kind: str, cfg: dict, refresh=False, offline=False) -> dict:
    path = root / f"data/raw/tr_{isin}.json"
    if path.exists() and not refresh:
        saved = json_read(path)
        age = (pd.Timestamp.now(tz="UTC") - pd.Timestamp(saved["downloaded_at"])).total_seconds() / 3600
        if (offline or age < cfg["cache_hours"]) and saved.get("country") == cfg["country"]:
            return saved
    if offline:
        raise ValueError("Sin metadatos públicos TR en caché para comprobar el tipo y disponibilidad.")
    with connect("wss://api.traderepublic.com", origin="https://app.traderepublic.com",
                 open_timeout=20, close_timeout=5) as ws:
        ws.send("connect 34 " + json.dumps({"locale": "en", "platformId": "webtrading",
                 "platformVersion": "python", "clientId": "app.traderepublic.com", "clientVersion": "1.2639.8"}))
        if ws.recv(timeout=30) != "connected":
            raise ValueError("No responde el catálogo público de TR.")
        body = request_topic(ws, 1, {"type": "instrument", "id": isin})
        if body.get("isin") != isin:
            raise ValueError("Metadatos TR con ISIN incorrecto.")
        fund = body.get("fundInfo") or {}
        result = {"isin": isin, "country": cfg["country"], "downloaded_at": utc_now(),
                  "active": body.get("jurisdictions", {}).get(cfg["country"], {}).get("active"),
                  "type": body.get("typeId"), "ucits": fund.get("ucits"),
                  "distribution": fund.get("useOfProfits"), "category": "",
                  "underlying_class": fund.get("underlyingClass"), "index": fund.get("index"),
                  "ter": fund.get("ter"), "source": f"{BROWSE}{TR_TYPES[kind]}?jurisdiction={cfg['country']}"}
        if kind == "mutual_fund":
            detail = request_topic(ws, 2, {"type": "mutualFundDetails", "id": isin})
            result.update({"category": detail.get("fundType"), "distribution": detail.get("dividend"),
                           "underlying_class": detail.get("assetType"), "ter": detail.get("totalExpenseRatio")})
        json_write(path, result)
        time.sleep(cfg["request_pause_seconds"])
        return result
