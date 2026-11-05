import shutil
from pathlib import Path

import nbformat
import numpy as np
import pandas as pd
import pytest
from nbclient import NotebookClient

from tr_pipeline.catalog import build_catalog
from tr_pipeline.common import csv_write, json_write
from tr_pipeline.pipeline import conclusions, run_all, select_assets
from tr_pipeline.providers import eur_rates, resolve

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    shutil.copy(REPO / "config.toml", tmp_path / "config.toml")
    shutil.copy(REPO / "pyproject.toml", tmp_path / "pyproject.toml")
    cfg = (tmp_path / "config.toml").read_text(encoding="utf-8").replace(
        'authoritative_catalog = ""', 'authoritative_catalog = "data/manual/full.csv"')
    (tmp_path / "config.toml").write_text(cfg, encoding="utf-8")
    rows = [{"isin": isin, "name": f"SYNTHETIC TEST {kind}", "asset_type": kind,
             "source": "https://example.com/test-fixture", "verified_at": str(pd.Timestamp.today().date())}
            for isin, kind in [("US0378331005", "stock"), ("IE00B03HD191", "mutual_fund"),
                               ("IE00B4L5Y983", "etf")]]
    csv_write(tmp_path / "data/manual/full.csv", pd.DataFrame(rows))
    json_write(tmp_path / "data/manual/full.json", {"complete": True, "country": "ES",
               "as_of": str(pd.Timestamp.today().date()), "source": "TEST FIXTURE ONLY"})
    index = pd.bdate_range("2020-01-01", pd.Timestamp.today().normalize() - pd.Timedelta(days=1))
    for i, row in enumerate(rows):
        values = 100 * np.exp(np.arange(len(index)) * (.0001 + i * .0001))
        frame = pd.DataFrame({"date": index, "price": values, "adjusted_price": values,
                              "currency": "EUR", "source_url": "https://example.com/synthetic-data"})
        csv_write(tmp_path / f"data/manual/prices/{row['isin']}.csv", frame)
    monkeypatch.setenv("TR_PIPELINE_ROOT", str(tmp_path))
    monkeypatch.setenv("TR_PIPELINE_OFFLINE", "1")
    return tmp_path


def test_end_to_end_artifacts_and_conclusions_invalidate(workspace):
    result = run_all(workspace, offline=True)
    assert set(result) == {"stock", "mutual_fund", "etf"}
    assert all(v["manifest"]["downloaded"] == 1 for v in result.values())
    before = result["etf"]["manifest"]["run_id"]
    # El alcance predeterminado de 250 no se considera un ensayo que deba repetirse.
    reused = conclusions(workspace, offline=True)
    assert reused["etf"]["manifest"]["run_id"] == before
    path = workspace / "data/manual/prices/IE00B4L5Y983.csv"
    frame = pd.read_csv(path)
    frame.loc[frame.index[-1], ["price", "adjusted_price"]] *= 1.2
    csv_write(path, frame)
    after = conclusions(workspace, offline=True)
    assert after["etf"]["manifest"]["run_id"] != before
    assert after["etf"]["top_global"].return_12m_pct.iloc[0] > result["etf"]["top_global"].return_12m_pct.iloc[0]
    text = (workspace / "reports/conclusiones.md").read_text(encoding="utf-8")
    assert "US0378331005" in text and "IE00B03HD191" in text


def test_absent_price_is_reported_and_never_replaced_with_another_isin(workspace):
    (workspace / "data/manual/prices/IE00B03HD191.csv").unlink()
    result = run_all(workspace, offline=True)
    funds = result["mutual_fund"]
    assert funds["manifest"]["failed"] == 1
    assert funds["top_global"].empty
    assert not funds["manifest"]["complete_analysis"]


def test_stale_or_wrong_country_catalog_cannot_be_declared_complete(workspace):
    json_write(workspace / "data/manual/full.json", {"complete": True, "country": "DE",
               "as_of": "2020-01-01", "source": "TEST"})
    _, manifest = build_catalog(workspace)
    assert not manifest["complete"]


def test_wrong_reverse_isin_is_rejected(monkeypatch):
    import tr_pipeline.providers as providers
    class Search:
        quotes = [{"symbol": "WRONG", "quoteType": "EQUITY", "isin": "US5949181045"}]
    monkeypatch.setattr(providers.yf, "Search", lambda *a, **kw: Search())
    with pytest.raises(ValueError, match="Sin correspondencia"):
        resolve("US0378331005", "stock", {}, {"retries": 0})


def test_cap_uses_numeric_relevance_instead_of_isin_and_requires_a_criterion():
    frame = pd.DataFrame({"isin": [str(i) for i in range(300)],
                          "relevance_rank": [str(i) for i in range(300, 0, -1)]})
    selected = select_assets(frame, 250)
    assert len(selected) == 250
    assert selected["isin"].tolist() == [str(i) for i in range(299, 49, -1)]
    with pytest.raises(ValueError, match="sin relevance_rank"):
        select_assets(frame.drop(columns="relevance_rank"), 250)
    frame.loc[0, "relevance_rank"] = ""
    with pytest.raises(ValueError, match="positivo"):
        select_assets(frame, 250)


def test_yahoo_etf_label_does_not_reclassify_a_tr_mutual_fund(monkeypatch):
    import tr_pipeline.providers as providers
    class Search:
        quotes = [{"symbol": "FUND.F", "quoteType": "ETF", "isin": "LU0099574567"}]
    monkeypatch.setattr(providers.yf, "Search", lambda *a, **kw: Search())
    assert resolve("LU0099574567", "mutual_fund", {}, {"retries": 0})[0] == "FUND.F"
    # La compatibilidad de etiqueta no permite aceptar otra clase.
    Search.quotes[0]["isin"] = "LU1261432659"
    with pytest.raises(ValueError, match="Sin correspondencia"):
        resolve("LU0099574567", "mutual_fund", {}, {"retries": 0})


def test_ecb_conversion_handles_pence_and_does_not_backfill(workspace):
    index = pd.bdate_range("2025-01-02", "2025-01-06")
    path = workspace / "data/raw/fx_GBP_2024-12-23_2025-01-06.csv"
    csv_write(path, pd.DataFrame({"date": ["2025-01-02", "2025-01-03"], "rate": [1.2, 1.3]}))
    rates = eur_rates(workspace, "GBp", index, {}, offline=True)
    assert np.isclose(rates.iloc[0], .012) and np.isclose(rates.iloc[-1], .013)


def test_all_four_notebooks_execute_offline_and_fourth_recovers_dependencies(workspace):
    # La prueba ejecuta exactamente los notebooks que se entregan, con datos sintéticos aislados.
    for path in sorted((REPO / "notebooks").glob("*.ipynb")):
        if path.name.startswith("04"):
            (workspace / "data/processed/etf/manifest.json").unlink()
        notebook = nbformat.read(path, as_version=4)
        NotebookClient(notebook, timeout=180, kernel_name="python3",
                       resources={"metadata": {"path": str(REPO)}}).execute()
        assert not any(o.output_type == "error" for c in notebook.cells if c.cell_type == "code" for o in c.outputs)
    assert (workspace / "reports/conclusiones.md").exists()
    assert (workspace / "data/processed/etf/manifest.json").exists()
