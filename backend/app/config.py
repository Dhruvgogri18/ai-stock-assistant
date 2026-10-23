from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # LLM (chat)
    llm_api_key: str = ""
    llm_base_url: str = "https://aigateway-api.godigitaltc.com/aigw/v1"
    llm_model: str = ""

    # Storage: sqlite path OR full Postgres URL
    # Examples:
    #   db_path=stock.db
    #   database_url=postgresql://user:pass@localhost:5432/stock
    db_path: str = "stock.db"
    database_url: str = ""  # if set, preferred over db_path

    # Data provider: "auto" | "db" | "indianapi" | "yfinance"
    # auto = prefer DB if populated, else IndianAPI if key set, else yfinance
    data_provider: str = "auto"

    # IndianAPI (https://pro.indianapi.in) — optional
    indianapi_key: str = ""
    indianapi_base: str = "https://stock.indianapi.in"

    # Embeddings for RAG (optional; leave empty to use keyword/FTS only)
    embedding_api_key: str = ""
    embedding_base_url: str = ""
    embedding_model: str = "text-embedding-3-small"

    # Chat / LLM session audit log (JSONL)
    session_log_path: str = "logs/chat_sessions.jsonl"

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
