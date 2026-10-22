"""Failing-first action-validation tests for M2 (Task 5).

Every test must fail (ImportError) until actions.py is implemented.
After implementation (Task 6) every test must pass.
"""
import unittest

from football_lab.actions import ONE_SHOT_COMMANDS, resolve_command
from football_lab.match_contracts import EngineBallState, EnginePlayerState, EngineState


def _player(team_id: int, player_id: int, x: float = 0.0, y: float = 0.0) -> EnginePlayerState:
    return {
        "team_id": team_id,
        "player_id": player_id,
        "x": x,
        "y": y,
        "vx": 0.0,
        "vy": 0.0,
        "stamina": 1.0,
    }


def _ball(owner_team: int | None = None, owner_player: int | None = None) -> EngineBallState:
    return {
        "x": 0.0,
        "y": 0.0,
        "z": 0.0,
        "vx": 0.0,
        "vy": 0.0,
        "vz": 0.0,
        "owner_team": owner_team,
        "owner_player": owner_player,
        "in_flight": False,
    }


def _state(owner_team: int | None = None, owner_player: int | None = None) -> EngineState:
    players = [_player(0, i, x=-20.0 + i * 5) for i in range(5)] + [
        _player(1, i, x=20.0 - i * 5) for i in range(5)
    ]
    return {
        "tick": 0,
        "play_mode": "OPEN_PLAY",
        "mode_team": None,
        "players": players,
        "ball": _ball(owner_team=owner_team, owner_player=owner_player),
        "score": [0, 0],
    }


def _cmd(
    command_type: str,
    team_id: int = 0,
    player_id: int = 1,
    **params,
):
    return {
        "team_id": team_id,
        "player_id": player_id,
        "command_type": command_type,
        "parameters": params,
    }


class TestMoveToValidation(unittest.TestCase):
    def test_move_to_accepted(self) -> None:
        """MOVE_TO with valid target is accepted."""
        state = _state()
        intent, result = resolve_command(_cmd("MOVE_TO", target_x=10.0, target_y=5.0), state)
        self.assertTrue(result["accepted"])
        self.assertIsNone(result["rejection_reason"])
        self.assertEqual(intent["command_type"], "MOVE_TO")

    def test_move_to_out_of_bounds_rejected(self) -> None:
        """MOVE_TO with target outside pitch is rejected."""
        state = _state()
        intent, result = resolve_command(_cmd("MOVE_TO", target_x=100.0, target_y=0.0), state)
        self.assertFalse(result["accepted"])
        self.assertEqual(result["rejection_reason"], "target_out_of_bounds")
        # Fallback intent must be SET_STANCE
        self.assertEqual(intent["command_type"], "SET_STANCE")

    def test_move_to_y_out_of_bounds_rejected(self) -> None:
        """MOVE_TO with y outside pitch is rejected."""
        state = _state()
        intent, result = resolve_command(_cmd("MOVE_TO", target_x=0.0, target_y=50.0), state)
        self.assertFalse(result["accepted"])
        self.assertEqual(result["rejection_reason"], "target_out_of_bounds")


class TestPassValidation(unittest.TestCase):
    def test_pass_to_self_rejected(self) -> None:
        """PASS to own player_id is rejected."""
        state = _state(owner_team=0, owner_player=1)
        intent, result = resolve_command(
            _cmd("PASS", player_id=1, target_player_id=1, type="GROUND"), state
        )
        self.assertFalse(result["accepted"])
        self.assertEqual(result["rejection_reason"], "pass_to_self")

    def test_pass_unknown_type_rejected(self) -> None:
        """PASS with unknown type is rejected."""
        state = _state(owner_team=0, owner_player=1)
        intent, result = resolve_command(
            _cmd("PASS", player_id=1, target_player_id=2, type="LASER"), state
        )
        self.assertFalse(result["accepted"])
        self.assertEqual(result["rejection_reason"], "unknown_pass_type")

    def test_pass_valid_accepted(self) -> None:
        """PASS GROUND to another teammate is accepted."""
        state = _state(owner_team=0, owner_player=1)
        intent, result = resolve_command(
            _cmd("PASS", player_id=1, target_player_id=2, type="GROUND"), state
        )
        self.assertTrue(result["accepted"])
        self.assertIsNone(result["rejection_reason"])


class TestShootValidation(unittest.TestCase):
    def test_shoot_bad_aim_rejected(self) -> None:
        """SHOOT with invalid aim_location is rejected."""
        state = _state(owner_team=0, owner_player=1)
        intent, result = resolve_command(
            _cmd("SHOOT", player_id=1, aim_location="SIDE", power=0.8), state
        )
        self.assertFalse(result["accepted"])
        self.assertEqual(result["rejection_reason"], "bad_aim_location")

    def test_shoot_valid_accepted(self) -> None:
        """SHOOT with valid aim and power is accepted."""
        state = _state(owner_team=0, owner_player=1)
        intent, result = resolve_command(
            _cmd("SHOOT", player_id=1, aim_location="BL", power=0.8), state
        )
        self.assertTrue(result["accepted"])


class TestMarkValidation(unittest.TestCase):
    def test_mark_normal_tightness_rejected(self) -> None:
        """MARK with tightness=NORMAL is rejected (TIGHT/LOOSE only)."""
        state = _state()
        intent, result = resolve_command(
            _cmd("MARK", player_id=1, target_player_id=0, tightness="NORMAL"), state
        )
        self.assertFalse(result["accepted"])
        self.assertEqual(result["rejection_reason"], "bad_tightness")

    def test_mark_tight_accepted(self) -> None:
        """MARK with TIGHT is accepted."""
        state = _state()
        intent, result = resolve_command(
            _cmd("MARK", player_id=1, target_player_id=0, tightness="TIGHT"), state
        )
        self.assertTrue(result["accepted"])


class TestSetStanceValidation(unittest.TestCase):
    def test_set_stance_negative_rejected(self) -> None:
        """SET_STANCE with stance < 0 is rejected."""
        state = _state()
        intent, result = resolve_command(_cmd("SET_STANCE", stance=-1), state)
        self.assertFalse(result["accepted"])
        self.assertEqual(result["rejection_reason"], "negative_stance")

    def test_set_stance_zero_accepted(self) -> None:
        """SET_STANCE with stance=0 is accepted."""
        state = _state()
        intent, result = resolve_command(_cmd("SET_STANCE", stance=0), state)
        self.assertTrue(result["accepted"])

    def test_set_stance_large_int_accepted(self) -> None:
        """SET_STANCE accepts any non-negative int."""
        state = _state()
        intent, result = resolve_command(_cmd("SET_STANCE", stance=99), state)
        self.assertTrue(result["accepted"])


class TestUnknownCommand(unittest.TestCase):
    def test_unknown_command_rejected(self) -> None:
        """Unknown commandType produces rejected ActionResult."""
        state = _state()
        intent, result = resolve_command(_cmd("DRIBBLE", target_x=5.0, target_y=0.0), state)
        self.assertFalse(result["accepted"])
        self.assertEqual(result["rejection_reason"], "unknown_command")

    def test_rejection_produces_set_stance_intent(self) -> None:
        """Any rejection falls back to SET_STANCE intent with stance=0."""
        state = _state()
        intent, result = resolve_command(_cmd("UNKNOWN_CMD"), state)
        self.assertFalse(result["accepted"])
        self.assertEqual(intent["command_type"], "SET_STANCE")
        self.assertEqual(intent["parameters"]["stance"], 0)


class TestOneShotVsContinuous(unittest.TestCase):
    def test_one_shot_commands_flagged(self) -> None:
        """PASS, SHOOT, GK_DISTRIBUTE, SLIDE_TACKLE are one-shot."""
        self.assertIn("PASS", ONE_SHOT_COMMANDS)
        self.assertIn("SHOOT", ONE_SHOT_COMMANDS)
        self.assertIn("GK_DISTRIBUTE", ONE_SHOT_COMMANDS)
        self.assertIn("SLIDE_TACKLE", ONE_SHOT_COMMANDS)

    def test_pass_is_one_shot(self) -> None:
        """PASS intent has is_one_shot=True."""
        state = _state(owner_team=0, owner_player=1)
        intent, result = resolve_command(
            _cmd("PASS", player_id=1, target_player_id=2, type="GROUND"), state
        )
        self.assertTrue(result["accepted"])
        self.assertTrue(intent["is_one_shot"])

    def test_move_to_is_continuous(self) -> None:
        """MOVE_TO intent has is_one_shot=False."""
        state = _state()
        intent, result = resolve_command(_cmd("MOVE_TO", target_x=5.0, target_y=0.0), state)
        self.assertTrue(result["accepted"])
        self.assertFalse(intent["is_one_shot"])

    def test_set_stance_not_one_shot(self) -> None:
        """SET_STANCE intent has is_one_shot=False."""
        state = _state()
        intent, result = resolve_command(_cmd("SET_STANCE", stance=0), state)
        self.assertFalse(intent["is_one_shot"])
