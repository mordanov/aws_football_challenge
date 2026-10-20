import json
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agent import commands as C  # noqa: E402
from agent import events as E  # noqa: E402
from agent import tactics as T  # noqa: E402
from agent.main import Brain  # noqa: E402
from agent.playbook import KNOBS, Playbook  # noqa: E402
from agent.policy import decide  # noqa: E402
from agent.recorder import Recorder  # noqa: E402
from agent.scout import Scout  # noqa: E402
from agent.store import MemoryStore  # noqa: E402
from agent.wire import parse_payload, parse_world, to_wire  # noqa: E402
from tests.helpers import AWAY, HOME, payload, sample, state  # noqa: E402


class WireTest(unittest.TestCase):
    def test_aws_sample_home(self):
        gs, team, pids = parse_payload(payload(sample(), 0, [3]), None)
        w = parse_world(gs, team, pids[0])
        self.assertEqual((team, pids), (0, [3]))
        self.assertTrue(w.i_have_ball)
        self.assertEqual(len(w.opps), 5)
        self.assertEqual((w.score_us, w.score_them), (1, 0))

    def test_away_is_mirrored_and_round_trips(self):
        w = parse_world(sample(), 1, 1)
        self.assertEqual((w.me.x, w.me.y), (-10, 3))      # away #1 at world (10,-3)
        self.assertTrue(w.they_have_ball)                  # home #3 holds it
        self.assertEqual((w.score_us, w.score_them), (0, 1))
        out = to_wire(C.move_to(1, -20, 5), w)
        self.assertEqual((out["parameters"]["target_x"], out["parameters"]["target_y"]), (20, -5))
        self.assertEqual(out["teamId"], 1)

    def test_prompt_as_dict_and_default_pid(self):
        gs, team, pids = parse_payload({"prompt": {"gameState": sample(), "teamId": 0}}, 4)
        self.assertEqual(pids, [4])

    def test_garbage_payload_gives_valid_fallback(self):
        b = Brain(MemoryStore(), background=False)
        for bad in ({"prompt": "not json"}, {"prompt": "[]"}, "x"):
            out = b.handle(bad, 2)
            self.assertEqual(len(out), 1)
            self.assertTrue(C.validate(out[0]))


class PlaybookTest(unittest.TestCase):
    def test_clamp_and_unknown_knobs(self):
        pb = Playbook({"shoot_range": 999, "bogus": 1, "def_line": "x"})
        self.assertEqual(pb.get("shoot_range"), KNOBS["shoot_range"].hi)
        self.assertNotIn("bogus", pb.values)
        self.assertEqual(pb.get("def_line"), KNOBS["def_line"].default)

    def test_nudges_are_additive_clamped_and_do_not_mutate(self):
        pb = Playbook()
        n = pb.with_nudges({"shot_power": 5, "pass_lane_min": 0.6})
        self.assertEqual(n.get("shot_power"), 1.0)
        self.assertAlmostEqual(n.get("pass_lane_min"), pb.get("pass_lane_min") + 0.6)
        self.assertEqual(pb.get("shot_power"), KNOBS["shot_power"].default)

    def test_json_round_trip(self):
        pb = Playbook({"shoot_range": 20}, 3, "challenger", ["x"])
        self.assertEqual(Playbook.from_json(json.loads(json.dumps(pb.to_json()))), pb)


def world(home, away, team=0, pid=0, **kw):
    return parse_world(state(home, away, **kw), team, pid)


class PolicyTest(unittest.TestCase):
    def test_open_shot_shoots_with_valid_aim(self):
        home = {**HOME, 3: (40, 2)}
        away = {0: (54, 0), 1: (20, 20), 2: (0, 0), 3: (-20, 0), 4: (-30, 10)}
        w = world(home, away, pid=3, ball=(40, 2), owner=("home", 3))
        d = decide(w, Playbook())
        self.assertEqual(d.cmd["commandType"], "SHOOT", d.why)
        self.assertIn(d.cmd["parameters"]["aim_location"], C.AIMS)

    def test_blocked_lane_does_not_shoot(self):
        home = {**HOME, 3: (40, 0)}
        away = {0: (54, 0), 1: (43, 1.6), 2: (43, -1.6), 3: (44, 0), 4: (-30, 10)}
        w = world(home, away, pid=3, ball=(40, 0), owner=("home", 3))
        self.assertNotEqual(decide(w, Playbook()).cmd["commandType"], "SHOOT")

    def test_away_team_shoots_at_the_correct_goal(self):
        away = {**AWAY, 3: (-40, 2)}
        home = {0: (-54, 0), 1: (-20, 20), 2: (0, 0), 3: (20, 0), 4: (30, 10)}
        w = world(home, away, team=1, pid=3, ball=(-40, 2), owner=("away", 3))
        self.assertEqual(decide(w, Playbook()).cmd["commandType"], "SHOOT")

    def _team(self, gs, team):
        return {p: decide(parse_world(gs, team, p), Playbook()) for p in range(5)}

    def test_one_presser_distinct_markers_all_valid(self):
        for team in (0, 1):
            for bx, by in ((-30, 10), (0, 0), (25, -20)):
                carrier = 3 if team == 0 else 3
                code = "away" if team == 0 else "home"
                gs = state(HOME, AWAY, ball=(bx, by), owner=(code, carrier))
                ds = self._team(gs, team)
                kinds = [d.cmd["commandType"] for d in ds.values()]
                self.assertEqual(sum(k in ("PRESS_BALL", "SLIDE_TACKLE") for k in kinds[1:]), 1, (team, bx, kinds))
                marks = [d.cmd["parameters"]["target_player_id"] for d in ds.values() if d.cmd["commandType"] == "MARK"]
                self.assertEqual(len(marks), len(set(marks)))
                self.assertTrue(all(C.validate(d.cmd) for d in ds.values()))

    def test_one_interceptor_on_loose_ball(self):
        gs = state(HOME, AWAY, ball=(0, 0), owner=None)
        kinds = [d.cmd["commandType"] for d in self._team(gs, 0).values()]
        self.assertEqual(kinds.count("INTERCEPT"), 1)

    def test_keeper_distributes(self):
        w = world(HOME, AWAY, pid=0, ball=(-52, 0), owner=("home", 0))
        self.assertEqual(decide(w, Playbook()).cmd["commandType"], "GK_DISTRIBUTE")

    def test_fast(self):
        gs = sample()
        t0 = time.perf_counter()
        for _ in range(20):
            for p in range(5):
                decide(parse_world(gs, 0, p), Playbook())
        self.assertLess((time.perf_counter() - t0) / 100, 0.05)


class EventsScoutTest(unittest.TestCase):
    def _snap(self, owner, cmds=None, t=10, k=1, score=(0, 0), ball=(0, 0), away=AWAY):
        w = world(HOME, away, ball=ball, owner=owner, t=t, tick=k, score=score)
        return E.snapshot(w, cmds or {})

    def test_pass_lost_and_ok(self):
        a = self._snap(("home", 2), {2: C.pass_to(2, 3)}, k=1)
        lost = E.detect(a, self._snap(("away", 2), k=2))
        ok = E.detect(a, self._snap(("home", 3), k=2))
        self.assertEqual([e["type"] for e in lost], ["PASS_LOST"])
        self.assertEqual([e["type"] for e in ok], ["PASS_OK"])

    def test_goal_against_on_counter(self):
        home = {0: (-52, 0), 1: (5, 0), 2: (10, 5), 3: (20, -10), 4: (20, 10)}
        a = E.snapshot(world(home, AWAY, owner=("away", 3), tick=1), {})
        b = E.snapshot(world(home, AWAY, owner=None, tick=2, score=(0, 1)), {})
        ev = E.detect(a, b)
        self.assertEqual(ev[-1]["type"], "GOAL_AGAINST")
        self.assertTrue(ev[-1]["counter"])

    def test_scout_hysteresis_on_keeper_off_line(self):
        sc = Scout()
        far = {**AWAY, 0: (40, 0)}
        for k in range(4):
            sc.observe(self._snap(None, k=k, away=far))
        self.assertNotIn("gk_off_line", sc.active())
        sc.observe(self._snap(None, k=5, away=far))
        self.assertIn("gk_off_line", sc.active())
        self.assertGreater(sc.nudges()["shoot_range"], 0)
        for k in range(6, 8):
            sc.observe(self._snap(None, k=k))
        self.assertIn("gk_off_line", sc.active())   # needs 3 contrary ticks
        sc.observe(self._snap(None, k=9))
        self.assertNotIn("gk_off_line", sc.active())

    def test_scout_ignores_duplicate_ticks_and_fingerprints(self):
        sc = Scout()
        s = self._snap(None, k=1)
        sc.observe(s)
        sc.observe(s)
        self.assertEqual(sc.ticks, 1)
        for k in range(2, 12):
            sc.observe(self._snap(("away", 3), k=k, ball=(-10, 10)))
        fp = sc.fingerprint()
        self.assertEqual(len(fp), 6 + len(E.ACTION_FAMILIES))
        self.assertTrue(all(0 <= v <= 1 for v in fp))


class RecorderMainTest(unittest.TestCase):
    def test_recorder_flushes_and_detects_new_match(self):
        st, now = MemoryStore(), [1000.0]
        r = Recorder(st, "3", clock=lambda: now[0])
        self.assertTrue(r.is_new_match(0))
        mid = r.start(0)
        for i in range(12):
            r.add({"t": i * 2.0, "k": i})
        r.join()
        r.flush()
        lines = st.get_text(f"matches/{mid}/p3.jsonl").splitlines()
        self.assertEqual(len(lines), 12)
        self.assertFalse(r.is_new_match(30))
        self.assertTrue(r.is_new_match(2))          # clock went backwards -> kickoff
        now[0] += 500
        self.assertTrue(r.is_new_match(30))         # long idle -> new match

    def test_playbook_swaps_only_at_kickoff(self):
        st = MemoryStore()
        b = Brain(st, runtime="3", background=False)
        b.handle(payload(state(HOME, AWAY, t=10, tick=1), 0, [3]))
        st.put_json("active.json", {"playbook": Playbook({"shoot_range": 30}, 7, "challenger").to_json()})
        b._last_refresh = -1e9
        b.handle(payload(state(HOME, AWAY, t=12, tick=2), 0, [3]))
        self.assertEqual(b.base.version, 0)          # loaded but not applied mid-match
        b.handle(payload(state(HOME, AWAY, t=0.5, tick=3), 0, [3]))
        self.assertEqual((b.base.name, b.base.version), ("challenger", 7))

    def test_dossier_pre_nudges(self):
        st = MemoryStore()
        b = Brain(st, runtime="1", background=False)
        for k in range(12):
            b.handle(payload(state(HOME, AWAY, t=2.0 * k, tick=k), 0, [1]))
        fp = b.scout.fingerprint()
        st.put_json("dossiers/index.json", [{"key": "opp-a", "fp": fp, "nudges": {"pass_lane_min": 0.3}}])
        b._last_refresh = -1e9
        b.handle(payload(state(HOME, AWAY, t=31, tick=20), 0, [1]))
        self.assertEqual(b.dossier_key, "opp-a")
        self.assertAlmostEqual(b.scout.nudges()["pass_lane_min"], 0.3)

    def test_multi_player_runtime(self):
        b = Brain(MemoryStore(), background=False)
        out = b.handle(payload(sample(), 0, [0, 1, 2, 3, 4]))
        self.assertEqual([c["playerId"] for c in out], [0, 1, 2, 3, 4])
        self.assertTrue(all(C.validate(c) for c in out))

    def test_missing_player_gets_fallback_others_still_play(self):
        b = Brain(MemoryStore(), background=False)
        out = b.handle(payload(state(HOME, AWAY), 0, [1, 7]))
        self.assertEqual([c["playerId"] for c in out], [1, 7])
        self.assertEqual(out[1]["commandType"], "SET_STANCE")
        self.assertNotEqual(out[0]["commandType"], "SET_STANCE")

    def test_playbook_loaded_just_after_kickoff_is_applied(self):
        st = MemoryStore()
        b = Brain(st, background=False)
        b.handle(payload(state(HOME, AWAY, t=0.5, tick=1), 0, [2]))
        st.put_json("active.json", {"playbook": Playbook({}, 4, "challenger").to_json()})
        b._last_refresh = -1e9
        b.handle(payload(state(HOME, AWAY, t=3, tick=2), 0, [2]))
        self.assertEqual(b.base.version, 4)

    def test_stale_pending_is_cleared(self):
        st = MemoryStore()
        b = Brain(st, background=False)
        st.put_json("active.json", {"playbook": Playbook({}, 2, "challenger").to_json()})
        b.refresh()
        self.assertIsNotNone(b.pending)
        st.put_json("active.json", {"playbook": Playbook().to_json()})
        b.refresh()
        self.assertIsNone(b.pending)

    def test_last_seconds_are_flushed_every_tick(self):
        st = MemoryStore()
        b = Brain(st, runtime="2", background=False)
        for k, t in enumerate([200, 202, 204, 290, 292, 299]):
            b.handle(payload(state(HOME, AWAY, t=t, tick=k, score=(0, 1 if t == 299 else 0)), 0, [2]))
            b.recorder.join()
        lines = st.get_text(f"matches/{b.recorder.match_id}/p2.jsonl").splitlines()
        self.assertEqual(json.loads(lines[-1])["st"], 1)

    def test_late_joining_runtime_gets_same_match_id(self):
        clock = [10_000.0]
        a = Recorder(MemoryStore(), "1", clock=lambda: clock[0], idle_flush=False)
        ida = a.start(0, game_time=0)
        clock[0] += 150
        late = Recorder(MemoryStore(), "2", clock=lambda: clock[0], idle_flush=False)
        self.assertEqual(late.start(0, game_time=150), ida)


if __name__ == "__main__":
    unittest.main()
