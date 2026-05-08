"""
Backend base URL: internal config only. Never displayed in UI.
Priority: st.secrets["GNI_API_BASE_URL"] > os.getenv("GNI_API_BASE_URL") > DEFAULT_API_BASE_URL.
Works without Streamlit secrets (optional).
"""
import os
import streamlit as st

DEFAULT_API_BASE_URL = "http://217.216.84.81:8000"


def get_api_base_url() -> str:
    url = ""
    try:
        if hasattr(st, "secrets") and st.secrets is not None:
            url = (st.secrets.get("GNI_API_BASE_URL") or "").strip().rstrip("/")
    except Exception:
        pass
    if not url:
        url = (os.getenv("GNI_API_BASE_URL") or "").strip().rstrip("/")
    if not url:
        url = DEFAULT_API_BASE_URL.rstrip("/")
    return url
