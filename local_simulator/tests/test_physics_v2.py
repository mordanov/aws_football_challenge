from __future__ import annotations

from typing import Any

import pytest

from football_lab.match_contracts import EngineState, Intent
from football_lab.physics import is_inactive, step


def _p(team: int, pid: int, x: float, y: float = 0.0, **extra: Any) -> dict[str, Any]:
    return {"team_id": team, "player_id": pid, "x": x, "y": y,
            "vx": 0.0, "vy": 0.0, "stamina": 1.0, **extra}


def _state(players: list[dict[str, Any]], ball: dict[str, Any] | None = None,
           tick: int = 10) -> EngineState:
    b = {"x": 0.0, "y": 0.0, "z": 0.0, "vx": 0.0, "vy": 0.0, "vz": 0.0,
         "owner_team": None, "owner_player": None, "in_flight": False}
    b.update(ball or {})
    return {"tick": tick, "play_mode": "OPEN_PLAY", "mode_team": None,
            "players": players, "ball": b, "score": [0, 0]}  # type: ignore[typeddict-item]


def _move(team: int, pid: int, x: float, y: float) -> Intent:
    return {"team_id": team, "player_id": pid, "command_type": "MOVE_TO",
            "parameters": {"target_x": x, "target_y": y}, "is_one_shot": False,
            "executed": False}


def test_is_inactive() -> None:
    assert not is_inactive(_p(0, 1, 0.0), 5)  # type: ignore[arg-type]
    assert is_inactive(_p(0, 1, 0.0, stunned_until=5), 5)  # type: ignore[arg-type]
    assert not is_inactive(_p(0, 1, 0.0, stunned_until=5), 6)  # type: ignore[arg-type]
    assert is_inactive(_p(0, 1, 0.0, sent_off=True), 10**6)  # type: ignore[arg-type]


def test_stunned_player_does_not_move_until_stun_expires() -> None:
    s = _state([_p(0, 1, 0.0, stunned_until=12)], tick=10)
    s, _ = step(s, [_move(0, 1, 10.0, 0.0)])  # tick 11: inactive
    s, _ = step(s, [_move(0, 1, 10.0, 0.0)])  # tick 12: inactive
    assert s["players"][0]["x"] == 0.0
    s, _ = step(s, [_move(0, 1, 10.0, 0.0)])  # tick 13: moves
    assert s["players"][0]["x"] > 0.0


def test_sent_off_player_cannot_pick_up_ball() -> None:
    s = _state([_p(0, 1, 0.0, sent_off=True)], ball={"x": 0.5})
    s, _ = step(s, [])
    assert s["ball"]["owner_team"] is None


def test_contest_hook_sees_moved_players_and_ball_follows_new_owner() -> None:
    seen: dict[str, Any] = {}

    def hook(players, ball, score, intents, tick):  # type: ignore[no-untyped-def]
        seen["x"], seen["tick"] = players[0]["x"], tick
        ball["owner_team"], ball["owner_player"] = 1, 2
        return [{"tick": tick, "kind": "TACKLE_SUCCESS", "team_id": 1, "player_id": 2,
                 "x": None, "y": None}]

    players = [_p(0, 1, 0.0), _p(1, 2, 20.0, 5.0)]
    s = _state(players, ball={"x": 0.0, "owner_team": 0, "owner_player": 1}, tick=10)
    s, events = step(s, [_move(0, 1, 10.0, 0.0)], contest=hook)
    assert seen["tick"] == 11 and seen["x"] > 0.0
    assert [e["kind"] for e in events] == ["TACKLE_SUCCESS"]
    assert (s["ball"]["x"], s["ball"]["y"]) == (20.0, 5.0)


def _shoot(team: int, pid: int, aim: str) -> Intent:
    return {"team_id": team, "player_id": pid, "command_type": "SHOOT",
            "parameters": {"aim_location": aim, "power": 1.0}, "is_one_shot": True,
            "executed": False}


def _shot_window(shooter_team: int, aim: str, **kw: Any) -> tuple[EngineState, list[str]]:
    """Shooter at ±30 owns the ball; opposing keeper (pid 0) stands at the goal centre."""
    s = 1 if shooter_team == 0 else -1
    keeper_team = 1 - shooter_team
    players = [_p(shooter_team, 1, s * 30.0), _p(keeper_team, 0, s * 50.0),
               _p(shooter_team, 2, 0.0, -30.0), _p(keeper_team, 1, 0.0, 30.0)]
    state = _state(players, ball={"x": s * 30.0, "owner_team": shooter_team,
                                  "owner_player": 1})
    intents = [_shoot(shooter_team, 1, aim)]
    kinds: list[str] = []
    for _ in range(40):
        state, events = step(state, intents, **kw)
        kinds += [e["kind"] for e in events]
    return state, kinds


@pytest.mark.parametrize("aim", ["BL", "TR"])
def test_honour_aim_beats_centred_keeper(aim: str) -> None:
    state, kinds = _shot_window(0, aim, honour_aim=True)
    assert "GOAL" in kinds and state["score"] == [1, 0]


def test_honour_aim_center_is_caught_by_centred_keeper() -> None:
    state, kinds = _shot_window(0, "CENTER", honour_aim=True)
    assert "GOAL" not in kinds
    assert (state["ball"]["owner_team"], state["ball"]["owner_player"]) == (1, 0)


def test_default_ignores_aim_so_centred_keeper_catches() -> None:
    state, kinds = _shot_window(0, "BL")
    assert "GOAL" not in kinds
    assert (state["ball"]["owner_team"], state["ball"]["owner_player"]) == (1, 0)


def test_honour_aim_team1_shoots_toward_negative_x() -> None:
    state, kinds = _shot_window(1, "BR", honour_aim=True)
    assert "GOAL" in kinds and state["score"] == [0, 1]
