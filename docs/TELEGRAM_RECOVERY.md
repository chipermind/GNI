# Bot stopped sending to Telegram – recovery

If the Global News Intel channel stopped getting messages:

1. **Worker must be running** – the worker runs the pipeline (score → LLM → render → publish to Telegram). If it’s restarting or down, nothing is sent.

2. **Ollama must answer in time** – if you see `Ollama request failed: timed out` in tests or logs, increase the timeout on the **VM** in `.env`:
   ```bash
   OLLAMA_TIMEOUT_SECONDS=120
   ```
   Then restart: `docker compose up -d api worker`.

3. **Apply the worker fix (missing import)** – ensure the repo has the fix for `get_int_env` in `apps/worker/llm/ollama_ensure.py` (import from `apps.shared.env_helpers`). Then on the VM:
   ```bash
   cd /opt/gni-bot-creator
   git pull origin main
   docker compose build worker
   docker compose up -d worker
   ```

4. **Check worker is Up** – wait ~1 minute, then:
   ```bash
   docker compose ps
   bash scripts/vm_logs.sh worker 50
   ```
   Worker should show `Up (healthy)`. If it keeps restarting, logs will show the error (e.g. missing import – pull and rebuild).

5. **Optional: pause flag** – if you use “pause publish” in the app, ensure it’s turned off so the worker is allowed to publish.

After worker is healthy, the next pipeline run (every `RUN_EVERY_MINUTES` or on trigger) will send to Telegram again.
