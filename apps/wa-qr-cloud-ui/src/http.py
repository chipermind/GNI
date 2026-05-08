"""
Minimal HTTP helper: timeout=10s, retries=2, safe error handling.
Returns (data, error_message). Never raises; never exposes URLs or tracebacks to caller.
"""
import requests
from typing import Any, Optional

TIMEOUT = 10
RETRIES = 2
USER_FACING_ERROR = "Something went wrong. Please try again later."


def _parse_json(r: requests.Response) -> Optional[dict]:
    if not r.content:
        return None
    try:
        return r.json()
    except Exception:
        return None


def get(url: str, headers: Optional[dict] = None) -> tuple[Optional[dict], Optional[str], Optional[int]]:
    """GET with retries. Returns (data, error_message, status_code). status_code is None on connection error."""
    headers = headers or {}
    headers.setdefault("Content-Type", "application/json")
    last_err: Optional[str] = None
    last_code: Optional[int] = None
    for attempt in range(RETRIES):
        try:
            r = requests.get(url, headers=headers, timeout=TIMEOUT)
            data = _parse_json(r)
            last_code = r.status_code
            if r.ok:
                return (data, None, last_code)
            if r.status_code == 401:
                return (None, "Invalid email or password.", 401)
            if r.status_code == 429:
                return (None, "Too many requests. Please try again later.", 429)
            last_err = USER_FACING_ERROR
        except requests.exceptions.Timeout:
            last_err = USER_FACING_ERROR
            last_code = None
        except requests.exceptions.RequestException:
            last_err = USER_FACING_ERROR
            last_code = None
        except Exception:
            last_err = USER_FACING_ERROR
            last_code = None
    return (None, last_err or USER_FACING_ERROR, last_code)


def post(
    url: str,
    json_body: Optional[dict] = None,
    headers: Optional[dict] = None,
) -> tuple[Optional[dict], Optional[str], Optional[int]]:
    """POST with retries. Returns (data, error_message, status_code). status_code is None on connection error."""
    headers = headers or {}
    headers.setdefault("Content-Type", "application/json")
    last_err: Optional[str] = None
    last_code: Optional[int] = None
    for attempt in range(RETRIES):
        try:
            r = requests.post(url, json=json_body or {}, headers=headers, timeout=TIMEOUT)
            data = _parse_json(r)
            last_code = r.status_code
            if r.ok:
                return (data, None, last_code)
            if r.status_code == 401:
                return (None, "Invalid email or password.", 401)
            if r.status_code == 429:
                return (None, "Too many requests. Please try again later.", 429)
            last_err = USER_FACING_ERROR
        except requests.exceptions.Timeout:
            last_err = USER_FACING_ERROR
            last_code = None
        except requests.exceptions.RequestException:
            last_err = USER_FACING_ERROR
            last_code = None
        except Exception:
            last_err = USER_FACING_ERROR
            last_code = None
    return (None, last_err or USER_FACING_ERROR, last_code)
