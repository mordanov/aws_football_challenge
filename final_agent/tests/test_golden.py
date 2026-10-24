import json
import os
import unittest
from unittest.mock import patch

from golden import FIXTURE, LEGACY_MODES, TACTICS_V2_FIXTURE, TACTICS_V2_MODES, responses


class GoldenDecisionsTest(unittest.TestCase):
    def setUp(self):
        modes = patch.dict(os.environ, LEGACY_MODES)
        modes.start()
        self.addCleanup(modes.stop)

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


class TacticsV2GoldenDecisionsTest(unittest.TestCase):
    def test_enabled_modes_reproduce_reviewed_tactics_v2_decisions(self):
        expected = json.loads(TACTICS_V2_FIXTURE.read_text())["responses"]
        with patch.dict(os.environ, TACTICS_V2_MODES):
            self.assertEqual(responses(), expected)


if __name__ == "__main__":
    unittest.main()
