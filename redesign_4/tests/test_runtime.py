import copy
import json
import unittest

from agent.model import Profile, Settings
from agent.protocol import decode, validate_command
from agent.runtime import Runtime
from agent.tactics import assignments, decide


def payload(owner=(0, 2), ball=(-5, 0), tick=10, team=0, pid=2):
    players = []
    for side, positions in (
        (0, [(-51, 0), (-28, 0), (-5, 0), (20, -15), (23, 15)]),
        (1, [(51, 0), (30, 0), (8, 20), (-10, -20), (-12, 20)]),
    ):
        for i, (x, y) in enumerate(positions):
            players.append(
                {
                    "agentId": f"agentId_{i}",
                    "teamCode": "HOME" if side == 0 else "away",
                    "position": {"x": x, "y": y},
                    "velocity": {"x": 0, "y": 0},
                    "stamina": 0.9,
                }
            )
    state = {
        "tick": tick,
        "gameTime": tick * 2,
        "matchDuration": 300,
        "players": players,
        "score": {"home": 0, "away": 0},
        "ball": {
            "position": {"x": ball[0], "y": ball[1]},
            "velocity": {"x": 0, "y": 0},
            "isFree": owner is None,
        },
    }
    if owner is not None:
        state["ball"].update(possessionAgentId=f"agentId_{owner[1]}", possessionTeamId=owner[0])
    return {"prompt": json.dumps({"gameState": state, "teamId": team, "myPlayers": [pid]})}


def edit(raw, change):
    envelope = json.loads(raw["prompt"])
    change(envelope["gameState"])
    return {"prompt": json.dumps(envelope)}


def position(state, team, pid, x, y):
    state["players"][team * 5 + pid]["position"] = {"x": x, "y": y}


class ProtocolTests(unittest.TestCase):
    def test_envelope_team_identity_and_legacy_state(self):
        for team in (0, 1):
            raw = payload(owner=(team, 2), team=team)
            snapshot, pid = decode(raw)
            self.assertEqual(snapshot.owner, (team, 2))
            self.assertEqual(pid, 2)
            self.assertEqual(snapshot.direction, 1 if team == 0 else -1)
            data = json.loads(raw["prompt"])
            for p in data["gameState"]["players"]:
                p["playerId"] = int(p.pop("agentId").split("_")[-1])
                p["teamId"] = 0 if p.pop("teamCode").lower() == "home" else 1
            ball = data["gameState"]["ball"]
            ball["possessionPlayerId"] = 2
            del ball["possessionAgentId"]
            legacy, _ = decode({"prompt": data})
            self.assertEqual(legacy.owner, snapshot.owner)

    def test_ambiguous_possession_is_not_a_free_ball(self):
        raw = edit(payload(), lambda s: s["ball"].pop("possessionTeamId"))
        snapshot, _ = decode(raw)
        self.assertIsNone(snapshot.owner)
        self.assertFalse(snapshot.free)
        self.assertNotEqual(decide(snapshot, 2).command["commandType"], "INTERCEPT")

    def test_free_ball_wins_over_stale_owner(self):
        raw = edit(payload(), lambda s: s["ball"].update(isFree=True))
        snapshot, _ = decode(raw)
        self.assertTrue(snapshot.free)
        self.assertIsNone(snapshot.owner)

    def test_invalid_position_and_stamina_are_not_silently_accepted(self):
        for value in (float("nan"), float("inf"), "bad"):
            raw = edit(payload(), lambda s, value=value: position(s, 0, 2, value, 0))
            with self.assertRaises(ValueError):
                decode(raw)
        raw = edit(payload(), lambda s: s["players"][2].update(stamina=90))
        with self.assertRaises(ValueError):
            decode(raw)
        snapshot, _ = decode(raw, stamina_scale=100)
        self.assertEqual(snapshot.player(2).stamina, 0.9)

    def test_command_validation_is_parameter_specific(self):
        good = {
            "commandType": "MOVE_TO",
            "teamId": 0,
            "playerId": 2,
            "parameters": {"target_x": 5, "target_y": 7, "sprint": True},
            "duration": 0,
        }
        validate_command(good)
        for patch in ({"target_x": 100}, {"target_x": float("nan")}, {"sprint": "yes"}, {"x": 2}):
            bad = copy.deepcopy(good)
            bad["parameters"].update(patch)
            with self.assertRaises(ValueError):
                validate_command(bad)
        for kind in ("DRIBBLE", "CLEAR", "TACKLE", "IDLE"):
            with self.assertRaises(ValueError):
                validate_command({**good, "commandType": kind})


class TacticalTests(unittest.TestCase):
    def test_shared_plan_is_order_independent_and_keeps_cover(self):
        raw = payload(owner=(1, 3), ball=(-20, -3))
        first, _ = decode(raw)
        other, _ = decode(edit(raw, lambda s: s["players"].reverse()))
        plan = assignments(first)
        self.assertEqual(plan, assignments(other))
        actions = [decide(first, i).command for i in range(1, 5)]
        self.assertEqual(sum(c["commandType"] == "PRESS_BALL" for c in actions), 1)
        self.assertNotEqual(plan["presser"], plan["cover"])
        targets = list(plan["marks"].values())
        self.assertEqual(len(targets), len(set(targets)))

    def test_midfielder_covers_when_defender_engages(self):
        raw = payload(owner=(1, 3), ball=(-28, 1))
        snapshot, _ = decode(raw)
        plan = assignments(snapshot)
        self.assertEqual(plan["presser"], 1)
        self.assertEqual(plan["cover"], 2)
        command = decide(snapshot, 2).command
        self.assertEqual(command["commandType"], "MOVE_TO")
        self.assertLess(command["parameters"]["target_x"], -28)

    def test_teammate_possession_creates_separated_support_not_interception(self):
        snapshot, _ = decode(payload())
        a = decide(snapshot, 3).command
        b = decide(snapshot, 4).command
        self.assertEqual(a["commandType"], "MOVE_TO")
        self.assertEqual(b["commandType"], "MOVE_TO")
        self.assertLess(a["parameters"]["target_y"], 0)
        self.assertGreater(b["parameters"]["target_y"], 0)
        self.assertNotEqual(a["parameters"]["target_x"], b["parameters"]["target_x"])

    def test_goalkeeper_tracks_projected_goal_crossing(self):
        raw = payload(owner=None, ball=(-35, 0), pid=0)
        slow, _ = decode(raw)
        fast, _ = decode(edit(raw, lambda s: s["ball"].update(velocity={"x": -15, "y": 3})))
        a = decide(slow, 0).command["parameters"]
        b = decide(fast, 0).command["parameters"]
        self.assertGreater(b["target_y"], a["target_y"])
        self.assertLess(abs(b["target_y"]), 4)

    def test_keeper_does_not_sweep_when_an_opponent_wins_race(self):
        raw = edit(payload(owner=None, ball=(-43, 0), pid=0), lambda s: position(s, 1, 3, -44, 0))
        snapshot, _ = decode(raw)
        self.assertNotEqual(assignments(snapshot)["interceptor"], 0)
        self.assertNotEqual(decide(snapshot, 0).command["commandType"], "INTERCEPT")

    def test_prediction_selects_interceptor_running_toward_ball(self):
        raw = payload(owner=None, ball=(0, 0))

        def change(s):
            position(s, 0, 2, -10, 0)
            position(s, 0, 3, 10, 0)
            s["players"][2]["velocity"] = {"x": -5, "y": 0}
            s["players"][3]["velocity"] = {"x": -5, "y": 0}

        snapshot, _ = decode(edit(raw, change))
        self.assertEqual(assignments(snapshot)["interceptor"], 3)

    def test_clear_forward_shoots_blocked_forward_recycles(self):
        raw = payload(owner=(0, 3), ball=(39, -5), pid=3)
        raw = edit(raw, lambda s: position(s, 0, 3, 39, -5))
        snapshot, _ = decode(raw)
        shot = decide(snapshot, 3).command
        self.assertEqual(shot["commandType"], "SHOOT")
        self.assertIn(shot["parameters"]["aim_location"], ("TL", "TR", "BL", "BR", "CENTER"))

        def wall(s):
            for i, y in enumerate((-4, -2, 0, 2, 4)):
                position(s, 1, i, 45, y)

        blocked, _ = decode(edit(raw, wall))
        self.assertNotEqual(decide(blocked, 3).command["commandType"], "SHOOT")

    def test_both_teams_and_all_roles_emit_legal_bounded_actions(self):
        for team in (0, 1):
            for owner in (None, (0, 2), (1, 2)):
                snapshot, _ = decode(payload(owner=owner, team=team))
                for pid in range(5):
                    command = decide(snapshot, pid).command
                    validate_command(command)
                    self.assertEqual(command["teamId"], team)
                    self.assertEqual(command["playerId"], pid)

    def test_tighter_pass_margin_changes_a_risky_outlet(self):
        def setup(s):
            position(s, 0, 2, 0, 0)
            position(s, 0, 3, 20, -10)
            position(s, 0, 4, 20, 10)
            position(s, 1, 2, 10, -2)
            position(s, 1, 3, 10, 2)

        snapshot, _ = decode(edit(payload(ball=(0, 0)), setup))
        a = decide(snapshot, 2, Settings(pass_margin=2.5))
        b = decide(snapshot, 2, Settings(pass_margin=4.5))
        self.assertNotEqual(a.command, b.command)

    def test_defender_intercepting_free_ball_keeps_midfielder_cover(self):
        snapshot, _ = decode(payload(owner=None, ball=(-28, 1)))
        plan = assignments(snapshot)
        self.assertEqual(plan["interceptor"], 1)
        self.assertEqual(plan["cover"], 2)

    def test_unknown_ownership_does_not_send_forwards_rushing_upfield(self):
        raw = edit(
            payload(owner=(1, 3), ball=(-10, 0)), lambda s: s["ball"].pop("possessionTeamId")
        )
        snapshot, _ = decode(raw)
        for pid in (3, 4):
            action = decide(snapshot, pid).command
            self.assertLess(action["parameters"]["target_x"], 0)


class RuntimeTests(unittest.TestCase):
    def test_records_frozen_profile_and_context_and_returns_array_string(self):
        records = []
        runtime = Runtime(
            Profile(version="test-v1"), match_id="m1", opponent_id="opponent", emit=records.append
        )
        self.addCleanup(runtime.close)
        out = json.loads(runtime.handle(payload()))
        self.assertTrue(runtime.flush_logs())
        self.assertEqual(len(out), 1)
        self.assertEqual(records[-1]["profile_version"], "test-v1")
        self.assertEqual(records[-1]["match_id"], "m1")
        self.assertEqual(records[-1]["opponent_id"], "opponent")
        self.assertIn("snapshot", records[-1])
        self.assertIn("alternatives", records[-1])

    def test_malformed_state_logs_error_and_returns_balanced_stance(self):
        records = []
        runtime = Runtime(emit=records.append)
        self.addCleanup(runtime.close)
        bad = {"prompt": {"gameState": {}, "teamId": 1, "myPlayers": [3]}}
        command = json.loads(runtime.handle(bad))[0]
        self.assertTrue(runtime.flush_logs())
        self.assertEqual(command["parameters"], {"stance": 0})
        self.assertEqual(command["teamId"], 1)
        self.assertEqual(command["playerId"], 3)
        self.assertIn("error", records[-1])
        with self.assertRaises(ValueError):
            runtime.handle({"prompt": "invalid json"})


class SlideTackleSchemaTest(unittest.TestCase):
    def command(self, **params):
        return {"commandType": "SLIDE_TACKLE", "teamId": 0, "playerId": 2,
                "parameters": params, "duration": 0}

    def test_slide_tackle_requires_only_a_valid_target(self):
        validate_command(self.command(target_player_id=3))
        for params in ({}, {"target_player_id": 5}, {"target_player_id": 3, "force": 1}):
            with self.subTest(params=params), self.assertRaises(ValueError):
                validate_command(self.command(**params))
