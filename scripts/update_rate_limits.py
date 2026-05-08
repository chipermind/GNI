#!/usr/bin/env python3
"""Update rate limits in Settings. Run on VM: docker compose exec api python scripts/update_rate_limits.py"""
import sys
sys.path.insert(0, ".")
from apps.api.db import SessionLocal, init_db
from apps.api.settings import set_settings

init_db()
s = SessionLocal()
try:
    set_settings(s, rate_limits={
        "telegram": {"per_minute": 15, "per_hour": 300},
        "whatsapp_web": {"per_minute": 15, "per_hour": 300},
        "make": {"per_minute": 15, "per_hour": 300},
    })
    s.commit()
    print("Rate limits updated: 15/min, 300/hour per channel")
finally:
    s.close()
