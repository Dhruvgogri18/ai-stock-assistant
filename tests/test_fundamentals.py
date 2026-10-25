"""Company fundamentals: statement parsing and storage, computed results/ratios, peers, the company API, the bulk loader's selection.
All fake yfinance data (fin_fixtures); no network, temp databases."""
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest
from fastapi.testclient import TestClient

import fin_fixtures as f
from backend.app.config.settings import settings
from backend.app.repositories import bars_repository as bars, warehouse
from backend.app.services.data import live_prices as lp, market_data
from backend.app.services.fundamentals import compute, loader, peers, service

C = 1e7
FACTORY = f.factory(f.IT_PEERS)


# ------------------------------------------------------------------ fixtures
def load_prices(specs, master_sector="Information Technology"):
    rows, cos = [], []
    for s in specs:
        for i in range(300):
            d = (datetime(2026, 10, 16) - timedelta(days=299 - i)).date().isoformat()
            rows.append({"symbol": s.symbol, "date": d, "series": "EQ", "prev_close": s.price, "open": s.price, "high": s.price * 1.01, "low": s.price * .99,
                         "close": s.price, "volume": 1000, "trades": 10, "turnover_cr": 5.0, "deliv_pct": 50.0})
        cos.append({"symbol": s.symbol, "name": f"{s.symbol} Ltd.", "sector": master_sector})
    bars.upsert_bars(pd.DataFrame(rows))
    bars.upsert_companies(cos)


@pytest.fixture()
def client(env, monkeypatch):
    from backend.app.main import app
    market_data.invalidate(); lp.reset_state()
    monkeypatch.setattr(market_data, "MASTER_MIN_ROWS", 4)
    monkeypatch.setattr(lp, "configured", lambda: False)
    monkeypatch.setattr(settings, "fundamentals_refresh_cooldown_min", 30)
    load_prices(f.IT_PEERS + [f.Spec("ILLIQ")])
    yield TestClient(app)
    market_data.invalidate(); lp.reset_state()


def load_all(specs=None):
    for s in specs or f.IT_PEERS:
        loader.refresh_symbol(s.symbol, f.factory(specs or f.IT_PEERS))


def rows(stmt, pt, per, items):
    return [dict(statement=stmt, period_type=pt, period=per, line_item=k, value=v) for k, v in items.items()]


# ----------------------------------------------------------------- parsing
def test_parse_frame_skips_nan_formats_periods_and_respects_the_limit():
    p = loader.parse_frame(f.income(f.Spec("X"), True), 6)
    assert list(p) == f.ANNUAL and "Special Income" not in p["2025-03-31"] and p["2025-03-31"]["Net Income"] > 0
    assert list(loader.parse_frame(f.income(f.Spec("X"), True), 2)) == f.ANNUAL[:2]
    assert loader.parse_frame(None, 5) == {} and loader.parse_frame(pd.DataFrame(), 5) == {}
    dup = pd.DataFrame([[1.0], [2.0]], index=["Net Income", "Net Income"], columns=[pd.Timestamp("2025-03-31")])
    assert loader.parse_frame(dup, 5) == {"2025-03-31": {"Net Income": 1.0}}                     # first one wins, never summed


def test_a_failing_statement_is_recorded_and_the_rest_still_load():
    got = loader.fetch("TCS", f.factory(f.IT_PEERS, broken=("cashflow", "quarterly_balance_sheet")))
    assert ("cashflow", "annual") not in got.frames and ("balance", "annual") in got.frames and ("income", "quarterly") in got.frames
    assert any("cashflow annual" in e for e in got.errors) and any("balance quarterly" in e for e in got.errors)


# ----------------------------------------------------------------- storing
def test_store_keeps_every_line_idempotently_and_never_overwrites_the_nse_master(client):
    warehouse.upsert_company("TCS", name="Tata Consultancy Services Ltd.", sector="Information Technology")
    r = loader.refresh_symbol("TCS", FACTORY)
    assert r["annual_periods"] == 4 and r["quarterly_periods"] == 5 and "income (annual)" in r["statements"] and r["errors"] == []
    n = len(warehouse.get_financials_all("TCS"))
    assert n > 100                                                          # all lines, not three
    loader.refresh_symbol("TCS", FACTORY)
    assert len(warehouse.get_financials_all("TCS")) == n                    # idempotent
    st = warehouse.get_fundamentals_status("TCS")
    assert st["source"] == loader.SOURCE and st["currency"] == "INR" and st["provider_sector"] == "Technology" and st["shares"] == pytest.approx(36.2 * C)
    co = bars.company_map()["TCS"]
    assert co["name"] == "TCS Ltd." or co["name"] == "Tata Consultancy Services Ltd."      # the master's name is untouched...
    assert co["sector"] == "Information Technology"                         # ...and so is its sector (yfinance says "Technology")
    with warehouse.connect() as c:
        assert c.execute("SELECT industry FROM companies WHERE symbol='TCS'").fetchone()[0] == "Information Technology Services"


def test_store_writes_the_ratios_row_from_the_statements(client):
    loader.refresh_symbol("TCS", FACTORY)
    r = warehouse.get_ratios("TCS")
    assert r["source"] == loader.RATIOS_SOURCE and r["market_cap"] == pytest.approx(3900 * 36.2 * C)
    fin = compute.Fin(warehouse.get_financials_all("TCS"))
    ttm_eps = compute.results_view(fin, "quarterly")["ttm"]["eps"]
    assert r["pe"] == pytest.approx(3900 / ttm_eps, rel=1e-3) and r["roce"] > 0 and r["net_margin"] == pytest.approx(19.0, abs=0.1)


def test_foreign_currency_and_empty_responses_are_refused_honestly(client):
    with pytest.raises(ValueError, match="USD"):
        loader.refresh_symbol("ADR", f.factory([f.Spec("ADR", currency="USD")]))
    assert warehouse.get_financials_all("ADR") == [] and "USD" in warehouse.get_fundamentals_status("ADR")["error"]
    with pytest.raises(ValueError, match="No financial statements"):
        loader.refresh_symbol("NOPE", FACTORY)


# --------------------------------------------------------------- results
def test_results_growth_is_matched_by_date_and_ttm_needs_four_consecutive_quarters(client):
    loader.refresh_symbol("TCS", FACTORY)
    fin = compute.Fin(warehouse.get_financials_all("TCS"))
    a = compute.results_view(fin, "annual")
    assert [r["label"] for r in a["rows"]] == ["Mar 2022", "Mar 2023", "Mar 2024", "Mar 2025"] and a["rows"][-1]["revenue"] == pytest.approx(2400.0)
    assert a["rows"][-1]["revenue_yoy"] == pytest.approx(9.0, abs=0.01) and a["rows"][0]["revenue_yoy"] is None
    assert a["rows"][-1]["opm"] == pytest.approx(26.0) and a["rows"][-1]["npm"] == pytest.approx(19.0) and a["ttm"] is None
    q = compute.results_view(fin, "quarterly")
    last = q["rows"][-1]
    assert last["label"] == "Sep 2025" and last["revenue_yoy"] == pytest.approx(9.0, abs=0.05)             # vs Sep 2024, found by date
    assert last["revenue_qoq"] == pytest.approx(((1.09) ** 0.25 - 1) * 100, abs=0.05)
    assert q["ttm"]["label"] == "TTM to Sep 2025" and q["ttm"]["revenue"] == pytest.approx(sum(r["revenue"] for r in q["rows"][-4:]), abs=0.05)
    gap = compute.Fin([r for r in warehouse.get_financials_all("TCS") if not (r["period_type"] == "quarterly" and r["period"] == "2025-03-31")])
    assert compute.results_view(gap, "quarterly")["ttm"] is None                                          # a missing quarter: no TTM, not a wrong one


# ---------------------------------------------------------------- ratios
def hand_fin():
    R = []
    R += rows("income", "annual", "2024-03-31", {"Total Revenue": 800 * C, "Operating Income": 160 * C, "Net Income": 120 * C, "Diluted EPS": 12.0})
    R += rows("income", "annual", "2025-03-31", {"Total Revenue": 1000 * C, "Operating Income": 220 * C, "EBIT": 230 * C, "EBITDA": 260 * C,
                                                 "Net Income": 150 * C, "Diluted EPS": 15.0, "Interest Expense": 10 * C})
    R += rows("balance", "annual", "2025-03-31", {"Stockholders Equity": 700 * C, "Total Assets": 1200 * C, "Current Liabilities": 300 * C,
                                                  "Current Assets": 600 * C, "Total Debt": 140 * C, "Cash And Cash Equivalents": 100 * C})
    R += rows("balance", "annual", "2024-03-31", {"Stockholders Equity": 600 * C, "Total Assets": 1100 * C})
    R += rows("cashflow", "annual", "2025-03-31", {"Operating Cash Flow": 180 * C, "Capital Expenditure": -60 * C})
    return compute.Fin(R)


@pytest.mark.parametrize("key,expected", [("market_cap", 3000.0), ("pe", 20.0), ("pb", 4.29), ("ev_ebitda", 11.69), ("roe", 23.08), ("roce", 25.56),
                                          ("roa", 13.04), ("op_margin", 22.0), ("net_margin", 15.0), ("revenue_growth", 25.0), ("ni_growth", 25.0),
                                          ("debt_equity", 0.2), ("interest_cover", 23.0), ("current_ratio", 2.0), ("fcf", 120.0),
                                          ("fcf_margin", 12.0), ("cash_conversion", 1.2)])
def test_ratios_match_hand_calculations(key, expected):
    it = compute.ratios_view(hand_fin(), 300.0, 10 * C, "Technology", "IT")["by_key"][key]
    assert it["value"] == pytest.approx(expected, abs=0.01) and it["formula"] and it["reason"] is None


def test_every_ratio_says_where_it_came_from():
    v = compute.ratios_view(hand_fin(), 300.0, 10 * C)["by_key"]
    assert v["roe"]["period"] == "Mar 2025" and "average" in v["roe"]["basis"] and [i["name"] for i in v["roe"]["inputs"]] == ["Net profit", "Average equity"]
    assert v["pe"]["basis"] == "latest annual (Mar 2025)"


def test_banks_get_no_meaningless_ratios_but_keep_the_valid_ones():
    v = compute.ratios_view(hand_fin(), 300.0, 10 * C, "Financial Services", "Banks - Regional")["by_key"]
    for k in ("roce", "debt_equity", "current_ratio", "interest_cover", "ev_ebitda"):
        assert v[k]["value"] is None and "banks and financial" in v[k]["reason"]
    assert v["pe"]["value"] == 20.0 and v["roe"]["value"] == pytest.approx(23.08, abs=0.01)


def test_loss_making_and_missing_inputs_give_a_reason_not_a_number():
    R = rows("income", "annual", "2025-03-31", {"Total Revenue": 100 * C, "Net Income": -5 * C, "Diluted EPS": -0.5})
    v = compute.ratios_view(compute.Fin(R), 100.0, 10 * C)["by_key"]
    assert v["pe"]["value"] is None and "Loss-making" in v["pe"]["reason"]
    assert v["roce"]["value"] is None and v["roce"]["reason"] and v["pb"]["value"] is None
    assert compute.ratios_view(compute.Fin([]), None, None)["by_key"]["pe"]["value"] is None


def test_pe_uses_ttm_eps_when_four_quarters_exist_and_cagr_needs_three_years(client):
    loader.refresh_symbol("TCS", FACTORY)
    v = compute.ratios_view(compute.Fin(warehouse.get_financials_all("TCS")), 3900.0, 36.2 * C)["by_key"]
    assert v["pe"]["basis"] == "TTM (last 4 quarters)"
    assert v["revenue_cagr"]["label"] == "Revenue CAGR (3-yr)" and v["revenue_cagr"]["value"] == pytest.approx(9.0, abs=0.1)
    two = compute.Fin([r for r in warehouse.get_financials_all("TCS") if r["period"] >= "2024-03-31" or r["period_type"] == "quarterly"])
    assert compute.ratios_view(two, 3900.0, 36.2 * C)["by_key"]["revenue_cagr"]["value"] is None


# ------------------------------------------------------------------ peers
def test_peers_are_same_sector_with_data_ordered_by_market_cap_closeness(client):
    load_all()
    bank = f.Spec("BANKX", price=3900, shares_cr=36.0, sector="Financial Services", industry="Banks - Regional")     # same size as TCS, different sector
    load_prices([bank], master_sector="Banks")
    loader.refresh_symbol("BANKX", f.factory([bank]))
    load_prices([f.Spec("NODATA")])                                                                   # in the IT sector, no statements
    c = peers.compare("TCS", limit=3)
    assert c["group"]["name"] == "Information Technology" and c["group"]["taxonomy"] == "NSE sector"
    assert [p["symbol"] for p in c["peers"]] == ["INFY", "HCLTECH", "WIPRO"]                           # BANKX / NODATA / TCS itself are out
    everyone = [p["symbol"] for p in peers.compare("TCS", limit=12)["peers"]]
    assert sorted(everyone) == ["HCLTECH", "INFY", "LTIM", "PERSISTENT", "TECHM", "WIPRO"] and "BANKX" not in everyone
    assert c["subject"]["symbol"] == "TCS" and c["subject"]["market_cap"] == pytest.approx(3900 * 36.2, rel=1e-3)
    assert any("no statements stored yet" in n for n in c["notes"])
    assert c["median"]["roce"] == pytest.approx(sorted(p["roce"] for p in c["peers"])[1], abs=0.01)
    assert c["rank"]["roce"] == {"rank": 1, "of": 4} and c["rank"]["debt_equity"]["of"] == 4


def test_peers_say_so_when_there_are_none_or_few(client):
    loader.refresh_symbol("TCS", FACTORY)
    c = peers.compare("TCS")
    assert c["peers"] == [] and any("nothing to compare" in n for n in c["notes"])
    loader.refresh_symbol("INFY", FACTORY)
    assert any("Only 1 peer" in n for n in peers.compare("TCS")["notes"])
    assert any("No statements are stored" in n for n in peers.compare("WIPRO")["notes"])


def test_provider_sector_is_used_only_when_the_nse_sector_is_missing(client):
    specs = [f.Spec("AAA", price=100), f.Spec("BBB", price=120)]
    load_prices(specs, master_sector="")
    for s in specs:
        loader.refresh_symbol(s.symbol, f.factory(specs))
    c = peers.compare("AAA")
    assert c["group"]["taxonomy"] == "provider sector" and [p["symbol"] for p in c["peers"]] == ["BBB"]
    assert any("provider's sector label" in n for n in c["notes"])


def test_stocks_from_an_older_load_still_count_as_peers_and_show_the_providers_market_cap(client):
    # what the old loader left behind: three annual income lines, no balance sheet, no provenance record, a ratios row with market cap
    for per, rev, ni in (("2024-03-31", 800, 120), ("2025-03-31", 1000, 150)):
        warehouse.upsert_financial_rows("TCS", per, "annual", "income", {"Total Revenue": rev * C, "Operating Income": rev * 0.2 * C, "Net Income": ni * C})
    warehouse.upsert_ratios({"symbol": "TCS", "name": "TCS", "sector": "Technology", "pe": 15.0, "pb": 6.0, "roce": 50.0, "roe": 45.0, "net_margin": 18.0,
                             "op_margin": 24.0, "market_cap": 7.5e12, "as_of": "2026-10-07", "source": "yfinance-fundamentals"})
    assert warehouse.get_fundamentals_status("TCS") is None
    loader.refresh_symbol("INFY", FACTORY)
    c = peers.compare("INFY")
    assert [p["symbol"] for p in c["peers"]] == ["TCS"] and c["peers"][0]["market_cap"] == pytest.approx(750000.0)      # provider's figure, in crore
    assert c["peers"][0]["net_margin"] == pytest.approx(15.0) and c["peers"][0]["pe"] is None                          # computed where possible, never invented
    d = client.get("/api/company/TCS").json()
    mc = next(i for g in d["ratios"]["groups"] for i in g["items"] if i["key"] == "market_cap")
    assert mc["value"] == pytest.approx(750000.0) and "not today's price" in mc["basis"] and mc["period"] == "2026-10-07"
    assert d["coverage"]["has_data"] and d["coverage"]["fetched_at"] is None and d["coverage"]["statements"] == ["income (annual)"]
    pb = next(i for g in d["ratios"]["groups"] for i in g["items"] if i["key"] == "pb")
    assert pb["value"] is None                                                                                          # P/B needs the share count


def test_summary_lines_are_computed_facts_only(client):
    load_all()
    fin = compute.Fin(warehouse.get_financials_all("TCS"))
    cmp = peers.compare("TCS")
    lines = peers.summary_lines(compute.ratios_view(fin, 3900.0, 36.2 * C), cmp)
    assert any(l.startswith("Net profit up") and "over 3 years" in l for l in lines)
    assert any("ROCE" in l and "1st of 7 compared Information Technology companies" in l for l in lines)
    assert any("P/E" in l and ("premium" in l or "discount" in l) and "peer median" in l for l in lines)
    assert peers.summary_lines(None, None) == [] and peers.ordinal(2) == "2nd" and peers.ordinal(11) == "11th" and peers.ordinal(23) == "23rd"


# -------------------------------------------------------------------- API
def test_unknown_symbol_is_404_and_bad_parameters_are_400(client):
    assert client.get("/api/company/ZZZNOPE").status_code == 404
    assert client.get("/api/company/ZZZNOPE/statements").status_code == 404
    assert client.get("/api/company/TCS/statements?kind=nope").status_code == 400
    assert client.get("/api/company/TCS/statements?period=weekly").status_code == 400
    assert client.get("/api/company/TCS/results?period=weekly").status_code == 400


def test_company_without_statements_gets_an_empty_state_with_a_price(client):
    d = client.get("/api/company/ILLIQ").json()
    assert d["coverage"]["has_data"] is False and d["ratios"] is None and d["results"] is None and d["summary"] == []
    assert d["price"]["value"] == 100.0 or d["price"]["value"] == 300.0
    assert d["price"]["mode"] == "eod" and "market close" in d["price"]["as_of"]
    assert client.get("/api/company/ILLIQ/results").json()["rows"] == []


def test_company_overview_with_statements(client):
    load_all()
    d = client.get("/api/company/TCS").json()
    assert d["name"] == "TCS Ltd." and d["sector"] == "Information Technology" and d["industry"] == "Information Technology Services"
    assert d["coverage"]["annual_periods"] == 4 and d["coverage"]["quarterly_periods"] == 5 and d["coverage"]["currency"] == "INR"
    pe = next(i for g in d["ratios"]["groups"] for i in g["items"] if i["key"] == "pe")
    assert pe["value"] > 0 and pe["basis"] == "TTM (last 4 quarters)"
    assert d["summary"] and d["results"]["rows"][-1]["revenue"] == pytest.approx(2400.0)
    assert d["in_watchlist"] is False
    client.post("/api/watchlist/TCS")
    assert client.get("/api/company/TCS").json()["in_watchlist"] is True


def test_statements_endpoint_scales_money_but_not_per_share_counts_or_rates(client):
    loader.refresh_symbol("TCS", FACTORY)
    inc = client.get("/api/company/TCS/statements?kind=income&period=annual").json()
    assert [p["label"] for p in inc["periods"]] == ["Mar 2022", "Mar 2023", "Mar 2024", "Mar 2025"]
    by = {r["label"]: r for r in inc["rows"]}
    assert inc["rows"][0]["label"] == "Total Revenue" and by["Total Revenue"]["values"]["2025-03-31"] == pytest.approx(2400.0)         # crore
    assert by["Diluted EPS"]["kind"] == "per_share" and by["Diluted EPS"]["values"]["2025-03-31"] == pytest.approx(2400 * C * 0.19 / (36.2 * C), rel=1e-3)
    assert by["Tax Rate For Calcs"]["kind"] == "rate" and by["Tax Rate For Calcs"]["values"]["2025-03-31"] == pytest.approx(25.0)
    assert "Special Income" not in by                                                                                              # all-NaN line dropped
    bal = client.get("/api/company/TCS/statements?kind=balance&period=quarterly").json()
    assert len(bal["periods"]) == 5 and {r["label"]: r for r in bal["rows"]}["Ordinary Shares Number"]["kind"] == "count"
    assert client.get("/api/company/TCS/statements?kind=cashflow").json()["rows"][0]["label"] == "Operating Cash Flow"


def test_peers_endpoint(client):
    load_all()
    d = client.get("/api/company/TCS/peers?limit=2").json()
    assert len(d["peers"]) == 2 and d["median"] and d["metrics"][0]["key"] == "market_cap" and d["price_source"]["price_mode"] == "eod"


def test_refresh_loads_on_demand_then_respects_the_cooldown(client, monkeypatch):
    monkeypatch.setattr(loader, "make_ticker", FACTORY)
    r = client.post("/api/company/TCS/refresh")
    assert r.status_code == 200 and r.json()["annual_periods"] == 4 and client.get("/api/company/TCS").json()["coverage"]["has_data"]
    again = client.post("/api/company/TCS/refresh")
    assert again.status_code == 429 and "refreshed recently" in again.json()["detail"]
    monkeypatch.setattr(settings, "fundamentals_refresh_cooldown_min", 0)
    assert client.post("/api/company/TCS/refresh").status_code == 200


def test_refresh_failures_are_clear(client, monkeypatch):
    monkeypatch.setattr(loader, "make_ticker", f.factory([f.Spec("TCS", currency="USD")]))
    r = client.post("/api/company/TCS/refresh")
    assert r.status_code == 400 and "USD" in r.json()["error"]
    monkeypatch.setattr(loader, "make_ticker", FACTORY)
    assert service._refresh_lock.acquire(blocking=False)
    try:
        busy = client.post("/api/company/INFY/refresh")
        assert busy.status_code == 429 and "Another refresh is running" in busy.json()["detail"]
    finally:
        service._refresh_lock.release()
    assert client.post("/api/company/ZZZNOPE/refresh").status_code == 404


def test_loading_statements_widens_the_screeners_fundamental_coverage(client):
    cov = lambda: {m["key"]: m["coverage"] for m in client.get("/api/screeners/metrics").json()["metrics"]}
    assert cov()["P/E"] == 0 and cov()["Market Cap (Cr)"] == 0
    load_all()
    assert cov()["P/E"] == 7 and cov()["Market Cap (Cr)"] == 7 and cov()["ROCE %"] == 7
    r = client.post("/api/screeners/run", json={"definition": {"columns": ["Market Cap (Cr)", "ROCE %"], "filters": [{"metric": "ROCE %", "op": ">", "value": 25}]}}).json()
    assert [x["symbol"] for x in r["rows"]] == ["TCS"] and r["rows"][0]["Market Cap (Cr)"] == pytest.approx(3900 * 36.2, rel=1e-3)


# ------------------------------------------------------ bulk loader selection
def test_top_by_turnover_orders_by_traded_value(client):
    rows_ = [{"symbol": s, "date": "2026-10-16", "series": "EQ", "prev_close": 1, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1, "trades": 1,
              "turnover_cr": t, "deliv_pct": 1.0} for s, t in (("LOW", 1.0), ("MID", 50.0), ("TOP", 900.0))]
    bars.upsert_bars(pd.DataFrame(rows_))
    assert bars.top_by_turnover(2, sessions=1) == ["TOP", "MID"]


def test_bulk_selection_skips_fresh_includes_failed_and_honours_force(client):
    from scripts.load_financials import select_symbols
    loader.refresh_symbol("TCS", FACTORY)
    warehouse.upsert_fundamentals_status("INFY", fetched_at=warehouse.utc_now(), source="x", currency="INR", annual_periods=0, error="boom")
    syms, info = select_symbols(["TCS", "INFY", "WIPRO"])
    assert syms == ["INFY", "WIPRO"] and info["fresh"] == ["TCS"]                                     # failed ones are retried
    assert select_symbols(["TCS", "INFY"], force=True)[0] == ["TCS", "INFY"]
    later = datetime.now(timezone.utc) + timedelta(days=8)
    assert select_symbols(["TCS"], now=later)[0] == ["TCS"]                                            # older than the freshness window
    assert select_symbols(["TCS", "INFY", "WIPRO"], limit=1)[0] == ["INFY"]
    client.post("/api/watchlist/WIPRO")
    assert select_symbols(use_watchlist=True)[0] == ["WIPRO"] and select_symbols(refresh_loaded=True)[0] == []
    top, info = select_symbols(top=3)
    assert len(top) <= 3 and "top 3" in info["source"] and "TCS" not in top