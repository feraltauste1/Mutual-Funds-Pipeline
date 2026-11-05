import pandas as pd
import pytest

from tr_pipeline.common import csv_write, json_write, utc_now
from tr_pipeline.tr_public import collect_instruments, collect_relevant, fetch_public_catalog, request_topic


def test_relevance_takes_250_in_global_order_without_enumerating_full_catalog():
    calls = []
    # Orden deliberadamente distinto del ISIN; no se consulta por gestora/país.
    items = [{"isin": str(i)} for i in range(999, -1, -1)]
    def query(topic):
        calls.append(topic)
        page = topic["data"]["page"]
        return {"results": items[(page - 1) * 100:page * 100], "resultCount": len(items)}
    rows, total, stable = collect_relevant(query, [], 250)
    assert len(calls) == 3 and len(rows) == 250 and total == 1000 and stable
    assert [r["isin"] for r in rows] == [r["isin"] for r in items[:250]]
    assert [r["relevance_rank"] for r in rows] == list(range(1, 251))


def test_relevance_deduplicates_pages_and_detects_changing_counts():
    def query(topic):
        page = topic["data"]["page"]
        return {"results": [{"isin": str(i)} for i in range(100)], "resultCount": 300 + page}
    rows, total, stable = collect_relevant(query, [], 250)
    assert len(rows) == 100 and total == 301 and not stable


def test_partitioning_fills_an_isin_missing_from_tied_pages():
    items = [{"isin": str(i)} for i in range(101)]
    def query(topic):
        filters = topic["data"]["filter"]
        if topic["type"] == "neonSearchAggregations":
            return {"results": [{"id": "A", "type": "country"}, {"id": "B", "type": "country"}]}
        group = next((f["value"] for f in filters if f["key"] == "country"), None)
        rows = items[:50] if group == "A" else items[50:] if group == "B" else items[:100]
        return {"results": rows, "resultCount": len(rows) if group else 101}
    rows, expected, stable = collect_instruments(query, [], ("country",))
    assert len(rows) == expected == 101 and stable


def test_truncated_or_repeated_catalog_remains_detectably_incomplete():
    def query(topic):
        return {"results": [{"isin": str(i)} for i in range(100)], "resultCount": 101}
    rows, expected, _ = collect_instruments(query, [], ())
    assert len(rows) < expected


def test_public_authentication_error_does_not_trigger_a_login_or_other_attempt():
    class Socket:
        def __init__(self):
            self.messages = []
        def send(self, message):
            self.messages.append(message)
        def recv(self, timeout):
            return '7 E {"errors":[{"errorCode":"AUTHENTICATION_ERROR"}]}'
    socket = Socket()
    with pytest.raises(ValueError, match="rechaza"):
        request_topic(socket, 7, {"type": "instrument", "id": "test"})
    assert len(socket.messages) == 2 and socket.messages[-1] == "unsub 7"


def test_offline_catalog_cache_never_connects(tmp_path, monkeypatch):
    from tr_pipeline import tr_public
    monkeypatch.setattr(tr_public, "connect", lambda *a, **k: pytest.fail("No debe usar red"))
    csv_write(tmp_path / "data/catalog/public_universe.csv", pd.DataFrame({"isin": ["US0378331005"]}))
    json_write(tmp_path / "data/catalog/public_manifest.json", {
        "downloaded_at": utc_now(), "country": "ES", "complete": True, "note": "test"})
    frame, manifest = fetch_public_catalog(tmp_path, {"country": "ES", "cache_hours": 24,
                                                     "catalog_max_age_days": 7}, offline=True)
    assert len(frame) == 1 and manifest["complete"]
    # Un catálogo antiguo ordenado por ISIN no acredita una selección por relevancia.
    with pytest.raises(ValueError, match="límite"):
        fetch_public_catalog(tmp_path, {"country": "ES", "cache_hours": 24,
                                      "catalog_max_age_days": 7, "max_assets_per_type": 250}, offline=True)

