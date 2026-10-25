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
