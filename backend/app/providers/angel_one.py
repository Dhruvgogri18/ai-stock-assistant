"""Angel One SmartAPI — technical data (LTP, candles, indices).

Requires env: ANGEL_API_KEY, ANGEL_CLIENT_ID, ANGEL_PASSWORD, ANGEL_TOTP_SECRET
Docs: https://smartapi.angelbroking.com/

Does NOT provide fundamentals (P/E, ROE, statements, shareholding).
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from backend.app.config.settings import settings

BASE = "https://apiconnect.angelone.in"
SCRIP_MASTER = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"

INDEX_TOKENS = {
    "NIFTY": ("99926000", "Nifty 50", "NSE"),
    "NIFTY50": ("99926000", "Nifty 50", "NSE"),
    "NIFTY 50": ("99926000", "Nifty 50", "NSE"),
    "BANKNIFTY": ("99926009", "Nifty Bank", "NSE"),
    "NIFTYBANK": ("99926009", "Nifty Bank", "NSE"),
    "NIFTY BANK": ("99926009", "Nifty Bank", "NSE"),
}

_session: dict[str, Any] = {"jwt": "", "feed": "", "expires": 0.0}
_token_cache: dict[str, tuple[str, str, str]] = {}  # key -> (token, tradingsymbol, exchange)
_master: list[dict] | None = None
_master_loaded_at: float = 0.0


def configured() -> bool:
    return bool(settings.angel_api_key and settings.angel_client_id and settings.angel_password)


def _totp() -> str:
    secret = (settings.angel_totp_secret or "").strip()
    if not secret:
        raise ValueError("Set ANGEL_TOTP_SECRET (base32) from SmartAPI TOTP setup.")
    try:
        import pyotp
    except ImportError as e:
        raise ValueError("Install pyotp: pip install pyotp") from e
    return pyotp.TOTP(secret).now()


def _login() -> None:
    if not configured():
        raise ValueError("Angel One not configured. Set ANGEL_API_KEY, ANGEL_CLIENT_ID, ANGEL_PASSWORD.")
    if _session["jwt"] and time.time() < _session["expires"] - 60:
        return
    payload = {
        "clientcode": settings.angel_client_id,
        "password": settings.angel_password,
        "totp": _totp() if settings.angel_totp_secret else "",
    }
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "X-UserType": "USER",
        "X-SourceID": "WEB",
        "X-ClientLocalIP": "127.0.0.1",
        "X-ClientPublicIP": "127.0.0.1",
        "X-MACAddress": "00:00:00:00:00:00",
        "X-PrivateKey": settings.angel_api_key,
    }
    with httpx.Client(timeout=30.0) as client:
        r = client.post(
            f"{BASE}/rest/auth/angelbroking/user/v1/loginByPassword",
            json=payload,
            headers=headers,
        )
        r.raise_for_status()
        data = r.json()
    if not data.get("status"):
        raise ValueError(f"Angel login failed: {data.get('message') or data}")
    d = data.get("data") or {}
    _session["jwt"] = d.get("jwtToken") or ""
    _session["feed"] = d.get("feedToken") or ""
    _session["expires"] = time.time() + 6 * 3600
    if not _session["jwt"]:
        raise ValueError("Angel login returned no jwtToken")


def _auth_headers() -> dict:
    _login()
    return {
        "Authorization": f"Bearer {_session['jwt']}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "X-UserType": "USER",
        "X-SourceID": "WEB",
        "X-ClientLocalIP": "127.0.0.1",
        "X-ClientPublicIP": "127.0.0.1",
        "X-MACAddress": "00:00:00:00:00:00",
        "X-PrivateKey": settings.angel_api_key,
    }


def _meta() -> dict:
    return {
        "source": "Angel One SmartAPI",
        "as_of": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    }


_KEEP_EXCH = {"NSE", "NSE_CM", "NSECM"}


def _filter_master(raw: bytes) -> list[dict]:
    """Parse Angel's instrument dump one record at a time and keep only NSE rows (and only the fields we use).

    The full file covers every exchange incl. all futures/options (100k+ rows); loading it whole costs hundreds of MB,
    which gets a 512 MB host killed.
    """
    text = raw.decode("utf-8")
    dec = json.JSONDecoder()
    i, n = text.index("[") + 1, len(text)
    out: list[dict] = []
    while i < n:
        while i < n and text[i] in " \t\r\n,":
            i += 1
        if i >= n or text[i] == "]":
            break
        row, i = dec.raw_decode(text, i)
        if isinstance(row, dict) and str(row.get("exch_seg") or "").upper() in _KEEP_EXCH:
            out.append({"token": row.get("token"), "symbol": row.get("symbol"),
                        "name": row.get("name"), "exch_seg": row.get("exch_seg")})
    return out


def _load_master(force: bool = False) -> list[dict]:
    """NSE instruments from Angel's dump (cached ~12h)."""
    global _master, _master_loaded_at
    if _master is not None and not force and (time.time() - _master_loaded_at) < 12 * 3600:
        return _master
    with httpx.Client(timeout=120.0) as client:
        r = client.get(SCRIP_MASTER)
        r.raise_for_status()
        raw = r.content
    data = _filter_master(raw)
    del raw
    if not data:
        raise ValueError("Unexpected scrip master format")
    _master = data
    _master_loaded_at = time.time()
    return _master


def _from_master(symbol: str) -> tuple[str, str, str] | None:
    """Match NSE equity token from scrip master."""
    sym = symbol.upper().strip()
    candidates = []
    for row in _load_master():
        exch = (row.get("exch_seg") or row.get("exchange") or "").upper()
        ts = (row.get("symbol") or row.get("tradingsymbol") or "").upper()
        name = (row.get("name") or "").upper()
        token = str(row.get("token") or row.get("symboltoken") or "")
        if not token:
            continue
        # NSE cash equity
        if exch in ("NSE", "NSE_CM", "nse_cm", "NSECM"):
            if ts == f"{sym}-EQ" or ts == sym or name == sym:
                candidates.append((token, ts if ts else f"{sym}-EQ", "NSE"))
            elif ts.startswith(f"{sym}-") and ts.endswith("-EQ"):
                candidates.append((token, ts, "NSE"))
        # also allow exact name match on nse_cm lowercase form used in dump
        if (row.get("exch_seg") or "").lower() == "nse_cm":
            if ts == f"{sym}-EQ" or name == sym:
                candidates.append((token, ts or f"{sym}-EQ", "NSE"))
    if not candidates:
        # fuzzy: name contains symbol and -EQ
        for row in _load_master():
            if (row.get("exch_seg") or "").lower() != "nse_cm":
                continue
            ts = (row.get("symbol") or "").upper()
            if ts.startswith(sym) and ts.endswith("-EQ"):
                token = str(row.get("token") or "")
                if token:
                    candidates.append((token, ts, "NSE"))
                    break
    return candidates[0] if candidates else None


def resolve_token(symbol: str, exchange: str = "NSE") -> tuple[str, str, str]:
    """Return (token, tradingsymbol, exchange)."""
    sym = symbol.upper().strip().replace(" ", "")
    if sym in INDEX_TOKENS:
        tok, ts, ex = INDEX_TOKENS[sym]
        return tok, ts, ex

    key = f"{exchange}:{sym}"
    if key in _token_cache:
        return _token_cache[key]

    # 1) Scrip master (reliable for VARROC-EQ etc.)
    try:
        hit = _from_master(sym)
        if hit:
            _token_cache[key] = hit
            return hit
    except Exception:
        pass

    # 2) searchScrip API
    try:
        with httpx.Client(timeout=30.0) as client:
            r = client.post(
                f"{BASE}/rest/secure/angelbroking/order/v1/searchScrip",
                headers=_auth_headers(),
                json={"exchange": exchange, "searchscrip": sym},
            )
            r.raise_for_status()
            data = r.json()
        rows = data.get("data") or []
        for row in rows:
            ts = (row.get("tradingsymbol") or "").upper()
            tok = str(row.get("symboltoken") or "")
            if not tok:
                continue
            if ts == f"{sym}-EQ" or ts == sym:
                hit = (tok, ts or f"{sym}-EQ", exchange)
                _token_cache[key] = hit
                return hit
        # prefer any -EQ
        for row in rows:
            ts = (row.get("tradingsymbol") or "").upper()
            tok = str(row.get("symboltoken") or "")
            if tok and ts.endswith("-EQ"):
                hit = (tok, ts, exchange)
                _token_cache[key] = hit
                return hit
        if rows:
            row = rows[0]
            hit = (
                str(row.get("symboltoken")),
                (row.get("tradingsymbol") or f"{sym}-EQ"),
                exchange,
            )
            _token_cache[key] = hit
            return hit
    except Exception:
        pass

    raise ValueError(
        f"Angel One: could not resolve token for {sym}. "
        "Check the symbol is NSE-listed equity (e.g. VARROC)."
    )


def ltp(symbol: str, exchange: str = "NSE") -> dict:
    token, tradingsymbol, ex = resolve_token(symbol, exchange)
    with httpx.Client(timeout=30.0) as client:
        r = client.post(
            f"{BASE}/rest/secure/angelbroking/order/v1/getLtpData",
            headers=_auth_headers(),
            json={
                "exchange": ex,
                "tradingsymbol": tradingsymbol,
                "symboltoken": token,
            },
        )
        r.raise_for_status()
        data = r.json()
    if not data.get("status"):
        raise ValueError(data.get("message") or "LTP failed")
    d = data.get("data") or {}
    # API sometimes returns a list
    if isinstance(d, list) and d:
        d = d[0]
    price = d.get("ltp")
    if price is None:
        raise ValueError(f"Angel One: no LTP in response for {symbol}")
    return {
        "symbol": symbol.upper(),
        "tradingsymbol": tradingsymbol,
        "token": token,
        "ltp": float(price),
        "open": d.get("open"),
        "high": d.get("high"),
        "low": d.get("low"),
        "close": d.get("close"),
        **_meta(),
    }


# ------------------------------------------------------------ batch quotes
# ASSUMPTION, verify with `python -m scripts.check_angel_quotes`: SmartAPI's market-data "quote" endpoint takes up to 50 tokens
# per request ({"mode": "FULL", "exchangeTokens": {"NSE": [...]}}) and answers {"data": {"fetched": [...], "unfetched": [...]}}
# with ltp/open/high/low/close (close = previous close), tradeVolume, avgPrice and exchFeedTime. Requests are rate limited by
# the caller (services/data/live_prices.py), not here.
QUOTE_MAX = 50
CASH_SUFFIXES = ("-EQ", "-BE", "-BZ", "-SM", "-ST")          # NSE cash segment series, in order of preference
_index: dict[str, tuple[str, str]] | None = None
_index_for: int = 0


def token_index() -> dict[str, tuple[str, str]]:
    """NSE cash symbol -> (token, tradingsymbol), built once from the instrument dump (resolve_token scans it per call)."""
    global _index, _index_for
    master = _load_master()
    if _index is not None and _index_for == id(master):
        return _index
    best: dict[str, tuple[int, str, str]] = {}
    for row in master:
        if (row.get("exch_seg") or "").upper() != "NSE":
            continue
        ts = (row.get("symbol") or "").upper()
        token = str(row.get("token") or "")
        suffix = next((s for s in CASH_SUFFIXES if ts.endswith(s)), None)
        if not suffix or not token:
            continue
        base, rank = ts[: -len(suffix)], CASH_SUFFIXES.index(suffix)
        if base not in best or rank < best[base][0]:
            best[base] = (rank, token, ts)
    _index, _index_for = {k: (v[1], v[2]) for k, v in best.items()}, id(master)
    return _index


def _num(v: Any) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f and f not in (float("inf"), float("-inf")) else None


def parse_quote_response(data: dict, by_token: dict[str, str]) -> dict[str, dict]:
    """Angel quote response -> {SYMBOL: fields}. A row without a usable (positive) price is not a quote and is dropped."""
    if not data.get("status"):
        raise ValueError(data.get("message") or "Angel One quote request failed")
    d = data.get("data")
    if not isinstance(d, dict) or not isinstance(d.get("fetched"), list):
        raise ValueError("Unexpected Angel One quote response shape")
    out: dict[str, dict] = {}
    for row in d["fetched"]:
        sym = by_token.get(str(row.get("symbolToken") or ""))
        ltp = _num(row.get("ltp"))
        if not sym or not ltp or ltp <= 0:
            continue
        out[sym] = {"ltp": ltp, "open": _num(row.get("open")), "high": _num(row.get("high")), "low": _num(row.get("low")),
                    "prev_close": _num(row.get("close")), "volume": _num(row.get("tradeVolume")),
                    "avg_price": _num(row.get("avgPrice")),
                    "w52_high": _num(row.get("52WeekHigh") or row.get("52WeekHighPrice")),
                    "w52_low": _num(row.get("52WeekLow") or row.get("52WeekLowPrice")), "feed_time": row.get("exchFeedTime") or row.get("exchTradeTime")}
    return out


def quote_chunk(symbols: list[str]) -> tuple[dict[str, dict], list[str]]:
    """One batch request for up to 50 symbols. Returns (quotes by symbol, symbols that got no quote)."""
    idx = token_index()
    by_token: dict[str, str] = {}
    for s in symbols:
        hit = idx.get(s.upper().strip())
        if hit:
            by_token[hit[0]] = s.upper().strip()
    if len(by_token) > QUOTE_MAX:
        raise ValueError(f"At most {QUOTE_MAX} symbols per quote request.")
    found: dict[str, dict] = {}
    if by_token:
        with httpx.Client(timeout=30.0) as client:
            r = client.post(f"{BASE}/rest/secure/angelbroking/market/v1/quote/", headers=_auth_headers(),
                            json={"mode": "FULL", "exchangeTokens": {"NSE": list(by_token)}})
            r.raise_for_status()
            found = parse_quote_response(r.json(), by_token)
    return found, [s for s in symbols if s.upper().strip() not in found]


def _candle_rows(symbol: str, days: int) -> tuple[list, str]:
    """Raw daily candles [timestamp, open, high, low, close, volume] for roughly the last `days` calendar days."""
    token, tradingsymbol, ex = resolve_token(symbol)
    to = datetime.now()
    fr = to - timedelta(days=min(days, 2000))
    payload = {
        "exchange": ex,
        "symboltoken": token,
        "interval": "ONE_DAY",
        "fromdate": fr.strftime("%Y-%m-%d 09:15"),
        "todate": to.strftime("%Y-%m-%d 15:30"),
    }
    with httpx.Client(timeout=60.0) as client:
        r = client.post(
            f"{BASE}/rest/secure/angelbroking/historical/v1/getCandleData",
            headers=_auth_headers(),
            json=payload,
        )
        r.raise_for_status()
        data = r.json()
    rows = data.get("data") or []
    if not rows:
        raise ValueError(f"Angel One: no candles for {symbol} ({tradingsymbol}): {data.get('message') or ''}".strip())
    return rows, tradingsymbol


def history(symbol: str, period: str = "5y") -> dict:
    """Daily candles → chart payload (close only)."""
    days = {
        "1mo": 30, "1m": 30, "3mo": 90, "3m": 90, "6mo": 180, "6m": 180,
        "1y": 365, "2y": 730, "5y": 1800, "max": 2000,
    }.get(period, 365)
    rows, tradingsymbol = _candle_rows(symbol, days)
    dates, closes = [], []
    for row in rows:
        if isinstance(row, (list, tuple)) and len(row) >= 5:
            dates.append(str(row[0])[:10])
            closes.append(round(float(row[4]), 2))
    if not closes:
        raise ValueError(f"Angel One: could not parse candles for {symbol}")
    return {"chart": {"dates": dates, "close": closes}, "symbol": symbol.upper(), **_meta()}


def candles(symbol: str, bars: int = 300) -> list[dict]:
    """Last ~`bars` daily candles with open/high/low/close/volume (used for technical indicators).

    Includes today's still-forming candle while the market is open; the indicator code drops it."""
    days = min(int(bars * 1.55) + 15, 2000)
    rows, tradingsymbol = _candle_rows(symbol, days)
    out = []
    for row in rows:
        if isinstance(row, (list, tuple)) and len(row) >= 5:
            try:
                out.append({
                    "date": str(row[0])[:10],
                    "open": float(row[1]), "high": float(row[2]), "low": float(row[3]), "close": float(row[4]),
                    "volume": float(row[5]) if len(row) > 5 and row[5] is not None else None,
                })
            except (TypeError, ValueError):
                continue
    if not out:
        raise ValueError(f"Angel One: could not parse candles for {symbol} ({tradingsymbol})")
    return out


def indices() -> dict:
    out = []
    for name, (token, ts, ex) in [
        ("NIFTY 50", INDEX_TOKENS["NIFTY 50"]),
        ("NIFTY BANK", INDEX_TOKENS["NIFTY BANK"]),
    ]:
        try:
            with httpx.Client(timeout=30.0) as client:
                r = client.post(
                    f"{BASE}/rest/secure/angelbroking/order/v1/getLtpData",
                    headers=_auth_headers(),
                    json={"exchange": ex, "tradingsymbol": ts, "symboltoken": token},
                )
                d = r.json().get("data") or {}
                if isinstance(d, list) and d:
                    d = d[0]
            out.append({
                "index": name,
                "ltp": d.get("ltp"),
                "open": d.get("open"),
                "high": d.get("high"),
                "low": d.get("low"),
            })
        except Exception as e:
            out.append({"index": name, "error": str(e)})
    return {"rows": out, **_meta()}