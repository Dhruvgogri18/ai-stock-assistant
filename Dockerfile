FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY backend ./backend
COPY frontend ./frontend
COPY scripts ./scripts
# Hugging Face Spaces uses 7860; most other hosts inject $PORT.
ENV PORT=7860 DB_PATH=/app/storage/stock.db SESSION_DB_PATH=/app/storage/sessions.db LLM_LOG_DIR=/app/storage/logs
RUN mkdir -p /app/storage && useradd -m app && chown -R app /app
USER app
CMD python -m scripts.bootstrap_db && uvicorn backend.app.main:app --host 0.0.0.0 --port ${PORT}
