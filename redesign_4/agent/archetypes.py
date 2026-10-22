"""Strategy archetypes (spec §5) and AWS-like opponent stand-ins (spec §4)."""
from __future__ import annotations

from dataclasses import replace

from .model import DEFAULT_SETTINGS, DEFAULT_STYLE, Profile, Style


def _p(name: str, style: Style, **settings: float) -> Profile:
    return Profile(
        version=f"archetype-{name}",
        settings=replace(DEFAULT_SETTINGS, **settings),
        style=style,
    )


ARCHETYPES: dict[str, Profile] = {
    "balanced": _p("balanced", DEFAULT_STYLE),
    "press_aggressive": _p(
        "press_aggressive", Style(1.0, 3, 0.6, 0.8, "1-1-2"), press_radius=30.0
    ),
    "park_the_bus": _p("park_the_bus", Style(0.1, 1, 0.4, 0.3, "2-1-1"), press_radius=12.0),
    "time_waster": _p(
        "time_waster", Style(0.5, 1, 0.2, 0.0, "1-2-1"), pass_margin=4.5, shot_range=20.0
    ),
    "hard_tackle": _p("hard_tackle", Style(0.7, 2, 1.0, 0.5, "1-1-2"), press_radius=26.0),
}

# Stand-ins for the unknown AWS practice bots; they only widen the opponent pool.
STAND_INS: dict[str, Profile] = {
    "aws_like_balanced": _p(
        "aws_like_balanced", DEFAULT_STYLE, press_radius=18.0, support_width=14.0
    ),
    "aws_like_attacking": _p(
        "aws_like_attacking", Style(1.0, 3, 0.3, 0.9, "1-1-2"), shot_range=33.0,
        press_radius=28.0,
    ),
    "aws_like_defensive": _p(
        "aws_like_defensive", Style(0.3, 1, 0.0, 0.3, "2-1-1"), press_radius=15.0
    ),
}
