"""Unit tests for live_coach and live_settings — no real AWS calls."""
from __future__ import annotations

import json
import unittest
import unittest.mock

from agent.live_coach import (
    DEFAULTS,
    PARAM_RANGES,
    MatchState,
    _state_prompt,
    call_coach,
    write_advice,
)
from agent.live_settings import LiveSettingsCache

# ---------------------------------------------------------------------------
# LiveSettingsCache
# ---------------------------------------------------------------------------

class LiveSettingsCacheTest(unittest.TestCase):
    def _make_cache(self, s3_mock, ttl=10.0):
        return LiveSettingsCache(s3_mock, "test-bucket", "match-abc", ttl=ttl)

    def test_returns_empty_dict_on_missing_key(self):
        s3 = unittest.mock.MagicMock()
        s3.get_object.side_effect = Exception("NoSuchKey")
        cache = self._make_cache(s3)
        self.assertEqual(cache.get(), {})

    def test_reads_changes_key_from_s3(self):
        s3 = unittest.mock.MagicMock()
        body = unittest.mock.MagicMock()
        body.read.return_value = json.dumps({"changes": {"rush_s": 10}, "reason": "x"}).encode()
        s3.get_object.return_value = {"Body": body}
        cache = self._make_cache(s3)
        self.assertEqual(cache.get(), {"rush_s": 10})

    def test_reads_flat_dict_without_changes_wrapper(self):
        s3 = unittest.mock.MagicMock()
        body = unittest.mock.MagicMock()
        body.read.return_value = json.dumps({"rush_enabled": 0}).encode()
        s3.get_object.return_value = {"Body": body}
        cache = self._make_cache(s3)
        self.assertEqual(cache.get(), {"rush_enabled": 0})

    def test_caches_within_ttl(self):
        s3 = unittest.mock.MagicMock()
        body = unittest.mock.MagicMock()
        body.read.return_value = json.dumps({"rush_s": 5}).encode()
        s3.get_object.return_value = {"Body": body}
        cache = self._make_cache(s3, ttl=60.0)
        cache.get()
        cache.get()
        self.assertEqual(s3.get_object.call_count, 1)

    def test_refreshes_after_ttl(self):
        s3 = unittest.mock.MagicMock()
        body = unittest.mock.MagicMock()
        body.read.return_value = json.dumps({"rush_s": 5}).encode()
        s3.get_object.return_value = {"Body": body}
        cache = self._make_cache(s3, ttl=0.0)  # always expired
        cache.get()
        cache.get()
        self.assertGreaterEqual(s3.get_object.call_count, 2)

    def test_keeps_last_value_on_error(self):
        s3 = unittest.mock.MagicMock()
        body = unittest.mock.MagicMock()
        body.read.return_value = json.dumps({"rush_s": 7}).encode()
        s3.get_object.return_value = {"Body": body}
        cache = self._make_cache(s3, ttl=0.0)
        cache.get()  # populates cache
        s3.get_object.side_effect = Exception("timeout")
        result = cache.get()  # should keep old value
        self.assertEqual(result.get("rush_s"), 7)


# ---------------------------------------------------------------------------
# live_coach helpers
# ---------------------------------------------------------------------------

class StatePromptTest(unittest.TestCase):
    def test_prompt_contains_score(self):
        state = MatchState(clock=60, duration=120, our_score=1, opp_score=0)
        text = _state_prompt(state, DEFAULTS)
        self.assertIn("Winning 1:0", text)

    def test_prompt_contains_all_params(self):
        state = MatchState()
        text = _state_prompt(state, DEFAULTS)
        for k in DEFAULTS:
            self.assertIn(k, text)

    def test_prompt_shows_losing(self):
        state = MatchState(our_score=0, opp_score=2)
        text = _state_prompt(state, DEFAULTS)
        self.assertIn("Losing", text)

    def test_prompt_shows_drawing(self):
        state = MatchState(our_score=1, opp_score=1)
        text = _state_prompt(state, DEFAULTS)
        self.assertIn("Drawing", text)


class CallCoachTest(unittest.TestCase):
    def _bedrock(self, response_text: str):
        bedrock = unittest.mock.MagicMock()
        bedrock.converse.return_value = {
            "output": {
                "message": {
                    "content": [{"text": response_text}]
                }
            }
        }
        return bedrock

    def test_parses_valid_json_response(self):
        bedrock = self._bedrock('{"changes": {"rush_s": 8}, "reason": "too risky"}')
        result = call_coach(bedrock, MatchState(), dict(DEFAULTS))
        self.assertEqual(result["changes"], {"rush_s": 8.0})
        self.assertEqual(result["reason"], "too risky")

    def test_clamps_out_of_range_values(self):
        bedrock = self._bedrock('{"changes": {"rush_s": 999}, "reason": "extreme"}')
        result = call_coach(bedrock, MatchState(), dict(DEFAULTS))
        lo, hi = PARAM_RANGES["rush_s"]
        self.assertLessEqual(result["changes"]["rush_s"], hi)

    def test_ignores_unknown_params(self):
        bedrock = self._bedrock('{"changes": {"unknown_param": 42}, "reason": "x"}')
        result = call_coach(bedrock, MatchState(), dict(DEFAULTS))
        self.assertNotIn("unknown_param", result["changes"])

    def test_empty_changes_on_bad_json(self):
        bedrock = self._bedrock("sorry I cannot help with that")
        result = call_coach(bedrock, MatchState(), dict(DEFAULTS))
        self.assertEqual(result["changes"], {})

    def test_empty_changes_on_bedrock_error(self):
        bedrock = unittest.mock.MagicMock()
        bedrock.converse.side_effect = RuntimeError("throttled")
        result = call_coach(bedrock, MatchState(), dict(DEFAULTS))
        self.assertEqual(result["changes"], {})
        self.assertIn("error", result["reason"])

    def test_json_with_surrounding_text(self):
        bedrock = self._bedrock('Here is my advice: {"changes": {"press_radius": 18}, "reason": "ok"}')
        result = call_coach(bedrock, MatchState(), dict(DEFAULTS))
        self.assertEqual(result["changes"].get("press_radius"), 18.0)


class WriteAdviceTest(unittest.TestCase):
    def test_writes_to_correct_key(self):
        s3 = unittest.mock.MagicMock()
        write_advice(s3, "my-bucket", "match-xyz", {"changes": {}, "reason": "test"})
        call_kwargs = s3.put_object.call_args[1]
        self.assertEqual(call_kwargs["Bucket"], "my-bucket")
        self.assertIn("match-xyz", call_kwargs["Key"])
        body = json.loads(call_kwargs["Body"])
        self.assertEqual(body["match_id"], "match-xyz")
        self.assertIn("ts", body)


# ---------------------------------------------------------------------------
# tactics.py live overrides
# ---------------------------------------------------------------------------

class TacticsLiveOverrideTest(unittest.TestCase):
    """Verify that live dict overrides work in decide() and _tactical_context()."""

    def _snap(self, clock=5.0, score=(0, 0), owner_team=0, owner_pid=2):
        from agent.model import Player, Snapshot
        OURS = [(-51, 0), (-28, 0), (-5, 0), (20, -15), (23, 15)]
        THEIRS = [(51, 0), (30, 0), (8, 20), (-10, -20), (-12, 20)]
        m = 1
        players = tuple(
            Player(side, pid, m * x, y, stamina=0.9)
            for side, coords in ((0, OURS), (1, THEIRS))
            for pid, (x, y) in enumerate(coords)
        )
        owner = (owner_team, owner_pid) if owner_pid is not None else None
        return Snapshot(0, 5, clock, 120.0, players, (0.0, 0.0), (0.0, 0.0), owner, owner is None, score)

    def test_live_rush_s_suppresses_rush(self):
        from agent.tactics import _tactical_context
        s = self._snap(clock=5.0)
        # Default rush_s=15 → rush should be on at t=5
        rush, _ = _tactical_context(s)
        self.assertEqual(rush, 1)
        # Override rush_s=3 → t=5 > 3, rush off
        rush, _ = _tactical_context(s, live={"rush_s": 3})
        self.assertEqual(rush, 0)

    def test_live_rush_enabled_0_disables_rush(self):
        from agent.tactics import _tactical_context
        s = self._snap(clock=5.0)
        rush, _ = _tactical_context(s, live={"rush_enabled": 0})
        self.assertEqual(rush, 0)

    def test_live_none_identical_to_no_live(self):
        from agent.tactics import decide
        s = self._snap(clock=40.0, score=(0, 0), owner_pid=None)
        d1 = decide(s, 2)
        d2 = decide(s, 2, live=None)
        self.assertEqual(d1.command, d2.command)

    def test_live_empty_dict_no_change(self):
        from agent.tactics import decide
        s = self._snap(clock=40.0, score=(0, 0), owner_pid=None)
        d1 = decide(s, 2)
        d2 = decide(s, 2, live={})
        self.assertEqual(d1.command, d2.command)


if __name__ == "__main__":
    unittest.main()
