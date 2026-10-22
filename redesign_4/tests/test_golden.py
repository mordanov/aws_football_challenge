import json
import unittest

from golden import FIXTURE, responses


class GoldenDecisionsTest(unittest.TestCase):
    def test_default_profile_reproduces_golden_decisions(self):
        expected = json.loads(FIXTURE.read_text())["responses"]
        self.assertEqual(responses(), expected)

    def test_golden_covers_varied_commands(self):
        rows = json.loads(FIXTURE.read_text())["responses"]
        kinds = {json.loads(r)[0]["commandType"] for row in rows for r in row}
        self.assertGreaterEqual(len(kinds), 5, kinds)

    def test_v1_shaped_profile_reproduces_golden_decisions(self):
        from dataclasses import asdict

        from agent.model import DEFAULT_SETTINGS
        from agent.profile import parse_profile

        v1 = parse_profile(
            {"version": "baseline", "settings": asdict(DEFAULT_SETTINGS), "opponents": None}
        )
        expected = json.loads(FIXTURE.read_text())["responses"]
        self.assertEqual(responses(v1), expected)


if __name__ == "__main__":
    unittest.main()
