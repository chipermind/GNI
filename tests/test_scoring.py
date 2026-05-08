"""Unit tests for rumor detection and scoring rules."""
import pytest

from apps.worker.scoring import score_item


def test_rumor_detection_rumor():
    """Text containing 'rumor' => risk=high, template=ANALISE_INTEL, needs_review=True."""
    score = score_item(title="Market rumor suggests rate cut", summary="Traders are betting.")
    assert score["risk"] == "high"
    assert score["template"] == "ANALISE_INTEL"
    assert score["needs_review"] is True


def test_rumor_detection_rumours():
    """Text containing 'rumours' => risk=high, template=ANALISE_INTEL."""
    score = score_item(summary="There are rumours of a merger.")
    assert score["risk"] == "high"
    assert score["template"] == "ANALISE_INTEL"
    assert score["needs_review"] is True


def test_rumor_detection_unconfirmed():
    """Text containing 'unconfirmed' => risk=high, template=ANALISE_INTEL."""
    score = score_item(title="Unconfirmed reports of breach", summary="Sources say.")
    assert score["risk"] == "high"
    assert score["template"] == "ANALISE_INTEL"
    assert score["needs_review"] is True


def test_rumor_detection_allegedly():
    """Text containing 'allegedly' => risk=high, template=ANALISE_INTEL."""
    score = score_item(title="CEO allegedly sold shares before drop", summary=".")
    assert score["risk"] == "high"
    assert score["template"] == "ANALISE_INTEL"
    assert score["needs_review"] is True


def test_rumor_detection_alleged():
    """Text containing 'alleged' => risk=high."""
    score = score_item(summary="The alleged fraud is under investigation.")
    assert score["risk"] == "high"
    assert score["template"] == "ANALISE_INTEL"


def test_no_rumor_no_risk_high():
    """Text without rumor keywords => risk is None (or not high)."""
    score = score_item(title="Bitcoin hits new high", summary="Price surge continues.")
    assert score.get("risk") != "high"
    assert score["needs_review"] is False


def test_flash_editorial_announcement():
    """Text with 'announcement' => template=FLASH_SETORIAL when no rumor."""
    score = score_item(title="Company announcement: new product launch")
    assert score["template"] == "FLASH_SETORIAL"
    assert score.get("risk") != "high"


def test_rumor_wins_over_flash():
    """Rumor + announcement => ANALISE_INTEL (rumor rule takes precedence)."""
    score = score_item(
        title="Rumor of partnership announcement",
        summary="Unconfirmed reports of a deal.",
    )
    assert score["risk"] == "high"
    assert score["template"] == "ANALISE_INTEL"


def test_priority_p0_p1_p2():
    """priority is 0, 1, or 2 (P0, P1, P2)."""
    score = score_item(title="Rumor test", source_name="CoinDesk")
    assert score["priority"] in (0, 1, 2)
    score2 = score_item(title="Normal news", source_name="Reddit r/CryptoCurrency")
    assert score2["priority"] in (0, 1, 2)
