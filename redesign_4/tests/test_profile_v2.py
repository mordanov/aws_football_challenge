import copy
import unittest
from dataclasses import asdict

from agent.model import DEFAULT_SETTINGS, DEFAULT_STYLE, Profile, Style
from agent.profile import parse_profile

V1 = {"version": "baseline", "settings": asdict(DEFAULT_SETTINGS), "opponents": None}


class ProfileV2Test(unittest.TestCase):
    def test_v1_profile_loads_with_identity_style(self):
        profile = parse_profile(copy.deepcopy(V1))
        self.assertEqual(profile.style, DEFAULT_STYLE)
        self.assertEqual(profile.coach_policy, "none")
        self.assertEqual(
            DEFAULT_STYLE, Style(1.0, 3, 0.0, 0.5, "1-1-2")
        )

    def test_v2_round_trip(self):
        profile = Profile(
            version="park",
            style=Style(block_depth=0.1, attackers_committed=1, tackle_aggression=0.4,
                        tempo=0.3, formation="2-1-1"),
            coach_policy="rules",
        )
        self.assertEqual(parse_profile(profile.to_dict()), profile)

    def test_rejects_invalid_style_and_policy(self):
        good = Profile().to_dict()
        bad_styles = [
            {"block_depth": 1.5}, {"tempo": -0.1}, {"tackle_aggression": 2},
            {"attackers_committed": 0}, {"attackers_committed": 2.0},
            {"formation": "3-1"}, {"extra": 1}, {"carry": 1.5},
        ]
        for change in bad_styles:
            data = copy.deepcopy(good)
            data["style"].update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                parse_profile(data)
        missing = copy.deepcopy(good)
        del missing["style"]["tempo"]
        for data in (missing, {**good, "coach_policy": "llm"}, {**good, "surprise": 1}):
            with self.subTest(data=data), self.assertRaises(ValueError):
                parse_profile(data)

    def test_style_carry_is_optional_for_older_profiles(self):
        data = Profile().to_dict()
        del data["style"]["carry"]
        self.assertEqual(parse_profile(data).style.carry, 0.0)
        data["style"]["carry"] = 0.7
        self.assertEqual(parse_profile(data).style.carry, 0.7)


if __name__ == "__main__":
    unittest.main()
