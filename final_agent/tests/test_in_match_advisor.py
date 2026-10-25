import math
from dataclasses import replace
from agent.model import Player, Snapshot
from agent.advisor.in_match import TacticalAdvice, AdviceEvent, _extract_features


def _snap(team=0, clock=30.0, score=(0, 0), owner=None, ball=(5.0, 0.0)):
    d = 1 if team == 0 else -1
    players = (
        Player(team,   0, d * -50.0,  0.0, stamina=0.9),   # GK
        Player(team,   1, d * -25.0,  0.0, stamina=0.8),   # DEF
        Player(team,   2, d *   0.0,  0.0, stamina=0.7),   # MID
        Player(team,   3, d *  25.0, -10.0, stamina=0.6),  # FWD3
        Player(team,   4, d *  25.0,  10.0, stamina=0.5),  # FWD4
        Player(1-team, 0, d *  50.0,  0.0),
        Player(1-team, 1, d *  30.0,  0.0),
        Player(1-team, 2, d *  10.0,  0.0),
        Player(1-team, 3, d * -10.0, -10.0),
        Player(1-team, 4, d * -10.0,  10.0),
    )
    return Snapshot(team, 5, clock, 120.0, players, (d * ball[0], ball[1]),
                    (0.0, 0.0), owner, owner is None, score)


class TestExtractFeatures:
    def test_clock_and_remaining(self):
        s = _snap(clock=40.0)
        f = _extract_features(s)
        assert f["clock"] == 40.0
        assert f["remaining"] == 80.0

    def test_score_diff_our_minus_opp(self):
        s = _snap(team=0, score=(2, 1))
        assert _extract_features(s)["score_diff"] == 1
        s = _snap(team=1, score=(2, 1))
        assert _extract_features(s)["score_diff"] == -1

    def test_possession_labels(self):
        s = _snap(team=0, owner=(0, 2))
        assert _extract_features(s)["possession"] == "us"
        s = _snap(team=0, owner=(1, 1))
        assert _extract_features(s)["possession"] == "them"
        s = _snap(team=0, owner=None)
        assert _extract_features(s)["possession"] == "free"

    def test_our_avg_x_reflects_actual_positions(self):
        s = _snap(team=0)
        f = _extract_features(s)
        # GK=-50, DEF=-25, MID=0, FWD=+25×2 → avg = -5.0 for team 0 (d=1, no flip)
        assert f["our_avg_x"] == -5.0

    def test_sign_flip_for_team1(self):
        # team 1 should get same positive avg_x as team 0 when mirrored
        s0 = _snap(team=0)
        s1 = _snap(team=1)
        assert abs(_extract_features(s0)["our_avg_x"] - _extract_features(s1)["our_avg_x"]) < 1.0

    def test_opp_in_our_half(self):
        s = _snap(team=0)  # opp players at -10,-10 which is our half (d=1, opp x < 0)
        f = _extract_features(s)
        assert f["opp_in_our_half"] == 2

    def test_avg_stamina(self):
        s = _snap(team=0)
        f = _extract_features(s)
        expected = (0.9 + 0.8 + 0.7 + 0.6 + 0.5) / 5
        assert abs(f["avg_stamina"] - expected) < 0.01


# ---------------------------------------------------------------------------
# Task 2: InMatchAdvisor skeleton
# ---------------------------------------------------------------------------
from agent.advisor.in_match import InMatchAdvisor


class TestInMatchAdvisor:
    def _advisor(self):
        return InMatchAdvisor(llm=None, team_id="test-team")

    def test_default_mode_is_normal(self):
        adv = self._advisor()
        assert adv.current_mode().mode == "normal"

    def test_trigger_fires_on_first_tick(self):
        """First tick always fires: clock - (-inf) >= 20."""
        adv = self._advisor()
        adv.reset("m1")
        calls = []
        adv._call_llm = lambda feats: calls.append(feats) or adv._cache
        adv.maybe_advise(_snap(clock=0.0))
        assert len(calls) == 1

    def test_trigger_fires_on_score_change(self):
        adv = self._advisor()
        adv.reset("m1")
        calls = []
        adv._call_llm = lambda feats: calls.append(feats) or adv._cache
        adv.maybe_advise(_snap(clock=0.0))          # first trigger
        adv.maybe_advise(_snap(clock=5.0))          # no trigger (too soon, score same)
        adv.maybe_advise(_snap(clock=5.0, score=(1, 0)))  # score changed → trigger
        assert len(calls) == 2

    def test_no_double_trigger_when_both_conditions_met(self):
        """Score change AND >=20 s: fire exactly once, not twice."""
        adv = self._advisor()
        adv.reset("m1")
        calls = []
        adv._call_llm = lambda feats: calls.append(feats) or adv._cache
        adv.maybe_advise(_snap(clock=0.0))             # first
        adv.maybe_advise(_snap(clock=25.0, score=(1, 0)))  # both conditions
        assert len(calls) == 2

    def test_trigger_fires_on_20s_elapsed(self):
        adv = self._advisor()
        adv.reset("m1")
        calls = []
        adv._call_llm = lambda feats: calls.append(feats) or adv._cache
        adv.maybe_advise(_snap(clock=0.0))
        adv.maybe_advise(_snap(clock=19.9))   # no trigger
        adv.maybe_advise(_snap(clock=20.0))   # trigger (20.0 - 0.0 >= 20)
        assert len(calls) == 2

    def test_outcome_filled_on_next_advice(self):
        """Previous event gets outcome when next advice fires >=20 s later."""
        adv = self._advisor()
        adv.reset("m1")
        adv._call_llm = lambda feats: adv._cache
        adv.maybe_advise(_snap(clock=0.0, score=(0, 0)))
        assert adv._advice_log[0].outcome is None
        adv.maybe_advise(_snap(clock=20.0, score=(1, 0)))
        assert adv._advice_log[0].outcome is not None
        assert adv._advice_log[0].outcome["score_delta"] == 1

    def test_reset_clears_log_and_cache(self):
        adv = self._advisor()
        adv.reset("m1")
        adv._call_llm = lambda feats: adv._cache
        adv.maybe_advise(_snap(clock=0.0))
        adv.reset("m2")
        assert adv._advice_log == []
        assert adv.current_mode().mode == "normal"

    def test_flush_clears_log(self):
        adv = self._advisor()
        adv.reset("m1")
        adv._call_llm = lambda feats: adv._cache
        adv.maybe_advise(_snap(clock=0.0))
        flushed = []
        adv.flush_advice_log(flushed.append)
        assert len(flushed) == 1
        assert adv._advice_log == []   # cleared after flush


# ---------------------------------------------------------------------------
# Task 3: decide() integration
# ---------------------------------------------------------------------------
from agent.tactics import decide
from agent.advisor.in_match import TacticalAdvice

_ATTACK_ADVICE = TacticalAdvice(mode="attack", def_x_fraction=0.3, gk_stays=True, rationale="test")
_NORMAL_ADVICE = TacticalAdvice(mode="normal", def_x_fraction=0.0, gk_stays=False, rationale="test")


class TestDecideWithAdvice:
    def _target_x(self, pid, s, advice=None):
        d = decide(s, pid, advice=advice)
        return d.command["parameters"]["target_x"]

    def test_def_moves_forward_in_attack_mode(self):
        """DEF (pid 1) x-position should be further forward with attack advice."""
        for team in (0, 1):
            direction = 1 if team == 0 else -1
            s = _snap(team=team, clock=60.0)
            x_normal = self._target_x(1, s)
            x_attack = self._target_x(1, s, advice=_ATTACK_ADVICE)
            assert direction * x_attack > direction * x_normal, f"team={team}"

    def test_def_unaffected_by_normal_advice(self):
        """advice.mode='normal' does not change DEF position."""
        s = _snap(clock=60.0)
        assert self._target_x(1, s) == self._target_x(1, s, advice=_NORMAL_ADVICE)

    def test_def_unaffected_when_advice_is_none(self):
        """No advice → existing behavior unchanged."""
        s = _snap(clock=60.0)
        assert self._target_x(1, s) == self._target_x(1, s, advice=None)

    def test_gk_stays_near_goal_line(self):
        """GK (pid 0) with gk_stays=True should be at x near -0.90 * 55 from our side."""
        for team in (0, 1):
            direction = 1 if team == 0 else -1
            s = _snap(team=team, clock=60.0)
            x = self._target_x(0, s, advice=_ATTACK_ADVICE)
            assert direction * x <= -45.0, f"GK not near goal line: x={x}, team={team}"

    def test_gk_with_ball_not_overridden(self):
        """GK carrying the ball → gk_stays does not apply (carrier block returns first)."""
        s = _snap(team=0, owner=(0, 0), clock=60.0)  # GK has ball
        d = decide(s, 0, advice=_ATTACK_ADVICE)
        # GK with ball gets a carrier action (PASS/SHOOT/MOVE_TO with forward target),
        # not repositioned to goal line
        if d.command["commandType"] == "MOVE_TO":
            assert d.command["parameters"]["target_x"] > -45.0


# ---------------------------------------------------------------------------
# Task 4: _call_llm
# ---------------------------------------------------------------------------

import sys
import types
import unittest.mock

def _make_fake_langchain():
    """Inject a stub langchain_core.messages into sys.modules for tests."""
    mod = types.ModuleType("langchain_core")
    msgs = types.ModuleType("langchain_core.messages")

    class SystemMessage:
        def __init__(self, content):
            self.content = content

    class HumanMessage:
        def __init__(self, content):
            self.content = content

    msgs.SystemMessage = SystemMessage
    msgs.HumanMessage = HumanMessage
    mod.messages = msgs
    sys.modules.setdefault("langchain_core", mod)
    sys.modules.setdefault("langchain_core.messages", msgs)
    return msgs


class _FakeLLM:
    def __init__(self, response_json: str):
        self._response_json = response_json

    def invoke(self, messages):
        class _Resp:
            pass
        r = _Resp()
        r.content = self._response_json
        return r


class TestCallLlm:
    def setup_method(self):
        _make_fake_langchain()

    def _advisor(self, llm):
        return InMatchAdvisor(llm=llm, team_id="test-team")

    def test_valid_response_parsed(self):
        """Happy path: LLM returns valid JSON → TacticalAdvice with that mode."""
        import json
        payload = json.dumps({"mode": "attack", "def_x_fraction": 0.3, "gk_stays": True, "rationale": "go"})
        adv = self._advisor(_FakeLLM(payload))
        adv.reset("m1")
        result = adv._call_llm([])
        assert result.mode == "attack"
        assert result.def_x_fraction == 0.3
        assert result.gk_stays is True

    def test_invalid_mode_returns_cache(self):
        """LLM returns unknown mode → fall back to cached advice."""
        import json
        payload = json.dumps({"mode": "blitz", "def_x_fraction": 0.0, "gk_stays": False, "rationale": "x"})
        adv = self._advisor(_FakeLLM(payload))
        adv.reset("m1")
        result = adv._call_llm([])
        assert result.mode == "normal"  # default cache

    def test_malformed_json_returns_cache(self):
        """LLM returns garbage → fall back to cached advice, no exception."""
        adv = self._advisor(_FakeLLM("not-json!!!"))
        adv.reset("m1")
        result = adv._call_llm([])
        assert result.mode == "normal"

    def test_extra_fields_ignored(self):
        """LLM returns extra keys in JSON → no TypeError, parses cleanly."""
        import json
        payload = json.dumps({"mode": "defend", "def_x_fraction": -0.2, "gk_stays": False,
                              "rationale": "hold", "extra_field": "ignored"})
        adv = self._advisor(_FakeLLM(payload))
        adv.reset("m1")
        result = adv._call_llm([])
        assert result.mode == "defend"
        assert result.def_x_fraction == -0.2

    def test_none_llm_returns_cache(self):
        """llm=None → _call_llm returns cache immediately."""
        adv = self._advisor(None)
        adv.reset("m1")
        result = adv._call_llm([])
        assert result.mode == "normal"
