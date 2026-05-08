"""
Shared UI: CSS injection and sidebar layout for GNI Streamlit Cloud app.
Use inject_app_css() once per page; use render_sidebar(role, current_page) after auth.
"""
from pathlib import Path
from typing import Literal

import streamlit as st

CurrentPage = Literal["home", "whatsapp", "monitoring", "posts"]

APP_CSS = """
<style>
/* === Layout: spacing and max-width === */
.main .block-container {
    max-width: 42rem;
    padding-top: 1.75rem;
    padding-bottom: 2rem;
}
.main .block-container > * {
    margin-bottom: 0.75rem;
}
@media (max-width: 640px) {
    .main .block-container { padding-left: 1rem; padding-right: 1rem; }
}

/* === Typography: hierarchy and sizes === */
.main h1 { font-size: 1.65rem; margin-bottom: 0.35rem; font-weight: 600; }
.main h2 { font-size: 1.2rem; margin-top: 1.25rem; margin-bottom: 0.5rem; font-weight: 600; }
.main h3 { font-size: 1rem; margin-top: 0.75rem; margin-bottom: 0.35rem; font-weight: 600; }
.subtitle-muted { color: rgba(49, 51, 63, 0.65); font-size: 0.9rem; margin-bottom: 0.75rem; }
.main [data-testid="stCaptionContainer"] { font-size: 0.8rem; }

/* === Cards: consistent borders and spacing === */
.stForm, .status-card, .content-card {
    border: 1px solid rgba(49, 51, 63, 0.1);
    border-radius: 0.5rem;
    box-shadow: 0 1px 2px rgba(0,0,0,0.04);
}
.stForm {
    padding: 1.5rem 1.25rem;
    margin-bottom: 1rem;
    background: var(--background-color, #fff);
}
.status-card {
    padding: 1rem 1.25rem;
    margin: 0.75rem 0;
    background: var(--secondary-background-color, #f0f2f6);
    color: var(--text-color, #262730);
    font-size: 0.9rem;
}
.status-card .muted { color: rgba(49, 51, 63, 0.6); font-size: 0.85rem; }
.content-card {
    padding: 1.25rem 1.5rem;
    margin: 0.75rem 0;
    background: var(--background-color, #fff);
}
.logo-title-block { text-align: center; margin-bottom: 1.25rem; }
.logo-title-block img { margin-bottom: 0.5rem; }

/* === Inputs and buttons === */
.stTextInput input, .stTextInput label { font-size: 0.9rem; }
.stButton > button {
    border-radius: 0.375rem;
    font-weight: 500;
    transition: background 0.15s ease;
    min-height: 2.25rem;
}
/* Button row alignment when in columns */
[data-testid="column"] .stButton { margin-top: 0.25rem; }

/* === Dividers and spacing === */
.main hr { margin: 1rem 0; border-color: rgba(49, 51, 63, 0.08); }

/* === Sidebar: section headings and spacing === */
[data-testid="stSidebar"] .stMarkdown { margin-bottom: 0.25rem; }
[data-testid="stSidebar"] section:first-of-type { padding-top: 0.5rem; }
[data-testid="stSidebar"] > div { padding: 0.5rem 0.75rem; }
.sidebar-header {
    font-size: 1.2rem;
    font-weight: 700;
    letter-spacing: 0.02em;
    margin-bottom: 0.5rem;
    padding-bottom: 0.5rem;
}
.sidebar-user-block {
    font-size: 0.8rem;
    color: rgba(49, 51, 63, 0.85);
    padding: 0.5rem 0;
    margin-bottom: 0.25rem;
    line-height: 1.4;
}
.sidebar-user-block .muted { color: rgba(49, 51, 63, 0.55); font-size: 0.75rem; word-break: break-all; }
.sidebar-section-label, .sidebar-account-label {
    font-size: 0.65rem;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: rgba(49, 51, 63, 0.55);
    font-weight: 600;
    margin: 0.5rem 0 0.2rem 0;
    padding-top: 0.25rem;
}
.sidebar-account-label { margin: 0.75rem 0 0.35rem 0; padding-top: 0.5rem; }
.sidebar-current-hint {
    font-size: 0.78rem;
    color: rgba(49, 51, 63, 0.5);
    margin-top: 0.2rem;
    padding: 0.2rem 0;
}
</style>
"""


def inject_app_css() -> None:
    """Inject app-wide CSS for cards, max-width, typography. Call once at top of each page."""
    st.markdown(APP_CSS, unsafe_allow_html=True)


def section_container(border: bool = True):
    """Return a container for a page section. Uses bordered container when available (Streamlit 1.30+)."""
    try:
        return st.container(border=border)
    except TypeError:
        return st.container()


def render_api_error_hint(display_info: dict | None = None) -> None:
    """Optional hint when API calls fail (e.g. show base URL). No-op if not used."""
    if display_info and display_info.get("base_url"):
        st.caption(f"Backend: `{display_info['base_url']}` — check that it is reachable from Streamlit Cloud.")


def render_sidebar(
    role: str,
    current_page: CurrentPage,
    api_base_url: str = "",
    user_email: str = "",
) -> None:
    """
    Render the left sidebar: compact GNI header, user/backend block, nav with icons, Account section at bottom.
    Call after login (so role and user_email are set). current_page highlights where the user is.
    """
    _base = Path(__file__).resolve().parent.parent
    _logo_path = _base / "assets" / "whatsapp-logo.webp"

    # --- Compact logo/header at top ---
    if _logo_path.exists():
        st.sidebar.image(str(_logo_path), use_container_width=True)
    st.sidebar.markdown('<p class="sidebar-header">GNI</p>', unsafe_allow_html=True)

    # --- User email + backend URL in a clean block ---
    if user_email or api_base_url:
        _short_url = (api_base_url[:32] + "…") if api_base_url and len(api_base_url) > 35 else (api_base_url or "")
        _lines = []
        if user_email:
            _lines.append(user_email)
        if _short_url:
            _lines.append(f'<span class="muted">Backend: {_short_url}</span>')
        st.sidebar.markdown(
            '<div class="sidebar-user-block">' + "<br>".join(_lines) + "</div>",
            unsafe_allow_html=True,
        )
    st.sidebar.caption("")  # subtle spacing
    st.sidebar.divider()

    # --- Navigation: grouped links with icons ---
    st.sidebar.markdown('<p class="sidebar-section-label">Navigation</p>', unsafe_allow_html=True)
    st.sidebar.page_link("app.py", label="Home", icon="🏠")
    st.sidebar.page_link("pages/01_WhatsApp_Connect.py", label="WhatsApp Connect", icon="📲")
    st.sidebar.page_link("pages/02_Monitoring.py", label="Monitoring", icon="📊")
    st.sidebar.page_link("pages/03_Posts.py", label="Posts", icon="📝")
    _current_labels = {"home": "Home", "whatsapp": "WhatsApp Connect", "monitoring": "Monitoring", "posts": "Posts"}
    st.sidebar.markdown(
        f'<p class="sidebar-current-hint">You\'re on: <strong>{_current_labels.get(current_page, current_page)}</strong></p>',
        unsafe_allow_html=True,
    )

    st.sidebar.divider()

    # --- Account section at bottom ---
    st.sidebar.markdown('<p class="sidebar-account-label">Account</p>', unsafe_allow_html=True)
    if st.sidebar.button("Change backend URL", key="sidebar_change_backend"):
        st.session_state.api_base_url = None
        st.rerun()
    if st.sidebar.button("Log out", key="sidebar_logout"):
        from src.auth import logout
        logout()
        st.rerun()
