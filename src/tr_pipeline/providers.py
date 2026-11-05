from __future__ import annotations

import time
from pathlib import Path

import pandas as pd
import requests
import yfinance as yf

from .common import csv_write, fingerprint, json_read, json_write, today, utc_now

QUOTE_TYPES = {"stock": "EQUITY", "mutual_fund": "MUTUALFUND", "etf": "ETF"}
# Yahoo también etiqueta como ETF cotizaciones bursátiles de fondos tradicionales.
# El tipo económico lo fija el catálogo TR; la identidad sigue comprobándose por ISIN.
COMPATIBLE_QUOTE_TYPES = {"stock": {"EQUITY"}, "mutual_fund": {"MUTUALFUND", "ETF"}, "etf": {"ETF"}}


def retry(fn, cfg):
    for attempt in range(cfg["retries"] + 1):
        try:
            return fn()
        except Exception:
            if attempt == cfg["retries"]:
                raise
            time.sleep(2 ** attempt)


def mapping_for(root: Path, isin: str) -> dict:
    path = root / "data/manual/mappings.csv"
    if not path.exists():
        return {}
    frame = pd.read_csv(path, dtype=str).fillna("")
    if frame["isin"].duplicated().any():
        raise ValueError("mappings.csv contiene ISIN duplicados.")
    found = frame[frame["isin"] == isin]
    return found.iloc[0].to_dict() if len(found) else {}


def resolve(isin: str, kind: str, mapping: dict, cfg: dict) -> tuple[str, str]:
    if mapping.get("yahoo_symbol"):
        if not mapping.get("identity_source", "").startswith("https://"):
            raise ValueError("Un ticker manual necesita identity_source (URL de comprobación ISIN/clase).")
        return mapping["yahoo_symbol"], mapping["identity_source"]
    quotes = retry(lambda: yf.Search(isin, max_results=8, enable_fuzzy_query=False).quotes, cfg)
    candidates = [q for q in quotes if q.get("quoteType") in COMPATIBLE_QUOTE_TYPES[kind]]
    for quote in candidates:
        symbol = quote["symbol"]
        # Buscar por nombre no acredita la clase: comprobación inversa por ISIN.
        back = quote.get("isin") or retry(lambda symbol=symbol: yf.Ticker(symbol).get_isin(), cfg)
        if back == isin:
            return symbol, f"Yahoo search ISIN + reverse ISIN: {isin}"
        # Yahoo no suele publicar el ISIN inverso de fondos. Su búsqueda exacta
        # sin fuzzy es utilizable si devuelve UNA sola clase y no contradice el ISIN.
        if back in {None, "", "-"} and len(candidates) == 1:
            return symbol, f"https://finance.yahoo.com/lookup/?s={isin} (búsqueda ISIN exacta, sin fuzzy)"
    raise ValueError("Sin correspondencia Yahoo verificable para este ISIN y tipo. Revisar mappings.csv.")


def local_prices(root: Path, row: dict) -> dict | None:
    path = root / f"data/manual/prices/{row['isin']}.csv"
    if not path.exists():
        return None
    frame = pd.read_csv(path)
    required = {"date", "price", "adjusted_price", "currency", "source_url"}
    if not required.issubset(frame):
        raise ValueError(f"CSV emisor necesita {sorted(required)}")
    if frame.currency.nunique() != 1 or frame.source_url.nunique() != 1:
        raise ValueError("CSV emisor debe tener una sola divisa y fuente.")
    if not str(frame.source_url.iloc[0]).startswith("https://"):
        raise ValueError("CSV emisor sin URL de fuente válida.")
    return {"prices": frame.to_dict("records"), "info": {"currency": frame.currency.iloc[0]},
            "annual_eps": [], "symbol": "", "identity_source": str(path),
            "source": frame.source_url.iloc[0], "downloaded_at": utc_now(), "warnings": []}


def download_asset(root: Path, row: dict, cfg: dict, refresh=False, offline=False) -> dict:
    local = local_prices(root, row)
    if local is not None:
        return local
    mapping = mapping_for(root, row["isin"])
    cache = root / f"data/raw/{row['isin']}.json"
    cache_key = fingerprint({"isin": row["isin"], "kind": row["asset_type"], "start": cfg["start"],
                             "end": str(today().date()), "mapping": mapping, "schema": 1})
    if cache.exists():
        saved = json_read(cache)
        age = (pd.Timestamp.now(tz="UTC") - pd.Timestamp(saved["downloaded_at"])).total_seconds() / 3600
        if saved.get("cache_key") == cache_key and (offline or (not refresh and age < cfg["cache_hours"])):
            return saved
    if offline:
        raise ValueError("Sin caché vigente para esta configuración; ejecuta conectado primero.")
    symbol, identity = resolve(row["isin"], row["asset_type"], mapping, cfg)
    ticker = yf.Ticker(symbol)
    history = retry(lambda: ticker.history(start=cfg["start"], end=str(today().date()),
                                           auto_adjust=False, actions=True, repair=True,
                                           keepna=True, raise_errors=True, timeout=25), cfg)
    if history.empty:
        raise ValueError("Yahoo no devuelve precios.")
    if "Adj Close" not in history:
        raise ValueError("Yahoo no devuelve precio ajustado; no se sustituye por cierre sin ajustar.")
    history.index = history.index.tz_localize(None).normalize()
    warnings = []
    try:
        info = retry(ticker.get_info, cfg)
    except Exception as exc:
        info = {}
        warnings.append(f"Metadatos incompletos: {exc}")
    if info.get("quoteType") and info["quoteType"] not in COMPATIBLE_QUOTE_TYPES[row["asset_type"]]:
        raise ValueError(f"Tipo Yahoo {info['quoteType']} no coincide con el catálogo.")
    if row["asset_type"] == "mutual_fund" and info.get("quoteType") == "ETF":
        warnings.append("Yahoo etiqueta ETF una cotización de este fondo tradicional; se conserva el tipo del catálogo. "
                        "La serie es un cierre bursátil ajustado de la misma clase, no necesariamente su NAV.")
    currency = info.get("currency") or ticker.get_history_metadata().get("currency")
    if not currency:
        raise ValueError("No hay divisa del precio.")
    frame = history.rename(columns={"Close": "price", "Adj Close": "adjusted_price",
                                    "Stock Splits": "split", "Dividends": "dividend"})
    frame["date"] = frame.index.strftime("%Y-%m-%d")
    fields = [c for c in ["date", "price", "adjusted_price", "split", "dividend", "Repaired?"] if c in frame]
    frame = frame[fields].reset_index(drop=True)
    annual = []
    if row["asset_type"] == "stock":
        try:
            stmt = retry(lambda: ticker.get_income_stmt(freq="yearly"), cfg)
            key = next((k for k in ("DilutedEPS", "Diluted EPS") if k in stmt.index), None)
            if key:
                annual = [{"fiscal_date": str(pd.Timestamp(d).date()), "eps": float(v),
                           "currency": info.get("financialCurrency"), "split_adjusted": False,
                           "source_url": f"https://finance.yahoo.com/quote/{symbol}/financials/"}
                          for d, v in stmt.loc[key].items() if pd.notna(v)]
        except Exception as exc:
            warnings.append(f"EPS anual no disponible: {exc}")
    keep = ["shortName", "longName", "currency", "financialCurrency", "sector", "industry", "category",
            "trailingPE", "forwardPE", "trailingEps", "forwardEps", "earningsGrowth", "revenueGrowth",
            "returnOnEquity", "debtToEquity", "profitMargins", "quoteType"]
    result = {"prices": frame.to_dict("records"), "info": {k: info.get(k) for k in keep},
              "annual_eps": annual, "symbol": symbol, "identity_source": identity,
              "source": f"https://finance.yahoo.com/quote/{symbol}/history/", "currency": currency,
              "downloaded_at": utc_now(), "warnings": warnings, "cache_key": cache_key}
    json_write(cache, result)
    time.sleep(cfg["request_pause_seconds"])
    return result


def eur_rates(root: Path, currency: str, index: pd.DatetimeIndex, cfg: dict,
              refresh=False, offline=False) -> pd.Series:
    # GBp/GBX y ZAc son unidades de cotización, no códigos ISO de divisa.
    currency, scale = {"GBp": ("GBP", .01), "GBX": ("GBP", .01),
                       "ZAc": ("ZAR", .01)}.get(currency, (currency, 1.0))
    if currency == "EUR":
        return pd.Series(scale, index=index)
    start = index.min() - pd.Timedelta(days=10)
    end = index.max()
    path = root / f"data/raw/fx_{currency}_{start.date()}_{end.date()}.csv"
    if refresh or not path.exists():
        if offline:
            raise ValueError(f"Sin caché BCE de {currency}/EUR.")
        url = f"https://api.frankfurter.dev/v1/{start.date()}..{end.date()}"
        def fetch():
            res = requests.get(url, params={"base": currency, "symbols": "EUR"}, timeout=40)
            res.raise_for_status()
            return res.json()
        body = retry(fetch, cfg)
        records = [{"date": d, "rate": v["EUR"]} for d, v in body["rates"].items()]
        if not records:
            raise ValueError(f"Sin cambio BCE para {currency}.")
        csv_write(path, pd.DataFrame(records))
        json_write(path.with_suffix(".json"), {"source": url, "provider": "ECB (Frankfurter v1)",
                                              "downloaded_at": utc_now()})
    frame = pd.read_csv(path, parse_dates=["date"]).set_index("date").sort_index()
    rates = frame.rate.reindex(frame.index.union(index)).ffill().reindex(index)
    # No arrastrar cotizaciones FX viejas indefinidamente.
    seen = pd.Series(frame.index, index=frame.index).reindex(frame.index.union(index)).ffill().reindex(index)
    rates[(pd.Series(index, index=index) - seen).dt.days > 7] = float("nan")
    if rates.isna().any():
        raise ValueError(f"Cambio BCE ausente o antiguo para {currency}.")
    return rates * scale

