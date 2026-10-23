"""Golden decisions: preserve behavior outside explicitly reviewed tactical corrections.

Regenerate after inspecting each intended difference: `.venv/bin/python tests/golden.py`.
Three-match feedback additionally changes 36/750 commands: center shots against
stranded keepers, progressive safe keeper outlets, midfield support and pressing.
Carrying is separately covered with carry-enabled profiles in test_style_tactics.
These legacy fixtures are checked with rush/desperate modes disabled; enabled
modes have explicit scenario coverage in test_rush_tactics.
"""

from __future__ import annotations

import json
import os
import random
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.model import Profile  # noqa: E402
from agent.runtime import Runtime  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "default_style_golden.json"
SEED = 20261022
PRESETS = ("", "go_all_out_attack", "slow_the_tempo", "press_high", "shoot_on_sight")


def _xy(rng: random.Random, sx: float, sy: float) -> dict:
    return {"x": round(rng.uniform(-sx, sx), 3), "y": round(rng.uniform(-sy, sy), 3)}


def payloads(n: int = 150) -> list[dict]:
    rng = random.Random(SEED)
    out = []
    for _ in range(n):
        team = rng.randrange(2)
        players = [
            {
                "playerId": pid,
                "teamId": side,
                "position": _xy(rng, 54, 34),
                "velocity": _xy(rng, 1, 1),
                "stamina": round(rng.uniform(0.1, 1.0), 3),
            }
            for side in (0, 1)
            for pid in range(5)
        ]
        u = rng.random()
        owner = (
            (team, rng.randrange(5))
            if u < 0.4
            else ((1 - team, rng.randrange(1, 5)) if u < 0.8 else None)
        )
        position = players[owner[0] * 5 + owner[1]]["position"] if owner else _xy(rng, 50, 30)
        tick = rng.randrange(2400)
        state = {
            "tick": tick,
            "gameTime": round(tick * 0.05, 2),
            "matchDuration": 120.0,
            "playMode": "OPEN_PLAY",
            "score": {"home": rng.randrange(3), "away": rng.randrange(3)},
            "players": players,
            "ball": {
                "position": dict(position),
                "velocity": {"x": 0, "y": 0} if owner else _xy(rng, 1.5, 1.5),
                "isFree": owner is None,
                "possessionPlayerId": owner[1] if owner else None,
                "possessionTeamId": owner[0] if owner else None,
            },
            "coachInstruction": rng.choice(PRESETS),
        }
        out.append({"gameState": state, "teamId": team, "myPlayers": [0]})
    return out


def responses(profile: Profile | None = None) -> list[list[str]]:
    runtimes = [Runtime(profile, emit=lambda _record: None) for _ in range(5)]
    try:
        rows = []
        for p in payloads():
            row = []
            for pid in range(5):
                rows_payload = {**p, "myPlayers": [pid]}
                row.append(runtimes[pid].handle(rows_payload))
            rows.append(row)
        return rows
    finally:
        for runtime in runtimes:
            runtime.close()


if __name__ == "__main__":
    FIXTURE.parent.mkdir(exist_ok=True)
    with patch.dict(
        os.environ, {"RUSH_ENABLED": "0", "DESPERATE_S": "-1", "DRAW_AT_ANY_COST": "0"}
    ):
        FIXTURE.write_text(json.dumps({"seed": SEED, "responses": responses()}, indent=1) + "\n")
    print(f"wrote {FIXTURE}")
