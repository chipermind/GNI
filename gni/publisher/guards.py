"""
Editorial contract guards: validate text before publish. If validation fails, do NOT post.
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Literal

logger = logging.getLogger(__name__)

# Contract markers (must match gni/templates)
LONG_HEADER = "🌐 GNI — BRIEFING GLOBAL"
LONG_FOOTER = "🔐 GNI — Um passo à frente."
SHORT_RADAR = "🔎 Radar Ativo"
SHORT_LEITURA = "📌 Leitura GNI"
SHORT_SIGNATURE = "— Equipe GNI"
FLASH_HEADER = "🚨 GNI — FLASH"
FLASH_IMPACTO = "📌 Impacto"

FormatKind = Literal["BRIEFING_LONG", "RADAR_SHORT", "FLASH_BREAKING"]


def validate_long(text: str) -> tuple[bool, str]:
    """
    LONG must contain header and footer. Returns (ok, reason).
    """
    if not text or not text.strip():
        return False, "empty_text"
    t = text.strip()
    if LONG_HEADER not in t:
        return False, "missing_long_header"
    if LONG_FOOTER not in t:
        return False, "missing_long_footer"
    return True, ""


def validate_short(text: str) -> tuple[bool, str]:
    """
    SHORT must contain Radar Ativo, Leitura GNI, and signature. Returns (ok, reason).
    """
    if not text or not text.strip():
        return False, "empty_text"
    t = text.strip()
    if SHORT_RADAR not in t:
        return False, "missing_short_radar"
    if SHORT_LEITURA not in t:
        return False, "missing_short_leitura"
    if SHORT_SIGNATURE not in t:
        return False, "missing_short_signature"
    return True, ""


def validate_flash(text: str) -> tuple[bool, str]:
    """
    FLASH must contain flash header and Impacto. Returns (ok, reason).
    """
    if not text or not text.strip():
        return False, "empty_text"
    t = text.strip()
    if FLASH_HEADER not in t:
        return False, "missing_flash_header"
    if FLASH_IMPACTO not in t:
        return False, "missing_flash_impacto"
    return True, ""


def validate_for_format(text: str, format_mode: str) -> tuple[bool, str]:
    """
    Dispatch by format_mode. Returns (ok, reason). reason empty if ok.
    """
    mode = (format_mode or "").strip().upper()
    if mode == "BRIEFING_LONG":
        return validate_long(text)
    if mode == "RADAR_SHORT":
        return validate_short(text)
    if mode == "FLASH_BREAKING":
        return validate_flash(text)
    return False, f"unknown_format_mode_{format_mode!r}"


def _logs_dir() -> Path:
    """Directory for failed payload dumps (logs/ under repo or cwd)."""
    for base in [Path(__file__).resolve().parent.parent.parent, Path.cwd()]:
        d = base / "logs"
        if d.is_dir() or base == Path.cwd():
            return d
    return Path.cwd() / "logs"


def save_failed_payload_for_debug(text: str, format_mode: str, reason: str) -> str | None:
    """
    Save payload to logs/ for debug when guard fails. No DB. Returns path if written.
    """
    try:
        log_dir = _logs_dir()
        log_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        safe_mode = (format_mode or "unknown").replace("/", "_")[:32]
        path = log_dir / f"failed_guard_{safe_mode}_{ts}.txt"
        payload = f"reason={reason}\nformat_mode={format_mode}\n---\n{text}"
        path.write_text(payload, encoding="utf-8")
        return str(path)
    except Exception as e:
        logger.warning("guard_save_failed_payload error=%s", e)
        return None


def guard_and_validate(
    text: str,
    format_mode: str,
) -> tuple[bool, str]:
    """
    Validate text for format_mode. If invalid: log error, save payload to logs/, return (False, reason).
    Caller must NOT post when False.
    """
    ok, reason = validate_for_format(text, format_mode)
    if ok:
        return True, ""
    logger.error(
        "guard_failed format_mode=%s reason=%s text_len=%s",
        format_mode,
        reason,
        len(text) if text else 0,
    )
    path = save_failed_payload_for_debug(text, format_mode, reason)
    if path:
        logger.info("guard_failed payload_saved path=%s", path)
    return False, reason


# ---------------------------------------------------------------------------
# Editorial validator (tone / headline / emoji / hard rules)
# ---------------------------------------------------------------------------

EditorialTemplate = Literal["FLASH", "ALERTA", "RADAR", "BRIEFING", "FECHAMENTO"]

_LEXICON_PATH = Path(__file__).resolve().parent.parent / "templates" / "forbidden_lexicon.json"


@dataclass
class EditorialViolation:
    code: str
    field: str
    match: str = ""

    def as_tuple(self) -> tuple[str, str, str]:
        return (self.code, self.field, self.match)


@dataclass
class EditorialResult:
    ok: bool
    violations: list[EditorialViolation] = field(default_factory=list)

    @property
    def first_reason(self) -> str:
        if self.ok or not self.violations:
            return ""
        v = self.violations[0]
        return f"{v.code}:{v.field}" if v.field else v.code


class EditorialValidator:
    """
    Enforces tone, headline, emoji, and hard rules defined in forbidden_lexicon.json.
    Use validate(payload) for structured field-level validation, or
    validate_text(text, template) for whole-text validation against body rules.
    """

    def __init__(self, lexicon_path: Path | None = None) -> None:
        self._path = lexicon_path or _LEXICON_PATH
        self._lex: dict = {}
        self._compiled: dict[str, list[re.Pattern]] = {}
        self._reload()

    def _reload(self) -> None:
        with self._path.open("r", encoding="utf-8") as f:
            self._lex = json.load(f)
        self._compiled.clear()
        for rule_name, rule in self._lex.get("rules", {}).items():
            patterns = rule.get("patterns") or ([rule["pattern"]] if rule.get("pattern") else [])
            self._compiled[rule_name] = [
                re.compile(p, flags=re.IGNORECASE | re.UNICODE) for p in patterns
            ]

    # ------------- helpers -------------

    def _rule_applies_to_template(self, rule: dict, template: str) -> bool:
        applies = rule.get("applies_to")
        if applies is None:
            return False
        return template in applies

    def _is_field_exempt(self, rule: dict, template: str, field_name: str) -> bool:
        exempt_map = rule.get("exempt_fields") or {}
        return field_name in (exempt_map.get(template) or [])

    def _scan(
        self,
        rule_name: str,
        text: str,
        field_name: str,
    ) -> list[EditorialViolation]:
        out: list[EditorialViolation] = []
        rule = self._lex["rules"][rule_name]
        for pattern in self._compiled.get(rule_name, []):
            m = pattern.search(text)
            if m:
                out.append(EditorialViolation(code=rule["code"], field=field_name, match=m.group(0)))
                break
        return out

    # ------------- public -------------

    def validate_headline(self, title: str) -> list[EditorialViolation]:
        v: list[EditorialViolation] = []
        spec = self._lex.get("headline_pattern", {})
        if not isinstance(title, str) or not title.strip():
            v.append(EditorialViolation(code="headline_empty", field="title"))
            return v
        t = title.strip()
        char_min = spec.get("char_min", 40)
        char_max = spec.get("char_max", 90)
        if not (char_min <= len(t) <= char_max):
            v.append(EditorialViolation(code="headline_length_invalid", field="title", match=str(len(t))))
        starts = spec.get("must_start_with_emoji") or []
        if starts and not any(t.startswith(e + " ") for e in starts):
            v.append(EditorialViolation(code="headline_missing_priority_emoji", field="title"))
        sep = spec.get("separator_char", "—")
        required = spec.get("required_separators", 2)
        if t.count(sep) < required:
            v.append(EditorialViolation(code="headline_missing_separators", field="title"))
        for ending in spec.get("forbidden_endings", []):
            if t.endswith(ending):
                v.append(EditorialViolation(code="headline_forbidden_ending", field="title", match=ending))
                break
        for ch in spec.get("forbidden_chars", []):
            if ch in t:
                v.append(EditorialViolation(code="headline_forbidden_char", field="title", match=ch))
                break
        v.extend(self._scan("clickbait_headline", t, "title"))
        return v

    def validate_emojis(self, text: str, template: str) -> list[EditorialViolation]:
        whitelist = self._lex.get("emoji_whitelist", {})
        priority = set(whitelist.get("priority") or [])
        structural = set((whitelist.get("structural_by_template") or {}).get(template) or [])
        allowed = priority | structural
        emoji_re = re.compile(
            "["
            "\U0001F300-\U0001FAFF"
            "\U00002600-\U000027BF"
            "\U0001F1E6-\U0001F1FF"
            "]",
            flags=re.UNICODE,
        )
        v: list[EditorialViolation] = []
        seen_pairs = re.findall(r"(.)\1", text)
        for ch in seen_pairs:
            if ch in priority or ch in structural:
                v.append(EditorialViolation(code="emoji_repetition", field="body", match=ch))
                break
        for m in emoji_re.finditer(text):
            ch = m.group(0)
            if ch not in allowed:
                v.append(EditorialViolation(code="emoji_not_whitelisted", field="body", match=ch))
                break
        return v

    def validate_all_caps(self, text: str) -> list[EditorialViolation]:
        rule = self._lex["rules"].get("all_caps_word")
        if not rule:
            return []
        whitelist = set(rule.get("acronym_whitelist") or [])
        pattern = re.compile(rule["pattern"], flags=re.UNICODE)
        for m in pattern.finditer(text):
            word = m.group(0)
            if word in whitelist:
                continue
            return [EditorialViolation(code=rule["code"], field="body", match=word)]
        return []

    def validate_redundancy(self, payload: dict) -> list[EditorialViolation]:
        cfg = self._lex.get("redundancy") or {}
        threshold = float(cfg.get("similarity_threshold", 0.85))
        pairs = cfg.get("applies_to_pairs") or []
        try:
            from rapidfuzz import fuzz
        except ImportError:
            return []
        v: list[EditorialViolation] = []
        for a, b in pairs:
            va = payload.get(a)
            vb = payload.get(b)
            texts_a = va if isinstance(va, list) else [va] if va else []
            texts_b = vb if isinstance(vb, list) else [vb] if vb else []
            for ta in texts_a:
                if not isinstance(ta, str):
                    continue
                for tb in texts_b:
                    if not isinstance(tb, str):
                        continue
                    score = fuzz.token_set_ratio(ta, tb) / 100.0
                    if score >= threshold:
                        v.append(
                            EditorialViolation(
                                code=cfg.get("code", "redundancy_detected"),
                                field=f"{a}↔{b}",
                                match=f"sim={score:.2f}",
                            )
                        )
                        return v
        return v

    def validate_text(self, text: str, template: str, field_name: str = "body") -> list[EditorialViolation]:
        if not text:
            return []
        violations: list[EditorialViolation] = []
        for rule_name, rule in self._lex.get("rules", {}).items():
            if rule.get("applies_to_field") and rule["applies_to_field"] != field_name:
                continue
            if rule.get("applies_to") and not self._rule_applies_to_template(rule, template):
                continue
            if self._is_field_exempt(rule, template, field_name):
                continue
            if rule_name == "all_caps_word":
                violations.extend(self.validate_all_caps(text))
                continue
            if rule_name == "clickbait_headline":
                continue  # handled in validate_headline
            violations.extend(self._scan(rule_name, text, field_name))
        violations.extend(self.validate_emojis(text, template))
        return violations

    def validate(self, payload: dict) -> EditorialResult:
        if not isinstance(payload, dict):
            return EditorialResult(ok=False, violations=[EditorialViolation(code="invalid_payload", field="")])
        template = payload.get("template")
        if template not in {"FLASH", "ALERTA", "RADAR", "BRIEFING", "FECHAMENTO"}:
            return EditorialResult(ok=False, violations=[EditorialViolation(code="invalid_template", field="template")])

        violations: list[EditorialViolation] = []

        title = payload.get("title")
        if title is not None or template != "FLASH":
            if title is not None:
                violations.extend(self.validate_headline(str(title)))

        for fname, fvalue in payload.items():
            if fname in {"template"}:
                continue
            if fname == "title":
                continue
            if isinstance(fvalue, str):
                violations.extend(self.validate_text(fvalue, template, field_name=fname))
            elif isinstance(fvalue, list):
                for idx, item in enumerate(fvalue):
                    if isinstance(item, str):
                        sub = self.validate_text(item, template, field_name=fname)
                        for s in sub:
                            s.field = f"{fname}[{idx}]"
                        violations.extend(sub)

        violations.extend(self.validate_redundancy(payload))

        return EditorialResult(ok=len(violations) == 0, violations=violations)


_DEFAULT_VALIDATOR: EditorialValidator | None = None


def get_editorial_validator() -> EditorialValidator:
    global _DEFAULT_VALIDATOR
    if _DEFAULT_VALIDATOR is None:
        _DEFAULT_VALIDATOR = EditorialValidator()
    return _DEFAULT_VALIDATOR
