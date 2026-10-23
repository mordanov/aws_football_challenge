"""redesign_4_v2: the built entrant is in sync with redesign_4 and every variant loads."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from football_lab.cli_campaign import _load_entrant_spec, _runner_env
from football_lab.entrant_contracts import BudgetSpec
from football_lab.evolution.integrity import hash_tree
from football_lab.team_runner import TeamRunner

SIM = Path(__file__).resolve().parents[1]
ENTRANT = SIM / "entrants" / "redesign_4_v2"
SOURCE = SIM.parent / "redesign_4" / "agent"


def _filtered_hash(src: Path, dst: Path, *skip: str) -> str:
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", *skip))
    return hash_tree(dst)


def _state() -> dict:
    players = [
        {"team_id": tid, "player_id": pid, "x": float((-40 + pid * 8) * (1 - 2 * tid)),
         "y": 0.0, "vx": 0.0, "vy": 0.0, "stamina": 1.0}
        for pid in range(5) for tid in range(2)
    ]
    return {
        "tick": 0, "play_mode": "OPEN_PLAY", "mode_team": None, "players": players,
        "ball": {"x": 0.0, "y": 0.0, "z": 0.0, "vx": 0.0, "vy": 0.0, "vz": 0.0,
                 "owner_team": None, "owner_player": None, "in_flight": False},
        "score": [0, 1],
    }


def test_variants_cover_archetypes_formations_and_pool() -> None:
    variants = json.loads((ENTRANT / "variants.json").read_text())["variants"]
    ids = [v["id"] for v in variants]
    assert len(ids) == len(set(ids)) == 40
    for v in variants:
        profile = json.loads((ENTRANT / v["profile"]).read_text())
        assert profile["version"] == v["id"]
        assert profile["coach_policy"] == ("rules" if "_rules" in v["id"] else "none")
    coaches = {v["id"]: v["coach"] for v in variants}
    assert coaches["r4v2_balanced_extcoach"] == "scripted"
    assert sum(c is not None for c in coaches.values()) == 1
    park = json.loads((ENTRANT / "profiles" / "r4v2_park_the_bus_rules_f112.json").read_text())
    assert park["style"]["formation"] == "1-1-2" and park["style"]["block_depth"] == 0.1


def test_agent_copy_matches_redesign_4_and_hash_is_current(tmp_path: Path) -> None:
    assert _filtered_hash(SOURCE, tmp_path / "src", "advisor") == _filtered_hash(
        ENTRANT / "agent", tmp_path / "copy"
    )
    recorded = json.loads((ENTRANT / "entrant.json").read_text())["entrant_hash"]
    assert _filtered_hash(ENTRANT, tmp_path / "entrant", "entrant.json") == recorded


@pytest.mark.parametrize(
    "vid", ["r4v2_hard_tackle_rules", "r4v2_park_the_bus_none", "r4v2_time_waster_rules_f112"]
)
def test_variant_decides_one_tick(tmp_path: Path, vid: str) -> None:
    spec = _load_entrant_spec(vid)
    budget: BudgetSpec = {"startup_timeout_s": 15.0, "request_timeout_s": 10.0,
                          "output_size_limit": 65536}
    with TeamRunner(spec, team_id=0, budget=budget, work_dir=tmp_path,
                    env=_runner_env(spec)) as runner:
        cmds = runner.decide(_state(), coach="press_high")
    assert len(cmds) == 5
    assert any(c["command_type"] != "SET_STANCE" for c in cmds)  # not the error fallback
