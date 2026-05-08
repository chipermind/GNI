#!/usr/bin/env python3
"""
Smoke test for Desk24H grounded output: evidence → filler vs content.
Proves: no evidence => filler only; evidence + no citations => blocked_claims; evidence + citations => content allowed.
No Telegram. No network. No Ollama.
Exit 0 only if all scenarios behave as expected.
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

# --- Evidence packs ---

# A) Empty items
PACK_EMPTY = {"topic": "Geopolitics", "items": []}

# B) Valid evidence (snippets + confidence)
PACK_WITH_EVIDENCE = {
    "topic": "Geopolitics",
    "items": [
        {
            "title": "Fed holds rates",
            "source": {"name": "Reuters", "url": "https://reuters.com/fed-holds", "published_at": "2025-02-11T12:00:00Z"},
            "evidence_snippets": ["Fed kept rates at 5.25%."],
            "tags": [],
            "confidence": 0.72,
        }
    ],
}


def _filler_sections():
    """Minimal filler sections for PANORAMA (Geopolitics, Cyber)."""
    from desk.grounded_schema import safe_filler_section

    return [safe_filler_section("Geopolitics"), safe_filler_section("Cyber")]


def _fake_section_claims_no_citations():
    """Simulate LLM output: substantive content but citations=[] (violation)."""
    from desk.grounded_schema import ALLOWED_SECTION_NAMES

    return [
        {
            "name": "Geopolitics",
            "summary": "Fed held rates steady.",
            "leitura": "Central bank maintains stance.",
            "insight": "Neutral for risk assets.",
            "strategic_implication": "Monitoring",
            "risk_level": "Neutral",
            "time_horizon": "72h",
            "secondary_effects": "TBD",
            "citations": [],  # violation: claims without citations
        },
        {
            "name": "Cyber",
            "summary": "—",
            "leitura": "Sem sinal confirmado",
            "insight": "—",
            "strategic_implication": "Monitoring",
            "risk_level": "Neutral",
            "time_horizon": "72h",
            "secondary_effects": "TBD",
            "citations": [],
        },
    ]


def _fake_section_with_citations():
    """Simulate LLM output: substantive content + citations (allowed)."""
    url = "https://reuters.com/fed-holds"
    return [
        {
            "name": "Geopolitics",
            "summary": "Fed held rates steady.",
            "leitura": "Central bank maintains stance.",
            "insight": "Neutral for risk assets.",
            "strategic_implication": "Monitoring",
            "risk_level": "Neutral",
            "time_horizon": "72h",
            "secondary_effects": "TBD",
            "citations": [url],
        },
    ]


def _post_with_sections(sections, desk_type="FLOW_1330", text_prefix="Flow update"):
    """Build post dict with sections. FLOW_1330 avoids reading-pin requirement."""
    from desk.grounded_schema import render_section

    text_parts = [text_prefix]
    for sec in sections:
        text_parts.append(render_section(sec))
    text = "\n\n".join(text_parts)
    return {
        "type": desk_type,
        "text": text,
        "sections": sections,
        "meta": {},
    }


def run():
    n_ok = 0
    n_total = 0

    # --- A) No evidence => filler only, passes ---
    n_total += 1
    from desk.composer import _sanitize_grounded_sections
    from desk.grounded_schema import validate_grounded_output
    from desk.grounded_validators import validate_grounded_sections
    from desk.validators import validate

    filler = _filler_sections()
    obj_a = {"sections": filler, "meta": {}}
    ok_schema, _ = validate_grounded_output(obj_a)
    ok_sec, _ = validate_grounded_sections(obj_a)
    post_a = _post_with_sections(filler)
    post_a["meta"]["evidence_pack"] = PACK_EMPTY
    ok_val, reason = validate(post_a, prev_texts=[], packs=PACK_EMPTY)
    if ok_schema and ok_sec and ok_val:
        print("[PASS] A) No evidence => filler only, passes")
        n_ok += 1
    else:
        print(f"[FAIL] A) No evidence: schema={ok_schema} sections={ok_sec} validate={ok_val} reason={reason}")

    # --- B) Evidence but no citations returned => blocked_claims and filler used ---
    n_total += 1
    sections_b = _fake_section_claims_no_citations()
    sanitized, blocked = _sanitize_grounded_sections(sections_b, PACK_WITH_EVIDENCE)
    # First section violated citation policy -> replaced with filler
    first_is_filler = sanitized[0].get("leitura") == "Sem sinal confirmado" and sanitized[0].get("citations") == []
    if blocked >= 1 and first_is_filler:
        # Post with sanitized sections should pass
        post_b = _post_with_sections(sanitized)
        post_b["meta"]["evidence_pack"] = PACK_WITH_EVIDENCE
        ok_b, r = validate(post_b, prev_texts=[], packs=PACK_WITH_EVIDENCE)
        if ok_b:
            print("[PASS] B) Evidence + no citations => blocked_claims, filler used, passes")
            n_ok += 1
        else:
            print(f"[FAIL] B) Sanitized post validate failed: {r}")
    else:
        print(f"[FAIL] B) blocked={blocked} first_is_filler={first_is_filler}")

    # --- C) Evidence + citations => content allowed ---
    n_total += 1
    sections_c = _fake_section_with_citations()
    obj_c = {"sections": sections_c, "meta": {}}
    ok_schema_c, _ = validate_grounded_output(obj_c)
    ok_sec_c, _ = validate_grounded_sections(obj_c)
    post_c = _post_with_sections(sections_c)
    post_c["meta"]["evidence_pack"] = PACK_WITH_EVIDENCE
    ok_val_c, reason_c = validate(post_c, prev_texts=[], packs=PACK_WITH_EVIDENCE)
    if ok_schema_c and ok_sec_c and ok_val_c:
        print("[PASS] C) Evidence + citations => content allowed, passes")
        n_ok += 1
    else:
        print(f"[FAIL] C) Evidence + citations: schema={ok_schema_c} sections={ok_sec_c} validate={ok_val_c} reason={reason_c}")

    print("")
    print(f"Grounded smoke: {n_ok}/{n_total} passed")
    return 0 if n_ok == n_total else 1


if __name__ == "__main__":
    sys.exit(run())
