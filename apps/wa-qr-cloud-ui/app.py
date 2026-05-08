"""
Home — GNI Streamlit app. Login via API (POST /auth/login), then WhatsApp / Monitoring / Posts.
No required secrets; optional GNI_API_BASE_URL or paste Backend URL in UI.
"""
from pathlib import Path

import streamlit as st

from src.config import get_config, has_seed_for_legacy
from src.auth import seed_user_if_needed, login as legacy_login, logout, require_login
from src.api import get_health, post_auth_login, get_auth_me
from src.ui import inject_app_css, render_sidebar

st.set_page_config(page_title="GNI — Home", layout="centered", initial_sidebar_state="expanded")
inject_app_css()

# --- 1) Session state: backend URL (env/secrets or user-pasted), auth ---
for key in ("api_base_url", "auth_user", "auth_role", "auth_email", "auth_token"):
    if key not in st.session_state:
        st.session_state[key] = None
if not st.session_state.api_base_url and get_config().get("GNI_API_BASE_URL"):
    st.session_state.api_base_url = get_config().get("GNI_API_BASE_URL", "").strip().rstrip("/")
if has_seed_for_legacy():
    seed_user_if_needed()

# --- 2) Backend URL not set: show paste input (card-style form) ---
base = (st.session_state.get("api_base_url") or "").strip().rstrip("/")
if not base:
    _logo_path = Path(__file__).parent / "assets" / "whatsapp-logo.webp"
    _c1, _c2, _c3 = st.columns([1, 2, 1])
    with _c2:
        if _logo_path.exists():
            st.image(str(_logo_path), width=120)
        st.markdown('<p class="subtitle-muted">Set your backend API URL below (or set <strong>GNI_API_BASE_URL</strong> in Streamlit Cloud Secrets).</p>', unsafe_allow_html=True)
    with st.form("backend_url_form"):
        url_input = st.text_input("Backend URL", placeholder="https://your-api.example.com:8000", key="url_input")
        st.caption("Example: https://api.yourdomain.com or https://YOUR_IP:8000 — no trailing slash.")
        if st.form_submit_button("Save"):
            u = (url_input or "").strip().rstrip("/")
            if u:
                st.session_state.api_base_url = u
                st.rerun()
            else:
                st.warning("Enter a URL.")
    st.stop()

# --- 3) Login gate: card-style form + helper text; then Status placeholder ---
if not st.session_state.get("auth_token") and not st.session_state.get("auth_email"):
    _logo_path = Path(__file__).parent / "assets" / "whatsapp-logo.webp"
    _c1, _c2, _c3 = st.columns([1, 2, 1])
    with _c2:
        if _logo_path.exists():
            st.image(str(_logo_path), width=120)
        st.markdown('<p class="subtitle-muted">Sign in with your email and password to continue.</p>', unsafe_allow_html=True)
    with st.form("login_form"):
        email = st.text_input("Email", key="login_email", autocomplete="email")
        st.caption("Use the same email you registered with on the backend.")
        password = st.text_input("Password", type="password", key="login_password", autocomplete="current-password")
        st.caption("Your password is never stored in this app.")
        submitted = st.form_submit_button("Log in")
        if submitted:
            email = (email or "").strip()
            password = password or ""
            if not email or not password:
                st.error("Email and password required.")
            else:
                body, err = post_auth_login(email, password)
                if err:
                    if has_seed_for_legacy() and legacy_login(email, password):
                        st.rerun()
                    else:
                        st.error(err or "Invalid email or password.")
                else:
                    token = (body or {}).get("access_token") if isinstance(body, dict) else None
                    if token:
                        st.session_state.auth_token = token
                        me, me_err = get_auth_me()
                        if not me_err and isinstance(me, dict):
                            st.session_state.auth_email = me.get("email") or email
                            st.session_state.auth_role = "client"
                        else:
                            st.session_state.auth_email = email
                        st.rerun()
                    else:
                        st.error("Login failed.")
    st.caption("Backend: %s" % base)
    st.stop()

# --- 4) Sidebar: GNI, nav with icons, current-page hint ---
role = (st.session_state.get("auth_role") or "client").strip().lower()
render_sidebar(role, "home", api_base_url=base, user_email=st.session_state.auth_email or "")

# --- 5) Main: header, Status placeholder card, API health, quick links ---
st.title("Home")
st.markdown('<p class="subtitle-muted">Dashboard and quick links.</p>', unsafe_allow_html=True)
st.success("Logged in.")

# Status placeholder card (UI-only; no backend logic)
st.markdown(
    '<div class="status-card"><strong>Status</strong><br><span class="muted">Not connected yet. Go to WhatsApp Connect to link your account.</span></div>',
    unsafe_allow_html=True,
)

# API health
health_data, health_err = get_health()
if health_err:
    st.warning(f"⚠️ API health: {health_err}")
else:
    status = health_data.get("status", "ok") if isinstance(health_data, dict) else "ok"
    st.success(f"✅ API health: **{status}**")

# Quick links
st.subheader("Quick links")
cols = st.columns(3)
with cols[0]:
    st.page_link("pages/01_WhatsApp_Connect.py", label="WhatsApp Connect", icon="📱")
with cols[1]:
    st.page_link("pages/02_Monitoring.py", label="Monitoring", icon="📊")
with cols[2]:
    st.page_link("pages/03_Posts.py", label="Posts", icon="📝")
