"""Application settings (pydantic-settings; reads real env vars and the repo-root .env).

The .env path is absolute so it is found no matter which directory uvicorn
is started from.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/app/config/settings.py -> parents[3] is the repo root
REPO_ROOT = Path(__file__).resolve().parents[3]


def _abs(p: str) -> str:
    path = Path(p)
    return str(path if path.is_absolute() else REPO_ROOT / path)


def _as_list(v: Any) -> list[str]:
    """Parse env values that may be *, comma-separated, or JSON lists."""
    if v is None or v == "":
        return []
    if isinstance(v, (list, tuple)):
        return [str(x).strip() for x in v if str(x).strip()]
    s = str(v).strip()
    if not s:
        return []
    if s.startswith("["):
        import json
        try:
            data = json.loads(s)
            if isinstance(data, list):
                return [str(x).strip() for x in data if str(x).strip()]
        except Exception:
            pass
    return [p.strip() for p in s.split(",") if p.strip()]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(REPO_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---- alerts ----
    # How often (minutes) the background checker looks for new price triggers and new results filings.
    # 0 turns the background loop off; "Check now" on the Alerts page still works.
    alerts_check_interval_min: int = 5
    # Live ("touch") alerts: poll Angel One prices during NSE market hours. Seconds between polls; 0 turns it off.
    alerts_live_interval_sec: int = 60
    alerts_live_pause_sec: float = 0.3      # pause between per-symbol price calls, to stay inside broker rate limits
    alerts_live_max_symbols: int = 60       # most symbols looked up per poll

    # Build the end-of-day market snapshot at startup (uses a lot of RAM; set false on small hosts)
    warm_snapshot: bool = True

    # ---- company fundamentals ----
    fundamentals_refresh_cooldown_min: int = 30   # the company page's "load statements" button: minimum minutes between refreshes of one stock

    # ---- live prices (Angel One first, NSE bhavcopy as the fallback) ----
    prices_live: bool = True                # False = always use end-of-day bhavcopy
    prices_batch_pause_sec: float = 1.1     # minimum gap between Angel quote requests (shared by everything); check Angel's limits
    prices_ttl_sec: int = 10                # a quote younger than this is reused
    prices_max_age_sec: int = 180           # the market-wide board only counts quotes younger than this
    prices_min_coverage: float = 0.95       # share of stocks that must have a fresh quote before screens switch to live prices
    prices_demand_window_sec: int = 600     # keep refreshing the whole market this long after the last screener/movers request

    # ---- product ----
    app_name: str = "Stock Research"
    user_name: str = ""

    # ---- LLM (any OpenAI-compatible gateway) ----
    # Leave empty until paid API keys are configured (Grok / Claude / gateway)
    llm_api_key: str = ""
    llm_base_url: str = ""
    llm_model: str = ""
    llm_fast_model: str = ""
    llm_timeout: float = 90.0
    llm_max_tokens: int = 1200
    max_tool_rounds: int = 2  # fewer model calls per chat turn (cost control)
    router_enabled: bool = True
    # Optional fallbacks (gateway_client tries these if primary fails / is unset)
    fallback_llm_api_key: str = ""
    fallback_llm_base_url: str = ""
    fallback_llm_model: str = ""
    fallback_llm_fast_model: str = ""
    fallback2_llm_api_key: str = ""
    fallback2_llm_base_url: str = ""
    fallback2_llm_model: str = ""
    fallback2_llm_fast_model: str = ""

    # ---- LLM logging ----
    llm_log: bool = True
    llm_log_dir: str = "logs"
    llm_log_prompts: bool = True
    llm_log_max_content: int = 50000
    llm_pricing_model: str = ""

    # ---- storage ----
    db_path: str = "stock.db"
    database_url: str = ""
    session_db_path: str = "data/sessions.db"

    # ---- data providers: auto | db | indianapi | yfinance ----
    # Demo default: local stock.db; live quotes still come from Angel One when configured
    data_provider: str = "db"
    indianapi_key: str = ""
    indianapi_base: str = "https://stock.indianapi.in"

    # ---- NSE end-of-day market data (bhavcopy) ----
    # Demo: do not download or append more EOD data — use whatever is already in stock.db
    bhavcopy_auto_refresh: bool = False
    bhavcopy_publish_after_ist: str = "18:30"
    bhavcopy_check_interval_min: int = 30
    bhavcopy_startup_backfill_days: int = 0
    # Stored as plain str so .env does not need JSON. Use commas or a single URL.
    bhavcopy_base_urls: str = "https://nsearchives.nseindia.com,https://archives.nseindia.com"
    nse_user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
    movers_min_turnover_cr: float = 1.0

    # ---- optional embeddings for RAG ----
    embedding_api_key: str = ""
    embedding_base_url: str = ""
    embedding_model: str = "text-embedding-3-small"

    # ---- Angel One SmartAPI ----
    angel_api_key: str = ""
    angel_client_id: str = ""
    angel_password: str = ""
    angel_totp_secret: str = ""
    angel_feed_token: str = ""
    angel_jwt: str = ""

    # ---- Announcements loader ----
    announcements_symbols: str = (
        "RELIANCE,TCS,INFY,HDFCBANK,ICICIBANK,WIPRO,HCLTECH,SBIN,ITC,BHARTIARTL"
    )

    # ---- web (plain str — avoids pydantic-settings JSON decode on list fields) ----
    cors_allow_origins: str = "*"

    @property
    def cors_origins_list(self) -> list[str]:
        return _as_list(self.cors_allow_origins) or ["*"]

    @property
    def bhavcopy_base_urls_list(self) -> list[str]:
        return _as_list(self.bhavcopy_base_urls)

    @property
    def warehouse_path(self) -> str:
        return _abs(self.db_path)

    @property
    def sessions_path(self) -> str:
        return _abs(self.session_db_path)

    @property
    def log_root(self) -> Path:
        return Path(_abs(self.llm_log_dir))


settings = Settings()