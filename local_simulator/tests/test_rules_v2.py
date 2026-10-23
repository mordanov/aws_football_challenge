from __future__ import annotations

from typing import Any

import pytest

from football_lab.match_contracts import Intent
from football_lab.rules_v2 import RulesV2Config, contest

TICK = 100


def test_config_defaults_and_validation() -> None:
    cfg = RulesV2Config()
    assert (cfg.foul_scale, cfg.p_foul, cfg.p_win_slide, cfg.stun_steps) == (1.0, 0.25, 0.55, 40)
    assert cfg.send_off_fouls == 3 and cfg.p_penalty_goal == 0.75
    with pytest.raises(ValueError):
        RulesV2Config(foul_scale=-0.1)


class _Seq:
    def __init__(self, *values: float) -> None:
        self.values = list(values)

    def random(self) -> float:
        if not self.values:
            raise AssertionError("unexpected RNG draw")
        return self.values.pop(0)


def _pl(team: int, pid: int, x: float, y: float = 0.0, **extra: Any) -> dict[str, Any]:
    return {"team_id": team, "player_id": pid, "x": x, "y": y,
            "vx": 0.0, "vy": 0.0, "stamina": 1.0, **extra}


def _intent(team: int, pid: int, kind: str, **params: Any) -> Intent:
    return {"team_id": team, "player_id": pid, "command_type": kind,
            "parameters": params, "is_one_shot": kind == "SLIDE_TACKLE", "executed": False}


def _run(rng: _Seq, *, carrier_x: float = 10.0, tackler_x: float = 11.5,
         kind: str = "SLIDE_TACKLE", target: int = 4, cfg: RulesV2Config | None = None,
         **tackler_extra: Any):
    carrier = _pl(0, 4, carrier_x)
    tackler = _pl(1, 2, tackler_x, **tackler_extra)
    players = [carrier, tackler]
    ball = {"x": carrier_x, "y": 0.0, "z": 0.0, "vx": 0.0, "vy": 0.0, "vz": 0.0,
            "owner_team": 0, "owner_player": 4, "in_flight": False}
    score = [0, 0]
    params = {"target_player_id": target} if kind == "SLIDE_TACKLE" else {"intensity": 1.0}
    intent = _intent(1, 2, kind, **params)
    events = contest(players, ball, score, [intent], TICK,  # type: ignore[arg-type]
                     rng=rng, cfg=cfg or RulesV2Config())
    assert not rng.values, "RNG values left unused"
    return [e["kind"] for e in events], tackler, ball, score, intent


def test_slide_foul_outside_box_stuns_and_keeps_possession() -> None:
    kinds, tackler, ball, _, intent = _run(_Seq(0.1))
    assert kinds == ["FOUL"]
    assert tackler["fouls"] == 1 and tackler["stunned_until"] == TICK + 40
    assert (ball["owner_team"], ball["owner_player"]) == (0, 4)
    assert intent["executed"] is True


def test_slide_win_gives_tackler_the_ball() -> None:
    kinds, _, ball, _, _ = _run(_Seq(0.9, 0.3))
    assert kinds == ["TACKLE_SUCCESS"]
    assert (ball["owner_team"], ball["owner_player"]) == (1, 2)


def test_slide_miss_resolves_once() -> None:
    kinds, _, ball, _, intent = _run(_Seq(0.9, 0.9))
    assert kinds == [] and ball["owner_team"] == 0 and intent["executed"] is True


def test_out_of_range_draws_nothing() -> None:
    kinds, _, _, _, intent = _run(_Seq(), tackler_x=20.0)
    assert kinds == [] and intent["executed"] is False


def test_from_behind_doubles_foul_probability() -> None:
    assert _run(_Seq(0.4), tackler_x=8.5)[0] == ["FOUL"]       # behind: p = 0.5
    assert _run(_Seq(0.4, 0.9), tackler_x=11.5)[0] == []        # front: p = 0.25


def test_foul_scale_multiplies_probability() -> None:
    assert _run(_Seq(0.4), cfg=RulesV2Config(foul_scale=2.0))[0] == ["FOUL"]


def test_penalty_scored_and_missed_restart_at_centre() -> None:
    kinds, _, ball, score, _ = _run(_Seq(0.1, 0.5), carrier_x=45.0, tackler_x=46.5)
    assert kinds == ["FOUL", "PENALTY", "GOAL"] and score == [1, 0]
    assert (ball["x"], ball["y"], ball["owner_team"]) == (0.0, 0.0, None)
    kinds, _, ball, score, _ = _run(_Seq(0.1, 0.9), carrier_x=45.0, tackler_x=46.5)
    assert kinds == ["FOUL", "PENALTY"] and score == [0, 0]
    assert (ball["x"], ball["y"], ball["owner_team"]) == (0.0, 0.0, None)


def test_third_foul_sends_off_to_touchline() -> None:
    kinds, tackler, _, _, _ = _run(_Seq(0.1), fouls=2)
    assert kinds == ["FOUL", "SEND_OFF"]
    assert tackler["sent_off"] is True and tackler["y"] == 35.0
    assert "stunned_until" not in tackler


def test_press_challenge_rarely_wins_and_never_fouls() -> None:
    kinds, _, ball, _, _ = _run(_Seq(0.005), kind="PRESS_BALL")
    assert kinds == ["TACKLE_SUCCESS"] and ball["owner_team"] == 1
    assert _run(_Seq(0.5), kind="PRESS_BALL")[0] == []


def test_inactive_tackler_and_wrong_target_are_ignored() -> None:
    assert _run(_Seq(), stunned_until=TICK + 5)[0] == []
    kinds, _, _, _, intent = _run(_Seq(), target=3)
    assert kinds == [] and intent["executed"] is False
