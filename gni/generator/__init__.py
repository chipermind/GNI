"""GNI content generators (V1).

Per-template, deterministic content generators. Each generator is import-safe,
side-effect free, and produces a payload compatible with the editorial guards
in ``gni.publisher.guards``.
"""
from gni.generator.alerta import generate_alerta
from gni.generator.briefing import generate_briefing
from gni.generator.flash import generate_flash

__all__ = ["generate_alerta", "generate_briefing", "generate_flash"]
