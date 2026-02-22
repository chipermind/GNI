"""
Minimal template loader for desk report types.
Maps DeskType string names to .txt files in this package.
"""
from pathlib import Path

_THIS_DIR = Path(__file__).resolve().parent

_EXPECTED_NAMES = (
    "OVERNIGHT_GLOBAL_0500",
    "PREMARKET_BR_0800",
    "PANORAMA_0900",
    "THREAT_MONITOR_1130",
    "ALERTA_TATICO_1200",
    "FLOW_1330",
    "REALTIME_VOL_1530",
    "RISK_MATRIX_1800",
    "EXEC_SUMMARY_2030",
    "OVERNIGHT_WATCH_2300",
)


def get_template_path(desk_type: str) -> Path:
    """Return Path to template file for desk_type. Raises if file missing."""
    name = str(desk_type).strip()
    path = _THIS_DIR / f"{name}.txt"
    if not path.exists():
        raise FileNotFoundError(
            f"Template file not found: {path} (expected one of: {', '.join(n + '.txt' for n in _EXPECTED_NAMES)})"
        )
    return path


def load_template(desk_type: str) -> str:
    """Load template content for desk_type. Raises if file missing."""
    path = get_template_path(desk_type)
    return path.read_text(encoding="utf-8")
