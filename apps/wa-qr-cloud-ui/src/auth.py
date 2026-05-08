"""
Auth: session-only (st.session_state). Seed user uses sentinel to avoid passlib/bcrypt
(which fails on Streamlit Cloud with Python 3.13). No file persistence (Cloud-safe).
"""
from typing import Any, Optional

_SEED_PASSWORD_SENTINEL = "__SEED_PLAIN_COMPARE__"

try:
    import bcrypt as bcrypt_lib
    _has_bcrypt = True
except ImportError:
    bcrypt_lib = None
    _has_bcrypt = False

_users: dict[str, dict[str, Any]] = {}


def _verify_password(plain: str, hashed: str) -> bool:
    if not plain:
        return False
    if hashed == _SEED_PASSWORD_SENTINEL:
        from src.config import get_config
        seed = (get_config().get("SEED_CLIENT_PASSWORD") or "").strip()
        return seed == plain
    if not hashed:
        return False
    try:
        if bcrypt_lib:
            return bcrypt_lib.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False
    return False


def seed_user_if_needed() -> None:
    """Seed user from config. Uses sentinel — no passlib/bcrypt call (Streamlit Cloud safe)."""
    from src.config import get_config
    cfg = get_config()
    email = (cfg.get("SEED_CLIENT_EMAIL") or "").strip().lower()
    password = (cfg.get("SEED_CLIENT_PASSWORD") or "").strip()
    role = (cfg.get("SEED_CLIENT_ROLE") or "client").strip().lower()
    if not email or not password:
        return
    if email in _users:
        return
    _users[email] = {"email": email, "password_hash": _SEED_PASSWORD_SENTINEL, "role": role}


def login(email: str, password: str) -> bool:
    """Verify credentials and set session. Returns True on success."""
    import streamlit as st
    email = (email or "").strip().lower()
    if not email or not password:
        return False
    user = _users.get(email)
    if not user or not _verify_password(password, user.get("password_hash", "")):
        return False
    st.session_state.auth_user = user
    st.session_state.auth_role = user.get("role") or "client"
    st.session_state.auth_email = user.get("email")
    return True


def logout() -> None:
    """Clear auth from session (legacy + JWT)."""
    import streamlit as st
    for key in ("auth_user", "auth_role", "auth_email", "auth_token"):
        if key in st.session_state:
            del st.session_state[key]


def require_login() -> None:
    """Require logged-in user (auth_email or auth_token). st.stop() if not."""
    import streamlit as st
    if st.session_state.get("auth_email") or st.session_state.get("auth_token"):
        return
    st.warning("Please log in to continue.")
    st.stop()


def require_role(roles: list[str] | tuple[str]) -> None:
    """Require current user role in allowed list. Call after require_login(). st.stop() if not."""
    import streamlit as st
    role = (st.session_state.get("auth_role") or "").strip().lower()
    allowed = [r.strip().lower() for r in roles]
    if role not in allowed:
        st.error("🔒 You do not have permission to view this page.")
        st.stop()


def current_user() -> Optional[dict[str, Any]]:
    """Return current user dict (email, role) or None."""
    import streamlit as st
    if not st.session_state.get("auth_email"):
        return None
    return {
        "email": st.session_state.get("auth_email"),
        "role": (st.session_state.get("auth_role") or "client").strip().lower(),
    }
