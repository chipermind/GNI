"""
Desk 24H product contract: scheduled report types.
Must remain stable — downstream code depends on these exact names.
"""
from enum import Enum

# Type           | max_lines | max_chars | conditional
# -----------------|-----------|-----------|-------------
# OVERNIGHT_GLOBAL_0500 | ~50   | ~3500     |
# PREMARKET_BR_0800    | ~40   | ~3000     |
# PANORAMA_0900        | ~60   | ~4000     |
# THREAT_MONITOR_1130  | ~30   | ~2000     | yes
# ALERTA_TATICO_1200   | ~25   | ~1800     | yes
# FLOW_1330            | ~45   | ~3200     |
# REALTIME_VOL_1530    | ~35   | ~2500     |
# RISK_MATRIX_1800     | ~55   | ~3800     |
# EXEC_SUMMARY_2030    | ~40   | ~3000     |
# OVERNIGHT_WATCH_2300 | ~45   | ~3200     |


class DeskType(str, Enum):
    """Scheduled desk report types."""

    OVERNIGHT_GLOBAL_0500 = "OVERNIGHT_GLOBAL_0500"
    PREMARKET_BR_0800 = "PREMARKET_BR_0800"
    PANORAMA_0900 = "PANORAMA_0900"
    THREAT_MONITOR_1130 = "THREAT_MONITOR_1130"
    ALERTA_TATICO_1200 = "ALERTA_TATICO_1200"
    FLOW_1330 = "FLOW_1330"
    REALTIME_VOL_1530 = "REALTIME_VOL_1530"
    RISK_MATRIX_1800 = "RISK_MATRIX_1800"
    EXEC_SUMMARY_2030 = "EXEC_SUMMARY_2030"
    OVERNIGHT_WATCH_2300 = "OVERNIGHT_WATCH_2300"


LIMITS = {
    DeskType.OVERNIGHT_GLOBAL_0500: {"max_lines": 50, "max_chars": 3500},
    DeskType.PREMARKET_BR_0800: {"max_lines": 40, "max_chars": 3000},
    DeskType.PANORAMA_0900: {"max_lines": 60, "max_chars": 4000},
    DeskType.THREAT_MONITOR_1130: {"max_lines": 30, "max_chars": 2000},
    DeskType.ALERTA_TATICO_1200: {"max_lines": 25, "max_chars": 1800},
    DeskType.FLOW_1330: {"max_lines": 45, "max_chars": 3200},
    DeskType.REALTIME_VOL_1530: {"max_lines": 35, "max_chars": 2500},
    DeskType.RISK_MATRIX_1800: {"max_lines": 55, "max_chars": 3800},
    DeskType.EXEC_SUMMARY_2030: {"max_lines": 40, "max_chars": 3000},
    DeskType.OVERNIGHT_WATCH_2300: {"max_lines": 45, "max_chars": 3200},
}

# Desk 24H sequence: 05:00 -> 08:00 -> 09:00 -> 11:30 -> 12:00 -> 13:30 -> 15:30 -> 18:00 -> 20:30 -> 23:00
ALL_DESK_TYPES = [
    DeskType.OVERNIGHT_GLOBAL_0500,
    DeskType.PREMARKET_BR_0800,
    DeskType.PANORAMA_0900,
    DeskType.THREAT_MONITOR_1130,
    DeskType.ALERTA_TATICO_1200,
    DeskType.FLOW_1330,
    DeskType.REALTIME_VOL_1530,
    DeskType.RISK_MATRIX_1800,
    DeskType.EXEC_SUMMARY_2030,
    DeskType.OVERNIGHT_WATCH_2300,
]

CONDITIONAL_TYPES = frozenset({DeskType.ALERTA_TATICO_1200, DeskType.REALTIME_VOL_1530})

_DEFAULT_MAX_LINES = 40
_DEFAULT_MAX_CHARS = 3500


_VALID_STRINGS = [dt.value for dt in DeskType]


def as_str(desk_type: DeskType | str) -> str:
    """Return normalized string for DeskType or str. Raises ValueError for invalid str."""
    if isinstance(desk_type, DeskType):
        return desk_type.value
    s = str(desk_type).strip()
    for dt in DeskType:
        if dt.value.upper() == s.upper():
            return dt.value
    raise ValueError(f"invalid desk type {desk_type!r}; valid: {_VALID_STRINGS}")


def parse_desk_type(value: str) -> DeskType:
    """Parse string to DeskType. Case-insensitive. Raises ValueError if invalid."""
    s = str(value).strip()
    for dt in DeskType:
        if dt.value.upper() == s.upper():
            return dt
    raise ValueError(f"invalid desk type {value!r}; valid: {_VALID_STRINGS}")


def get_limits(desk_type: DeskType) -> tuple[int, int]:
    """Return (max_lines, max_chars) for the given desk type."""
    entry = LIMITS.get(desk_type)
    if entry:
        return (entry["max_lines"], entry["max_chars"])
    return (_DEFAULT_MAX_LINES, _DEFAULT_MAX_CHARS)


def _assert_contract() -> None:
    """Verify LIMITS contract: all DeskTypes, no duplicates, valid ints > 0."""
    all_types = set(DeskType)
    limit_keys = set(LIMITS)
    if limit_keys != all_types:
        missing = all_types - limit_keys
        extra = limit_keys - all_types
        msg_parts = []
        if missing:
            msg_parts.append(f"missing: {missing}")
        if extra:
            msg_parts.append(f"extra: {extra}")
        raise AssertionError(f"LIMITS must have exactly all DeskTypes; {'; '.join(msg_parts)}")
    if len(LIMITS) != len(all_types):
        raise AssertionError("LIMITS has duplicates")
    for dt, entry in LIMITS.items():
        for key in ("max_lines", "max_chars"):
            val = entry.get(key)
            if not isinstance(val, int) or val <= 0:
                raise AssertionError(f"{dt}.{key} must be int > 0, got {val!r}")


def validate_contract() -> None:
    """Validate the Desk 24H contract. Raises AssertionError on failure."""
    _assert_contract()
