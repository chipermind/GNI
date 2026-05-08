# Collector: RSS + Telegram ingest on interval
FROM python:3.11-slim
WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Full repo so collector can import apps.api.db, apps.collector, apps.worker.dedupe
COPY . .

ENV PYTHONUNBUFFERED=1

# Runs RSS + Telegram ingest every COLLECTOR_INTERVAL_MINUTES (default 15)
CMD ["python", "-m", "apps.collector.main"]
