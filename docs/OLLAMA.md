# Ollama for GNI pipeline

The worker uses Ollama for:

- **Classifying** news (template A/B, risk, priority, sector, flag, reason)
- **Generating** strict JSON for Portuguese GNI templates (ANALISE_INTEL, FLASH_SETORIAL)

We do **not** need large 32B models. Use VM-friendly **7B or 14B** models.

## Recommended models

| Model           | Size  | RAM (approx) | Use case              |
|----------------|-------|--------------|------------------------|
| **qwen2.5:7b**  | ~4.5GB| ~8GB         | Default; good balance |
| qwen2.5:14b     | ~9GB  | ~16GB        | Higher quality        |
| llama3.2:3b     | ~2GB  | ~4GB         | Minimal footprint     |

Set `OLLAMA_MODEL` in `.env` (e.g. `OLLAMA_MODEL=qwen2.5:7b`). The worker pulls the model on startup if missing (`OLLAMA_PULL_ON_START=true`).

## Changing the model

1. Set in `.env`:
   ```bash
   OLLAMA_MODEL=qwen2.5:14b
   ```
2. Restart the worker. If the model is not present, it will be pulled (or the worker will retry in degraded mode until it is available).
3. Optional: pre-pull on the host so the worker does not wait:
   ```bash
   docker compose exec ollama ollama pull qwen2.5:14b
   ```

## Disk and RAM

- **Disk:** Each model is stored in the `ollama_models` volume (e.g. 4–10GB per 7B/14B model). Ensure enough free space on the volume.
- **RAM:** 7B typically needs ~8GB RAM; 14B ~16GB. The Ollama container and worker both use memory; size the VM accordingly.

## Env vars (.env.example)

| Variable | Default | Description |
|----------|---------|-------------|
| `OLLAMA_BASE_URL` | `http://ollama:11434` | Ollama API (use service name in Docker). |
| `OLLAMA_MODEL` | `qwen2.5:7b` | Model tag for classify + generate. |
| `OLLAMA_PULL_ON_START` | `true` | Pull model on worker startup if missing. |
| `OLLAMA_PULL_TIMEOUT_SECONDS` | `1800` | Timeout for a single pull attempt. |
| `OLLAMA_PULL_MAX_RETRIES` | `6` | Retries with backoff if pull fails. |
| `OLLAMA_PULL_BACKOFF_SECONDS` | `20` | Seconds to wait between retries. |

## Verify

List models in the Ollama container:

```bash
docker compose exec ollama ollama list
```

Check that the API is reachable from the API container:

```bash
docker compose exec api curl -s http://ollama:11434/api/tags
```

You should see `"models": [...]` with your configured `OLLAMA_MODEL` (e.g. `qwen2.5:7b`) listed.

## Worker behaviour

- On startup the worker calls **ensure_ollama_model()**: it checks `GET /api/tags` and, if the configured model is missing and `OLLAMA_PULL_ON_START=true`, runs a pull via `POST /api/pull` with timeout and retries.
- If the pull fails (timeout or error), the worker **does not crash**: it logs "degraded mode" and retries the ensure step every 5 minutes until the model is available.
- Progress during pull is logged at intervals (no spam). Once the model is present, the pipeline runs as usual.
