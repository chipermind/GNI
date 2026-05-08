# Pipeline worker: scoring → LLM draft → render → publish (no ingest; collector handles ingest)
FROM python:3.11-slim
WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Full repo so pipeline can import apps.api, apps.publisher, apps.collector (normalize)
COPY . .

ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

# Runs scheduler: scoring → llm_draft → render_and_publish every RUN_EVERY_MINUTES
CMD ["python", "-m", "apps.worker.tasks"]
