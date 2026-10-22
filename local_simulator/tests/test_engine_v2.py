from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from football_lab.engine import MatchEngine
from football_lab.rules_v2 import RulesV2Config


class _NullStore:
    def save(self, replay: Any, path: Path) -> None:
        return None


class _Press:
    """Outfield presses the ball; the carrier keeps pressing (moves with the ball)."""

    hold = False

    def __init__(self, team_id: int) -> None:
        self.team_id = team_id
        self.observations: list[dict[str, Any]] = []

    def decide(self, obs: dict[str, Any]) -> list[dict[str, Any]]:
        self.observations.append(obs)
        ball = obs["ball"]
        out = []
        for pid in range(5):
            carrier = ball["owner_team"] == self.team_id and ball["owner_player"] == pid
            if carrier and self.hold:
                cmd = ("SET_STANCE", {"stance": 0})
            elif pid == 0:
                cmd = ("MOVE_TO", {"target_x": -50.0 if self.team_id == 0 else 50.0,
                                   "target_y": 0.0})
            else:
                cmd = ("PRESS_BALL", {"intensity": 1.0})
            out.append({"team_id": self.team_id, "player_id": pid,
                        "command_type": cmd[0], "parameters": cmd[1]})
        return out


class _PressHold(_Press):
    hold = True


def _run(**kw: Any) -> dict[str, Any]:
    engine = MatchEngine(11, _Press(0), _PressHold(1), _NullStore(), **kw)
    return engine.run(Path("unused.json"))  # type: ignore[return-value]


def _count(replay: dict[str, Any], kind: str) -> int:
    return sum(e["kind"] == kind for e in replay["events"])


def test_v1_has_no_tackles_v2_does() -> None:
    assert _count(_run(), "TACKLE_SUCCESS") == 0
    v2 = _run(rules_version="v2")
    assert v2["rules_version"] == "v2"
    assert _count(v2, "TACKLE_SUCCESS") > 0


def test_v2_records_params_and_is_deterministic() -> None:
    a = _run(rules_version="v2", rules_config=RulesV2Config(foul_scale=2.0))
    b = _run(rules_version="v2", rules_config=RulesV2Config(foul_scale=2.0))
    assert a["rules_params"]["foul_scale"] == 2.0
    assert a["events"] == b["events"] and a["frames"][-1] == b["frames"][-1]
    assert "rules_params" not in _run()


def test_rules_config_requires_v2() -> None:
    with pytest.raises(ValueError):
        MatchEngine(1, _Press(0), _Press(1), _NullStore(), rules_config=RulesV2Config())


def test_coach_reaches_only_the_coached_team() -> None:
    t0, t1 = _Press(0), _Press(1)
    MatchEngine(3, t0, t1, _NullStore(),
                coaches=(lambda state, team: "press_high", None)).run(Path("unused.json"))
    assert {o["coach"] for o in t0.observations} == {"press_high"}
    assert all("coach" not in o for o in t1.observations)
