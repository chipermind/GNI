"""
API wrapper: api_get / api_post with base URL and auth. Friendly errors; never log secrets.

WA (WhatsApp) status/QR/reconnect/netcheck use GET/POST {base}/wa/* with X-API-Key only (no /admin/wa/*).
- Set API_KEY or ADMIN_API_KEY in Streamlit secrets.
- Exponential backoff on transient errors (429, 502, 503, 504).
- Client-side throttling: same GET endpoint < N seconds ago returns cached result.
"""
import time
from typing import Any, Optional
# Explicit import required: __import__('urllib.parse') returns the top-level 'urllib' module,
# not urllib.parse, so .urlencode would raise AttributeError on the Monitoring page.
from urllib.parse import urlencode


def _append_query(path: str, params: dict) -> str:
    """Build path + querystring safely. Only appends when params have truthy values; uses '&' if '?' already in path."""
    path = (path or "").strip() or "/"
    clean = {}
    for k, v in params.items():
        if v is None or v == "":
            continue
        clean[k] = str(v) if not isinstance(v, str) else v
    return path if not clean else path + ("&" if "?" in path else "?") + urlencode(clean)


def add_query(path: Optional[str], params: dict) -> str:
    """Public helper for URL query building. Delegates to _append_query for consistent behavior."""
    return _append_query(path or "", params)


# --- Client-side throttle: {cache_key: (timestamp, (data, error))} ---
_wa_cache: dict[str, tuple[float, tuple[Any, Optional[str]]]] = {}
WA_THROTTLE_STATUS = 12  # seconds (status cache)
WA_THROTTLE_QR = 15      # seconds (QR cache)

# --- Last request info for UI (safe: no tokens, URL only) ---
_last_http_status: Optional[int] = None
_last_request_url: Optional[str] = None
_last_request_path: Optional[str] = None  # path used (e.g. /wa/status) for debug
_last_response_preview: Optional[str] = None  # first 200 chars sanitized (no token values)
_last_wa_poll_timestamp: Optional[float] = None  # time.time() when last WA status/qr poll ran
_last_wa_error: Optional[str] = None  # last error message from WA request (for diagnostics)


def _get_config():
    from src.config import get_config
    return get_config()


def _get_wa_token() -> str:
    """WA bridge token: session_state first (UI paste), then config (env/secrets). Never log or return to caller for display."""
    try:
        import streamlit as st
        t = (st.session_state.get("wa_qr_bridge_token") or "").strip()
        if t:
            return t
    except Exception:
        pass
    return (_get_config().get("WA_QR_BRIDGE_TOKEN") or "").strip()


def _headers(use_bearer: bool = True) -> dict:
    cfg = _get_config()
    h = {"Content-Type": "application/json"}
    if use_bearer:
        token = (cfg.get("WA_QR_BRIDGE_TOKEN") or "").strip()
        if token:
            h["Authorization"] = f"Bearer {token}"
    else:
        api_key = (cfg.get("API_KEY") or "").strip()
        if api_key:
            h["X-API-Key"] = api_key
    return h


def _headers_jwt(token: Optional[str] = None) -> dict:
    """Headers with JWT from session (for /auth/me, /whatsapp/*)."""
    h = {"Content-Type": "application/json"}
    t = (token or "").strip()
    if not t:
        try:
            import streamlit as st
            t = (st.session_state.get("auth_token") or "").strip()
        except Exception:
            pass
    if t:
        h["Authorization"] = f"Bearer {t}"
    return h


def _get_timeout() -> int:
    """Request timeout (connect + read) in seconds. From GNI_API_TIMEOUT env/secrets, default 10."""
    try:
        return int(_get_config().get("GNI_API_TIMEOUT") or 10)
    except (TypeError, ValueError):
        return 10


def _base_url() -> str:
    """Backend base URL: session_state api_base_url first, then config (secrets/env). Never log."""
    out = (_get_config().get("GNI_API_BASE_URL") or "").strip().rstrip("/")
    try:
        import streamlit as st
        session_url = (st.session_state.get("api_base_url") or "").strip().rstrip("/")
        if session_url:
            out = session_url
    except Exception:
        pass
    return out


def _conn_err(msg: str, url: str) -> str:
    """Build connection error message with safe URL (no tokens)."""
    return f"{msg} Tried: {url}"


def get_api_display_info() -> dict[str, Any]:
    """Return safe info for UI: base_url, last_http_status, last_request_url. No secrets."""
    return {
        "base_url": _base_url(),
        "last_http_status": _last_http_status,
        "last_request_url": _last_request_url,
    }


def get_wa_debug_info() -> dict[str, Any]:
    """Return debug/diagnostics: base URL, api_key_set, last status, last error, endpoint used. No secrets."""
    status_path, qr_path, reconnect_path, netcheck_path = _wa_paths()
    from datetime import datetime, timezone
    last_poll_ts = _last_wa_poll_timestamp
    last_poll_str = datetime.fromtimestamp(last_poll_ts, tz=timezone.utc).isoformat() if last_poll_ts else None
    return {
        "effective_base_url": _base_url() or "(not set)",
        "api_key_set": has_wa_api_key(),
        "auth_mode": "x-api-key",
        "endpoints": {
            "status": status_path,
            "qr": qr_path,
            "reconnect": reconnect_path,
            "netcheck": netcheck_path,
        },
        "endpoint_used": _last_request_path,
        "last_poll_timestamp": last_poll_str,
        "last_http_status": _last_http_status,
        "last_error": _last_wa_error or "",
        "last_response_preview": _last_response_preview or "",
    }


def api_get(path: str, *, timeout: Optional[int] = None, use_bearer: bool = True) -> tuple[Optional[Any], Optional[str]]:
    """GET {base}{path}. Returns (data, error). On non-200 returns friendly error (no secrets)."""
    import requests
    global _last_http_status, _last_request_url
    base = _base_url()
    if not base:
        return None, "API base URL not set"
    url = f"{base}{path}"
    _last_request_url = url
    to = (timeout if timeout is not None else _get_timeout())
    try:
        r = requests.get(url, headers=_headers(use_bearer=use_bearer), timeout=(to, to))
        _last_http_status = r.status_code
        r.raise_for_status()
        return r.json() if r.content else None, None
    except requests.exceptions.HTTPError as e:
        _last_http_status = e.response.status_code if e.response else None
        try:
            detail = e.response.json().get("detail", "Request failed") if e.response else "Request failed"
        except Exception:
            detail = (e.response.text[:200] if e.response and e.response.text else "Request failed")
        if e.response and e.response.status_code == 401:
            return None, "Authentication failed (401). Check your secrets."
        if e.response and e.response.status_code == 403:
            return None, "Forbidden (403). Invalid or missing API key or token."
        if e.response and e.response.status_code == 404:
            return None, "Endpoint not found (404)."
        return None, f"Request failed ({_last_http_status}): {str(detail)[:200]}"
    except requests.exceptions.ConnectTimeout:
        _last_http_status = None
        return None, _conn_err("Connection error: connect timed out.", url)
    except requests.exceptions.ReadTimeout:
        _last_http_status = None
        return None, _conn_err("Connection error: read timed out.", url)
    except requests.exceptions.ConnectionError as e:
        _last_http_status = None
        reason = str(e).split("\n")[0][:80] if str(e) else "connection refused or unreachable"
        return None, _conn_err(f"Connection error: {reason}.", url)
    except requests.exceptions.RequestException as e:
        _last_http_status = None
        return None, _conn_err(f"Connection error: {str(e)[:80]}.", url)
    except Exception as e:
        _last_http_status = None
        return None, _conn_err(f"Connection error: {str(e)[:80]}.", url)


def api_post(path: str, json_body: Optional[dict] = None, *, timeout: Optional[int] = None, use_bearer: bool = False) -> tuple[Optional[Any], Optional[str]]:
    """POST {base}{path}. Returns (data, error). use_bearer=True for WA bridge; False for API key (monitoring/posts)."""
    import requests
    global _last_http_status, _last_request_url
    base = _base_url()
    if not base:
        return None, "API base URL not set"
    url = f"{base}{path}"
    _last_request_url = url
    to = (timeout if timeout is not None else _get_timeout())
    try:
        r = requests.post(url, headers=_headers(use_bearer=use_bearer), json=json_body or {}, timeout=(to, to))
        _last_http_status = r.status_code
        r.raise_for_status()
        return r.json() if r.content else {}, None
    except requests.exceptions.HTTPError as e:
        _last_http_status = e.response.status_code if e.response else None
        try:
            detail = e.response.json().get("detail", "Request failed") if e.response else "Request failed"
        except Exception:
            detail = (e.response.text[:200] if e.response and e.response.text else "Request failed")
        if e.response and e.response.status_code == 401:
            return None, "Authentication failed (401). Check your secrets."
        if e.response and e.response.status_code == 403:
            return None, "Forbidden (403). Invalid or missing API key or token."
        if e.response and e.response.status_code == 404:
            return None, "Endpoint not found (404)."
        return None, f"Request failed ({_last_http_status}): {str(detail)[:200]}"
    except requests.exceptions.ConnectTimeout:
        _last_http_status = None
        return None, _conn_err("Connection error: connect timed out.", url)
    except requests.exceptions.ReadTimeout:
        _last_http_status = None
        return None, _conn_err("Connection error: read timed out.", url)
    except requests.exceptions.ConnectionError as e:
        _last_http_status = None
        reason = str(e).split("\n")[0][:80] if str(e) else "connection refused or unreachable"
        return None, _conn_err(f"Connection error: {reason}.", url)
    except requests.exceptions.RequestException as e:
        _last_http_status = None
        return None, _conn_err(f"Connection error: {str(e)[:80]}.", url)
    except Exception as e:
        _last_http_status = None
        return None, _conn_err(f"Connection error: {str(e)[:80]}.", url)


def api_get_jwt(path: str, *, timeout: Optional[int] = None, token: Optional[str] = None) -> tuple[Optional[Any], Optional[str]]:
    """GET with JWT from session (or passed token). For /auth/me, /whatsapp/*."""
    import requests
    global _last_http_status, _last_request_url
    base = _base_url()
    if not base:
        return None, "API base URL not set"
    url = f"{base}{path}"
    _last_request_url = url
    to = (timeout if timeout is not None else _get_timeout())
    try:
        r = requests.get(url, headers=_headers_jwt(token=token), timeout=(to, to))
        _last_http_status = r.status_code
        r.raise_for_status()
        return r.json() if r.content else None, None
    except requests.exceptions.HTTPError as e:
        _last_http_status = e.response.status_code if e.response else None
        try:
            detail = e.response.json().get("detail", "Request failed")
        except Exception:
            detail = "Request failed"
        if e.response and e.response.status_code == 401:
            return None, "Invalid or expired token (401). Please log in again."
        if e.response and e.response.status_code == 404:
            return None, "Endpoint not found (404)."
        return None, f"Request failed ({_last_http_status}): {str(detail)[:180]}"
    except (requests.exceptions.ConnectTimeout, requests.exceptions.ReadTimeout):
        _last_http_status = None
        return None, _conn_err("Connection error: timed out.", url)
    except requests.exceptions.ConnectionError as e:
        _last_http_status = None
        reason = str(e).split("\n")[0][:80] if str(e) else "connection refused or unreachable"
        return None, _conn_err(f"Connection error: {reason}.", url)
    except Exception as e:
        _last_http_status = None
        return None, _conn_err(f"Connection error: {str(e)[:80]}.", url)


def api_post_jwt(path: str, json_body: Optional[dict] = None, *, timeout: Optional[int] = None, token: Optional[str] = None) -> tuple[Optional[Any], Optional[str]]:
    """POST with JWT from session. For /auth/login, /whatsapp/connect."""
    import requests
    global _last_http_status, _last_request_url
    base = _base_url()
    if not base:
        return None, "API base URL not set"
    url = f"{base}{path}"
    _last_request_url = url
    to = (timeout if timeout is not None else _get_timeout())
    try:
        r = requests.post(url, headers=_headers_jwt(token=token), json=json_body or {}, timeout=(to, to))
        _last_http_status = r.status_code
        r.raise_for_status()
        return r.json() if r.content else {}, None
    except requests.exceptions.HTTPError as e:
        _last_http_status = e.response.status_code if e.response else None
        try:
            detail = e.response.json().get("detail", "Request failed")
        except Exception:
            detail = "Request failed"
        if e.response and e.response.status_code == 401:
            return None, "Invalid or expired token (401). Please log in again."
        if e.response and e.response.status_code == 429:
            return None, "Rate limit exceeded (429). Try again later."
        return None, f"Request failed ({_last_http_status}): {str(detail)[:180]}"
    except (requests.exceptions.ConnectTimeout, requests.exceptions.ReadTimeout):
        _last_http_status = None
        return None, _conn_err("Connection error: timed out.", url)
    except requests.exceptions.ConnectionError as e:
        _last_http_status = None
        reason = str(e).split("\n")[0][:80] if str(e) else "connection refused or unreachable"
        return None, _conn_err(f"Connection error: {reason}.", url)
    except Exception as e:
        _last_http_status = None
        return None, _conn_err(f"Connection error: {str(e)[:80]}.", url)


# --- Convenience (used by pages) ---
def get_health() -> tuple[Optional[dict], Optional[str]]:
    return api_get("/health", use_bearer=False)


def post_auth_login(email: str, password: str) -> tuple[Optional[dict], Optional[str]]:
    """POST /auth/login. Returns (body with access_token, error). No auth header."""
    import requests
    global _last_http_status, _last_request_url
    base = _base_url()
    if not base:
        return None, "API base URL not set"
    url = f"{base}/auth/login"
    _last_request_url = url
    to = _get_timeout()
    try:
        r = requests.post(url, headers={"Content-Type": "application/json"}, json={"email": email, "password": password}, timeout=(to, to))
        _last_http_status = r.status_code
        r.raise_for_status()
        return r.json() if r.content else None, None
    except requests.exceptions.HTTPError as e:
        _last_http_status = e.response.status_code if e.response else None
        try:
            detail = e.response.json().get("detail", "Request failed")
        except Exception:
            detail = "Request failed"
        return None, f"Request failed ({_last_http_status}): {str(detail)[:180]}"
    except (requests.exceptions.ConnectTimeout, requests.exceptions.ReadTimeout):
        _last_http_status = None
        return None, _conn_err("Connection error: timed out.", url)
    except requests.exceptions.ConnectionError as e:
        _last_http_status = None
        reason = str(e).split("\n")[0][:80] if str(e) else "connection refused or unreachable"
        return None, _conn_err(f"Connection error: {reason}.", url)
    except Exception as e:
        _last_http_status = None
        return None, _conn_err(f"Connection error: {str(e)[:80]}.", url)


def get_auth_me() -> tuple[Optional[dict], Optional[str]]:
    """GET /auth/me. Requires auth_token in session."""
    return api_get_jwt("/auth/me")


def post_wa_connect() -> tuple[Optional[dict], Optional[str]]:
    """POST /whatsapp/connect. Requires JWT."""
    return api_post_jwt("/whatsapp/connect", json_body={})


def get_wa_qr_user() -> tuple[Optional[dict], Optional[str]]:
    """GET /whatsapp/qr. Requires JWT."""
    return api_get_jwt("/whatsapp/qr")


def get_wa_status_user() -> tuple[Optional[dict], Optional[str]]:
    """GET /whatsapp/status. Requires JWT."""
    return api_get_jwt("/whatsapp/status")


def has_wa_api_key() -> bool:
    """True if API_KEY or ADMIN_API_KEY is set (required for /wa/* endpoints)."""
    api_key = (_get_config().get("API_KEY") or _get_config().get("ADMIN_API_KEY") or "").strip()
    return bool(api_key)


def _sanitize_preview(text: str, max_len: int = 200) -> str:
    """Return text with token-like values redacted; cap at max_len."""
    if not text:
        return ""
    import re
    # Redact Bearer tokens, API keys in JSON, and long base64/hex strings
    out = re.sub(r"Bearer\s+[^\s\"']+", "Bearer [REDACTED]", text, flags=re.IGNORECASE)
    out = re.sub(r"(api[_-]?key|token|authorization|access_token)\s*[:=]\s*[\"']?[^\"'\s,}\]]+", r"\1=[REDACTED]", out, flags=re.IGNORECASE)
    out = re.sub(r"[\"']?(?:access_)?token[\"']?\s*:\s*[\"'][^\"']+[\"']", "\"token\": \"[REDACTED]\"", out, flags=re.IGNORECASE)
    return out[:max_len]


def _wa_paths() -> tuple[str, str, str, str]:
    """Return (status_path, qr_path, reconnect_path, netcheck_path). Always /wa/* with X-API-Key (no /admin/wa/*)."""
    return (
        "/wa/status",
        "/wa/qr",
        "/wa/reconnect",
        "/wa/netcheck",
    )


def _wa_request(
    method: str,
    path: str,
    json_body: Optional[dict] = None,
    *,
    throttle_seconds: float = 0,
) -> tuple[Optional[Any], Optional[str]]:
    """
    WA request: GET/POST {base}{path} with X-API-Key only. Never uses /admin/wa/* or Bearer.
    Requires API_KEY or ADMIN_API_KEY. Returns (data, error_string). Never raises.
    """
    import requests
    global _last_wa_error

    api_key = (_get_config().get("API_KEY") or _get_config().get("ADMIN_API_KEY") or "").strip()
    if not api_key:
        _last_wa_error = "API_KEY is required. Set API_KEY or ADMIN_API_KEY in Streamlit Cloud Secrets."
        return None, _last_wa_error
    headers = {"Content-Type": "application/json", "X-API-Key": api_key}

    cache_key = f"{method} {path}"
    now = time.time()
    if method == "GET" and throttle_seconds > 0 and cache_key in _wa_cache:
        ts, cached = _wa_cache[cache_key]
        if now - ts < throttle_seconds:
            return cached

    global _last_http_status, _last_request_url, _last_request_path, _last_response_preview, _last_wa_poll_timestamp
    _last_wa_poll_timestamp = time.time()
    _last_request_path = path
    base = _base_url()
    if not base:
        _last_wa_error = "API base URL not set"
        return None, _last_wa_error
    url = f"{base}{path}"
    _last_request_url = url
    to = _get_timeout()
    timeout = (to, to)

    def _err_from_response(r: requests.Response) -> str:
        code = r.status_code
        try:
            detail = r.json().get("detail", r.text[:200] if r.text else "Request failed")
        except Exception:
            detail = (r.text[:200] if r.text else "Request failed")
        if code == 401:
            return "Unauthorized (401). Check WA_QR_BRIDGE_TOKEN or API_KEY."
        if code == 403:
            return "Forbidden (403). Invalid or missing token or API key."
        if code == 404:
            return "Endpoint not found (404)."
        if code == 429:
            return "Rate limit exceeded (429). Try again in 30 seconds."
        return f"Request failed ({code}): {str(detail)[:200]}"

    max_retries = 2
    for attempt in range(max_retries + 1):
        try:
            if method == "GET":
                r = requests.get(url, headers=headers, timeout=timeout)
            else:
                r = requests.post(url, headers=headers, json=json_body or {}, timeout=timeout)

            _last_http_status = r.status_code
            _last_response_preview = _sanitize_preview(r.text[:200] if r.text else "")

            if r.status_code in (429, 502, 503, 504) and attempt < max_retries:
                time.sleep(2 ** attempt)
                continue

            if r.ok:
                _last_wa_error = None
                data = r.json() if r.content else ({} if method == "POST" else None)
                if method == "GET" and throttle_seconds > 0:
                    _wa_cache[cache_key] = (now, (data, None))
                return data, None

            err_msg = _err_from_response(r)
            _last_wa_error = err_msg
            return None, err_msg

        except requests.exceptions.HTTPError as e:
            _last_http_status = e.response.status_code if e.response else None
            if e.response is not None:
                _last_response_preview = _sanitize_preview(e.response.text[:200] if e.response.text else "")
            code = e.response.status_code if e.response else 0
            if code in (429, 502, 503, 504) and attempt < max_retries:
                time.sleep(2 ** attempt)
                continue
            if e.response is not None:
                _last_wa_error = _err_from_response(e.response)
                return None, _last_wa_error
            _last_wa_error = f"Request failed ({code})."
            return None, _last_wa_error

        except (requests.exceptions.ConnectTimeout, requests.exceptions.ReadTimeout):
            _last_http_status = None
            _last_wa_error = _conn_err("Connection error: timed out.", url)
            return None, _last_wa_error
        except requests.exceptions.ConnectionError as e:
            _last_http_status = None
            reason = str(e).split("\n")[0][:80] if str(e) else "connection refused or unreachable"
            _last_wa_error = _conn_err(f"Connection error: {reason}.", url)
            return None, _last_wa_error
        except Exception as e:
            _last_http_status = None
            _last_wa_error = _conn_err(f"Connection error: {str(e)[:80]}.", url)
            return None, _last_wa_error

    _last_wa_error = "Request failed after retries."
    return None, _last_wa_error


def clear_wa_cache() -> None:
    """Clear client-side WA cache. Call before manual Refresh QR."""
    global _wa_cache
    _wa_cache.clear()


def get_wa_status() -> tuple[Optional[dict], Optional[str]]:
    """GET WA status. Throttled 6s. Returns dict with 'connected' boolean."""
    path, _, _, _ = _wa_paths()
    data, err = _wa_request("GET", path, throttle_seconds=WA_THROTTLE_STATUS)
    if err:
        return None, err
    if not isinstance(data, dict):
        return {"connected": False, "status": "unknown"}, None
    connected = data.get("connected", False) or data.get("status") == "open"
    return {**data, "connected": connected}, None


def get_wa_qr(*, force_refresh: bool = False) -> tuple[Optional[dict], Optional[str]]:
    """GET WA QR. Throttled 8s unless force_refresh=True. Returns dict with 'qr' (str or None)."""
    _, path, _, _ = _wa_paths()
    if force_refresh:
        cache_key = f"GET {path}"
        _wa_cache.pop(cache_key, None)
    data, err = _wa_request("GET", path, throttle_seconds=0 if force_refresh else WA_THROTTLE_QR)
    if err:
        return None, err
    if not isinstance(data, dict):
        return {"qr": None}, None
    qr = data.get("qr")
    return {"qr": qr if qr else None, **data}, None


def post_wa_reconnect(*, wipe_auth: bool = False) -> tuple[Optional[dict], Optional[str]]:
    """POST WA reconnect. No throttle. Backoff on 429/5xx. wipe_auth defaults to False."""
    _, _, path, _ = _wa_paths()
    payload = {"wipe_auth": True} if wipe_auth else {}
    return _wa_request("POST", path, json_body=payload)


def get_wa_netcheck() -> tuple[Optional[dict], Optional[str]]:
    """GET WA netcheck (connectivity to WhatsApp). Returns {ok, status_code, error, server_time} or error."""
    _, _, _, path = _wa_paths()
    return _wa_request("GET", path, throttle_seconds=0)


def get_monitoring_status(tenant: Optional[str] = None) -> tuple[Optional[dict], Optional[str]]:
    """GET /monitoring: worker/collector status, last run, queue health. Returns full body as status dict."""
    t = (tenant if isinstance(tenant, str) else str(tenant)) if tenant is not None else None
    path = _append_query("/monitoring", {"tenant": t})
    return api_get(path, use_bearer=False)


def get_monitoring_recent(limit: int = 20, tenant: Optional[str] = None) -> tuple[Optional[list], Optional[str]]:
    """GET /monitoring and return recent jobs list (same endpoint as status)."""
    t = (tenant if isinstance(tenant, str) else str(tenant)) if tenant is not None else None
    data, err = api_get(_append_query("/monitoring", {"tenant": t}), use_bearer=False)
    if err:
        return None, err
    if isinstance(data, dict) and "recent" in data:
        return data["recent"], None
    return [], None


def post_monitoring_run(tenant: Optional[str] = None) -> tuple[Optional[dict], Optional[str]]:
    t = (tenant if isinstance(tenant, str) else str(tenant)) if tenant is not None else None
    return api_post("/monitoring/run", json_body={"tenant": t} if t else None, use_bearer=False)


def get_posts(
    status: str = "pending",
    limit: int = 20,
    offset: int = 0,
    source: Optional[str] = None,
    q: Optional[str] = None,
    tenant: Optional[str] = None,
) -> tuple[Optional[list], Optional[str]]:
    """List posts: GET /posts?status=pending|published. Returns items with rendered_text, draft_payload."""
    path = add_query("/posts", {
        "limit": limit,
        "offset": offset,
        "status": status,
    })
    data, err = api_get(path, use_bearer=False)
    if err:
        return None, err
    if isinstance(data, dict) and "items" in data:
        return data["items"], None
    return data or [], None


def post_approve(post_id: int) -> tuple[Optional[dict], Optional[str]]:
    return api_post(f"/review/{post_id}/approve", use_bearer=False)


def post_reject(post_id: int) -> tuple[Optional[dict], Optional[str]]:
    return api_post(f"/review/{post_id}/reject", use_bearer=False)
