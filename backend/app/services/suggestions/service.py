"""Build market-aware suggestion cards for the home screen chips.

Suggestions are derived from the latest end-of-day snapshot (gainers/losers,
near highs/lows, delivery) plus curated screeners and recent filings so the
list changes as the market moves — never a fixed static set.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from backend.app.repositories import warehouse
from backend.app.services.data import market_data, service as data
from backend.app.services.screener import catalog as cat


def _rel_time(iso: str | None) -> str:
    """Human relative time from an ISO / date string."""
    if not iso:
        return ""
    try:
        raw = iso.replace("Z", "+00:00")
        if "T" not in raw and len(raw) >= 10:
            raw = raw[:10] + "T00:00:00+00:00"
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        secs = max(0, int((now - dt).total_seconds()))
        if secs < 60:
            return "just now"
        if secs < 3600:
            return f"{secs // 60} min ago"
        if secs < 86400:
            h = secs // 3600
            return f"{h} hr ago" if h == 1 else f"{h} hrs ago"
        days = secs // 86400
        if days == 1:
            return "1 day ago"
        if days < 7:
            return f"{days} days ago"
        return dt.strftime("%d %b")
    except Exception:
        return iso[:10] if iso else ""


def _safe_movers(kind: str, limit: int = 8) -> list[dict]:
    try:
        res = market_data.movers(kind, limit=limit, include_sme=False)
        rows = res.get("rows") or res.get("items") or []
        return rows if isinstance(rows, list) else []
    except Exception:
        return []


def _as_of() -> str | None:
    try:
        st = market_data.status()
        return st.get("data_date") or st.get("as_of")
    except Exception:
        return None


def _screener_suggestions() -> list[dict]:
    """Pick curated screeners that match today's market regime + always-useful quality/value ones."""
    gainers = _safe_movers("gainers", 5)
    losers = _safe_movers("losers", 5)
    near_low = _safe_movers("near_52w_low", 5)
    near_high = _safe_movers("near_52w_high", 5)

    def _pct(row: dict) -> float:
        try:
            return float(row.get("Day Change %") or row.get("day_change_pct") or 0)
        except (TypeError, ValueError):
            return 0.0

    avg_gainer = 0.0
    if gainers:
        vals = [_pct(r) for r in gainers[:5]]
        avg_gainer = sum(vals) / max(len(vals), 1)

    # Tag each curated id with a short market-context subtitle
    context: dict[str, str] = {}
    if avg_gainer >= 2:
        context["c-top-gainers"] = "Momentum is hot — liquid names leading"
        context["c-high-delivery-up"] = "Strong hands joining the rally"
        context["c-1y-leaders"] = "Trend still intact on longer horizon"
    elif avg_gainer <= -1 or (losers and _pct(losers[0]) < -3):
        context["c-top-losers"] = "Risk-off day — biggest falls first"
        context["c-near-52w-low"] = "Beaten-down names near yearly lows"
        context["c-value"] = "Valuation screen when sentiment is weak"
    else:
        context["c-most-active"] = "Where the money is flowing today"
        context["c-large-cap-movers"] = "Large caps setting the tone"

    if near_low:
        context.setdefault("c-near-52w-low", "Large caps sitting in bounce-back territory")
    if near_high:
        context.setdefault("c-near-52w-high", "Names pressing against yearly highs")
    context.setdefault("c-promoter", "Most skin in the game")
    context.setdefault("c-quality", "Quality compounders with strong returns")
    context.setdefault("c-value", "Reasonable valuation, decent returns")
    context.setdefault("c-low-pb", "Cheap on book with decent ROE")
    context.setdefault("c-top-gainers", "Blue chips and midcaps leading today")
    context.setdefault("c-top-losers", "Where selling pressure is highest")

    # Priority order shifts with regime
    if avg_gainer >= 2:
        order = ["c-top-gainers", "c-high-delivery-up", "c-1y-leaders", "c-quality", "c-promoter", "c-value"]
    elif avg_gainer <= -1:
        order = ["c-near-52w-low", "c-top-losers", "c-value", "c-low-pb", "c-promoter", "c-quality"]
    else:
        order = ["c-most-active", "c-large-cap-movers", "c-promoter", "c-value", "c-quality", "c-near-52w-low"]

    items: list[dict] = []
    seen: set[str] = set()
    for sid in order:
        c = cat.CURATED_BY_ID.get(sid)
        if not c or sid in seen:
            continue
        seen.add(sid)
        items.append({
            "id": sid,
            "title": c["name"],
            "subtitle": context.get(sid, c["description"]),
            "prompt": f"Build a screener: {c['name']}",
            "kind": "screener",
            "screener_id": sid,
        })
        if len(items) >= 3:
            break

    # If we still have fewer than 3 (catalog missing), pad with static-but-labelled fallbacks
    fallbacks = [
        {"id": "fb-rsi", "title": "RSI below 30, large cap", "subtitle": "Large caps sitting in bounce-back territory",
         "prompt": "Build a screener: RSI below 30 and market cap above 20000 crore", "kind": "screener"},
        {"id": "fb-pe-roce", "title": "P/E below 25 and ROCE above 15", "subtitle": "Value with quality returns",
         "prompt": "Build a screener: P/E below 25 and ROCE above 15", "kind": "screener"},
        {"id": "fb-promoter", "title": "High promoter holding stocks", "subtitle": "Most skin in the game",
         "prompt": "Build a screener: promoter holding above 65% and market cap above 500 crore", "kind": "screener"},
    ]
    for fb in fallbacks:
        if len(items) >= 3:
            break
        if fb["id"] not in seen:
            items.append(fb)

    return items[:3]


def _research_suggestions() -> list[dict]:
    """Research-style questions driven by today's movers and notable names."""
    gainers = _safe_movers("gainers", 6)
    losers = _safe_movers("losers", 4)
    active = _safe_movers("most_active", 4)

    items: list[dict] = []

    def _name(row: dict) -> str:
        return (row.get("name") or row.get("Company Name") or row.get("symbol") or "").strip()

    def _sym(row: dict) -> str:
        return (row.get("symbol") or row.get("Symbol") or "").strip().upper()

    def _chg(row: dict) -> float:
        try:
            return float(row.get("Day Change %") or row.get("day_change_pct") or 0)
        except (TypeError, ValueError):
            return 0.0

    # Top gainer research angle
    if gainers:
        r = gainers[0]
        nm, sym, chg = _name(r), _sym(r), _chg(r)
        if nm or sym:
            label = nm or sym
            items.append({
                "id": f"res-gainer-{sym or label}",
                "title": f"{label} up {chg:.1f}%: What’s driving the move?",
                "subtitle": "Top gainer today — dig into results, orders and news",
                "prompt": f"Research {label}: why is the stock up today, key ratios, recent results and peer comparison",
                "kind": "research",
            })

    # Active name (volume story)
    if active:
        r = active[0]
        nm, sym = _name(r), _sym(r)
        if (nm or sym) and not any(sym and sym in (it.get("id") or "") for it in items):
            label = nm or sym
            items.append({
                "id": f"res-active-{sym or label}",
                "title": f"{label}: Why is volume elevated?",
                "subtitle": "Most actively traded — check delivery and news flow",
                "prompt": f"Research {label}: volume spike, delivery percentage, latest news and whether the move looks institutional",
                "kind": "research",
            })

    # Loser / risk angle
    if losers:
        r = losers[0]
        nm, sym, chg = _name(r), _sym(r), _chg(r)
        if nm or sym:
            label = nm or sym
            items.append({
                "id": f"res-loser-{sym or label}",
                "title": f"{label} down {abs(chg):.1f}%: Is the sell-off justified?",
                "subtitle": "Biggest faller — weigh fundamentals vs sentiment",
                "prompt": f"Research {label}: reasons for today's fall, valuation, recent filings and whether it looks oversold",
                "kind": "research",
            })

    # Sector / macro style prompts when we have fewer than 3
    extras = [
        {
            "id": "res-it-sector",
            "title": "IT sector: Are margins under pressure?",
            "subtitle": "Compare TCS, Infosys and HCL on margins and guidance",
            "prompt": "Research IT sector: compare TCS, Infosys and HCL Tech on margins, growth and valuation",
            "kind": "research",
        },
        {
            "id": "res-banks",
            "title": "Private banks: Which names look resilient?",
            "subtitle": "Asset quality, NIMs and valuations across large private banks",
            "prompt": "Research private banks: compare HDFC Bank, ICICI Bank and Axis Bank on asset quality, NIMs and valuation",
            "kind": "research",
        },
        {
            "id": "res-defence",
            "title": "Defence stocks: Is order book strength still priced in?",
            "subtitle": "BEL, HAL and peers on orders, margins and valuations",
            "prompt": "Research defence stocks: BEL and HAL order books, recent margins and whether valuations look stretched",
            "kind": "research",
        },
    ]
    for ex in extras:
        if len(items) >= 3:
            break
        if not any(ex["id"] == it["id"] for it in items):
            items.append(ex)

    return items[:3]


def _news_suggestions() -> list[dict]:
    """Recent filings / announcements as news cards with relative timestamps."""
    items: list[dict] = []
    rows: list[dict] = []

    # Prefer recent filings from warehouse
    try:
        with warehouse.connect() as conn:
            rows = conn.execute(
                """SELECT symbol, title, filed_at, source, doc_type
                   FROM filings
                   ORDER BY filed_at DESC
                   LIMIT 12"""
            ).fetchall()
            rows = [dict(r) for r in rows]
    except Exception:
        rows = []

    for r in rows:
        title = (r.get("title") or "").strip()
        sym = (r.get("symbol") or "").strip().upper()
        if not title:
            continue
        when = _rel_time(r.get("filed_at"))
        items.append({
            "id": f"news-{sym}-{r.get('filed_at')}-{len(items)}",
            "title": title if len(title) < 90 else title[:87] + "…",
            "subtitle": when or (r.get("source") or "Exchange filing"),
            "prompt": f"Latest news and filings on {sym}" if sym else f"Summarise: {title}",
            "kind": "news",
            "symbol": sym or None,
            "published": r.get("filed_at"),
        })
        if len(items) >= 3:
            break

    # Supplement with yfinance-style headlines for active names if filings are thin
    if len(items) < 3:
        for kind in ("most_active", "gainers"):
            for row in _safe_movers(kind, 4):
                if len(items) >= 3:
                    break
                sym = (row.get("symbol") or row.get("Symbol") or "").strip().upper()
                name = (row.get("name") or row.get("Company Name") or sym).strip()
                if not sym:
                    continue
                try:
                    news = data.news(sym)
                    nrows = (news or {}).get("rows") or []
                    if not nrows:
                        continue
                    n = nrows[0]
                    title = (n.get("title") or f"{name}: latest update").strip()
                    when = _rel_time(n.get("published"))
                    items.append({
                        "id": f"news-yf-{sym}-{len(items)}",
                        "title": title if len(title) < 90 else title[:87] + "…",
                        "subtitle": when or "Recent",
                        "prompt": f"Latest news on {name}",
                        "kind": "news",
                        "symbol": sym,
                        "published": n.get("published"),
                    })
                except Exception:
                    continue
            if len(items) >= 3:
                break

    # Final fallbacks so the panel is never empty
    if not items:
        items = [
            {
                "id": "news-fb-1",
                "title": "Nifty 50: market breadth and sector leadership today",
                "subtitle": "Market wrap",
                "prompt": "Latest market news: Nifty 50 performance, sector leaders and key headlines today",
                "kind": "news",
            },
            {
                "id": "news-fb-2",
                "title": "Corporate results and management commentary this week",
                "subtitle": "Earnings watch",
                "prompt": "Recent corporate results and management commentary for large-cap Indian stocks",
                "kind": "news",
            },
            {
                "id": "news-fb-3",
                "title": "Macro and policy news that could move markets",
                "subtitle": "Macro",
                "prompt": "Latest macro and policy news relevant for Indian equity markets",
                "kind": "news",
            },
        ]

    return items[:3]


def get_suggestions(kind: str | None = None) -> dict[str, Any]:
    """Return suggestion cards. kind = screener | research | news | None (all)."""
    kind = (kind or "").strip().lower() or None
    out: dict[str, Any] = {
        "as_of": _as_of(),
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    if kind in (None, "screener"):
        out["screener"] = _screener_suggestions()
    if kind in (None, "research"):
        out["research"] = _research_suggestions()
    if kind in (None, "news"):
        out["news"] = _news_suggestions()
    return out
