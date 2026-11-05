import numpy as np
import pandas as pd

from tr_pipeline.analytics import clean_prices, cluster_unknown, price_metrics, rank, valuation
from tr_pipeline.catalog import valid_isin


def test_cleanup_duplicates_weekends_and_forward_only():
    raw = pd.DataFrame({"date": ["2025-01-03", "2025-01-04", "2025-01-07", "2025-01-07", "bad"],
                        "price": [100, 999, 110, 111, 50], "adjusted_price": [100, 999, 110, 111, 50]})
    prices, quality = clean_prices(raw, "2025-01-01", "2025-01-10")
    assert prices.index.tolist() == list(pd.to_datetime(["2025-01-03", "2025-01-06", "2025-01-07"]))
    assert prices.loc["2025-01-06", "adjusted_price"] == 100
    assert prices.loc["2025-01-06", "was_imputed"]
    assert np.isclose(prices.loc["2025-01-07", "log_return"], np.log(111 / 100))
    assert quality["duplicate_dates"] == quality["conflicting_duplicates"] == 1
    assert quality["invalid_dates"] == quality["weekend_rows_removed"] == 1
    assert prices.log_return.iloc[0] != prices.log_return.iloc[0]  # primer retorno es NaN


def test_split_is_not_adjusted_twice():
    raw = pd.DataFrame({"date": ["2025-01-02", "2025-01-03"], "price": [50, 50],
                        "adjusted_price": [50, 50], "split": [0, 2]})
    clean, quality = clean_prices(raw, "2025-01-01", "2025-01-04")
    assert clean.log_return.iloc[-1] == 0
    assert quality["split_events"] == 1


def test_calendar_horizons_and_stale_price_exclusion():
    index = pd.bdate_range("2023-01-02", "2025-01-03")
    frame = pd.DataFrame({"adjusted_price_eur": np.linspace(100, 200, len(index)), "price": 100,
                          "adjusted_price": 100, "was_imputed": False, "log_return_eur": .001}, index=index)
    cfg = {"max_stale_business_days": 5, "max_imputed_fraction": .05, "min_observed_fraction": .9}
    m = price_metrics(frame, cfg, "2025-01-03")
    expected = frame.loc[frame.index > pd.Timestamp("2024-10-03"), "adjusted_price_eur"].mean()
    assert np.isclose(m["mean_3m"], expected)
    assert m["eligible"]
    assert not price_metrics(frame, cfg, "2025-02-01")["eligible"]
    frame.loc["2024-12-02":, "was_imputed"] = True
    assert not price_metrics(frame, cfg, "2025-01-03")["eligible"]


def test_pe_annual_positive_currency_and_split_guards():
    index = pd.bdate_range("2020-01-01", "2025-06-30")
    prices = pd.DataFrame({"price": 100, "split": 0, "was_imputed": False}, index=index)
    annual = [{"fiscal_date": f"{year}-12-31", "eps": 5., "currency": "USD", "split_adjusted": False}
              for year in [2021, 2022, 2023, 2024]]
    payload = {"currency": "USD", "info": {"trailingPE": 12, "trailingEps": 8, "earningsGrowth": .2},
               "annual_eps": annual}
    value, history = valuation(prices, payload)
    assert len(history) == 4 and value["pe_historical_median"] == 20
    assert value["pe_discount_pct"] == 40
    prices.loc["2025-01-02", "split"] = 2
    value, history = valuation(prices, payload)
    assert history.empty and np.isnan(value["pe_discount_pct"])
    annual[0]["split_adjusted"] = True
    annual[0]["currency"] = "EUR"
    _, history = valuation(prices, payload)
    assert history.empty


def test_rank_is_one_year_not_price_level_and_ties_are_stable():
    metrics = pd.DataFrame({"isin": ["B", "A", "C", "D"], "sector": ["Tech", "Tech", "Energy", "Energy"],
                            "eligible": [True, True, True, False], "return_12m_pct": [10., 10., 5., 100.]})
    glob, sector = rank(metrics)
    assert glob["isin"].tolist() == ["A", "B", "C"]
    assert sector.groupby("sector").size().max() <= 5


def test_future_fiscal_eps_is_not_used_as_realized_history():
    index = pd.bdate_range("2024-01-01", "2025-11-28")
    prices = pd.DataFrame({"price": 100, "split": 0, "was_imputed": False}, index=index)
    payload = {"currency": "EUR", "annual_eps": [{"fiscal_date": "2025-12-31", "eps": 5,
                                                  "currency": "EUR", "split_adjusted": True}]}
    value, history = valuation(prices, payload)
    assert history.empty and "posterior" in value["valuation_reason"]


def test_clustering_preserves_known_sector_and_never_names_an_economic_sector():
    rng = np.random.default_rng(42)
    index = pd.bdate_range("2024-01-01", periods=100)
    isins = list("ABCDE")
    metrics = pd.DataFrame({"isin": isins, "sector": ["Technology"] + ["Sin clasificar"] * 4,
                            "sector_source": ["official"] * 5})
    returns = {isin: pd.Series(rng.normal(size=100), index=index) for isin in isins}
    result = cluster_unknown(metrics, returns)
    assert result.loc[0, "sector"] == "Technology"
    assert result.loc[1:, "sector"].str.contains("sector desconocido").all()


def test_isin_checksum():
    assert valid_isin("US0378331005")
    assert valid_isin("IE00B03HD191")
    assert not valid_isin("US0378331006")

