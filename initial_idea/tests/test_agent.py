"""Run:  python3 -m unittest discover -s tests -v   (from the afc-agent folder)

The states below are MOCKS in the schema we *guess* the game uses, not real
server payloads. Replace them with a captured real state as soon as you have
one - that is the best test you can add.
"""
import copy
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent import commands as C  # noqa: E402
from agent import tactics as T  # noqa: E402
from agent.adapter import parse_world  # noqa: E402
from agent.main import handle  # noqa: E402
from agent.policy import decide  # noqa: E402


def pl(i, x, y, st=1.0):
    return {"id": i, "position": {"x": x, "y": y}, "velocity": {"x": 0, "y": 0}, "stamina": st}


def make(ball, owner, ours, theirs, score=(0, 0), clock=30):
    return {
        "ball": {"position": {"x": ball[0], "y": ball[1]}, "owner": owner},
        "teammates": ours, "opponents": theirs,
        "score": {"us": score[0], "them": score[1]}, "clock": clock,
    }


# We attack toward +x (our keeper is at x<0).
OURS = [pl(0, -50, 0), pl(1, -25, 0), pl(2, -5, 2), pl(3, 20, -12), pl(4, 20, 12)]
THEIRS = [pl(0, 50, 0), pl(1, 25, 3), pl(2, 5, -3), pl(3, -10, 10), pl(4, -15, -8)]


class Tests(unittest.TestCase):
    def setUp(self):
        T.reset_memory()

    def test_open_shot_is_taken_by_code(self):
        ours = [pl(0, -50, 0), pl(1, -25, 0), pl(2, -5, 2), pl(3, 38, -8), pl(4, 20, 12)]
        theirs = [pl(0, 50, 0), pl(1, 10, 20), pl(2, 5, -25), pl(3, -10, 10), pl(4, -15, -8)]
        s = make((38, -8), {"team": "us", "id": 3}, ours, theirs)
        s["ball"]["owner"] = 3  # id-only form too
        w = parse_world(s, 3)
        w.owner = ("us", 3)
        d = decide(w)
        self.assertEqual(d.cmd["commandType"], "SHOOT")
        self.assertEqual(d.cmd["parameters"]["power"], 1.0)

    def test_blocked_lane_does_not_hoof(self):
        ours = [pl(0, -50, 0), pl(1, -25, 0), pl(2, -5, 2), pl(3, 40, 0), pl(4, 30, 15)]
        theirs = [pl(0, 50, 0), pl(1, 46, 0), pl(2, 44, 1), pl(3, 45, -1), pl(4, 43, 0)]
        w = parse_world(make((40, 0), None, ours, theirs), 3)
        w.owner = ("us", 3)
        d = decide(w)
        self.assertNotEqual(d.cmd["commandType"], "SHOOT")

    def test_exactly_one_presser_and_distinct_markers(self):
        s = make((-18, 4), {"team": "them", "id": 3}, OURS, THEIRS)
        s["ball"]["owner"] = {"team": "them", "id": 3}
        s = {**s, "myTeam": "us"}
        cmds = {}
        for i in range(5):
            w = parse_world(s, i)
            w.owner = ("them", 3)
            cmds[i] = decide(w).cmd
        pressers = [i for i, c in cmds.items() if c["commandType"] in ("PRESS_BALL", "TACKLE")]
        self.assertEqual(len(pressers), 1, cmds)
        targets = [c["parameters"]["target_player_id"] for c in cmds.values() if c["commandType"] == "MARK"]
        self.assertEqual(len(targets), len(set(targets)))

    def test_mirrored_pitch_gives_same_decisions(self):
        def flip(players):
            return [pl(p["id"], -p["position"]["x"], -p["position"]["y"]) for p in players]
        s1 = make((-18, 4), None, OURS, THEIRS)
        s2 = make((18, -4), None, flip(OURS), flip(THEIRS))
        for i in range(5):
            T.reset_memory()
            w1 = parse_world(s1, i)
            T.reset_memory()
            w2 = parse_world(s2, i)
            self.assertEqual(w1.s, -w2.s)
            self.assertEqual(decide(w1).cmd["commandType"], decide(w2).cmd["commandType"], i)

    def test_all_five_return_valid_commands_fast(self):
        s = make((0, 0), None, OURS, THEIRS)
        for i in range(5):
            t0 = time.perf_counter()
            out = handle({"gameState": s}, player_id=i)
            self.assertLess(time.perf_counter() - t0, 0.05)
            self.assertTrue(C.is_valid(out[0], i), out)

    def test_flat_players_schema_and_corner_origin(self):
        flat = []
        for p in OURS:
            flat.append({**p, "team": "us", "position": {"x": p["position"]["x"] + 55, "y": p["position"]["y"] + 35}})
        for p in THEIRS:
            flat.append({**p, "team": "them", "position": {"x": p["position"]["x"] + 55, "y": p["position"]["y"] + 35}})
        s = {"myTeam": "us", "players": flat, "ball": {"position": {"x": 55, "y": 35}},
             "score": [0, 0], "clock": 10}
        w = parse_world(s, 2, origin="corner", length=110, width=70)
        self.assertAlmostEqual(w.me.x, -5.0)
        self.assertEqual(w.s, 1)
        out = handle({"gameState": s}, player_id=2)  # default origin=center: must still not crash
        self.assertTrue(C.is_valid(out[0], 2))

    def test_garbage_state_degrades_to_safe_command(self):
        out = handle({"gameState": {"nonsense": True}}, player_id=3)
        self.assertEqual(out[0]["commandType"], "SET_STANCE")

    def test_late_deficit_goes_aggressive_late_lead_protects(self):
        base = make((0, 0), None, OURS, THEIRS, score=(0, 1), clock=100)
        self.assertEqual(T.posture(parse_world(base, 2)), "chase")
        T.reset_memory()
        base = make((0, 0), None, OURS, THEIRS, score=(2, 1), clock=100)
        self.assertEqual(T.posture(parse_world(base, 2)), "protect")

    def test_coach_order_shifts_posture(self):
        s = make((0, 0), None, OURS, THEIRS)
        s["coachInstruction"] = "go_all_out_attack"
        self.assertEqual(T.posture(parse_world(s, 2)), "chase")

    def test_llm_only_consulted_when_close_call(self):
        calls = []

        def spy(w, cands):
            calls.append(len(cands))
            return 0

        # keeper w/ ball is forced -> no LLM
        w = parse_world(make((-50, 0), None, OURS, THEIRS), 0)
        w.owner = ("us", 0)
        decide(w, llm=spy)
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
