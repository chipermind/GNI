#!/usr/bin/env python3
"""
Smoke test for Desk24H anti-hallucination evidence contract.
No Telegram. No Ollama. Proves validator + evidence policy.
Exit 0 only if all checks pass.
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

# Dummy packs: valid item, empty, low confidence
PACK_VALID = {
    "topic": "Geopolitics",
    "items": [
        {
            "title": "Fed holds rates",
            "source": {"name": "Reuters", "url": "https://reuters.com/1", "published_at": "2025-02-11T12:00:00Z"},
            "evidence_snippets": ["Fed kept rates at 5.25%."],
            "tags": [],
            "confidence": 0.72,
        }
    ],
}

PACK_EMPTY = {"topic": "Cyber", "items": []}

PACK_LOW_CONF = {
    "topic": "Flows",
    "items": [
        {
            "title": "ETF outflow",
            "source": {"name": "Bloomberg", "url": "https://bloomberg.com/1", "published_at": "2025-02-11T10:00:00Z"},
            "evidence_snippets": ["Outflows seen."],
            "tags": [],
            "confidence": 0.50,
        }
    ],
}

MIN_CONF = 0.65


def run():
    from desk.validators import validate_evidence_policy

    n_ok = 0
    n_total = 0

    # 1) Empty pack + placeholder (Monitoring) + Leitura/Insight placeholders => PASS
    n_total += 1
    text_placeholder = "Cyber section. Monitoring. Leitura: Sem sinal confirmado (TBD). Insight: —"
    ok, reason = validate_evidence_policy(text_placeholder, {"_": PACK_EMPTY}, MIN_CONF)
    if ok:
        print("[PASS] Empty pack + placeholder => OK")
        n_ok += 1
    else:
        print(f"[FAIL] Empty pack + placeholder: {reason}")

    # 2) Empty pack + no placeholder => FAIL
    n_total += 1
    text_no_placeholder = "Cyber section. Major breach reported."  # no Monitoring/TBD/Summary
    ok, reason = validate_evidence_policy(text_no_placeholder, {"_": PACK_EMPTY}, MIN_CONF)
    if not ok and reason == "evidence_missing_section_not_placeholder":
        print("[PASS] Empty pack + no placeholder => correctly rejected")
        n_ok += 1
    else:
        print(f"[FAIL] Expected evidence_missing_section_not_placeholder, got ok={ok} reason={reason}")

    # 3) Low confidence + Leitura TBD + Insight — => PASS
    n_total += 1
    text_low_ok = "Flows. Leitura: Sem sinal confirmado (TBD). Insight: —"
    ok, reason = validate_evidence_policy(text_low_ok, {"_": PACK_LOW_CONF}, MIN_CONF)
    if ok:
        print("[PASS] Low confidence + placeholder Leitura/Insight => OK")
        n_ok += 1
    else:
        print(f"[FAIL] Low confidence + placeholder: {reason}")

    # 4) Low confidence + no Leitura TBD => FAIL
    n_total += 1
    text_low_fail = "Flows. Leitura: ETF outflows. Insight: Risk-off."
    ok, reason = validate_evidence_policy(text_low_fail, {"_": PACK_LOW_CONF}, MIN_CONF)
    if not ok and "evidence_gate_failed" in reason:
        print("[PASS] Low confidence + real Leitura/Insight => correctly rejected")
        n_ok += 1
    else:
        print(f"[FAIL] Expected evidence_gate_failed, got ok={ok} reason={reason}")

    # 5) Valid evidence => Leitura/Insight allowed => PASS
    n_total += 1
    text_valid = "Geopolitics. Leitura: Fed holds. Insight: Neutral stance."
    ok, reason = validate_evidence_policy(text_valid, {"_": PACK_VALID}, MIN_CONF)
    if ok:
        print("[PASS] Valid evidence => Leitura/Insight allowed => OK")
        n_ok += 1
    else:
        print(f"[FAIL] Valid evidence: {reason}")

    # 6) Validate evidence.validate_pack accepts PACK_VALID
    n_total += 1
    from desk.evidence import validate_pack

    ok, _ = validate_pack(PACK_VALID)
    if ok:
        print("[PASS] validate_pack(PACK_VALID) => OK")
        n_ok += 1
    else:
        print(f"[FAIL] validate_pack(PACK_VALID) failed")

    print("")
    print(f"Evidence smoke: {n_ok}/{n_total} passed")
    return 0 if n_ok == n_total else 1


if __name__ == "__main__":
    sys.exit(run())
