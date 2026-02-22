"""
Monitoring — 24/7 scraping/jobs dashboard. Login required. Client = own data; admin = all.

Clean UI: API health badge, Status card (db/redis/ollama pills + items_last_24h), Recent Items table,
auto-refresh toggle, manual Refresh. On API failure, shows last successful data from session cache.
"""
from datetime import datetime, timedelta
from typing import Any, Optional

import streamlit as st

from src.auth import require_login
from src.api import get_health, get_api_display_info, get_monitoring_status
from src.ui import inject_app_css, render_sidebar

try:
    from src.ui import render_api_error_hint
except ImportError:
    render_api_error_hint = lambda display_info=None: None

require_login()
inject_app_css()
role = (st.session_state.get("auth_role") or "client").strip().lower()
base = (st.session_state.get("api_base_url") or "").strip().rstrip("/")
render_sidebar(role, "monitoring", api_base_url=base, user_email=st.session_state.get("auth_email") or "")

tenant = None
if role != "admin":
    tenant = (st.session_state.get("auth_email") or "").strip() or st.session_state.get("monitoring_tenant") or "default"
if tenant is not None and not isinstance(tenant, str):
    tenant = str(tenant)

# --- Session state: cache for 24/7 stability + auto-refresh ---
for key, default in [
    ("mon_last_status", None),
    ("mon_last_recent", None),
    ("mon_last_success_at", None),
    ("mon_auto_refresh", False),
    ("mon_auto_refresh_interval", 30),
]:
    if key not in st.session_state:
        st.session_state[key] = default

st.title("Monitoring")
st.caption("Scraping and job status." + (" Showing all tenants." if role == "admin" else " Showing your data."))

# --- API Health badge (green / yellow / red) ---
health_data, health_err = get_health()
display_info = get_api_display_info()
if health_err:
    st.error("🔴 **API health:** Unreachable")
    st.caption(str(health_err)[:200])
    render_api_error_hint(display_info)
elif isinstance(health_data, dict):
    status = (health_data.get("status") or "ok").strip().lower()
    if status == "ok":
        st.success("🟢 **API health:** OK")
    elif status == "fail":
        st.error("🔴 **API health:** Fail")
    else:
        st.warning("🟡 **API health:** " + status)
else:
    st.success("🟢 **API health:** OK")

if display_info.get("base_url"):
    st.caption("**API base URL:** `" + str(display_info["base_url"]) + "`")

# --- Fetch monitoring data (one call returns status + recent) ---
status_data, status_err = get_monitoring_status(tenant=tenant)
recent: list = []
is_404 = status_err and ("Not Found" in str(status_err) or "404" in str(status_err))

if status_err:
    if st.session_state.mon_last_status is not None:
        status_data = st.session_state.mon_last_status
        recent = st.session_state.mon_last_recent or []
        st.warning("⚠️ Using last successful data. **Refresh** when the API is back. Error: " + str(status_err)[:120])
        if st.session_state.mon_last_success_at:
            st.caption("Last successful fetch: **" + str(st.session_state.mon_last_success_at) + "**")
        render_api_error_hint(display_info)
    else:
        st.error(status_err)
        render_api_error_hint(display_info)
        if is_404:
            st.info(
                "**Monitoring endpoint not available (404).** "
                "Deploy the latest API to your backend so the `/monitoring` route exists: "
                "run `deploy_vm.ps1 -Full` from **gni-bot-creator**, then on the VM run "
                "`docker compose build api && docker compose up -d api`. After that, click **Refresh**."
            )
            status_data = None
            recent = []
        else:
            st.caption("Scraping runs 24/7 on the backend. Use **Posts** to review and publish.")
            st.stop()
else:
    if isinstance(status_data, dict):
        recent = status_data.get("recent") or []
        st.session_state.mon_last_status = status_data
        st.session_state.mon_last_recent = recent
        st.session_state.mon_last_success_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

# --- Status card: db, redis, ollama pills + items_last_24h ---
if status_data and isinstance(status_data, dict):
    db_s = (status_data.get("db") or "—").strip().lower()
    redis_s = (status_data.get("redis") or "—").strip().lower()
    ollama_s = (status_data.get("ollama") or "—").strip().lower()

    def _pill(label: str, value: str) -> str:
        v = (value or "—").lower()
        if v in ("ok", "reachable"):
            color = "#0e7d38"
        elif v in ("unreachable", "fail"):
            color = "#c5221f"
        else:
            color = "#6b7280"
        return f'<span style="display:inline-block;background:{color};color:white;padding:0.2rem 0.5rem;border-radius:9999px;font-size:0.75rem;margin-right:0.35rem;">{label}: {value or "—"}</span>'

    st.markdown(
        '<div class="status-card">'
        "<strong>Status</strong><br>"
        + _pill("db", status_data.get("db"))
        + _pill("redis", status_data.get("redis"))
        + _pill("ollama", status_data.get("ollama"))
        + '<br><span style="margin-top:0.5rem;font-size:0.9rem;">Items (24h): <strong>' + str(status_data.get("items_last_24h", 0)) + "</strong></span>"
        "</div>",
        unsafe_allow_html=True,
    )

# --- Controls: Manual Refresh + Auto refresh toggle + interval ---
col_refresh, col_auto, col_interval = st.columns([1, 1, 1])
with col_refresh:
    if st.button("Refresh", key="mon_refresh"):
        st.rerun()
with col_auto:
    auto = st.checkbox("Auto refresh", value=st.session_state.mon_auto_refresh, key="mon_auto_cb")
    st.session_state.mon_auto_refresh = auto
with col_interval:
    opts = [10, 30, 60]
    cur = st.session_state.mon_auto_refresh_interval
    idx = opts.index(cur) if cur in opts else 1
    interval = st.selectbox("Interval (s)", options=opts, index=idx, key="mon_interval")
    st.session_state.mon_auto_refresh_interval = interval

if st.session_state.mon_last_success_at:
    st.caption("Last successful fetch: **" + str(st.session_state.mon_last_success_at) + "**")

st.divider()

# --- Recent Items table (id, source_name, status, created_at, updated_at) ---
st.subheader("Recent Items")
if recent and len(recent) > 0:
    rows = []
    for r in recent[:20]:
        if not isinstance(r, dict):
            rows.append({"id": str(r), "source_type": "—", "source_name": "—", "status": "—", "created_at": "—", "updated_at": "—"})
            continue
        rows.append({
            "id": r.get("id", "—"),
            "source_type": r.get("source_type") or "rss",
            "source_name": r.get("source_name") or "—",
            "status": r.get("status") or r.get("state") or "—",
            "created_at": r.get("created_at") or "—",
            "updated_at": r.get("updated_at") or "—",
        })
    st.dataframe(rows, use_container_width=True, hide_index=True)
else:
    st.info("No recent items.")

st.caption("Scraping runs 24/7 on the backend. Use **Posts** to review and publish.")

# --- Auto-refresh fragment (lightweight: rerun on interval when enabled) ---
if st.session_state.mon_auto_refresh and st.session_state.mon_auto_refresh_interval:
    sec = int(st.session_state.mon_auto_refresh_interval)
    try:
        @st.fragment(run_every=timedelta(seconds=sec))
        def _mon_auto_tick():
            st.rerun()
    except Exception:
        pass
