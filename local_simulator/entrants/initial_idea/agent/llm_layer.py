"""LLM tie-breaker disabled for local benchmark. Returns None (rules-only mode)."""
from __future__ import annotations

from typing import Optional

from .model import Candidate, World


def choose(w: World, cands: list[Candidate]) -> Optional[int]:
    return None
