import unittest
from dataclasses import asdict

from golden import payloads

from agent.archetypes import ARCHETYPES, STAND_INS
from agent.model import DEFAULT_SETTINGS, DEFAULT_STYLE
from agent.profile import parse_profile
from agent.runtime import Runtime


class ArchetypeTest(unittest.TestCase):
    def test_names_and_spec_values(self):
        self.assertEqual(
            sorted(ARCHETYPES),
            ["balanced", "hard_tackle", "park_the_bus", "press_aggressive", "time_waster"],
        )
        self.assertEqual(
            sorted(STAND_INS), ["aws_like_attacking", "aws_like_balanced", "aws_like_defensive"]
        )
        balanced = ARCHETYPES["balanced"]
        self.assertEqual((balanced.settings, balanced.style), (DEFAULT_SETTINGS, DEFAULT_STYLE))
        park = ARCHETYPES["park_the_bus"]
        self.assertEqual(
            (park.style.block_depth, park.style.attackers_committed, park.style.formation),
            (0.1, 1, "2-1-1"),
        )
        self.assertEqual(park.settings.press_radius, 12.0)
        waster = ARCHETYPES["time_waster"]
        self.assertEqual((waster.settings.pass_margin, waster.settings.shot_range), (4.5, 20.0))

    def test_every_profile_is_valid_and_round_trips(self):
        for name, profile in {**ARCHETYPES, **STAND_INS}.items():
            with self.subTest(name=name):
                self.assertEqual(profile.version, f"archetype-{name}")
                self.assertEqual(profile.coach_policy, "none")
                self.assertEqual(parse_profile(asdict(profile)), profile)

    def test_every_profile_decides_without_errors(self):
        for name, profile in {**ARCHETYPES, **STAND_INS}.items():
            records = []
            runtime = Runtime(profile, emit=records.append)
            try:
                for p in payloads()[:40]:
                    for pid in range(5):
                        runtime.handle({**p, "myPlayers": [pid]})
                self.assertTrue(runtime.flush_logs())
            finally:
                runtime.close()
            with self.subTest(name=name):
                self.assertEqual([r for r in records if r["type"] == "error"], [])


if __name__ == "__main__":
    unittest.main()
