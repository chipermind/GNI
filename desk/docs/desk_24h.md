# Desk 24H

Scheduled report module for GNI. Disabled by default.

## Feature flag

- **DESK_SCHEDULER_ENABLED**: `"0"` (default) = off; `"1"` / `"true"` / `"yes"` = on
- When off, the scheduler is not started; no cron jobs run
- When on, Desk jobs run in America/Recife timezone via APScheduler

## Smoke CLI

```bash
# Run one window manually (dry-run by default)
python scripts/run_desk_once.py PANORAMA_0900

# List scheduler job IDs (requires: pip install apscheduler)
python scripts/list_desk_jobs.py

# Day state + EXEC closure smoke test
python scripts/day_state_smoke.py
```
