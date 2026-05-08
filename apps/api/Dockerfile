# FastAPI API — build context MUST be repo root (docker-compose: context: .)
# Repository root is copied to /app so that Python can resolve apps.* (PYTHONPATH=/app).
# Alembic: alembic.ini and alembic/ at /app for migrations (alembic -c /app/alembic.ini upgrade head).
FROM python:3.11-slim
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends curl \
  && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy full repo so /app/apps exists and apps.api, apps.shared, etc. resolve
COPY alembic.ini ./
COPY alembic ./alembic/
COPY apps ./apps/
COPY desk ./desk/
COPY scripts ./scripts/

ENV PYTHONPATH=/app

# Note: API runs as root. For production, add non-root user; bind mounts may need chown.
EXPOSE 8000
CMD ["uvicorn", "apps.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
