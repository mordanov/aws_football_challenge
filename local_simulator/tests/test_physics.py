"""Failing-first physics tests for M2 engine (Task 3).

Run BEFORE implementing physics.py — every test here must fail until that
module exists. After implementation (Task 4) every test must pass.
"""
import math
import unittest

from football_lab.match_contracts import (
    EngineBallState,
    EnginePlayerState,
    EngineState,
    Intent,
)
from football_lab.physics import (
    FREE_BALL_DAMPING_PER_STEP,
    PICKUP_RADIUS,
    PITCH_HALF_L,
    PITCH_HALF_W,
    step,
)


def _player(
    team_id: int,
    player_id: int,
    x: float = 0.0,
    y: float = 0.0,
    stamina: float = 1.0,
) -> EnginePlayerState:
    return {
        "team_id": team_id,
        "player_id": player_id,
        "x": x,
        "y": y,
        "vx": 0.0,
        "vy": 0.0,
        "stamina": stamina,
    }


def _ball(
    x: float = 0.0,
    y: float = 0.0,
    vx: float = 0.0,
    vy: float = 0.0,
    owner_team: int | None = None,
    owner_player: int | None = None,
    in_flight: bool = False,
) -> EngineBallState:
    return {
        "x": x,
        "y": y,
        "z": 0.0,
        "vx": vx,
        "vy": vy,
        "vz": 0.0,
        "owner_team": owner_team,
        "owner_player": owner_player,
        "in_flight": in_flight,
    }


def _state(
    players: list[EnginePlayerState] | None = None,
    ball: EngineBallState | None = None,
    tick: int = 0,
    play_mode: str = "OPEN_PLAY",
) -> EngineState:
    if players is None:
        players = [_player(0, i, x=-30.0 + i * 10) for i in range(5)] + [
            _player(1, i, x=30.0 - i * 10) for i in range(5)
        ]
    if ball is None:
        ball = _ball()
    return {
        "tick": tick,
        "play_mode": play_mode,
        "mode_team": None,
        "players": players,
        "ball": ball,
        "score": [0, 0],
    }


def _move_intent(
    team_id: int,
    player_id: int,
    target_x: float,
    target_y: float,
) -> Intent:
    return {
        "team_id": team_id,
        "player_id": player_id,
        "command_type": "MOVE_TO",
        "parameters": {"target_x": target_x, "target_y": target_y},
        "is_one_shot": False,
        "executed": False,
    }


def _press_intent(team_id: int, player_id: int) -> Intent:
    return {
        "team_id": team_id,
        "player_id": player_id,
        "command_type": "PRESS_BALL",
        "parameters": {"intensity": 1.0},
        "is_one_shot": False,
        "executed": False,
    }


def _stance_intent(team_id: int, player_id: int) -> Intent:
    return {
        "team_id": team_id,
        "player_id": player_id,
        "command_type": "SET_STANCE",
        "parameters": {"stance": 0},
        "is_one_shot": False,
        "executed": False,
    }


def _default_intents(state: EngineState) -> list[Intent]:
    return [
        _stance_intent(p["team_id"], p["player_id"]) for p in state["players"]
    ]


class TestPlayerMovement(unittest.TestCase):
    def test_move_to_reaches_nearby_target(self) -> None:
        """Player at (0,0) moving to (5,0) closes within 1 unit after 40 steps."""
        players = [_player(0, 0, x=0.0, y=0.0)] + [
            _player(0, i, x=-30.0, y=0.0) for i in range(1, 5)
        ] + [_player(1, i, x=30.0, y=0.0) for i in range(5)]
        state = _state(players=players)
        intents = [_move_intent(0, 0, 5.0, 0.0)] + [
            _stance_intent(0, i) for i in range(1, 5)
        ] + [_stance_intent(1, i) for i in range(5)]
        for _ in range(40):
            state, _ = step(state, intents)
        mover = next(p for p in state["players"] if p["team_id"] == 0 and p["player_id"] == 0)
        self.assertLess(abs(mover["x"] - 5.0), 1.0)

    def test_player_clamped_to_pitch(self) -> None:
        """Player cannot move outside pitch bounds."""
        players = [_player(0, 0, x=54.0, y=0.0)] + [
            _player(0, i, x=-30.0) for i in range(1, 5)
        ] + [_player(1, i, x=30.0) for i in range(5)]
        state = _state(players=players)
        intents = [_move_intent(0, 0, 100.0, 0.0)] + [
            _stance_intent(0, i) for i in range(1, 5)
        ] + [_stance_intent(1, i) for i in range(5)]
        for _ in range(40):
            state, _ = step(state, intents)
        mover = next(p for p in state["players"] if p["team_id"] == 0 and p["player_id"] == 0)
        self.assertLessEqual(mover["x"], PITCH_HALF_L)

    def test_player_y_clamped_to_pitch(self) -> None:
        """Player cannot move outside pitch y bounds."""
        players = [_player(0, 0, x=0.0, y=34.0)] + [
            _player(0, i, x=-30.0) for i in range(1, 5)
        ] + [_player(1, i, x=30.0) for i in range(5)]
        state = _state(players=players)
        intents = [_move_intent(0, 0, 0.0, 100.0)] + [
            _stance_intent(0, i) for i in range(1, 5)
        ] + [_stance_intent(1, i) for i in range(5)]
        for _ in range(40):
            state, _ = step(state, intents)
        mover = next(p for p in state["players"] if p["team_id"] == 0 and p["player_id"] == 0)
        self.assertLessEqual(mover["y"], PITCH_HALF_W)


class TestBallPhysics(unittest.TestCase):
    def test_launched_pass_arrives_at_target(self) -> None:
        """Ball launched toward (20, 25) from (-10, 25) enters pickup radius.

        Uses y=25 so no default player (all at y=0) interferes.
        """
        dx = 20.0 - (-10.0)
        dist = math.hypot(dx, 0.0)
        speed = 1.5  # PASS GROUND speed
        vx = dx / dist * speed
        ball = _ball(x=-10.0, y=25.0, vx=vx, vy=0.0, in_flight=True)
        state = _state(ball=ball)
        intents = _default_intents(state)
        # 30 units at 1.5 u/step = 20 steps to reach target; check at step 21
        for _ in range(21):
            state, _ = step(state, intents)
        b = state["ball"]
        dist_to_target = math.hypot(b["x"] - 20.0, b["y"] - 25.0)
        self.assertLess(dist_to_target, PICKUP_RADIUS + 2.0)

    def test_free_ball_decays(self) -> None:
        """Free rolling ball velocity decays by factor 0.82 over 40 steps.

        Ball at y=25 so no default player (all at y=0) picks it up.
        """
        ball = _ball(x=0.0, y=25.0, vx=1.0, vy=0.0)
        state = _state(ball=ball)
        intents = _default_intents(state)
        for _ in range(40):
            state, _ = step(state, intents)
        expected = 1.0 * (FREE_BALL_DAMPING_PER_STEP ** 40)
        self.assertAlmostEqual(state["ball"]["vx"], expected, places=5)

    def test_free_ball_approx_0_82_after_decision(self) -> None:
        """After 40 steps free ball velocity ≈ 0.82 of original."""
        ball = _ball(x=0.0, y=25.0, vx=1.0, vy=0.0)
        state = _state(ball=ball)
        intents = _default_intents(state)
        for _ in range(40):
            state, _ = step(state, intents)
        self.assertAlmostEqual(state["ball"]["vx"], 0.82, delta=0.02)


class TestPossession(unittest.TestCase):
    def test_possession_pickup_on_contact(self) -> None:
        """Free ball within PICKUP_RADIUS of player gives possession after one step."""
        ball = _ball(x=2.0, y=0.0)  # 2.0 < PICKUP_RADIUS (2.1)
        players = [_player(0, 0, x=0.0, y=0.0)] + [
            _player(0, i, x=-30.0) for i in range(1, 5)
        ] + [_player(1, i, x=30.0) for i in range(5)]
        state = _state(players=players, ball=ball)
        intents = _default_intents(state)
        state, _ = step(state, intents)
        self.assertEqual(state["ball"]["owner_team"], 0)
        self.assertEqual(state["ball"]["owner_player"], 0)

    def test_no_pickup_outside_radius(self) -> None:
        """Ball 3m from nearest player stays free."""
        ball = _ball(x=3.5, y=0.0)  # > PICKUP_RADIUS
        players = [_player(0, 0, x=0.0, y=0.0)] + [
            _player(0, i, x=-30.0) for i in range(1, 5)
        ] + [_player(1, i, x=30.0) for i in range(5)]
        state = _state(players=players, ball=ball)
        intents = _default_intents(state)
        state, _ = step(state, intents)
        self.assertIsNone(state["ball"]["owner_team"])


class TestBoundaryAndGoal(unittest.TestCase):
    def test_goal_detected(self) -> None:
        """Ball crossing x > 55 with |y| <= 4.5 produces GOAL event for team 0."""
        ball = _ball(x=54.0, y=0.0, vx=2.5)
        state = _state(ball=ball)
        intents = _default_intents(state)
        state, events = step(state, intents)
        kinds = {e["kind"] for e in events}
        self.assertIn("GOAL", kinds)
        goal_ev = next(e for e in events if e["kind"] == "GOAL")
        self.assertEqual(goal_ev["team_id"], 0)

    def test_no_goal_outside_posts(self) -> None:
        """Ball crossing x > 55 with |y| > 4.5 is GOAL_KICK, not GOAL."""
        ball = _ball(x=54.0, y=5.0, vx=2.5)
        state = _state(ball=ball)
        intents = _default_intents(state)
        state, events = step(state, intents)
        kinds = {e["kind"] for e in events}
        self.assertNotIn("GOAL", kinds)
        self.assertIn("GOAL_KICK", kinds)

    def test_throw_in_on_touchline_exit(self) -> None:
        """Ball crossing |y| > 35 produces THROW_IN event."""
        ball = _ball(x=0.0, y=34.0, vy=1.5)
        state = _state(ball=ball)
        intents = _default_intents(state)
        state, events = step(state, intents)
        kinds = {e["kind"] for e in events}
        self.assertIn("THROW_IN", kinds)


class TestStamina(unittest.TestCase):
    def test_stamina_drains_while_pressing(self) -> None:
        """Player pressing for 40 steps loses 0.005 * 40 = 0.20 stamina."""
        players = [_player(0, 0, x=0.0, y=0.0, stamina=1.0)] + [
            _player(0, i, x=-30.0) for i in range(1, 5)
        ] + [_player(1, i, x=30.0) for i in range(5)]
        ball = _ball(x=5.0, y=0.0)
        state = _state(players=players, ball=ball)
        intents = [_press_intent(0, 0)] + [
            _stance_intent(0, i) for i in range(1, 5)
        ] + [_stance_intent(1, i) for i in range(5)]
        for _ in range(40):
            state, _ = step(state, intents)
        mover = next(p for p in state["players"] if p["team_id"] == 0 and p["player_id"] == 0)
        self.assertAlmostEqual(mover["stamina"], 0.80, delta=0.02)

    def test_stamina_recovers_on_stance(self) -> None:
        """Player on SET_STANCE for 40 steps recovers 0.002 * 40 = 0.08 stamina."""
        players = [_player(0, 0, x=0.0, y=0.0, stamina=0.5)] + [
            _player(0, i, x=-30.0) for i in range(1, 5)
        ] + [_player(1, i, x=30.0) for i in range(5)]
        state = _state(players=players)
        intents = _default_intents(state)
        for _ in range(40):
            state, _ = step(state, intents)
        mover = next(p for p in state["players"] if p["team_id"] == 0 and p["player_id"] == 0)
        self.assertAlmostEqual(mover["stamina"], 0.58, delta=0.02)


class TestDeterminism(unittest.TestCase):
    def test_deterministic_step(self) -> None:
        """Same state + intents produces identical state after 40 steps."""
        state_a = _state()
        state_b = _state()
        intents = _default_intents(state_a)
        for _ in range(40):
            state_a, _ = step(state_a, intents)
        for _ in range(40):
            state_b, _ = step(state_b, intents)
        self.assertEqual(state_a["players"], state_b["players"])
        self.assertEqual(state_a["ball"], state_b["ball"])
