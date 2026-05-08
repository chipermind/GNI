"""
Publish safety: assert_publish_allowed reads Settings from DB.
If pause_all_publish is true, raises PublishPausedError (controlled exception).
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from apps.api.db.models import Settings

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


class PublishPausedError(Exception):
    """Raised when publish is blocked because pause_all_publish is true in Settings."""

    pass


def assert_publish_allowed(session: "Session") -> None:
    """
    Read Settings from DB; if pause_all_publish is True, raise PublishPausedError.
    Call before any publish (Telegram or Make) so the pipeline can skip publish and log the block.
    """
    row = session.query(Settings).first()
    if row is not None and getattr(row, "pause_all_publish", False):
        raise PublishPausedError("publish blocked by pause (pause_all_publish=true)")
