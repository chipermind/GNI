# GNI Analysis — LLM Strategic Intelligence Formatter

## Overview

`llm_formatter` upgrades message generation into a structured LLM-driven strategic intelligence report. It does **not** modify:
- Telegram send function
- Scheduler
- Event collectors
- Message routing

Only the **message generation layer** is changed.

## Usage

### 1. Generate report only (no send)

```python
from gni.analysis.llm_formatter import generate_report

radar_data = {
    "geopolitics": "Tensions in region X affecting energy routes.",
    "cyber": "Elevated coordinated cyber activity detected.",
    "crypto": "Institutional flows monitored in crypto and ETFs.",
    "ai": "AI advances accelerating tech competition.",
    "energy": "Supply constraints in key markets.",
}

formatted_message = generate_report(radar_data)
# Returns Telegram-ready string. Uses cache (30 min). Falls back to static format if LLM fails.
```

### 2. Generate and send via Telegram

```python
from gni.analysis.radar_broadcast import run_radar_broadcast

radar_data = {...}
msg, sent = run_radar_broadcast(radar_data, dry_run=False)
# Uses existing publish_telegram — Telegram client unchanged.
```

### 3. Integration inside dispatch layer

Replace current static message builder with:

```python
from gni.analysis.llm_formatter import generate_report

formatted_message = generate_report(radar_data)
# Then pass to existing send:
publish_telegram([formatted_message], channel="telegram", dry_run=False, session=session)
```

## Config (optional)

| Key | Default | Description |
|-----|---------|-------------|
| `RADAR_LLM_BASE_URL` | `{OLLAMA_BASE_URL}/v1` | OpenAI-compatible API base |
| `RADAR_LLM_MODEL` | `OLLAMA_MODEL` or `llama3.2` | Model name |
| `RADAR_LLM_API_KEY` | — | API key (OpenAI); omit for Ollama |
| `RADAR_CACHE_TTL` | `1800` | Cache TTL seconds (30 min) |
| `RADAR_LLM_TIMEOUT` | `60` | Request timeout |
| `RADAR_MAX_TOKENS` | `2048` | Max output tokens |
| `RADAR_TEMPERATURE` | `0.3` | Sampling temperature |

## Fallback

If the LLM fails (timeout, error, empty response), `generate_report` returns a deterministic static format. Telegram is never broken.

## Logging

- `radar_report_cache_hit` — cache hit
- `radar_llm_success` — prompt_tokens, completion_tokens, latency_sec
- `radar_llm_fallback` — reason, fallback=static

API keys are never logged.
