"""
Desk daily state: day key in America/Recife, default state, helpers.
Stdlib only.
"""
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from desk.storage import get_day_state as _get_day_state, set_day_state as _set_day_state

logger = logging.getLogger(__name__)


def _utc_now_iso() -> str:
    """Return current UTC time as ISO8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _dedupe_list(items: list, max_n: int | None = None) -> list:
    """Return list with duplicates removed (order preserved). Optionally cap length."""
    seen: set = set()
    out: list = []
    for x in items:
        if x not in seen:
            seen.add(x)
            out.append(x)
            if max_n is not None and len(out) >= max_n:
                break
    return out


def day_key(dt_utc: datetime | None = None, tz: str = "America/Recife") -> str:
    """Return YYYY-MM-DD for the given datetime in the given timezone's day boundary."""
    if dt_utc is None:
        dt_utc = datetime.now(timezone.utc)
    if dt_utc.tzinfo is None:
        dt_utc = dt_utc.replace(tzinfo=timezone.utc)
    local = dt_utc.astimezone(ZoneInfo(tz))
    return local.strftime("%Y-%m-%d")


def default_state() -> dict:
    """Return default day state structure."""
    return {
        "top_vectors": [],
        "confirmed": [],
        "lost_strength": [],
        "watch_next_24h": [],
        "updated_at": _utc_now_iso(),
        "version": 1,
    }


def load_day_state(day: str) -> dict:
    """Return stored state for day or default_state. Catches storage errors."""
    try:
        stored = _get_day_state(day)
        if stored is not None:
            return stored
    except Exception as e:
        logger.warning("load_day_state failed for %s: %s", day, e)
    return default_state()


def save_day_state(day: str, state: dict) -> None:
    """Persist state for day. Catches storage errors, does not raise."""
    try:
        _set_day_state(day, state)
    except Exception as e:
        logger.warning("save_day_state failed for %s: %s", day, e)


def update_and_persist(
    desk_type: str,
    snapshot: dict,
    post: dict,
    tz: str = "America/Recife",
) -> dict:
    """Load, update, persist day state. Returns computed state; storage errors logged but do not crash."""
    day = day_key(tz=tz)
    existing = load_day_state(day)
    new = update_day_state(existing, desk_type, snapshot, post)
    save_day_state(day, new)
    return new


# Allowed categories for top_vectors (must match snapshot intel)
_ALLOWED_CATEGORIES = frozenset({"geo", "cyber", "ai", "macro"})


def update_day_state(
    existing: dict | None,
    desk_type: str,
    snapshot: dict,
    post: dict,
) -> dict:
    """
    Update day state from snapshot + post. Deterministic, no fabrication.
    Only updates when evidence exists.
    """
    state = dict(existing) if existing else default_state()
    for k in ("top_vectors", "confirmed", "lost_strength", "watch_next_24h"):
        state[k] = list(state.get(k) or [])

    # --- top_vectors (max 3) ---
    # Rule: post["tags"] if present; else intel categories (geo/cyber/ai/macro); else unchanged
    tags = post.get("tags")
    if isinstance(tags, list) and tags:
        candidates = [str(t).strip() for t in tags if t is not None and str(t).strip()]
        if candidates:
            state["top_vectors"] = _dedupe_list(candidates, max_n=3)
    else:
        intel_items = snapshot.get("intel") or []
        cats: list[str] = []
        for item in intel_items:
            if isinstance(item, dict):
                c = item.get("category")
                if c is not None:
                    s = str(c).strip().lower()
                    if s in _ALLOWED_CATEGORIES:
                        cats.append(s)
        if cats:
            state["top_vectors"] = _dedupe_list(cats, max_n=3)

    # --- confirmed (max 5) ---
    # Rule: append post["reasons"] containing "confirmed" OR intel items with explicit confirmed field
    to_confirm: list[str] = []
    reasons = post.get("reasons")
    if isinstance(reasons, list):
        for r in reasons:
            if r is not None and "confirmed" in str(r).lower():
                s = str(r).strip()
                if s:
                    to_confirm.append(s)
    intel_items = snapshot.get("intel") or []
    for item in intel_items:
        if isinstance(item, dict) and item.get("confirmed"):
            t = item.get("title")
            if t is not None:
                s = str(t).strip()
                if s:
                    to_confirm.append(s)
    if to_confirm:
        merged = _dedupe_list((state["confirmed"] or []) + to_confirm, max_n=5)
        state["confirmed"] = merged

    # --- lost_strength (max 5) ---
    # Rule: only if snapshot has explicit "lost_strength" list or deltas show reversal
    to_lost: list[str] = []
    explicit = snapshot.get("lost_strength")
    if isinstance(explicit, list):
        for x in explicit:
            if x is not None:
                s = str(x).strip()
                if s:
                    to_lost.append(s)
    deltas = snapshot.get("deltas") or {}
    for section in ("markets", "flow"):
        d = deltas.get(section) or {}
        if not isinstance(d, dict):
            continue
        for key, v in d.items():
            if isinstance(v, dict) and "prev" in v and "curr" in v:
                p, c = v.get("prev"), v.get("curr")
                if p is not None and c is not None:
                    try:
                        pv, cv = float(p), float(c)
                        if pv > 0 and cv < 0:
                            to_lost.append(f"{section}:{key}")
                        elif pv < 0 and cv > 0:
                            to_lost.append(f"{section}:{key}")
                    except (TypeError, ValueError):
                        pass
    if to_lost:
        merged = _dedupe_list((state["lost_strength"] or []) + to_lost, max_n=5)
        state["lost_strength"] = merged

    # --- watch_next_24h (max 5) ---
    # Rule: snapshot["watch_next_24h"] if present; else intel titles with impact="high"
    to_watch: list[str] = []
    explicit_watch = snapshot.get("watch_next_24h")
    if isinstance(explicit_watch, list):
        for x in explicit_watch:
            if x is not None:
                s = str(x).strip()
                if s:
                    to_watch.append(s)
    if not to_watch:
        intel_items = snapshot.get("intel") or []
        for item in intel_items:
            if isinstance(item, dict):
                imp = item.get("impact")
                if imp is not None and str(imp).strip().lower() == "high":
                    t = item.get("title")
                    if t is not None:
                        s = str(t).strip()
                        if s:
                            to_watch.append(s)
    if to_watch:
        merged = _dedupe_list((state["watch_next_24h"] or []) + to_watch, max_n=5)
        state["watch_next_24h"] = merged

    state["updated_at"] = _utc_now_iso()
    return state


def _build_exec_closure_text(
    day_str: str,
    confirmed: list[str],
    changed: list[str],
    watch_next: list[str],
    max_lines: int = 16,
) -> str:
    """Build Telegram-friendly exec closure text. Max lines, omit empty sections."""
    lines: list[str] = []
    lines.append(f"🧭 FECHAMENTO GNI 21:00 — {day_str}")
    lines.append("")
    if confirmed:
        lines.append("✅ Confirmou")
        for x in confirmed[:4]:
            lines.append(f"• {x}")
        lines.append("")
    if changed:
        lines.append("🔁 Mudou / Perdeu força")
        for x in changed[:4]:
            lines.append(f"• {x}")
        lines.append("")
    if watch_next:
        lines.append("👀 Próximas 24h")
        for x in watch_next[:4]:
            lines.append(f"• {x}")
    # Trim to max_lines
    if len(lines) > max_lines:
        lines = lines[:max_lines]
    return "\n".join(lines).strip()


def build_exec_closure(day_state: dict, last_posts: list[dict]) -> dict:
    """
    Build authoritative closing summary for EXEC 20:30.
    Uses only day_state lists; no invention.
    """
    day_str = day_key(tz="America/Recife")
    confirmed = [str(x).strip() for x in (day_state.get("confirmed") or []) if x is not None and str(x).strip()]
    changed = [str(x).strip() for x in (day_state.get("lost_strength") or []) if x is not None and str(x).strip()]
    watch_next = [str(x).strip() for x in (day_state.get("watch_next_24h") or []) if x is not None and str(x).strip()]

    top_vectors = day_state.get("top_vectors") or []
    if top_vectors:
        headline = f"Vetores: {', '.join(str(v) for v in top_vectors[:3])}"
    elif last_posts:
        headline = f"{len(last_posts)} posts no dia"
    else:
        headline = "Fechamento 20:30"

    text = _build_exec_closure_text(day_str, confirmed, changed, watch_next, max_lines=16)

    return {
        "headline": headline,
        "confirmed": confirmed,
        "changed": changed,
        "watch_next_24h": watch_next,
        "text": text,
    }
