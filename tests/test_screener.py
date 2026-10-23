"""Screener page backend: engine semantics, saved screeners and the HTTP contract. No network, temp databases."""
from datetime import date

import pytest
from fastapi.testclient import TestClient

HEADER = "SYMBOL, SERIES, DATE1, PREV_CLOSE, OPEN_PRICE, HIGH_PRICE, LOW_PRICE, LAST_PRICE, CLOSE_PRICE, AVG_PRICE, TTL_TRD_QNTY, TURNOVER_LACS, NO_OF_TRADES, DELIV_QTY, DELIV_PER"
# symbol, series, prev, close, turnover (lakhs), delivery %
DAY = [("TCS", "EQ", 100.0, 105.0, 5000.0, "60.5"), ("INFY", "EQ", 100.0, 99.0, 4000.0, "55.0"),
       ("ILLIQ", "EQ", 10.0, 20.0, 5.0, "-"), ("SMEX", "SM", 50.0, 60.0, 300.0, "-"),
       ("NIFTYBEES", "EQ", 200.0, 210.0, 9000.0, "-"), ("BEST", "BE", 40.0, 42.0, 200.0, "90.0")]


def csv_for(d, rows):
    lines = [HEADER]
    for sym, ser, prev, close, lacs, dper in rows:
        lines.append(f"{sym}, {ser}, {d:%d-%b-%Y}, {prev}, {prev}, {max(prev, close)}, {min(prev, close)}, {close}, {close}, "
                     f"{close}, {int(lacs * 1e5 / max(close, 1))}, {lacs}, 500, 1000, {dper}")
    return ("\n".join(lines) + "\n").encode()


@pytest.fixture()
def client(env, monkeypatch):
    from backend.app.main import app
    from backend.app.repositories import bars_repository as bars, warehouse
    from backend.app.services.data import bhavcopy, market_data
    market_data.invalidate()
    monkeypatch.setattr(market_data, "MASTER_MIN_ROWS", 4)
    d = date(2026, 10, 6)
    bhavcopy.ingest_day(d, csv_for(d, DAY))
    bars.upsert_companies([{"symbol": s, "name": f"{s} Ltd", "sector": "Tech" if s in ("TCS", "INFY") else ""}
                           for s in ("TCS", "INFY", "ILLIQ", "SMEX", "BEST")])
    warehouse.upsert_ratios({"symbol": "TCS", "name": "TCS", "sector": "Technology", "pe": 15.0, "pb": 6.0, "roce": 50.0,
                             "roe": 45.0, "net_margin": 18.0, "op_margin": 24.0, "market_cap": 7.5e12,
                             "as_of": "2026-10-07", "source": "test-fundamentals"})
    market_data.invalidate()
    yield TestClient(app)
    market_data.invalidate()


def run(c, **d):
    r = c.post("/api/screeners/run", json={"definition": d})
    assert r.status_code == 200, r.text
    return r.json()


def syms(res):
    return [r["symbol"] for r in res["rows"]]


def test_filter_sort_and_universe(client):
    res = run(client, filters=[{"metric": "Day Change %", "op": ">", "value": 0}], sort={"metric": "Day Change %", "dir": "desc"})
    assert syms(res) == ["ILLIQ", "SMEX", "BEST", "TCS"]            # BEST/TCS tie at +5%: symbol order; ETF never appears
    assert res["total"] == 4 and res["universe"] == 5
    assert syms(run(client, filters=[{"metric": "Day Change %", "op": ">", "value": 0}], universe={"include_sme": False})) == ["TCS", "BEST", "ILLIQ"]   # default sort: turnover desc
    assert res["columns"][0]["key"] == "Company Name"


def test_pagination_keeps_total(client):
    res = client.post("/api/screeners/run", json={"definition": {"sort": {"metric": "Turnover (Cr)", "dir": "desc"}}, "limit": 2, "offset": 2}).json()
    assert res["total"] == 5 and len(res["rows"]) == 2 and res["offset"] == 2


def test_missing_values_stay_missing_and_fail_filters(client):
    res = run(client, columns=["Market Cap (Cr)", "P/E", "Sector"], sort={"metric": "Turnover (Cr)", "dir": "desc"})
    by = {r["symbol"]: r for r in res["rows"]}
    assert by["TCS"]["Market Cap (Cr)"] == 750000.0 and by["TCS"]["P/E"] == 15.0
    assert by["INFY"]["Market Cap (Cr)"] is None and by["INFY"]["P/E"] is None           # never 0
    assert by["BEST"]["Sector"] is None
    hit = run(client, filters=[{"metric": "P/E", "op": "<", "value": 20}])
    assert syms(hit) == ["TCS"]
    assert any("P/E is available for 1 of 5" in n for n in hit["notes"])                 # coverage is disclosed
    assert any("fundamentals" in s["source"] for s in hit["sources"])
    assert syms(run(client, filters=[{"metric": "P/E", "op": "!=", "value": 1}])) == ["TCS"]   # != must not admit missing


def test_text_filter_and_sort_nulls_last(client):
    assert syms(run(client, filters=[{"metric": "Sector", "op": "contains", "value": "tec"}], sort={"metric": "Sector", "dir": "asc"})) == ["INFY", "TCS"]
    res = run(client, sort={"metric": "Sector", "dir": "desc"})
    assert syms(res)[:2] == ["INFY", "TCS"]            # same sector -> symbol order; companies with no sector go last
    assert all(r["symbol"] not in ("TCS", "INFY") for r in res["rows"][2:])


def test_shareholding_empty_is_reported(client):
    res = run(client, columns=["Promoter %"])
    assert all(r["Promoter %"] is None for r in res["rows"]) and any("No Promoter %" in n for n in res["notes"])
    m = client.get("/api/screeners/metrics").json()
    cov = {x["key"]: x["coverage"] for x in m["metrics"]}
    assert cov["Promoter %"] == 0 and cov["P/E"] == 1 and cov["LTP"] == 5 and m["universe"] == 5


@pytest.mark.parametrize("bad", [
    {"filters": [{"metric": "Nope", "op": ">", "value": 1}]},
    {"filters": [{"metric": "P/E", "op": "contains", "value": 1}]},
    {"filters": [{"metric": "P/E", "op": ">", "value": "abc"}]},
    {"filters": [{"metric": "P/E", "op": ">", "value": True}]},
    {"filters": [{"metric": "Company Name", "op": "=", "value": "x"}]},
    {"columns": ["Nope"]}, {"sort": {"metric": "Nope"}}, {"sort": {"metric": "LTP", "dir": "up"}}])
def test_invalid_definitions_are_400(client, bad):
    r = client.post("/api/screeners/run", json={"definition": bad})
    assert r.status_code == 400 and r.json()["error"]


def test_every_curated_screener_runs(client):
    items = client.get("/api/screeners/curated").json()["items"]
    assert len(items) == 12 and "Momentum" in client.get("/api/screeners/curated").json()["categories"]
    for it in items:
        assert it["preview"] is not None, it["id"]
        assert client.get(f"/api/screeners/{it['id']}").status_code == 200
    assert next(i for i in items if i["id"] == "c-top-gainers")["preview"]["symbols"] == ["SMEX", "BEST", "TCS"]


def test_saved_screener_lifecycle(client):
    d = {"columns": ["LTP"], "filters": [{"metric": "LTP", "op": ">", "value": 50}]}
    s = client.post("/api/screeners", json={"name": " Mine ", "description": "x", "definition": d}).json()
    assert s["name"] == "Mine" and s["source"] == "custom" and s["definition"]["sort"]["metric"] == "Turnover (Cr)"
    assert [i["id"] for i in client.get("/api/screeners/mine").json()["items"]] == [s["id"]]
    got = client.get(f"/api/screeners/{s['id']}").json()
    assert got["results"]["total"] == 3   # TCS, INFY, SMEX and got["screener"]["curated"] is False
    up = client.patch(f"/api/screeners/{s['id']}", json={"name": "Renamed", "definition": {**d, "columns": ["Volume"]}}).json()
    assert up["name"] == "Renamed" and up["definition"]["columns"] == ["Volume"]
    cp = client.post(f"/api/screeners/{s['id']}/duplicate").json()
    assert cp["name"] == "Renamed (copy)" and cp["id"] != s["id"]
    assert client.delete(f"/api/screeners/{s['id']}").status_code == 200
    assert client.get(f"/api/screeners/{s['id']}").status_code == 404
    assert client.delete("/api/screeners/nope").status_code == 404
    assert client.post("/api/screeners", json={"name": "", "definition": {}}).status_code == 422
    assert client.post("/api/screeners", json={"name": "x", "definition": {"columns": ["Nope"]}}).status_code == 400


def test_curated_is_read_only_but_can_be_copied(client):
    assert client.patch("/api/screeners/c-top-gainers", json={"name": "x"}).status_code == 403
    assert client.delete("/api/screeners/c-top-gainers").status_code == 403
    assert client.get("/api/screeners/c-missing").status_code == 404
    cp = client.post("/api/screeners/c-top-gainers/duplicate").json()
    assert cp["source"] == "curated" and cp["name"].startswith("Top NSE Gainers")


def test_chat_results_open_as_screeners(client):
    g = client.post("/api/screeners/from-tool", json={"tool": "market_movers", "args": {"kind": "gainers", "sector": "Tech"}}).json()
    assert g["source"] == "chat" and g["name"] == "Top Gainers · Tech"
    assert {"metric": "Sector", "op": "contains", "value": "Tech"} in g["definition"]["filters"]
    assert syms(client.get(f"/api/screeners/{g['id']}").json()["results"]) == ["TCS"]
    s = client.post("/api/screeners/from-tool", json={"tool": "screener", "args": {"filters": [
        {"metric": "P/E", "op": "<", "value": 25}, {"metric": "ROCE %", "op": ">", "value": 15}]}}).json()
    assert "P/E" in s["definition"]["columns"] and "ROCE %" in s["definition"]["columns"]
    assert syms(client.get(f"/api/screeners/{s['id']}").json()["results"]) == ["TCS"]
    assert client.post("/api/screeners/from-tool", json={"tool": "quote", "args": {}}).status_code == 400
    assert client.post("/api/screeners/from-tool", json={"tool": "market_movers", "args": {"kind": "zzz"}}).status_code == 400


def test_no_market_data_is_a_clear_400(env):
    from backend.app.main import app
    from backend.app.services.data import market_data
    market_data.invalidate()
    r = TestClient(app).post("/api/screeners/run", json={"definition": {}})
    assert r.status_code == 400 and "not been loaded" in r.json()["error"]
