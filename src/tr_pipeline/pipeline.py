from __future__ import annotations

import uuid
from pathlib import Path

import numpy as np
import pandas as pd

from .analytics import clean_prices, cluster_unknown, price_metrics, rank, valuation
from .catalog import build_catalog
from .common import ASSET_TYPES, LABELS, config, csv_write, fingerprint, json_read, json_write, today, utc_now
from .providers import download_asset, eur_rates, mapping_for
from .tr_public import public_metadata

METRIC_COLUMNS = ["isin", "name", "sector", "sector_source", "return_12m_pct", "eligible", "exclusion_reason"]
PRICE_COLUMNS = ["date", "isin", "currency", "price", "adjusted_price", "log_return", "was_imputed",
                 "split", "dividend", "adjusted_price_eur", "log_return_eur"]


def select_assets(frame: pd.DataFrame, cap: int | None) -> pd.DataFrame:
    """Aplica el límite por relevancia explícita; el orden lexicográfico no es relevancia."""
    selected = frame.copy()
    if "relevance_rank" in selected:
        ranks = pd.to_numeric(selected.relevance_rank, errors="coerce")
        valid = ranks.notna() & np.isfinite(ranks) & ranks.gt(0) & ranks.eq(ranks.round())
        if cap is not None and len(selected) > cap and not valid.all():
            raise ValueError("El catálogo necesita relevance_rank positivo para seleccionar los más relevantes.")
        selected["relevance_rank"] = ranks.where(valid)
        selected = selected.sort_values(["relevance_rank", "isin"], kind="stable", na_position="last")
    elif cap is not None and len(selected) > cap:
        raise ValueError("Catálogo sin relevance_rank: usa catalog_source='public' o aporta un orden de relevancia.")
    return selected.head(cap) if cap is not None else selected


def input_signature(root: Path, cfg: dict, catalog_manifest: dict, kind: str, limit=None, isins=None) -> str:
    # Cambios de catálogo, configuración, fuentes manuales o código invalidan conclusiones.
    import hashlib
    paths = list((root / "data/manual").rglob("*.csv"))
    code = Path(__file__).parent
    paths += list(code.glob("*.py"))
    signatures = {str(p.relative_to(root)) if p.is_relative_to(root) else p.name:
                  hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(paths)}
    return fingerprint({"config": cfg, "catalog": catalog_manifest["catalog_hash"],
                        "complete": catalog_manifest["complete"], "kind": kind,
                        "coverage": {k: catalog_manifest.get(k) for k in (
                            "expected_counts", "complete_by_type", "selection_complete_by_type", "selection_method")},
                        "as_of": str(today().date()), "limit": limit, "isins": sorted(isins or []), "files": signatures})


def classify(root: Path, row: dict, payload: dict) -> tuple[str, str]:
    mapping = mapping_for(root, row["isin"])
    if mapping.get("sector"):
        if not mapping.get("sector_source"):
            raise ValueError("Sector manual sin sector_source.")
        return mapping["sector"], mapping["sector_source"]
    if row.get("catalog_sector"):
        return row["catalog_sector"], "Trade Republic: etiqueta de sector"
    info = payload.get("info", {})
    # Fondos diversificados o de bonos tienen categoría, no un sector corporativo.
    if payload.get("tr_metadata", {}).get("category"):
        return payload["tr_metadata"]["category"], "Trade Republic: categoría del fondo"
    if row["asset_type"] == "etf" and payload.get("tr_metadata", {}).get("underlying_class"):
        meta = payload["tr_metadata"]
        return str(meta["underlying_class"]) + " (sin sector específico)", "Trade Republic: clase de activo del ETF"
    value = info.get("sector") if row["asset_type"] == "stock" else info.get("category")
    return (str(value), "Yahoo: sector" if row["asset_type"] == "stock" else "Yahoo: categoría del fondo") if value else (
        "Sin clasificar", "Sin metadatos suficientes")


def run_asset_type(root: Path, kind: str, refresh=False, offline=False, limit=None,
                   catalog=None, run_id=None, isins=None) -> dict:
    if kind not in ASSET_TYPES:
        raise ValueError(f"Tipo desconocido: {kind}")
    cfg = config(root)
    if cfg["base_currency"] != "EUR":
        raise ValueError("Esta versión compara en EUR; base_currency debe ser EUR.")
    universe, catalog_manifest = catalog if catalog is not None else build_catalog(root, offline=offline)
    selected = universe.loc[universe.asset_type == kind]
    total = len(selected)
    if isins:
        unknown = set(isins) - set(universe["isin"])
        if unknown:
            raise ValueError(f"ISIN fuera del catálogo cargado: {sorted(unknown)}")
        selected = selected.loc[selected["isin"].isin(isins)]
    selected = select_assets(selected, cfg.get("max_assets_per_type"))
    if limit is not None:
        if limit < 1:
            raise ValueError("limit debe ser positivo.")
        selected = selected.head(limit)
    folder = root / f"data/processed/{kind}"
    folder.mkdir(parents=True, exist_ok=True)
    # Se invalida el manifest previo antes de modificar productos dependientes.
    manifest_path = folder / "manifest.json"
    if manifest_path.exists():
        manifest_path.unlink()
    csv_write(folder / "selection.csv", selected)
    metrics, qualities, failures, valuations, returns = [], [], [], [], {}
    # Escritura incremental: miles de series no se acumulan completas en RAM.
    prices_tmp = folder / "prices.tmp"
    pd.DataFrame(columns=PRICE_COLUMNS).to_csv(prices_tmp, index=False)
    as_of = today() - pd.Timedelta(days=1)
    for n, row in enumerate(selected.to_dict("records"), 1):
        isin = row["isin"]
        print(f"[{kind} {n}/{len(selected)}] {isin} {row['name']}", flush=True)
        try:
            tr_meta = {}
            if row.get("source", "").startswith("https://app.traderepublic.com/browse/"):
                tr_meta = public_metadata(root, isin, kind, cfg, refresh=refresh, offline=offline)
                if tr_meta["active"] is not True:
                    raise ValueError("Trade Republic no confirma disponibilidad activa en esta jurisdicción.")
                if kind == "etf" and tr_meta["ucits"] is not True:
                    raise ValueError("Sin acreditación ETF UCITS: excluido para evitar mezclar ETC/ETN u otros vehículos.")
            payload = download_asset(root, row, cfg, refresh=refresh, offline=offline)
            payload["tr_metadata"] = tr_meta
            clean, quality = clean_prices(pd.DataFrame(payload["prices"]), cfg["start"], as_of)
            currency = payload.get("currency") or payload.get("info", {}).get("currency")
            fx = eur_rates(root, currency, clean.index, cfg, refresh=refresh, offline=offline)
            clean["adjusted_price_eur"] = clean.adjusted_price * fx
            clean["log_return_eur"] = np.log(clean.adjusted_price_eur / clean.adjusted_price_eur.shift(1))
            clean["isin"], clean["currency"] = isin, currency
            sector, sector_source = classify(root, row, payload)
            result = {"isin": isin, "name": row["name"], "yahoo_name": payload.get("info", {}).get("longName"),
                      "asset_type": kind, "symbol": payload.get("symbol", ""), "sector": sector,
                      "yahoo_quote_type": payload.get("info", {}).get("quoteType"),
                      "issuer": row.get("issuer", ""), "availability": row.get("availability", "Aportado por catálogo"),
                      "relevance_rank": row.get("relevance_rank"),
                      "sector_source": sector_source, "currency": currency, "source": payload["source"],
                      "downloaded_at": payload["downloaded_at"], "identity_source": payload["identity_source"],
                      "distribution": tr_meta.get("distribution"),
                      "benchmark": tr_meta.get("index"), "ter": tr_meta.get("ter"),
                      **price_metrics(clean, cfg, as_of)}
            if quality["conflicting_duplicates"]:
                result["eligible"] = False
                result["exclusion_reason"] += "; Duplicados con precios en conflicto: revisar"
            if (tr_meta.get("distribution") in {"distributing", "distribution"}
                    and clean.dividend.eq(0).all()
                    and np.allclose(clean.adjusted_price, clean.price, equal_nan=True)):
                result["eligible"] = False
                result["exclusion_reason"] += "; Clase de distribución sin distribuciones ni ajuste verificable"
            if kind == "stock":
                manual = root / f"data/manual/fundamentals/{isin}.csv"
                annual = None
                if manual.exists():
                    eps_frame = pd.read_csv(manual)
                    required = {"fiscal_date", "eps", "currency", "split_adjusted", "source_url"}
                    if not required.issubset(eps_frame):
                        raise ValueError(f"EPS manual necesita {sorted(required)}")
                    eps_frame["split_adjusted"] = eps_frame.split_adjusted.astype(str).str.lower().eq("true")
                    if not eps_frame.source_url.astype(str).str.startswith("https://").all():
                        raise ValueError("EPS manual sin fuentes válidas.")
                    if eps_frame.fiscal_date.duplicated().any():
                        raise ValueError("EPS manual con años fiscales duplicados.")
                    annual = eps_frame.to_dict("records")
                value, annual_pe = valuation(clean, payload, annual)
                result.update(value)
                if not annual_pe.empty:
                    annual_pe["isin"] = isin
                    valuations.append(annual_pe)
            exported = clean.reset_index()[PRICE_COLUMNS]
            csv_write(folder / f"prices/{isin}.csv", exported)
            exported.to_csv(prices_tmp, mode="a", header=False, index=False)
            metrics.append(result)
            qualities.append({"isin": isin, **quality, "warnings": "; ".join(payload.get("warnings", []))})
            if sector == "Sin clasificar":
                returns[isin] = clean.log_return_eur.tail(252)
        except Exception as exc:
            failures.append({"isin": isin, "name": row["name"], "reason": str(exc)})
            print(f"  Pendiente: {exc}", flush=True)
    metrics_df = pd.DataFrame(metrics) if metrics else pd.DataFrame(columns=METRIC_COLUMNS)
    if cfg["cluster_unknown"] and not metrics_df.empty:
        metrics_df = cluster_unknown(metrics_df, returns)
    top_global, top_sector = rank(metrics_df)
    artifacts = {
        "metrics.csv": metrics_df,
        "quality.csv": pd.DataFrame(qualities, columns=["isin", "raw_rows", "duplicate_dates",
            "conflicting_duplicates", "invalid_dates", "weekend_rows_removed", "missing_business_days",
            "imputed_fraction", "observed_rows", "clean_rows", "split_events", "first_date", "last_date", "warnings"]),
        "failures.csv": pd.DataFrame(failures, columns=["isin", "name", "reason"]),
        "pe_annual.csv": pd.concat(valuations, ignore_index=True) if valuations else pd.DataFrame(
            columns=["isin", "fiscal_date", "eps_annual", "mean_price", "pe_annual", "source"]),
    }
    for filename, frame in artifacts.items():
        csv_write(folder / filename, frame)
    prices_tmp.replace(folder / "prices.csv")
    csv_write(root / f"reports/{kind}_top5_global.csv", top_global)
    csv_write(root / f"reports/{kind}_top5_sector.csv", top_sector)
    catalog_complete = catalog_manifest.get("complete_by_type", {}).get(kind, catalog_manifest["complete"])
    expected = catalog_manifest.get("expected_counts", {}).get(kind, total)
    selection_limited = len(selected) < expected
    selection_method = catalog_manifest.get("selection_method") or (
        "relevance_rank aportado en el catálogo" if "relevance_rank" in selected else "Todos los ISIN del catálogo cargado")
    catalog_note = (f"Selección: {len(selected)} ISIN de {expected} publicados; catálogo cargado: {total}. "
                    f"Criterio: {selection_method}. "
                    "La disponibilidad en una cuenta concreta puede variar.")
    manifest = {"run_id": run_id or uuid.uuid4().hex, "created_at": utc_now(), "asset_type": kind,
                "as_of": str(as_of.date()), "catalog_total": total, "attempted": len(selected),
                "downloaded": len(metrics_df), "failed": len(failures),
                "eligible": int(metrics_df.eligible.sum()) if not metrics_df.empty else 0,
                "catalog_complete": catalog_complete, "catalog_note": catalog_note,
                "published_total": expected, "selection_cap": cfg.get("max_assets_per_type"),
                "selection_method": selection_method, "selection_limited": selection_limited,
                "selection_complete": (catalog_manifest.get("selection_complete_by_type", {}).get(kind, catalog_complete)
                                       and len(selected) == min(cfg.get("max_assets_per_type", expected), expected)
                                       and limit is None and not isins and len(failures) == 0),
                "scope_limited": limit is not None or bool(isins), "offline": offline,
                "complete_analysis": (catalog_complete and not selection_limited and limit is None
                                      and not isins and len(failures) == 0),
                "signature": input_signature(root, cfg, catalog_manifest, kind, limit, isins),
                "warnings": ["Ranking por retorno total a 12 meses en EUR, con cierre anterior al día actual.",
                             "Los grupos de fondos son categorías; un cluster no acredita un sector."]}
    json_write(manifest_path, manifest)
    return {"manifest": manifest, "metrics": metrics_df, "top_global": top_global, "top_sector": top_sector}


def run_all(root: Path, refresh=False, offline=False, limit=None, isins=None) -> dict:
    catalog = build_catalog(root, refresh=refresh and not offline, offline=offline)
    run_id = uuid.uuid4().hex
    # Primero fondos y ETF, que son la prioridad del análisis.
    for kind in ("mutual_fund", "etf", "stock"):
        run_asset_type(root, kind, refresh=refresh, offline=offline, limit=limit, catalog=catalog,
                       run_id=run_id, isins=isins)
    return conclusions(root, refresh_dependencies=False)


def ensure_results(root: Path, offline=False):
    cfg = config(root)
    catalog = build_catalog(root, offline=offline)
    for kind in ASSET_TYPES:
        path = root / f"data/processed/{kind}/manifest.json"
        valid = False
        if path.exists():
            saved = json_read(path)
            # Un ensayo --limit nunca puede convertirse en ranking completo al abrir 04.
            valid = (saved.get("signature") == input_signature(root, cfg, catalog[1], kind)
                     and not saved.get("scope_limited")
                     and all((root / f"data/processed/{kind}/{f}").exists() for f in
                             ["metrics.csv", "quality.csv", "prices.csv", "failures.csv", "pe_annual.csv", "selection.csv"])
                     and all((root / f"reports/{kind}_top5_{f}.csv").exists() for f in ["global", "sector"]))
        if not valid:
            run_asset_type(root, kind, offline=offline, catalog=catalog)


def conclusions(root: Path, refresh_dependencies=True, offline=False) -> dict:
    if refresh_dependencies:
        ensure_results(root, offline=offline)
    all_results = {}
    lines = ["# Resultados de Trade Republic", "", "**Clasificación por rentabilidad pasada a un año en EUR.**",
             "El primer puesto ha subido más; no significa que vaya a hacerlo mejor en el futuro.", "",
             "Medias y retornos usan precios ajustados por distribuciones y splits.",
             "Los precios de Yahoo pueden corresponder a otra bolsa del mismo ISIN; no son cotizaciones de ejecución de TR.", ""]
    for kind in ASSET_TYPES:
        folder = root / f"data/processed/{kind}"
        manifest = json_read(folder / "manifest.json")
        metrics = pd.read_csv(folder / "metrics.csv")
        global_top = pd.read_csv(root / f"reports/{kind}_top5_global.csv")
        sector_top = pd.read_csv(root / f"reports/{kind}_top5_sector.csv")
        lines += [f"## {LABELS[kind]}", "", f"Fecha de referencia: {manifest['as_of']}.",
                  f"Con precios: {manifest['downloaded']} de {manifest['attempted']} ISIN seleccionados "
                  f"(universo publicado: {manifest.get('published_total', manifest['catalog_total'])}). "
                  f"Con un año y calidad suficiente: {manifest['eligible']}. Errores: {manifest['failed']}.", ""]
        lines += [f"Criterio de selección: {manifest.get('selection_method', 'Ver catálogo')}. "
                  "La selección previa por relevancia es independiente del ranking por rentabilidad.", ""]
        if not manifest["complete_analysis"]:
            lines += ["**RESULTADOS PARCIALES. No representan el top de todo Trade Republic.**",
                      manifest["catalog_note"], ""]
        if manifest["scope_limited"]:
            lines += ["**Ejecución de prueba con límite de activos.**", ""]
        lines += ["### Top 5 global", "", table_markdown(global_top), "", "### Top 5 por sector o categoría", ""]
        if sector_top.empty:
            lines += ["No hay activos elegibles para clasificar.", ""]
        else:
            for sector, group in sector_top.groupby("sector", sort=True):
                lines += [f"**{sector}**", "", table_markdown(group), ""]
        if kind == "stock":
            signals = metrics.loc[metrics.get("pe_discount_pct", pd.Series(dtype=float)).notna()] if (
                "pe_discount_pct" in metrics) else pd.DataFrame()
            lines += ["### Lectura sencilla de PER", "",
                      "PER inferior al histórico: posible candidato a revisar por valoración. "
                      "PER superior y beneficios crecientes: posible perfil growth. "
                      "El PER por sí solo no confirma una oportunidad.", ""]
            if signals.empty:
                lines += ["No hay comparaciones históricas suficientes y fiables en esta ejecución.", ""]
            else:
                for _, r in signals.sort_values("pe_discount_pct", ascending=False).head(5).iterrows():
                    lines += [f"- {r['isin']} — {r['name']}: {r['valuation_signal']}. "
                              f"PER TTM {r['pe_current']:.1f}; mediana anual {r['pe_historical_median']:.1f}."]
                lines += [""]
        all_results[kind] = {"manifest": manifest, "top_global": global_top, "top_sector": sector_top}
    report = root / "reports/conclusiones.md"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text("\n".join(lines), encoding="utf-8")
    json_write(root / "reports/conclusiones_manifest.json",
               {"created_at": utc_now(), "dependencies": {k: v["manifest"] for k, v in all_results.items()}})
    return all_results


def table_markdown(frame: pd.DataFrame) -> str:
    if frame.empty:
        return "Sin datos elegibles. No se inventan cinco resultados."
    lines = ["| Puesto | ISIN | Nombre | Rentabilidad 1 año |", "| --- | --- | --- | ---: |"]
    for _, row in frame.iterrows():
        name = str(row["name"]).replace("|", "/").replace("\n", " ")
        lines.append(f"| {int(row['rank'])} | {row['isin']} | {name} | {row['return_12m_pct']:.2f}% |")
    return "\n".join(lines)

