"""Run a screener definition over every NSE stock we hold.

Rows start from the end-of-day snapshot (market_data.snapshot, all stocks) and are left-joined with the
warehouse's fundamentals and shareholding. A missing value stays missing (None, shown as a dash): it is never
turned into zero, and a stock missing a filtered metric is excluded from that filter, never guessed at.
"""
from __future__ import annotations

import math
import re

import pandas as pd

from backend.app.repositories import warehouse
from backend.app.services.data import market_data
from backend.app.services.screener import catalog as cat
from backend.app.services.screener.catalog import BY_KEY, COMPANY, NUM

MAX_COLUMNS, MAX_FILTERS, MAX_LIMIT = 25, 15, 200
RUPEES_PER_CRORE = 1e7       # warehouse ratios.market_cap is raw rupees (yfinance marketCap)
_RATIO_COLS = {"pe": "P/E", "pb": "P/B", "roce": "ROCE %", "roe": "ROE %", "net_margin": "Net margin %",
               "op_margin": "Operating margin %"}
_PRICE_COLS = ["LTP", "Prev Close", "Open", "High", "Low", "Change", "Day Change %", "Volume", "Turnover (Cr)",
               "Delivery %", "1M Return %", "3M Return %", "1Y Return %", "52W High", "52W Low",
               "% From 52W High", "% From 52W Low"]


# --------------------------------------------------------------- definitions
def normalize(defn: dict | None) -> dict:
    """Validate a definition and fill defaults. Raises ValueError with a message fit to show the user."""
    defn = defn or {}
    cols = defn.get("columns")
    cols = list(cat.DEFAULT_COLUMNS) if cols is None else cols
    if not isinstance(cols, list):
        raise ValueError("columns must be a list")
    out_cols = []
    for c in cols:
        if c == COMPANY or c in out_cols:
            continue
        if c not in BY_KEY:
            raise ValueError(f"Unknown column '{c}'.")
        out_cols.append(c)
    if len(out_cols) > MAX_COLUMNS:
        raise ValueError(f"At most {MAX_COLUMNS} columns are allowed.")

    filters = defn.get("filters") or []
    if not isinstance(filters, list) or len(filters) > MAX_FILTERS:
        raise ValueError(f"filters must be a list of at most {MAX_FILTERS}.")
    out_f = []
    for f in filters:
        m = BY_KEY.get((f or {}).get("metric"))
        if not m or m.key == COMPANY:
            raise ValueError(f"Cannot filter on '{(f or {}).get('metric')}'.")
        op = f.get("op")
        if op not in (cat.NUM_OPS if m.kind == NUM else cat.TEXT_OPS):
            raise ValueError(f"'{op}' is not a valid condition for {m.label}.")
        v = f.get("value")
        if m.kind == NUM:
            if isinstance(v, bool) or v in (None, ""):
                raise ValueError(f"{m.label} needs a number.")
            try:
                v = float(v)
            except (TypeError, ValueError):
                raise ValueError(f"{m.label} needs a number, got '{v}'.") from None
            if math.isnan(v) or math.isinf(v):
                raise ValueError(f"{m.label} needs a finite number.")
        else:
            v = str(v or "").strip()[:60]
            if not v:
                raise ValueError(f"{m.label} needs a value.")
        out_f.append({"metric": m.key, "op": op, "value": v})

    sort = defn.get("sort") or {}
    sm = sort.get("metric", "Turnover (Cr)")
    if sm not in BY_KEY:
        raise ValueError(f"Cannot sort by '{sm}'.")
    sdir = sort.get("dir", "desc")
    if sdir not in ("asc", "desc"):
        raise ValueError("sort dir must be asc or desc.")
    uni = defn.get("universe") or {}
    return {"columns": out_cols, "filters": out_f, "sort": {"metric": sm, "dir": sdir},
            "universe": {"include_sme": bool(uni.get("include_sme", True))}}


def describe_filters(filters: list[dict]) -> str:
    return " · ".join(f"{BY_KEY[f['metric']].label} {f['op']} {f['value']:g}" if isinstance(f["value"], float)
                      else f"{BY_KEY[f['metric']].label} {f['op']} {f['value']}" for f in filters)


# ---------------------------------------------------------------------- data
def build_frame(include_sme: bool = True) -> tuple[pd.DataFrame, dict]:
    snap = market_data.snapshot()                  # raises NoMarketData with a clear hint when bars are empty
    df = snap.df
    df = df[df["series"].isin(market_data.EQUITY_SERIES + (market_data.SME_SERIES if include_sme else []))]
    if snap.has_master:
        df = df[df["in_master"]]                   # drops ETFs, bonds, REITs that are not in the equity master
    df = df[[c for c in _PRICE_COLS if c in df.columns] + ["name", "sector"]].copy()
    df[COMPANY] = df["name"]
    df["Sector"] = df["sector"].where(df["sector"].ne("—"), None)

    ratios = warehouse.list_ratio_rows()
    fund_meta = {"sources": sorted({r["source"] for r in ratios if r.get("source")}),
                 "as_of": max((r["as_of"] for r in ratios if r.get("as_of")), default=None)}
    if ratios:
        rf = pd.DataFrame(ratios).set_index("symbol")
        j = rf[list(_RATIO_COLS)].rename(columns=_RATIO_COLS)
        j["Market Cap (Cr)"] = rf["market_cap"] / RUPEES_PER_CRORE
        df = df.join(j)
    holders = warehouse.latest_shareholding_all()
    if holders:
        hf = pd.DataFrame(holders)
        hf = hf[hf["holder_type"].isin(cat.HOLDER_COLUMN)]
        df = df.join(hf.pivot_table(index="symbol", columns="holder_type", values="pct", aggfunc="last")
                     .rename(columns=cat.HOLDER_COLUMN))
    for m in cat.METRICS:                          # a metric with no data is an all-missing column, not an error
        if m.key not in df.columns:
            df[m.key] = pd.Series([None] * len(df), index=df.index, dtype="object" if m.kind != NUM else "float64")
    return df, {"fund": fund_meta, "snapshot": snap}


def coverage() -> dict:
    df, meta = build_frame()
    return {"universe": len(df), "coverage": {m.key: int(df[m.key].notna().sum()) for m in cat.METRICS},
            "as_of": market_data._meta(meta["snapshot"])["as_of"]}


def _mask(df: pd.DataFrame, f: dict) -> pd.Series:
    col, op, v = df[f["metric"]], f["op"], f["value"]
    if BY_KEY[f["metric"]].kind == NUM:
        col = pd.to_numeric(col, errors="coerce")
        hit = {">": col > v, "<": col < v, ">=": col >= v, "<=": col <= v, "=": col == v, "!=": col != v}[op]
    else:
        s = col.astype("string").str.lower()
        v = v.lower()
        hit = (s.str.contains(re.escape(v), regex=True) if op == "contains" else s == v if op == "=" else s != v)
        hit = hit.fillna(False).astype(bool)
    return col.notna() & hit                       # missing never passes a filter


def _sort(df: pd.DataFrame, sort: dict) -> pd.DataFrame:
    key, asc = sort["metric"], sort["dir"] == "asc"
    df = df.sort_index()                           # stable, deterministic tie-break by symbol
    if BY_KEY[key].kind == NUM:
        return df.sort_values(key, ascending=asc, na_position="last", kind="mergesort")
    return df.assign(_k=df[key].astype("string").str.lower()).sort_values("_k", ascending=asc, na_position="last",
                                                                          kind="mergesort").drop(columns="_k")


def _val(v):
    if v is None or v is pd.NA:
        return None
    if isinstance(v, float):
        return None if math.isnan(v) or math.isinf(v) else round(v, 2)
    if hasattr(v, "item"):
        return _val(v.item())
    return v


def _row(sym: str, r: pd.Series, keys: list[str]) -> dict:
    row = {"symbol": sym, "name": r["name"], "LTP": _val(r["LTP"]), "Change": _val(r["Change"])}
    for k in keys:
        if k not in row:
            row[k] = _val(r[k])
    return row


# ----------------------------------------------------------------------- run
def run(defn: dict | None, limit: int = 50, offset: int = 0) -> dict:
    d = normalize(defn)
    limit, offset = max(1, min(int(limit or 50), MAX_LIMIT)), max(0, int(offset or 0))
    df, meta = build_frame(d["universe"]["include_sme"])
    universe = len(df)
    mask = pd.Series(True, index=df.index)
    for f in d["filters"]:
        mask &= _mask(df, f)
    sub = _sort(df[mask], d["sort"])
    page = sub.iloc[offset: offset + limit]
    rows = [_row(sym, r, d["columns"]) for sym, r in page.iterrows()]

    used = [f["metric"] for f in d["filters"]] + d["columns"] + [d["sort"]["metric"]]
    notes = []
    snap_meta = market_data.screen_meta()
    if snap_meta.get("note") and any(k in cat.NEEDS_HISTORY for k in used):
        notes.append(snap_meta["note"])
    for f in d["filters"]:
        have = int(df[f["metric"]].notna().sum())
        if BY_KEY[f["metric"]].source != "price" and have < universe:
            notes.append(f"{BY_KEY[f['metric']].label} is available for {have:,} of {universe:,} stocks; "
                         f"stocks without it are left out of this filter.")
    for k in d["columns"]:
        if int(df[k].notna().sum()) == 0 and k not in [f["metric"] for f in d["filters"]] and BY_KEY[k].source != "price":
            notes.append(f"No {BY_KEY[k].label} data has been loaded yet, so that column is empty.")
    if snap_meta.get("live_note"):
        notes.append(snap_meta["live_note"])
    if snap_meta.get("price_mode") == "live":
        notes.append("Prices are live from Angel One. Turnover is the value traded so far today, and Delivery % is published only after the close, "
                     "so it is empty until then.")

    sources = [{"source": snap_meta["source"], "as_of": snap_meta["as_of"]}]
    if any(BY_KEY[k].source in ("ratios", "holding") for k in used) and meta["fund"]["sources"]:
        sources.append({"source": "Local warehouse fundamentals (" + ", ".join(meta["fund"]["sources"]) + ")",
                        "as_of": meta["fund"]["as_of"]})
    shown = [BY_KEY[k] for k in [COMPANY, *d["columns"]]]
    return {"rows": rows, "total": len(sub), "limit": limit, "offset": offset, "universe": universe,
            "columns": [{"key": m.key, "label": m.label, "kind": m.kind, "fmt": m.fmt, "render": m.render} for m in shown],
            "definition": d, "notes": list(dict.fromkeys(notes)), "sources": sources,
            "source": snap_meta["source"], "as_of": snap_meta["as_of"], "data_date": snap_meta.get("data_date"),
            "price_mode": snap_meta.get("price_mode", "eod"), "live_progress": snap_meta.get("live_progress")}


def preview(defn: dict, n: int = 4) -> dict | None:
    """Match count and the first few symbols, for the cards on the screener home page."""
    try:
        r = run(defn, limit=n)
    except ValueError:
        return None
    return {"total": r["total"], "symbols": [x["symbol"] for x in r["rows"]]}


# ---------------------------------------------------------- from chat results
_MOVER = {"gainers": ("Day Change %", ">", 0, "desc", "Top Gainers"),
          "losers": ("Day Change %", "<", 0, "asc", "Top Losers"),
          "most_active": (None, None, None, "desc", "Most Active"),
          "near_52w_high": ("% From 52W High", ">=", -2, "desc", "Near 52-Week High"),
          "near_52w_low": ("% From 52W Low", "<=", 2, "desc", "Near 52-Week Low")}


def from_tool(tool: str, args: dict | None) -> tuple[str, str, dict]:
    """Turn a chat `market_movers` / `screener` call into (name, description, definition)."""
    from backend.app.config.settings import settings
    args = args or {}
    if tool == "market_movers":
        kind = args.get("kind") or "gainers"
        if kind not in _MOVER:
            raise ValueError(f"Unknown movers kind '{kind}'.")
        metric, op, val, sdir, title = _MOVER[kind]
        min_t = settings.movers_min_turnover_cr if args.get("min_turnover_cr") is None else float(args["min_turnover_cr"])
        filters = ([{"metric": metric, "op": op, "value": val}] if metric else []) + \
                  [{"metric": "Turnover (Cr)", "op": ">=", "value": min_t}]
        if args.get("sector"):
            filters.append({"metric": "Sector", "op": "contains", "value": str(args["sector"])})
        sort = (metric, sdir) if kind in ("gainers", "losers") else ("Turnover (Cr)", "desc")
        cols = list(cat.MOVER_COLUMNS)
        if kind.startswith("near_52w"):
            cols += ["52W High", "52W Low", metric]
        d = {"columns": cols, "filters": filters, "sort": {"metric": sort[0], "dir": sort[1]},
             "universe": {"include_sme": bool(args.get("include_sme", True))}}
        name = f"{title} · {args['sector']}" if args.get("sector") else title
    elif tool == "screener":
        filters = args.get("filters") or []
        metrics = list(dict.fromkeys(f.get("metric") for f in filters if f.get("metric") in BY_KEY))
        cols = ["Sector", *[m for m in metrics if m not in ("Sector", "Day Change %")], "Day Change %", "Turnover (Cr)"]
        d = {"columns": list(dict.fromkeys(cols)), "filters": filters,
             "sort": {"metric": "Turnover (Cr)", "dir": "desc"}, "universe": {"include_sme": True}}
        name = "Screener: " + (describe_filters(normalize(d)["filters"]) or "all stocks")
    else:
        raise ValueError("Only screener and market_movers results can be opened as a screener.")
    d = normalize(d)
    return name[:120], describe_filters(d["filters"]) or "All NSE stocks", d


# ------------------------------------------------------------ watchlist view
def lookup(symbols: list[str]) -> dict:
    """Latest prices for specific symbols (the watchlist page): live from Angel One where possible, otherwise the stored
    end-of-day values, and each row says which. Unknown symbols are flagged, not dropped."""
    from backend.app.services.data import live_prices
    cols = ["Day Change %", "1M Return %", "% From 52W High", "Turnover (Cr)", "Sector"]
    base = market_data.eod_snapshot()
    syms = list(dict.fromkeys(s.upper() for s in symbols))
    known = [s for s in syms if s in base.df.index]
    now = live_prices.now_ist()
    quotes, info = ({}, {"errors": [], "missing": []})
    if known and live_prices.in_window(now, base.as_of):
        quotes, info = live_prices.get_quotes(known)
    df = live_prices.apply_quotes(base.df.loc[known], quotes) if known else base.df.iloc[0:0]
    df = df.assign(Sector=df["sector"].where(df["sector"].ne("—"), None))
    rows = []
    for sym in syms:
        if sym not in df.index:
            rows.append({"symbol": sym, "name": None, "missing": True})
            continue
        row = _row(sym, df.loc[sym], cols)
        row["price_source"] = "live" if sym in quotes else "eod"
        rows.append(row)
    n_live = sum(1 for r in rows if r.get("price_source") == "live")
    mode = "live" if n_live and n_live == len(known) else "mixed" if n_live else "eod"
    stamp = ""
    if quotes:
        import datetime as _dt
        stamp = _dt.datetime.fromtimestamp(max(q["at"] for q in quotes.values()), now.tzinfo).strftime("%H:%M:%S IST")
    state = "live" if live_prices.market_open(now) else "latest price, market closed"
    notes = []
    if known and live_prices.in_window(now, base.as_of) and mode != "live":
        why = "; ".join(info["errors"]) or ("no live quote for " + ", ".join(s for s in known if s not in quotes))
        notes.append(f"Some prices are end of day (NSE bhavcopy) because live prices were not available: {why}.")
    return {"rows": rows, "mode": mode, "notes": notes,
            "as_of": f"{stamp} ({state})" if mode != "eod" else f"{base.as_of.isoformat()} (market close)",
            "source": "Angel One SmartAPI (live prices) + NSE bhavcopy (history)" if mode != "eod" else market_data.SOURCE,
            "market_open": live_prices.market_open(now), "refresh_sec": 15 if mode != "eod" else 0}