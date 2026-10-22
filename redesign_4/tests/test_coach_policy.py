import json
import time
import unittest
from dataclasses import replace

from golden import payloads
from test_runtime import payload
from test_style_tactics import snap

from agent.coach import choose_preset
from agent.model import Profile
from agent.runtime import Runtime


class ChoosePresetTest(unittest.TestCase):
    def test_table(self):
        base = snap()
        cases = [
            ((0, 1), 10.0, "", "press_high"),
            ((0, 1), 80.0, "", "go_all_out_attack"),  # r = 0.333
            ((1, 0), 80.0, "", "slow_the_tempo"),
            ((1, 0), 10.0, "", ""),
            ((0, 0), 100.0, "", "shoot_on_sight"),  # r = 0.167
            ((0, 0), 60.0, "", ""),
            ((0, 1), 10.0, "slow_the_tempo", "slow_the_tempo"),  # platform preset wins
        ]
        for score, clock, sent, expected in cases:
            with self.subTest(score=score, clock=clock, sent=sent):
                s = replace(base, score=score, clock=clock, coach=sent)
                self.assertEqual(choose_preset(s), expected)

    def test_score_is_read_from_own_side(self):
        s = replace(snap(team=1), score=(1, 0), clock=10.0)
        self.assertEqual(choose_preset(s), "press_high")


class RuntimeCoachTest(unittest.TestCase):
    def coach_seen(self, policy):
        losing = json.loads(payload()["prompt"])
        losing["gameState"]["score"] = {"home": 0, "away": 1}
        seen = []
        for pid in range(5):
            records = []
            runtime = Runtime(Profile(coach_policy=policy), emit=records.append)
            try:
                runtime.handle({"prompt": json.dumps({**losing, "myPlayers": [pid]})})
                self.assertTrue(runtime.flush_logs())
            finally:
                runtime.close()
            seen.append(records[-1]["snapshot"]["coach"])
        return seen

    def test_all_players_agree_on_the_rules_preset(self):
        self.assertEqual(self.coach_seen("rules"), ["press_high"] * 5)

    def test_no_policy_leaves_coach_empty(self):
        self.assertEqual(self.coach_seen("none"), [""] * 5)

    def test_p99_latency_under_50ms(self):
        profile = Profile(coach_policy="rules")
        runtime = Runtime(profile, emit=lambda _record: None)
        timings = []
        try:
            for p in payloads():
                for pid in range(5):
                    start = time.perf_counter()
                    runtime.handle({**p, "myPlayers": [pid]})
                    timings.append(time.perf_counter() - start)
        finally:
            runtime.close()
        timings.sort()
        self.assertLess(timings[int(0.99 * len(timings))], 0.050)


if __name__ == "__main__":
    unittest.main()
