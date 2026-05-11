# Collector: RSS + Telegram ingest on interval
FROM python:3.11-slim
WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Full repo so collector can import apps.api.db, apps.collector, apps.worker.dedupe
COPY . .

ENV PYTHONUNBUFFERED=1

# Session restore entrypoint (Railway: reconstructs Telethon session from split env vars)
COPY scripts/docker-entrypoint-collector.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
# Runs RSS + Telegram ingest every COLLECTOR_INTERVAL_MINUTES (default 15)
CMD ["python", "-m", "apps.collector.main"]
