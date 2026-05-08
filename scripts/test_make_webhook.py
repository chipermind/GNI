#!/usr/bin/env python3
"""
Smoke test for Make webhook. Sends a test payload and prints HTTP status + response.
Usage:
  MAKE_WEBHOOK_URL=https://... python scripts/test_make_webhook.py
Exits non-zero if MAKE_WEBHOOK_URL missing or response not 2xx.
"""
import os
import sys

try:
    import httpx
except ImportError:
    print("httpx required: pip install httpx", file=sys.stderr)
    sys.exit(2)


def main() -> int:
    url = os.environ.get("MAKE_WEBHOOK_URL", "").strip()
    if not url:
        print("MAKE_WEBHOOK_URL is required. Set it in .env or: MAKE_WEBHOOK_URL=https://... python scripts/test_make_webhook.py", file=sys.stderr)
        sys.exit(1)

    payload = {
        "channel": "whatsapp",
        "text": "🚨 GNI | Test ✅\nThis is a webhook smoke test from the GNI system.",
        "template": "FLASH_SETORIAL",
        "priority": "P2",
        "meta": {"source": "system-test", "url": "", "item_id": "test"},
    }

    timeout = float(os.environ.get("MAKE_WEBHOOK_TIMEOUT_SECONDS", "15"))
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(url, json=payload)
    except Exception as e:
        print(f"Request failed: {e}", file=sys.stderr)
        sys.exit(1)

    status = resp.status_code
    text = (resp.text or "").strip()
    if len(text) > 500:
        text = text[:500] + "..."

    print(f"HTTP {status}")
    if text:
        print(text)

    if status < 200 or status >= 300:
        sys.exit(1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
