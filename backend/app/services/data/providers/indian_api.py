"""IndianAPI provider (https://stock.indianapi.in / pro.indianapi.in).

Set INDIANAPI_KEY in .env. Endpoints vary by plan; this module is defensive
and falls back field-by-field when a key is missing or a field is absent.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx

from backend.app.config.settings import settings


def configured() -> bool:
    return bool(settings.indianapi_key)


def _meta() -> dict:
    return {
        "source": "IndianAPI (NSE/BSE aggregated)",
        "as_of": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    }


def _get(path: str, params: dict | None = None) -> Any:
    if not settings.indianapi_key:
        raise ValueError("Set INDIANAPI_KEY in .env to use IndianAPI.")
    base = settings.indianapi_base.rstrip("/")
    headers = {"X-Api-Key": settings.indianapi_key}
    with httpx.Client(timeout=30.0) as client:
        r = client.get(f"{base}{path}", params=params or {}, headers=headers)
        if r.status_code == 401:
            raise ValueError("IndianAPI rejected the API key.")
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()


def stock(symbol: str) -> dict:
    """Best-effort company payload. Tries common path shapes."""
    sym = symbol.upper().strip()
    for path, params in (
        ("/stock", {"name": sym}),
        ("/stock", {"symbol": sym}),
        (f"/stock/{sym}", None),
    ):
        try:
            data = _get(path, params)
            if data:
                return data if isinstance(data, dict) else {"raw": data}
        except Exception:
            continue
    raise ValueError(f"IndianAPI: no data for '{sym}'.")


def ratios(symbol: str) -> dict:
    data = stock(symbol)
    # Normalise common field names across plans
    def g(*keys):
        for k in keys:
            if k in data and data[k] is not None:
                return data[k]
            # nested companyInfo / keyMetrics
            for nest in ("companyInfo", "keyMetrics", "ratios", "data"):
                sub = data.get(nest) or {}
                if isinstance(sub, dict) and sub.get(k) is not None:
                    return sub[k]
        return None

    kv = {
        "P/E": g("peRatio", "pe", "trailingPE", "P/E"),
        "P/B": g("pbRatio", "pb", "priceToBook", "P/B"),
        "ROCE %": g("roce", "ROCE"),
        "ROE %": g("roe", "ROE"),
        "Net margin %": g("netProfitMargin", "profitMargins", "net_margin"),
        "Operating margin %": g("operatingMargins", "op_margin"),
        "Market cap": g("marketCap", "market_cap", "mcap"),
    }
    name = g("companyName", "longName", "name") or symbol.upper()
    sector = g("industry", "sector")
    return {"kv": kv, "name": name, "sector": sector, **_meta()}


def ownership(symbol: str) -> dict:
    data = stock(symbol)
    sh = data.get("shareholding") or data.get("shareHoldingPattern") or data.get("holdings") or {}
    if isinstance(sh, list):
        # take latest
        sh = sh[0] if sh else {}
    kv = {}
    mapping = {
        "promoter": ("promoter", "promoters", "Promoter"),
        "fii": ("fii", "FII", "foreignInstitutional"),
        "dii": ("dii", "DII", "domesticInstitutional"),
        "public": ("public", "Public", "retail"),
    }
    for label, keys in mapping.items():
        for k in keys:
            if isinstance(sh, dict) and sh.get(k) is not None:
                kv[label] = sh[k]
                break
    if not kv:
        raise ValueError(f"IndianAPI: no shareholding for '{symbol}'.")
    return {
        "kv": kv,
        "note": "Promoter / FII / DII / public from IndianAPI shareholding pattern.",
        **_meta(),
    }


def history(symbol: str, period: str = "5y") -> dict:
    """Price chart from IndianAPI /historical_data."""
    sym = symbol.upper().strip()
    # Map our period strings to IndianAPI periods
    period_map = {
        "1mo": "1m", "1m": "1m", "6mo": "6m", "6m": "6m",
        "1y": "1yr", "1yr": "1yr", "2y": "3yr", "5y": "5yr",
        "5yr": "5yr", "10y": "10yr", "max": "max",
    }
    api_period = period_map.get(period, "5yr")
    data = None
    for path, params in (
        ("/historical_data", {"stock_name": sym, "period": api_period, "filter": "price"}),
        ("/historical_data", {"stock_name": sym, "period": api_period, "filter": "default"}),
    ):
        try:
            data = _get(path, params)
            if data:
                break
        except Exception:
            continue
    if not data:
        raise ValueError(f"IndianAPI: no historical data for '{sym}'.")

    dates, closes = [], []
    # Common shapes: datasets[].values / price series / list of {date, close}
    if isinstance(data, dict):
        datasets = data.get("datasets") or data.get("data") or []
        if isinstance(datasets, list):
            for ds in datasets:
                if not isinstance(ds, dict):
                    continue
                label = (ds.get("label") or ds.get("name") or "").lower()
                if label and "price" not in label and "close" not in label and label not in ("", "default"):
                    # still accept first numeric series if needed
                    pass
                values = ds.get("values") or ds.get("data") or []
                if values and not closes:
                    for pt in values:
                        if isinstance(pt, dict):
                            d = pt.get("date") or pt.get("x") or pt.get("time")
                            c = pt.get("close") or pt.get("price") or pt.get("y") or pt.get("value")
                            if d is not None and c is not None:
                                dates.append(str(d)[:10])
                                try:
                                    closes.append(round(float(c), 2))
                                except Exception:
                                    pass
                        elif isinstance(pt, (list, tuple)) and len(pt) >= 2:
                            dates.append(str(pt[0])[:10])
                            try:
                                closes.append(round(float(pt[1]), 2))
                            except Exception:
                                pass
        # flat lists
        if not closes:
            raw_dates = data.get("dates") or data.get("labels") or []
            raw_close = data.get("close") or data.get("prices") or data.get("price") or []
            for d, c in zip(raw_dates, raw_close):
                dates.append(str(d)[:10])
                try:
                    closes.append(round(float(c), 2))
                except Exception:
                    pass

    if not closes:
        raise ValueError(f"IndianAPI: could not parse chart series for '{sym}'.")

    return {
        "chart": {"dates": dates, "close": closes},
        **_meta(),
    }
