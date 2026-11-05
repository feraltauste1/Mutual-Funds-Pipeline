from __future__ import annotations

import numpy as np
import pandas as pd


def clean_prices(raw: pd.DataFrame, start, end) -> tuple[pd.DataFrame, dict]:
    frame = raw.copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce", utc=True).dt.tz_localize(None).dt.normalize()
    invalid_dates = int(frame.date.isna().sum())
    frame = frame.dropna(subset=["date"])
    duplicate_count = int(frame.date.duplicated(keep="last").sum())
    conflicts = int(frame.groupby("date").adjusted_price.nunique().gt(1).sum())
    frame = frame.drop_duplicates("date", keep="last").sort_values("date").set_index("date")
    frame = frame.loc[(frame.index >= pd.Timestamp(start)) & (frame.index <= pd.Timestamp(end))]
    weekend_count = int((frame.index.dayofweek >= 5).sum())
    frame = frame.loc[frame.index.dayofweek < 5]
    for col in ["price", "adjusted_price"]:
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
        frame.loc[~np.isfinite(frame[col]) | (frame[col] <= 0), col] = np.nan
    valid = frame.adjusted_price.dropna()
    if valid.empty:
        raise ValueError("No hay precios ajustados positivos.")
    # No crear precios previos al primer dato ni posteriores al último NAV/cierre.
    index = pd.bdate_range(valid.index.min(), valid.index.max(), name="date")
    frame = frame.reindex(index)
    frame["was_imputed"] = frame.adjusted_price.isna()
    missing = int(frame.was_imputed.sum())
    frame[["price", "adjusted_price"]] = frame[["price", "adjusted_price"]].ffill()
    # No se aplica otro ajuste por split: Yahoo Adj Close ya lo incorpora.
    frame["log_return"] = np.log(frame.adjusted_price / frame.adjusted_price.shift(1))
    frame["split"] = pd.to_numeric(frame.get("split", pd.Series(0, index=index)), errors="coerce").fillna(0)
    frame["dividend"] = pd.to_numeric(frame.get("dividend", pd.Series(0, index=index)), errors="coerce").fillna(0)
    quality = {"raw_rows": len(raw), "duplicate_dates": duplicate_count, "conflicting_duplicates": conflicts,
               "invalid_dates": invalid_dates, "weekend_rows_removed": weekend_count,
               "missing_business_days": missing, "imputed_fraction": missing / len(frame),
               "observed_rows": int((~frame.was_imputed).sum()), "clean_rows": len(frame),
               "split_events": int(frame.split.ne(0).sum()), "first_date": str(index.min().date()),
               "last_date": str(index.max().date())}
    return frame, quality


def price_metrics(prices: pd.DataFrame, cfg: dict, as_of=None) -> dict:
    as_of = pd.Timestamp(as_of if as_of is not None else prices.index.max())
    series = prices.adjusted_price_eur
    last = prices.index.max()
    stale = len(pd.bdate_range(last + pd.Timedelta(days=1), as_of))
    result = {"last_date": str(last.date()), "price_current": float(prices.price.iloc[-1]),
              "adjusted_price_current": float(prices.adjusted_price.iloc[-1]),
              "adjusted_price_eur": float(series.iloc[-1]), "stale_business_days": stale,
              "imputed_fraction": float(prices.was_imputed.mean()), "observations": len(prices),
              "eligible": False, "exclusion_reason": ""}
    for months in (3, 6, 12):
        target = as_of - pd.DateOffset(months=months)
        base = series.loc[series.index <= target]
        window = series.loc[(series.index > target) & (series.index <= as_of)]
        full = not base.empty and (target - base.index[-1]).days <= 7
        result[f"mean_{months}m"] = float(window.mean()) if full and len(window) else np.nan
        result[f"vs_mean_{months}m_pct"] = (series.iloc[-1] / window.mean() - 1) * 100 if full else np.nan
        result[f"return_{months}m_pct"] = (series.iloc[-1] / base.iloc[-1] - 1) * 100 if full else np.nan
        if months == 12:
            # Calidad del año de ranking: no penalizar un hueco de hace seis años.
            selected = prices.loc[prices.index >= target]
            observed = 1 - selected.was_imputed.mean()
            result["observed_fraction_12m"] = observed
            reasons = []
            if not full:
                reasons.append("Sin un año completo")
            if stale > cfg["max_stale_business_days"]:
                reasons.append("Último precio antiguo")
            if (1 - observed) > cfg["max_imputed_fraction"] + 1e-10 or observed < cfg["min_observed_fraction"]:
                reasons.append("Demasiados datos imputados")
            result["eligible"] = not reasons
            result["exclusion_reason"] = "; ".join(reasons)
    returns = prices.log_return_eur.dropna()
    result["daily_log_return_mean"] = returns.mean()
    result["annualized_volatility_pct"] = returns.std() * np.sqrt(252) * 100
    result["max_drawdown_pct"] = (series / series.cummax() - 1).min() * 100
    return result


def valuation(prices: pd.DataFrame, payload: dict, annual: list | None = None) -> tuple[dict, pd.DataFrame]:
    info = payload.get("info", {})
    currency = payload.get("currency") or info.get("currency")
    fields = {"pe_current": info.get("trailingPE"), "pe_forward": info.get("forwardPE"),
              "eps_ttm": info.get("trailingEps"), "eps_forward": info.get("forwardEps"),
              "earnings_growth": info.get("earningsGrowth"), "revenue_growth": info.get("revenueGrowth"),
              "roe": info.get("returnOnEquity"), "debt_to_equity": info.get("debtToEquity"),
              "profit_margin": info.get("profitMargins"), "pe_historical_median": np.nan,
              "pe_discount_pct": np.nan, "valuation_signal": "Sin datos comparables", "valuation_reason": ""}
    records = annual if annual is not None else payload.get("annual_eps", [])
    history, reasons = [], []
    split_dates = prices.index[prices.split.ne(0)]
    for row in records:
        fiscal = pd.Timestamp(row["fiscal_date"])
        if fiscal > prices.index.max():
            reasons.append("Ejercicio fiscal posterior al último precio observado")
            continue
        beginning = fiscal - pd.DateOffset(years=1)
        eps = pd.to_numeric(row.get("eps"), errors="coerce")
        if not np.isfinite(eps) or eps <= 0 or row.get("currency") != currency:
            reasons.append("EPS no positivo o divisa del EPS distinta de la cotización")
            continue
        if not row.get("split_adjusted", False) and (split_dates >= beginning).any():
            reasons.append("EPS sin base de acciones comparable tras un split")
            continue
        window = prices.loc[(prices.index > beginning) & (prices.index <= fiscal)]
        if len(window) < 220 or window.was_imputed.mean() > .05:
            reasons.append("Año fiscal sin precios suficientes")
            continue
        history.append({"fiscal_date": str(fiscal.date()), "eps_annual": eps,
                        "mean_price": window.price.mean(), "pe_annual": window.price.mean() / eps,
                        "source": row.get("source_url", "")})
    frame = pd.DataFrame(history)
    current = pd.to_numeric(fields["pe_current"], errors="coerce")
    ttm = pd.to_numeric(fields["eps_ttm"], errors="coerce")
    # El PER actual es el TTM publicado por Yahoo, con fecha de descarga explícita.
    if len(frame) >= 3 and np.isfinite(current) and current > 0 and np.isfinite(ttm) and ttm > 0:
        median = frame.pe_annual.median()
        fields["pe_historical_median"] = median
        fields["pe_discount_pct"] = (1 - current / median) * 100
        if current <= .8 * median:
            fields["valuation_signal"] = "PER inferior a su historia; revisar posible value"
        elif current >= 1.2 * median and (info.get("earningsGrowth") or 0) > 0:
            fields["valuation_signal"] = "Prima de PER y beneficio creciente; revisar growth"
        else:
            fields["valuation_signal"] = "Sin señal clara por PER"
    else:
        reasons.append("Se requieren 3 años de PER y PER/EPS TTM positivos")
    fields["valuation_reason"] = "; ".join(sorted(set(reasons)))
    return fields, frame


def cluster_unknown(metrics: pd.DataFrame, returns: dict[str, pd.Series]) -> pd.DataFrame:
    from sklearn.cluster import KMeans
    from sklearn.preprocessing import StandardScaler

    result = metrics.copy()
    missing = result.loc[result.sector == "Sin clasificar", "isin"].tolist()
    if len(missing) < 4:
        return result
    # Cada activo se describe por su trayectoria; nunca se etiqueta un cluster como sector real.
    matrix = pd.concat({i: returns[i] for i in missing if i in returns}, axis=1).tail(252)
    matrix = matrix.loc[:, matrix.notna().mean() >= .9].dropna()
    if len(matrix) < 60 or matrix.shape[1] < 4:
        return result
    features = StandardScaler().fit_transform(matrix.T)
    if np.unique(features, axis=0).shape[0] < 2:
        return result
    groups = KMeans(n_clusters=min(3, max(2, matrix.shape[1] // 3)), random_state=42,
                    n_init=10).fit_predict(features)
    for isin, group in zip(matrix.columns, groups, strict=True):
        result.loc[result["isin"] == isin, "sector"] = f"Grupo estadístico {group + 1} (sector desconocido)"
        result.loc[result["isin"] == isin, "sector_source"] = "Clustering de retornos; no es un sector económico"
    return result


def rank(metrics: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    if metrics.empty:
        return metrics.copy(), metrics.copy()
    eligible = metrics.loc[metrics.eligible & metrics.return_12m_pct.notna()].copy()
    ordered = eligible.sort_values(["return_12m_pct", "isin"], ascending=[False, True])
    global_top = ordered.head(5).copy()
    sector_top = ordered.groupby("sector", sort=True, group_keys=False).head(5).copy()
    global_top["rank"] = range(1, len(global_top) + 1)
    sector_top["rank"] = sector_top.groupby("sector").cumcount() + 1
    return global_top, sector_top.sort_values(["sector", "rank"])
