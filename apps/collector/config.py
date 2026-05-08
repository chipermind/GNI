"""
Load data/sources.yaml and list RSS sources. Resolves ${VAR} in URLs from environment.
"""
import os
import re
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore


def _resolve_env(value: str) -> str:
    """Replace ${VAR} with os.environ.get(VAR, '')."""
    if not isinstance(value, str):
        return value
    pattern = re.compile(r"\$\{([^}]+)\}")
    return pattern.sub(lambda m: os.environ.get(m.group(1), ""), value)


def _sources_path() -> Path:
    path = os.environ.get("RSS_SOURCES_PATH") or os.environ.get("DATA_SOURCES_PATH")
    if path:
        return Path(path)
    # Default: data/sources.yaml relative to repo root (parent of apps/)
    return Path(__file__).resolve().parent.parent.parent / "data" / "sources.yaml"


def load_sources_yaml() -> dict[str, Any]:
    """Load and parse sources YAML; return raw dict (categories -> list of {name, url})."""
    path = _sources_path()
    if not path.exists():
        return {}
    if yaml is None:
        raise RuntimeError("PyYAML required: pip install pyyaml")
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data


def list_sources() -> list[dict[str, str]]:
    """
    Return list of configured RSS sources. Each item: {category, name, url}.
    URLs containing ${VAR} are resolved from environment (e.g. CNBC placeholder).
    """
    raw = load_sources_yaml()
    out: list[dict[str, str]] = []
    for category, items in raw.items():
        if not isinstance(items, list):
            continue
        for entry in items:
            if not isinstance(entry, dict):
                continue
            name = entry.get("name") or ""
            url = entry.get("url") or ""
            url = _resolve_env(url)
            out.append({"category": category, "name": name, "url": url})
    return out


def print_sources() -> None:
    """Print all configured RSS sources (for scripts)."""
    for s in list_sources():
        print(f"  [{s['category']}] {s['name']}: {s['url'] or '(url not set)'}")
