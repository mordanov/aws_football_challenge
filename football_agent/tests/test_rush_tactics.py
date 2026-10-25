import json
import os
import unittest
import unittest.mock

from agent.model import Player, Snapshot
from agent.tactics import _tactical_context, decide  # noqa: F401

OURS = [(-51, 0), (-28, 0), (-5, 0), (20, -15), (23, 15)]
THEIRS = [(51, 0), (30, 0), (8, 20), (-10, -20), (-12, 20)]


def snap120(team=0, clock=5.0, score=(0, 0), owner=None, ball=(0, 0), ours=None, theirs=None):
    """Build a Snapshot with matchDuration=120."""
    m = 1 if team == 0 else -1
    sides = {team: ours or OURS, 1 - team: theirs or THEIRS}
    players = tuple(
        Player(side, pid, m * x, y, stamina=0.9)
        for side in (0, 1)
        for pid, (x, y) in enumerate(sides[side])
    )
    resolved_owner = None
    if owner is not None:
        resolved_owner = (team if owner[0] == 0 else 1 - team, owner[1])
    return Snapshot(
        team, 5, clock, 120.0, players,
        (m * ball[0], ball[1]), (0.0, 0.0),
        resolved_owner, resolved_owner is None, score,
    )


def target(decision):
    return (
        decision.command["parameters"]["target_x"],
        decision.command["parameters"]["target_y"],
    )


class TacticalContextTest(unittest.TestCase):
    def test_rush_active_at_kickoff(self):
        s = snap120(clock=5.0, score=(0, 0), owner=(0, 2))
        rush, stage = _tactical_context(s)
        self.assertEqual(rush, 1)
        self.assertEqual(stage, 0)

    def test_rush_off_when_opponent_has_ball(self):
        s = snap120(clock=5.0, score=(0, 0), owner=(1, 1))
        rush, _ = _tactical_context(s)
        self.assertEqual(rush, 0)

    def test_rush_off_after_time(self):
        s = snap120(clock=20.0, score=(0, 0), owner=(0, 2))
        rush, _ = _tactical_context(s)
        self.assertEqual(rush, 0)

    def test_rush_off_when_scored(self):
        s = snap120(clock=5.0, score=(1, 0), owner=(0, 2))
        rush, _ = _tactical_context(s)
        self.assertEqual(rush, 0)

    def test_rush_off_when_free_ball_deep_in_own_half(self):
        """Rush must not activate when ball is free but deep in our own half (≥10m from center).

        Regression: free ball at x=+26 for team1 (own half) was triggering rush because
        ball_not_at_opp only checked owner, not ball position. Rush then sent players toward
        opponent territory while the opponent had a clear path to our goal — causing early goals.
        """
        for team in (0, 1):
            # ball=(-26, 0) in snap120 coords → actual x = m*(-26); for team1 m=-1 → x=+26 (own half)
            # for team0 m=+1 → x=-26 (also own half). Both cases should suppress rush.
            s = snap120(team=team, clock=7.0, score=(0, 0), owner=None, ball=(-26, 0))
            rush, _ = _tactical_context(s)
            with self.subTest(team=team):
                self.assertEqual(rush, 0, f"rush should be off for free ball deep in own half (team={team})")

    def test_rush_still_on_when_free_ball_near_center(self):
        """Rush activates when ball is free near or past center line."""
        for team in (0, 1):
            s = snap120(team=team, clock=5.0, score=(0, 0), owner=None, ball=(5, 0))
            rush, _ = _tactical_context(s)
            with self.subTest(team=team):
                self.assertEqual(rush, 1, f"rush should activate for free ball in opponent half (team={team})")

    def test_rush_disabled_by_env(self):
        s = snap120(clock=5.0, score=(0, 0), owner=(0, 2))
        with unittest.mock.patch.dict(os.environ, {"RUSH_ENABLED": "0"}):
            rush, _ = _tactical_context(s)
        self.assertEqual(rush, 0)

    def test_stage_1_when_losing_late(self):
        s = snap120(clock=95.0, score=(0, 1))  # 25s remaining, losing
        _, stage = _tactical_context(s)
        self.assertEqual(stage, 1)

    def test_stage_0_when_drawing_late(self):
        s = snap120(clock=95.0, score=(0, 0))
        _, stage = _tactical_context(s)
        self.assertEqual(stage, 0)

    def test_stage_2_requires_draw_at_any_cost_flag(self):
        s = snap120(clock=112.0, score=(0, 1))  # 8s remaining, losing
        _, stage = _tactical_context(s)
        self.assertEqual(stage, 1)  # flag off by default
        with unittest.mock.patch.dict(os.environ, {"DRAW_AT_ANY_COST": "1"}):
            _, stage = _tactical_context(s)
        self.assertEqual(stage, 2)


class RushTest(unittest.TestCase):
    # owner=(0, 0): GK has ball → players 1-4 are in support branch (MOVE_TO), no presser/marks.
    def test_rush_pushes_mid_fwd_forward(self):
        """AT-1: players 2,3,4 further forward at t=5 than t=40; player 1 (DEF) unchanged."""
        for team in (0, 1):
            d = 1 if team == 0 else -1
            rush_s = snap120(team=team, clock=5.0, score=(0, 0), owner=(0, 0))
            late_s = snap120(team=team, clock=40.0, score=(0, 0), owner=(0, 0))
            for pid in (2, 3, 4):
                rx = target(decide(rush_s, pid))[0]
                lx = target(decide(late_s, pid))[0]
                with self.subTest(team=team, pid=pid):
                    self.assertGreater(d * rx, d * lx, f"pid={pid} not further forward in rush")
            # DEF position unchanged (GK is carrier so skip pid=0)
            rx1 = target(decide(rush_s, 1))[0]
            lx1 = target(decide(late_s, 1))[0]
            with self.subTest(team=team, pid=1):
                self.assertAlmostEqual(rx1, lx1, delta=1.0)

    def test_rush_off_when_conditions_fail(self):
        """AT-2: rush off when time >= 15 or score != 0:0; positions equal normal play."""
        # Use owner=(0, 0) so players 2,3,4 are in support branch (MOVE_TO) in all cases.
        scored = snap120(clock=5.0, score=(1, 0), owner=(0, 0))   # score not 0:0 → no rush
        late = snap120(clock=16.0, score=(0, 0), owner=(0, 0))    # clock >= 15 → no rush
        normal = snap120(clock=40.0, score=(0, 0), owner=(0, 0))  # clearly off
        normal_x = {pid: target(decide(normal, pid))[0] for pid in (2, 3, 4)}
        for pid in (2, 3, 4):
            for s, label in ((late, "late"), (scored, "scored")):
                with self.subTest(pid=pid, case=label):
                    self.assertAlmostEqual(
                        target(decide(s, pid))[0], normal_x[pid], delta=0.5
                    )

    def test_rush_disabled_by_env_var(self):
        """AT-8: RUSH_ENABLED=0 gives same positions as normal play."""
        rush_s = snap120(clock=5.0, score=(0, 0), owner=(0, 0))
        normal = snap120(clock=40.0, score=(0, 0), owner=(0, 0))
        with unittest.mock.patch.dict(os.environ, {"RUSH_ENABLED": "0"}):
            for pid in (2, 3, 4):
                with self.subTest(pid=pid):
                    self.assertAlmostEqual(
                        target(decide(rush_s, pid))[0],
                        target(decide(normal, pid))[0],
                        delta=0.5,
                    )


def _losing(team: int) -> tuple[int, int]:
    """Score tuple where `team` is losing 0:1."""
    return (0, 1) if team == 0 else (1, 0)


class DesperateAttackTest(unittest.TestCase):
    def test_role_swap_stage1(self):
        """AT-3: players 1,2 further forward when losing 25s left vs 60s left."""
        # owner=(0, 0): GK has ball → players 1-4 in support branch (MOVE_TO).
        for team in (0, 1):
            d = 1 if team == 0 else -1
            desperate = snap120(team=team, clock=95.0, score=_losing(team), owner=(0, 0))
            normal = snap120(team=team, clock=60.0, score=_losing(team), owner=(0, 0))
            for pid in (1, 2):
                dx = target(decide(desperate, pid))[0]
                nx = target(decide(normal, pid))[0]
                with self.subTest(team=team, pid=pid):
                    self.assertGreater(d * dx, d * nx)
            draw = snap120(team=team, clock=95.0, score=(1, 1), owner=(0, 0))
            for pid in (1, 2):
                with self.subTest(team=team, pid=pid, case="draw"):
                    self.assertAlmostEqual(
                        target(decide(draw, pid))[0],
                        target(decide(normal, pid))[0],
                        delta=1.0,
                    )

    def test_gk_advances_in_stage2(self):
        """AT-4: GK past center with DRAW_AT_ANY_COST=1, losing, 8s left; stays home otherwise."""
        # owner=(0, 1): our DEF has ball → GK is NOT carrier, goes to GK branch → MOVE_TO.
        for team in (0, 1):
            d = 1 if team == 0 else -1
            s = snap120(team=team, clock=112.0, score=_losing(team), owner=(0, 1))
            with unittest.mock.patch.dict(os.environ, {"DRAW_AT_ANY_COST": "1"}):
                gk_x = target(decide(s, 0))[0]
            self.assertGreater(d * gk_x, 0, f"GK should be past center (team {team})")
            draw = snap120(team=team, clock=112.0, score=(1, 1), owner=(0, 1))
            with unittest.mock.patch.dict(os.environ, {"DRAW_AT_ANY_COST": "1"}):
                gk_x2 = target(decide(draw, 0))[0]
            self.assertLess(d * gk_x2, 0, "GK should stay home when drawing")
            gk_x3 = target(decide(s, 0))[0]
            self.assertLess(d * gk_x3, 0, "GK should stay home without flag")
            s_early = snap120(team=team, clock=90.0, score=_losing(team), owner=(0, 1))
            with unittest.mock.patch.dict(os.environ, {"DRAW_AT_ANY_COST": "1"}):
                gk_x4 = target(decide(s_early, 0))[0]
            self.assertLess(d * gk_x4, 0, "GK stays home at 30s remaining")

    def test_direction_correct_when_gk_advanced(self):
        """AT-5: outfield players still attack correct direction when GK is past center."""
        # owner=(0, 1): our DEF has ball → no marks/presser, players in support branch.
        for team in (0, 1):
            d = 1 if team == 0 else -1
            gk_pos = (8.0 * d, 0.0)
            ours = [(gk_pos[0], gk_pos[1]), (-28, 0), (-5, 0), (20, -15), (23, 15)]
            s = snap120(team=team, clock=112.0, score=_losing(team), owner=(0, 1), ours=ours)
            with unittest.mock.patch.dict(os.environ, {"DRAW_AT_ANY_COST": "1"}):
                fwd4_x = target(decide(s, 4))[0]
            self.assertGreater(d * fwd4_x, 0, f"FWD should still attack forward (team {team})")

    def test_gk_with_ball_acts_as_field_player_in_stage2(self):
        """AT-6: GK in stage 2 uses PASS/SHOOT not GK_DISTRIBUTE."""
        # team=0, score=(0,1): team 0 losing, 8s left → stage 2 with flag.
        s = snap120(clock=112.0, score=(0, 1), owner=(0, 0))
        with unittest.mock.patch.dict(os.environ, {"DRAW_AT_ANY_COST": "1"}):
            decision = decide(s, 0)
        self.assertNotEqual(decision.command["commandType"], "GK_DISTRIBUTE")


class ValidityTest(unittest.TestCase):
    def test_all_players_valid_at_key_times(self):
        """AT-7: valid commands for all 5 players at 5, 95, 112s."""
        from agent.protocol import validate_command

        for clock in (5.0, 95.0, 112.0):
            for team in (0, 1):
                score = (0, 1) if clock >= 90 else (0, 0)
                owner = (0, 2) if clock < 15 else None
                s = snap120(team=team, clock=clock, score=score, owner=owner)
                for pid in range(5):
                    with self.subTest(team=team, clock=clock, pid=pid):
                        decision = decide(s, pid)
                        validate_command(decision.command)


class LoggingTest(unittest.TestCase):
    def _record(self, team, clock, score, owner_pid=None, env=None):
        from agent.runtime import Runtime

        records = []
        rt = Runtime(emit=records.append)
        s = snap120(
            team=team, clock=clock, score=score,
            owner=(0, owner_pid) if owner_pid is not None else None,
        )
        players = [
            {
                "agentId": f"agentId_{p.id}",
                "teamCode": "HOME" if p.team == 0 else "away",
                "position": {"x": p.x, "y": p.y},
                "velocity": {"x": 0, "y": 0},
                "stamina": 0.9,
            }
            for p in s.players
        ]
        state = {
            "tick": 5,
            "gameTime": clock,
            "matchDuration": 120.0,
            "players": players,
            "score": {"home": score[0], "away": score[1]},
            "ball": {
                "position": {"x": s.ball[0], "y": s.ball[1]},
                "velocity": {"x": 0, "y": 0},
                "isFree": owner_pid is None,
            },
        }
        if owner_pid is not None:
            state["ball"]["possessionAgentId"] = f"agentId_{owner_pid}"
            state["ball"]["possessionTeamId"] = team
        payload = {
            "prompt": json.dumps({"gameState": state, "teamId": team, "myPlayers": [2]})
        }
        with unittest.mock.patch.dict(os.environ, env or {}):
            rt.handle(payload)
        rt.flush_logs()
        return records[0]

    def test_rush_logged(self):
        rec = self._record(team=0, clock=5.0, score=(0, 0), owner_pid=2)
        self.assertEqual(rec.get("rush"), 1)
        self.assertEqual(rec.get("stage"), 0)

    def test_stage_logged(self):
        rec = self._record(team=0, clock=95.0, score=(0, 1))
        self.assertEqual(rec.get("rush"), 0)
        self.assertEqual(rec.get("stage"), 1)
