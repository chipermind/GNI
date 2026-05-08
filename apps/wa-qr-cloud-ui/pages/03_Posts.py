"""
Posts — generated posts queue. Login required. Client = own; admin = all. Approve / Reject.
"""
import streamlit as st

from src.auth import require_login
from src.api import get_posts, post_approve, post_reject
from src.ui import inject_app_css, render_sidebar

require_login()
inject_app_css()
role = (st.session_state.get("auth_role") or "client").strip().lower()
base = (st.session_state.get("api_base_url") or "").strip().rstrip("/")
render_sidebar(role, "posts", api_base_url=base, user_email=st.session_state.get("auth_email") or "")
tenant = None if role == "admin" else (st.session_state.get("auth_email") or "").strip() or None

def _emoji(s):
    s = (s or "").lower()
    if s in ("published", "approved", "sent"):
        return "✅"
    if s in ("pending", "drafted", "draft"):
        return "🟡"
    return "🔴"

st.title("Posts")
st.caption("Generated posts queue." + (" All tenants." if role == "admin" else " Your data."))

tab_pending, tab_published = st.tabs(["Pending", "Published"])

with tab_pending:
    st.subheader("Pending")
    pending, err = get_posts(status="pending", limit=20, tenant=tenant)
    if err:
        st.error(err)
    elif pending and len(pending) > 0:
        for item in pending:
            id_ = item.get("id") if isinstance(item, dict) else None
            title = (item.get("title") or item.get("source_name") or f"#{id_}") if isinstance(item, dict) else str(item)
            status = item.get("status", "pending") if isinstance(item, dict) else "pending"
            created = (item.get("created_at") or "—") if isinstance(item, dict) else "—"
            needs_review = item.get("needs_review", False) if isinstance(item, dict) else False
            rendered = (item.get("rendered_text") or "").strip() if isinstance(item, dict) else ""
            draft_payload = item.get("draft_payload") or {} if isinstance(item, dict) else {}
            with st.container():
                st.markdown(f"**{_emoji(str(status))} {title}**")
                st.caption(f"ID {id_} · {created}" + (" · Needs approval" if needs_review else " · Auto-publish"))
                if rendered:
                    with st.expander("📝 Formatted post preview", expanded=True):
                        st.markdown(rendered.replace("\n", "\n\n"))
                elif draft_payload:
                    with st.expander("📝 Draft content"):
                        st.json(draft_payload)
                col1, col2, col3 = st.columns([1, 1, 2])
                with col1:
                    if needs_review and st.button("Approve ✅", key=f"approve_{id_}"):
                        _, action_err = post_approve(id_)
                        if action_err:
                            st.error(action_err)
                        else:
                            st.success("Approved.")
                            st.rerun()
                with col2:
                    if needs_review and st.button("Reject ❌", key=f"reject_{id_}"):
                        _, action_err = post_reject(id_)
                        if action_err:
                            st.error(action_err)
                        else:
                            st.success("Rejected.")
                            st.rerun()
                st.divider()
    else:
        st.info("No pending posts. Items move here after LLM drafting (score → draft). Ensure Ollama is reachable from the worker.")

with tab_published:
    st.subheader("Published")
    published, err2 = get_posts(status="published", limit=100, tenant=tenant)
    if err2:
        st.warning(err2)
    elif published and len(published) > 0:
        for item in published:
            if isinstance(item, dict):
                id_ = item.get("id")
                title = (item.get("title") or item.get("source_name") or f"#{id_}")[:80]
                created = item.get("created_at") or "—"
                rendered = (item.get("rendered_text") or "").strip()
                with st.container():
                    st.markdown(f"**{_emoji('published')} {title}**")
                    st.caption(f"ID {id_} · {created}")
                    if rendered:
                        with st.expander("📝 Formatted post", expanded=False):
                            st.markdown(rendered.replace("\n", "\n\n"))
                    st.divider()
    else:
        st.info("No published posts.")
