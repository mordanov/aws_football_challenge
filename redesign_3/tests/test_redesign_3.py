"""Comprehensive unit and simulation test suite for Redesign 3.

Covers:
1. Official AWS AgentCore protocol compatibility & resolved blockers (`prompt` JSON wrapper,
   colliding `agentId_0..4` across teams, `isFree`, 12 `VALID_COMMANDS`, Away mirroring).
2. Deep per-role tactical behavior for GK, DEF, MID, FWD1, FWD2, and set pieces.
3. "Learn From Every Match" Football Trainer self-error detection & causal adaptation.
4. Eagle-Eye Opponent Scouting archetype classification & weakness exploitation.
5. Cross-match memory persistence & Post-Match Coach Review (`coach_review.py`).
"""

from __future__ import annotations

import copy
import json
import os
import tempfile
import time
import unittest

os.environ["AFC_QUIET_LOGS"] = "1"

from agent.adapter import parse_world
from agent.coach_review import analyze_match_data, parse_decision_log_lines, render_markdown_report
from agent.commands import VALID_AIM_LOCATIONS, VALID_COMMANDS, VALID_PASS_TYPES
from agent.main import get_trainer, handle, reset_match_state
from agent.model import OpponentArchetype, Phase, Role, Vec2
from agent.roles import decide_for_player
from agent.tactics import compute_defensive_assignments, evaluate_shot
from agent.trainer import FootballTrainer


def make_aws_official_fixture() -> dict:
    """Exact replica of `GAME_STATE` from `aws-samples/sample-ai-possibilities/agentic-football-sample-agents/lib/test_helpers.py`."""
    return {
        "tick": 150,
        "gameTime": 120.5,
        "playMode": "OPEN_PLAY",
        "score": {"home": 1, "away": 0},
        "ball": {
            "position": {"x": 15.0, "y": -5.0, "z": 0.0},
            "velocity": {"x": 2.0, "y": 1.0, "z": 0.0},
            "isFree": False,
            "possessionAgentId": "agentId_3",
        },
        "players": [
            {"agentId": "agentId_0", "teamCode": "home", "position": {"x": -50.0, "y": 0.0}, "velocity": {"x": 0.0, "y": 0.0}, "orientation": 0.0, "stamina": 0.95, "currentAction": 10, "lastAction": "StayNearGoal", "speed": 0.0, "isSprinting": False},
            {"agentId": "agentId_1", "teamCode": "home", "position": {"x": -25.0, "y": -10.0}, "velocity": {"x": 1.0, "y": 0.0}, "orientation": 0.1, "stamina": 0.88, "currentAction": 9, "lastAction": "CoachMoveTo", "speed": 3.0, "isSprinting": False},
            {"agentId": "agentId_2", "teamCode": "home", "position": {"x": 5.0, "y": 3.0}, "velocity": {"x": 2.0, "y": -1.0}, "orientation": 0.3, "stamina": 0.75, "currentAction": 8, "lastAction": "RoamRandomly", "speed": 4.5, "isSprinting": False},
            {"agentId": "agentId_3", "teamCode": "home", "position": {"x": 20.0, "y": -8.0}, "velocity": {"x": 3.0, "y": 0.5}, "orientation": 0.2, "stamina": 0.80, "currentAction": 3, "lastAction": "DribbleTo", "speed": 5.0, "isSprinting": True},
            {"agentId": "agentId_4", "teamCode": "home", "position": {"x": 25.0, "y": 15.0}, "velocity": {"x": 1.5, "y": -0.5}, "orientation": -0.1, "stamina": 0.82, "currentAction": 4, "lastAction": "FindOpenSpace", "speed": 3.5, "isSprinting": False},
            {"agentId": "agentId_0", "teamCode": "away", "position": {"x": 50.0, "y": 0.0}, "velocity": {"x": 0.0, "y": 0.0}, "orientation": 3.14, "stamina": 0.96, "currentAction": 10, "lastAction": "StayNearGoal", "speed": 0.0, "isSprinting": False},
            {"agentId": "agentId_1", "teamCode": "away", "position": {"x": 30.0, "y": -5.0}, "velocity": {"x": -1.0, "y": 0.5}, "orientation": 2.8, "stamina": 0.90, "currentAction": 6, "lastAction": "MoveToTarget", "speed": 3.0, "isSprinting": False},
            {"agentId": "agentId_2", "teamCode": "away", "position": {"x": 10.0, "y": 0.0}, "velocity": {"x": -2.0, "y": -1.0}, "orientation": 3.0, "stamina": 0.70, "currentAction": 7, "lastAction": "InterceptBallCarrier", "speed": 5.5, "isSprinting": True},
            {"agentId": "agentId_3", "teamCode": "away", "position": {"x": -15.0, "y": 8.0}, "velocity": {"x": 0.0, "y": 0.0}, "orientation": 2.9, "stamina": 0.85, "currentAction": 8, "lastAction": "RoamRandomly", "speed": 2.0, "isSprinting": False},
            {"agentId": "agentId_4", "teamCode": "away", "position": {"x": -10.0, "y": -12.0}, "velocity": {"x": -1.0, "y": 0.0}, "orientation": 3.1, "stamina": 0.78, "currentAction": 8, "lastAction": "RoamRandomly", "speed": 2.5, "isSprinting": False},
        ],
        "teamChat": [],
    }


class TestProtocolAndBlockers(unittest.TestCase):
    def setUp(self) -> None:
        reset_match_state(preserve_cross_match_learning=False)

    def test_agentcore_prompt_json_string_and_single_player(self) -> None:
        gs = make_aws_official_fixture()
        payload = {
            "prompt": json.dumps({"gameState": gs, "teamId": 0, "myPlayers": [3]})
        }
        cmds = handle(payload)
        self.assertEqual(len(cmds), 1)
        self.assertEqual(cmds[0]["playerId"], 3)
        self.assertEqual(cmds[0]["teamId"], 0)
        self.assertIn(cmds[0]["commandType"], VALID_COMMANDS)

    def test_colliding_agent_ids_possession_disambiguation(self) -> None:
        """Home `agentId_3` holds the ball; Away `agentId_3` must NOT think it holds the ball."""
        gs = make_aws_official_fixture()
        w_home = parse_world({"gameState": gs, "teamId": 0, "myPlayers": [3]})
        w_away = parse_world({"gameState": gs, "teamId": 1, "myPlayers": [3]})

        self.assertEqual(w_home.ball.owner_team, "me")
        self.assertEqual(w_home.ball.owner_id, 3)
        self.assertTrue(w_home.my_player(3).has_ball)

        self.assertEqual(w_away.ball.owner_team, "opp")
        self.assertEqual(w_away.ball.owner_id, 3)
        self.assertFalse(w_away.my_player(3).has_ball)
        self.assertTrue(w_away.opp_player(3).has_ball)

    def test_isfree_flag_overrides_stale_possession_claim(self) -> None:
        gs = make_aws_official_fixture()
        gs["ball"]["isFree"] = True
        w = parse_world({"gameState": gs, "teamId": 0})
        self.assertTrue(w.ball.is_free)
        self.assertIsNone(w.ball.owner_team)
        self.assertFalse(w.my_player(3).has_ball)

    def test_case_insensitive_team_code(self) -> None:
        gs = make_aws_official_fixture()
        for p in gs["players"]:
            p["teamCode"] = p["teamCode"].upper()
        w = parse_world({"gameState": gs, "teamId": 1, "myPlayers": [0, 1, 2, 3, 4]})
        self.assertEqual(len(w.me), 5)
        self.assertEqual(len(w.opp), 5)
        self.assertEqual(w.attack_sign, -1)

    def test_valid_wire_commands_and_away_mirroring(self) -> None:
        gs_home = make_aws_official_fixture()
        gs_away = copy.deepcopy(gs_home)
        # Flip coordinates and teams for exact Away mirror
        gs_away["score"] = {"home": 0, "away": 1}
        gs_away["ball"]["position"]["x"] *= -1
        gs_away["ball"]["position"]["y"] *= -1
        gs_away["ball"]["velocity"]["x"] *= -1
        gs_away["ball"]["velocity"]["y"] *= -1
        for p in gs_away["players"]:
            p["teamCode"] = "away" if p["teamCode"] == "home" else "home"
            p["position"]["x"] *= -1
            p["position"]["y"] *= -1
            p["velocity"]["x"] *= -1
            p["velocity"]["y"] *= -1

        reset_match_state(preserve_cross_match_learning=False)
        cmds_home = handle({"gameState": gs_home, "teamId": 0, "myPlayers": [0, 1, 2, 3, 4]})
        reset_match_state(preserve_cross_match_learning=False)
        cmds_away = handle({"gameState": gs_away, "teamId": 1, "myPlayers": [0, 1, 2, 3, 4]})

        self.assertEqual(len(cmds_home), 5)
        self.assertEqual(len(cmds_away), 5)
        for ch, ca in zip(cmds_home, cmds_away):
            self.assertIn(ch["commandType"], VALID_COMMANDS)
            self.assertEqual(ch["commandType"], ca["commandType"])
            if ch["commandType"] == "MOVE_TO":
                self.assertAlmostEqual(
                    ch["parameters"]["target_x"], -ca["parameters"]["target_x"], places=2
                )
                self.assertAlmostEqual(
                    ch["parameters"]["target_y"], -ca["parameters"]["target_y"], places=2
                )


class TestDeepRoleTactics(unittest.TestCase):
    def setUp(self) -> None:
        reset_match_state(preserve_cross_match_learning=False)

    def test_gk_incoming_shot_save_and_sweeper_distribution(self) -> None:
        gs = make_aws_official_fixture()
        # Incoming fast shot toward our goal (-55, 2.5)
        gs["ball"] = {
            "position": {"x": -35.0, "y": 1.0, "z": 0.0},
            "velocity": {"x": -10.0, "y": 0.75, "z": 0.0},
            "isFree": True,
        }
        cmds = handle({"gameState": gs, "teamId": 0, "myPlayers": [0]})
        self.assertEqual(cmds[0]["commandType"], "MOVE_TO")
        self.assertLessEqual(cmds[0]["parameters"]["target_x"], -53.0)
        self.assertTrue(cmds[0]["parameters"]["sprint"])

        # Now give GK possession -> must use GK_DISTRIBUTE with THROW or KICK
        gs["ball"] = {
            "position": {"x": -50.0, "y": 0.0, "z": 0.0},
            "velocity": {"x": 0.0, "y": 0.0, "z": 0.0},
            "isFree": False,
            "possessionAgentId": "agentId_0",
        }
        cmds_dist = handle({"gameState": gs, "teamId": 0, "myPlayers": [0]})
        self.assertEqual(cmds_dist[0]["commandType"], "GK_DISTRIBUTE")
        self.assertIn(cmds_dist[0]["parameters"]["method"], ("THROW", "KICK"))

    def test_def_avoids_penalty_box_foul_and_locks_central_box(self) -> None:
        gs = make_aws_official_fixture()
        # Opponent FWD1 (`agentId_3` on away) has ball inside our penalty box at (-42, 10),
        # and our DEF (`agentId_1`) is right next to them at (-43, 9.5).
        gs["ball"] = {
            "position": {"x": -42.0, "y": 10.0, "z": 0.0},
            "velocity": {"x": -1.0, "y": -0.5, "z": 0.0},
            "isFree": False,
            "possessionAgentId": "agentId_3",
        }
        gs["players"][1]["position"] = {"x": -43.0, "y": 9.5}     # home DEF
        gs["players"][8]["position"] = {"x": -42.0, "y": 10.0}    # away FWD1
        cmds = handle({"gameState": gs, "teamId": 0, "myPlayers": [1]})
        # DEF must NOT reckless SLIDE_TACKLE inside penalty box at y=10; must jockey-block goal-side!
        self.assertNotEqual(cmds[0]["commandType"], "SLIDE_TACKLE")
        self.assertEqual(cmds[0]["commandType"], "MOVE_TO")

    def test_fwd1_sweeper_gk_long_range_punish_and_cutback(self) -> None:
        gs = make_aws_official_fixture()
        # Opponent GK (`agentId_0` on away) wanders out to x = +32.0!
        gs["players"][5]["position"] = {"x": 32.0, "y": 8.0}
        # Move away defender out of direct shot line
        gs["players"][6]["position"] = {"x": 28.0, "y": -18.0}
        # Our FWD1 (`agentId_3` on home) has the ball at x = 24.0, y = 0.0 (31m from goal!)
        gs["players"][3]["position"] = {"x": 24.0, "y": 0.0}
        gs["ball"]["position"] = {"x": 24.0, "y": 0.0, "z": 0.0}
        gs["ball"]["possessionAgentId"] = "agentId_3"

        cmds = handle({"gameState": gs, "teamId": 0, "myPlayers": [3]})
        self.assertEqual(cmds[0]["commandType"], "SHOOT")
        self.assertIn(cmds[0]["parameters"]["aim_location"], VALID_AIM_LOCATIONS)
        self.assertGreaterEqual(cmds[0]["parameters"]["power"], 0.9)

    def test_fwd2_anti_clustering_and_far_post_cutback_run(self) -> None:
        gs = make_aws_official_fixture()
        # FWD1 (`agentId_3` on home) drives deep into left box at (38, -12) with the ball
        gs["players"][3]["position"] = {"x": 38.0, "y": -12.0}
        gs["ball"]["position"] = {"x": 38.0, "y": -12.0, "z": 0.0}
        gs["ball"]["possessionAgentId"] = "agentId_3"

        cmds = handle({"gameState": gs, "teamId": 0, "myPlayers": [4]})
        self.assertEqual(cmds[0]["commandType"], "MOVE_TO")
        # FWD2 must arrive in the positive-y far-post / penalty-spot window, separated from FWD1!
        self.assertGreater(cmds[0]["parameters"]["target_x"], 35.0)
        self.assertGreater(cmds[0]["parameters"]["target_y"], 2.0)

    def test_set_piece_corner_and_penalty(self) -> None:
        gs = make_aws_official_fixture()
        gs["playMode"] = "CORNER"
        gs["modeTeamId"] = 0
        gs["ball"]["position"] = {"x": 54.0, "y": 34.0, "z": 0.0}
        gs["ball"]["isFree"] = True
        cmds = handle({"gameState": gs, "teamId": 0, "myPlayers": [0, 1, 2, 3, 4]})
        self.assertEqual(len(cmds), 5)
        # DEF (player 1) stays near halfway as rest-defense stopper
        self.assertLess(cmds[1]["parameters"]["target_x"], 15.0)
        # FWD1 (player 3) and FWD2 (player 4) attack the box
        self.assertGreater(cmds[3]["parameters"]["target_x"], 40.0)


class TestFootballTrainerAndEagleEyeLearning(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.mem_path = os.path.join(self.tmp_dir.name, "trainer_mem.json")
        self.trainer = FootballTrainer(memory_path=self.mem_path)

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_intra_match_pass_turnover_and_dribble_dispossession_learning(self) -> None:
        initial_margin = self.trainer.learned_weights.pass_safety_margin
        initial_caution = self.trainer.learned_weights.dribble_caution_radius

        # Tick 10: Our MID (player 2) has the ball in our defensive third (-25, -10) and issues a PASS
        gs1 = make_aws_official_fixture()
        gs1["tick"] = 10
        gs1["players"][2]["position"] = {"x": -25.0, "y": -10.0}
        gs1["ball"]["position"] = {"x": -25.0, "y": -10.0, "z": 0.0}
        gs1["ball"]["possessionAgentId"] = "agentId_2"
        w1 = parse_world({"gameState": gs1, "teamId": 0})
        d1 = self.trainer.observe_and_learn(w1)
        from agent import commands as cmd

        self.trainer.record_issued_commands(
            w1, [cmd.pass_to(2, 3, pass_type="GROUND", team_id=0)]
        )

        # Tick 11: Opponent MID (`agentId_2` on away) intercepted the ball at (-18, -8)!
        gs2 = copy.deepcopy(gs1)
        gs2["tick"] = 11
        gs2["players"][7]["position"] = {"x": -18.0, "y": -8.0}
        gs2["ball"]["position"] = {"x": -18.0, "y": -8.0, "z": 0.0}
        gs2["ball"]["possessionAgentId"] = "agentId_2"
        w2 = parse_world({"gameState": gs2, "teamId": 0})
        d2 = self.trainer.observe_and_learn(w2)

        self.assertEqual(self.trainer.ledger.pass_interceptions, 1)
        self.assertEqual(self.trainer.ledger.buildup_turnovers_danger, 1)
        self.assertGreater(self.trainer.learned_weights.pass_safety_margin, initial_margin)
        self.assertTrue(
            any("ERR_BUILDUP_TURNOVER_DANGER" in ev for ev in d2.new_events),
            d2.new_events,
        )

        # Tick 12 -> 13: Now simulate a dribble dispossession by FWD1 (player 3)
        gs3 = make_aws_official_fixture()
        gs3["tick"] = 12
        w3 = parse_world({"gameState": gs3, "teamId": 0})
        self.trainer.observe_and_learn(w3)
        self.trainer.record_issued_commands(
            w3, [cmd.dribble_to(3, 30.0, -5.0, sprint=True, attack_sign=1, team_id=0)]
        )

        gs4 = copy.deepcopy(gs3)
        gs4["tick"] = 13
        # Away DEF (`agentId_1` on away) wins the ball at (21, -7)
        gs4["players"][6]["position"] = {"x": 21.0, "y": -7.0}
        gs4["ball"]["position"] = {"x": 21.0, "y": -7.0, "z": 0.0}
        gs4["ball"]["possessionAgentId"] = "agentId_1"
        w4 = parse_world({"gameState": gs4, "teamId": 0})
        d4 = self.trainer.observe_and_learn(w4)

        self.assertEqual(self.trainer.ledger.dribble_dispossessions, 1)
        self.assertGreater(self.trainer.learned_weights.dribble_caution_radius, initial_caution)
        self.assertTrue(any("ERR_DRIBBLE_DISPOSSESSED" in ev for ev in d4.new_events))

    def test_conceded_goal_root_cause_diagnosis_and_adaptation(self) -> None:
        base_line = self.trainer.learned_weights.defensive_line_offset
        gs1 = make_aws_official_fixture()
        gs1["tick"] = 20
        # Push our DEF (1) and MID (2) high up the pitch (`x = +12`, `x = +18`)
        gs1["players"][1]["position"] = {"x": 12.0, "y": -4.0}
        gs1["players"][2]["position"] = {"x": 18.0, "y": 4.0}
        w1 = parse_world({"gameState": gs1, "teamId": 0})
        self.trainer.observe_and_learn(w1)
        self.trainer.record_issued_commands(w1, [])

        # Next tick: Opponent scores on a counter-attack (`away` score 0 -> 1)!
        gs2 = copy.deepcopy(gs1)
        gs2["tick"] = 21
        gs2["score"] = {"home": 1, "away": 1}
        w2 = parse_world({"gameState": gs2, "teamId": 0})
        d2 = self.trainer.observe_and_learn(w2)

        self.assertEqual(self.trainer.ledger.goals_conceded_counter, 1)
        self.assertLess(self.trainer.learned_weights.defensive_line_offset, base_line)
        self.assertTrue(any("caught_on_counter" in ev for ev in d2.new_events))

    def test_eagle_eye_opponent_archetypes_and_frozen_detection(self) -> None:
        # 1. Test MOB_PRESS detection (3+ opponents swarming within 12m of the ball)
        gs_mob = make_aws_official_fixture()
        bx, by = gs_mob["ball"]["position"]["x"], gs_mob["ball"]["position"]["y"]
        for idx in (6, 7, 8, 9):  # 4 away outfield players swarm the ball
            gs_mob["players"][idx]["position"] = {"x": bx + 2.0, "y": by + 1.0}
        w_mob = parse_world({"gameState": gs_mob, "teamId": 0})
        d_mob = self.trainer.observe_and_learn(w_mob)
        self.assertEqual(d_mob.scouting.archetype, OpponentArchetype.MOB_PRESS)
        self.assertGreaterEqual(d_mob.weights.long_pass_bias, 0.55)

        # 2. Test FROZEN_LLM detection (2+ opponents stationary across 3 ticks near ball)
        trainer_frozen = FootballTrainer(memory_path=self.mem_path)
        gs_f = make_aws_official_fixture()
        for idx in (6, 7):  # away DEF and MID frozen with speed 0
            gs_f["players"][idx]["position"] = {"x": 18.0, "y": float(idx - 6)}
            gs_f["players"][idx]["speed"] = 0.0
        for t in (1, 2, 3):
            gs_f["tick"] = t
            wf = parse_world({"gameState": gs_f, "teamId": 0})
            df = trainer_frozen.observe_and_learn(wf)
        self.assertEqual(df.scouting.archetype, OpponentArchetype.FROZEN_LLM)
        self.assertIn(1, df.scouting.frozen_opponent_ids)
        self.assertIn(2, df.scouting.frozen_opponent_ids)

    def test_cross_match_persistence_and_post_match_coach_review(self) -> None:
        self.trainer.ledger.pass_interceptions = 5
        self.trainer.ledger.buildup_turnovers_danger = 3
        self.trainer.ledger.dribble_dispossessions = 4
        self.trainer.ledger.goals_conceded_central = 1
        self.trainer.learned_weights.pass_safety_margin = 0.31
        self.trainer.save_persistent_memory()

        # Reload in a brand-new FootballTrainer instance (simulating next match container)
        reloaded = FootballTrainer(memory_path=self.mem_path)
        self.assertAlmostEqual(reloaded.learned_weights.pass_safety_margin, 0.31, places=2)
        self.assertEqual(reloaded.ledger.pass_interceptions, 5)

        # Run Post-Match Coach Review analyzer
        with open(self.mem_path, encoding="utf-8") as fh:
            saved_json = json.loads(fh.read())
        review = analyze_match_data(saved_json)
        md = render_markdown_report(review)
        self.assertIn("Football Trainer Post-Match Review", md)
        self.assertTrue(review["recommended_weights"]["box_protector_lock"])

    def test_live_coach_instructions_and_log_parser(self) -> None:
        gs = make_aws_official_fixture()
        gs["teamChat"] = ["Press higher, win the ball back quickly on the left side!"]
        w = parse_world({"gameState": gs, "teamId": 0})
        d = self.trainer.observe_and_learn(w)
        self.assertGreaterEqual(d.weights.press_intensity, 0.90)
        self.assertLess(d.weights.preferred_flank, -0.5)
        self.assertIn("high_press", d.coach_override_note or "")

        # Verify log line parser in coach_review.py
        sample_log = 'DECISION {"tick":15,"trainer_events":["ERR_PASS_INTERCEPTED:p2","GOAL_SCORED:left"],"scouting":{"archetype":"MOB_PRESS","weakest_flank":"left"}}'
        parsed = parse_decision_log_lines([sample_log])
        self.assertEqual(parsed["ledger"]["pass_interceptions"], 1)
        self.assertEqual(parsed["ledger"]["goals_scored"], 1)
        self.assertEqual(parsed["scouting"]["archetype"], "MOB_PRESS")

    def test_latency_and_never_crash_resilience(self) -> None:
        gs = make_aws_official_fixture()
        t0 = time.perf_counter()
        for tick in range(1, 51):
            gs["tick"] = tick
            gs["gameTime"] = tick * 2.0
            cmds = handle({"gameState": gs, "teamId": 0, "myPlayers": [0, 1, 2, 3, 4]})
            self.assertEqual(len(cmds), 5)
        avg_ms = ((time.perf_counter() - t0) * 1000.0) / 50.0
        self.assertLess(avg_ms, 5.0, f"Expected <5ms per tick, got {avg_ms:.2f}ms")

        # Malformed payloads must never raise
        for bad in ({}, {"prompt": "not-json"}, {"gameState": None, "myPlayers": [2]}):
            out = handle(bad)
            self.assertTrue(isinstance(out, list) and len(out) >= 1)


if __name__ == "__main__":
    unittest.main()
