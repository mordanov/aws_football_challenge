"""Bounds and validation taken from redesign_4's frozen profile validator."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

from .contracts import Bounds, Settings

AGENT_DIR = Path(__file__).resolve().parents[3] / "entrants" / "redesign_4" / "agent"
_PKG = "_redesign4_agent"


def _load() -> ModuleType:
    if f"{_PKG}.profile" in sys.modules:
        return sys.modules[f"{_PKG}.profile"]
    spec = importlib.util.spec_from_file_location(
        _PKG, AGENT_DIR / "__init__.py", submodule_search_locations=[str(AGENT_DIR)]
    )
    assert spec is not None and spec.loader is not None
    pkg = importlib.util.module_from_spec(spec)
    sys.modules[_PKG] = pkg
    spec.loader.exec_module(pkg)
    return importlib.import_module(f"{_PKG}.profile")


def load_bounds() -> Bounds:
    return {k: (float(lo), float(hi)) for k, (lo, hi) in _load().BOUNDS.items()}


def validate_settings(settings: Settings) -> None:
    """Raise ValueError unless the frozen validator accepts a profile with these settings."""
    _load().parse_profile({"version": "candidate", "settings": dict(settings), "opponents": None})
