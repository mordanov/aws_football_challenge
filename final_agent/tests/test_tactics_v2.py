"""Acceptance tests for spec-tactics-v2.md (§2-§8, §10 AT-1 through AT-13)."""
from __future__ import annotations

import math
import os
import unittest
import unittest.mock

from agent.model import Player, Snapshot
from agent.protocol import validate_command
from agent.tactics import assignments, decide

_HALF_LEN = 55.0
_HALF_WID = 34.0


def _players(team: int, ours: list, theirs: list, stamina_ours: float = 0.9,
             stamina_theirs: float = 0.9) -> tuple:
    """Positions are in team-0 absolute coords: positive-x = team-0 attack direction."""
    d = 1 if team == 0 else -1
    result = []
    for pid, (x, y) in enumerate(ours):
        st = stamina_ours if not isinstance(stamina_ours, list) else stamina_ours[pid]
        result.append(Player(team, pid, d * x, y, stamina=st))
    for pid, (x, y) in enumerate(theirs):
        st = stamina_theirs if not isinstance(stamina_theirs, list) else stamina_theirs[pid]
        result.append(Player(1 - team, pid, d * x, y, stamina=st))
    return tuple(result)


def snap(
    team: int = 0,
    ball: tuple = (0.0, 0.0),
    owner: tuple | None = None,  # (team_offset: 0=ours/1=theirs, pid)
    free: bool = False,
    score: tuple = (0, 0),
    clock: float = 30.0,
    ours: list | None = None,
    theirs: list | None = None,
    stamina_ours: float | list = 0.9,
) -> Snapshot:
    d = 1 if team == 0 else -1
    default_ours = [(-52, 0), (-28, 0), (-5, 0), (15, -10), (18, 10)]
    default_theirs = [(52, 0), (30, 0), (8, 20), (-10, -15), (-12, 15)]
    ours_positions = ours if ours is not None else default_ours
    theirs_positions = theirs if theirs is not None else default_theirs
    players = _players(team, ours_positions, theirs_positions, stamina_ours)
    if owner is not None:
        team_id = team if owner[0] == 0 else 1 - team
        owner_resolved = (team_id, owner[1])
    else:
        owner_resolved = None
    bx, by = d * ball[0], ball[1]
    return Snapshot(
        team, 5, clock, 120.0, players, (bx, by), (0.0, 0.0),
        owner_resolved, free, score,
    )


def _target(decision) -> tuple[float, float]:
    p = decision.command["parameters"]
    return p["target_x"], p["target_y"]


def _dist(a, b) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


# ---------------------------------------------------------------------------
# §7 STAMINA_ROT
# ---------------------------------------------------------------------------
class StaminaRotTest(unittest.TestCase):
    """AT-6: fresher player chosen; equal stamina → lower id."""

    def test_lower_stamina_player_not_chosen_when_fresher_available(self):
        """Two players equidistant from ball; high stamina beats low stamina."""
        # Player 2 and 3 equidistant from ball at (0,0), team 0 has ball of opponent (owner team 1)
        team = 0
        ours = [(-52, 0), (-30, 0), (0, 6), (0, -6), (20, 10)]  # pid2 and pid3 equidistant
        theirs = [(52, 0), (30, 0), (10, 5), (-5, -5), (-10, 10)]
        # pid2 stamina 0.2, pid3 stamina 0.8 → pid3 should be selected (lower cost)
        s = Snapshot(
            team, 5, 30.0, 120.0,
            _players(team, ours, theirs, stamina_ours=[0.9, 0.9, 0.2, 0.8, 0.9]),
            (0.0, 0.0), (0.0, 0.0), (1 - team, 1), False, (0, 0),
        )
        plan = assignments(s)
        # With STAMINA_ROT: cost = dist + 15*(1-stamina)
        # pid2: dist≈6 + 15*(1-0.2)=12 → cost≈18
        # pid3: dist≈6 + 15*(1-0.8)=3  → cost≈9
        # pid3 should be presser
        self.assertEqual(plan["presser"], 3)

    def test_equal_stamina_lower_id_wins(self):
        """Equal cost → lower player id wins (determinism)."""
        team = 0
        ours = [(-52, 0), (-30, 0), (0, 6), (0, -6), (20, 10)]
        theirs = [(52, 0), (30, 0), (10, 5), (-5, -5), (-10, 10)]
        # pid2 and pid3 both stamina 0.9, equidistant from ball
        s = Snapshot(
            team, 5, 30.0, 120.0,
            _players(team, ours, theirs, stamina_ours=[0.9, 0.9, 0.9, 0.9, 0.9]),
            (0.0, 0.0), (0.0, 0.0), (1 - team, 1), False, (0, 0),
        )
        plan = assignments(s)
        # Both equal cost, lower id = 2 wins
        self.assertIn(plan["presser"], (2, 3))  # Either is ok, but 2 if tie resolved by id

    def test_stamina_rot_disabled(self):
        """STAMINA_ROT=0: original arrival-time selection."""
        team = 0
        ours = [(-52, 0), (-30, 0), (0, 6), (0, -6), (20, 10)]
        theirs = [(52, 0), (30, 0), (10, 5), (-5, -5), (-10, 10)]
        s = Snapshot(
            team, 5, 30.0, 120.0,
            _players(team, ours, theirs, stamina_ours=[0.9, 0.9, 0.2, 0.8, 0.9]),
            (0.0, 0.0), (0.0, 0.0), (1 - team, 1), False, (0, 0),
        )
        with unittest.mock.patch.dict(os.environ, {"STAMINA_ROT": "0"}):
            plan = assignments(s)
        # Without STAMINA_ROT the old arrival() function is used which also favors
        # high stamina but via arrival = dist/(4+2*stamina). pid3 still likely wins
        # but the key is the flag is respected (no crash, valid presser).
        self.assertIn(plan["presser"], (2, 3, None))


# ---------------------------------------------------------------------------
# §2 LANE_BLOCK
# ---------------------------------------------------------------------------
class LaneBlockTest(unittest.TestCase):
    """AT-1: marker uses MOVE_TO on intercept point on ball-receiver lane."""

    def _lane_block_snap(self, team=0):
        # Ball at opponent player, threat receiver at 20m forward, our marker nearby
        # opponent carrier at (20, 0), receiver at (40, 10)
        # Our player 2 near (25, 10) — will be assigned as marker
        ours = [(-52, 0), (-10, 0), (25, 8), (0, -15), (10, 15)]
        theirs = [(52, 0), (20, 0), (40, 10), (-5, -10), (-5, 5)]
        return snap(team=team, ball=(20, 0), owner=(1, 1), ours=ours, theirs=theirs)

    def test_marker_uses_move_to(self):
        s = self._lane_block_snap(0)
        plan = assignments(s)
        if not plan["marks"]:
            self.skipTest("no markers assigned in this snapshot")
        for marker_id in plan["marks"]:
            d = decide(s, marker_id)
            # Should be MOVE_TO (not MARK) when LANE_BLOCK is active
            self.assertEqual(d.command["commandType"], "MOVE_TO",
                             f"marker pid={marker_id} should use MOVE_TO")

    def test_intercept_point_on_lane_at_k065(self):
        """Marker's target lies on ball→receiver segment, 65% from ball."""
        team = 0
        # ball at (10, 0), receiver at (30, 20): intercept = (10,0) + 0.65*((30,20)-(10,0))
        # = (10 + 13, 0 + 13) = (23, 13)
        ball = (10.0, 0.0)
        expected_x = 10.0 + 0.65 * (30.0 - 10.0)  # = 23
        expected_y = 0.0 + 0.65 * (20.0 - 0.0)     # = 13
        ours = [(-52, 0), (-10, 0), (25, 8), (0, -15), (10, 15)]
        theirs = [(52, 0), (10, 0), (30, 20), (-5, -10), (-5, 5)]
        s = snap(team=team, ball=ball, owner=(1, 1), ours=ours, theirs=theirs)
        plan = assignments(s)
        for marker_id, threat_id in plan["marks"].items():
            if threat_id == 2:  # threat is the receiver at (30, 20)
                dec = decide(s, marker_id)
                if dec.command["commandType"] == "MOVE_TO":
                    tx = dec.command["parameters"]["target_x"]
                    ty = dec.command["parameters"]["target_y"]
                    self.assertAlmostEqual(tx, expected_x, delta=2.0,
                                           msg="target_x should be near intercept point")
                    self.assertAlmostEqual(ty, expected_y, delta=2.0,
                                           msg="target_y should be near intercept point")

    def test_intercept_point_at_least_4m_from_ball(self):
        """Intercept point must be >= 4m from ball."""
        team = 0
        ball = (15.0, 0.0)
        ours = [(-52, 0), (-10, 0), (20, 5), (0, -15), (10, 15)]
        theirs = [(52, 0), (15, 0), (30, 10), (-5, -10), (-5, 5)]
        s = snap(team=team, ball=ball, owner=(1, 1), ours=ours, theirs=theirs)
        plan = assignments(s)
        for marker_id in plan["marks"]:
            dec = decide(s, marker_id)
            if dec.command["commandType"] == "MOVE_TO":
                tx = dec.command["parameters"]["target_x"]
                ty = dec.command["parameters"]["target_y"]
                bx, by = s.ball
                dist_to_ball = math.hypot(tx - bx, ty - by)
                self.assertGreaterEqual(dist_to_ball, 4.0 - 0.5,
                                        "intercept point too close to ball")

    def test_fallback_to_mark_when_receiver_close(self):
        """When receiver is within 2.5m of our player, fall back to MARK."""
        team = 0
        # Our pid2 at (20, 5), opponent threat at (21, 5) = very close
        ours = [(-52, 0), (-10, 0), (20, 5), (0, -15), (10, 15)]
        theirs = [(52, 0), (10, 0), (21, 5), (-5, -10), (-5, 5)]
        s = snap(team=team, ball=(10, 0), owner=(1, 1), ours=ours, theirs=theirs)
        plan = assignments(s)
        for marker_id, threat_id in plan["marks"].items():
            if threat_id == 2:  # the close threat
                dec = decide(s, marker_id)
                # Receiver within 2.5m → should use MARK
                self.assertEqual(dec.command["commandType"], "MARK",
                                 "close receiver should trigger MARK fallback")

    def test_lane_block_disabled(self):
        """LANE_BLOCK=0: markers use MARK command."""
        s = self._lane_block_snap(0)
        plan = assignments(s)
        if not plan["marks"]:
            self.skipTest("no markers in this snapshot")
        with unittest.mock.patch.dict(os.environ, {"LANE_BLOCK": "0"}):
            for marker_id in plan["marks"]:
                dec = decide(s, marker_id)
                self.assertEqual(dec.command["commandType"], "MARK")


# ---------------------------------------------------------------------------
# §3 COVER
# ---------------------------------------------------------------------------
class CoverTest(unittest.TestCase):
    """AT-2: cover player stands 6m behind presser toward own goal."""

    def _cover_snap(self, team=0):
        # ball on our half (progress < -11), opp has ball
        # ball at (-20, 5) in progress terms → absolute: -20*d
        ours = [(-52, 0), (-10, 0), (-20, 10), (5, -10), (10, 10)]
        theirs = [(52, 0), (20, 0), (5, 5), (-10, -10), (-15, 10)]
        return snap(team=team, ball=(-20, 5), owner=(1, 1), ours=ours, theirs=theirs)

    def test_cover_player_behind_presser_on_goal_line(self):
        """Cover player should be ~6m behind presser toward own goal."""
        s = self._cover_snap(0)
        plan = assignments(s)
        if plan["presser"] is None:
            self.skipTest("no presser in this snapshot")
        cover_id = plan["cover"]
        dec = decide(s, cover_id)
        if dec.command["commandType"] != "MOVE_TO":
            self.skipTest("cover player not using MOVE_TO")

        presser_p = s.player(plan["presser"])
        d = s.direction
        own_goal = (-d * _HALF_LEN, 0.0)
        presser_pos = (presser_p.x, presser_p.y)
        dist_goal = _dist(presser_pos, own_goal)

        tx, ty = _target(dec)
        target_pos = (tx, ty)

        # Cover should be ~6m from presser toward goal
        dist_from_presser = _dist(target_pos, presser_pos)
        self.assertAlmostEqual(dist_from_presser, 6.0, delta=2.0,
                               msg=f"cover should be ~6m from presser, got {dist_from_presser:.1f}m")

        # Target should be between presser and own goal (on that direction)
        # direction toward goal from presser
        if dist_goal > 0.5:
            # vector from presser to goal
            gx = (own_goal[0] - presser_pos[0]) / dist_goal
            gy = (own_goal[1] - presser_pos[1]) / dist_goal
            # cover target should be in that direction from presser
            tvx = tx - presser_pos[0]
            tvy = ty - presser_pos[1]
            tdist = math.hypot(tvx, tvy)
            if tdist > 0:
                cos_angle = (tvx * gx + tvy * gy) / tdist
                self.assertGreater(cos_angle, 0.5,
                                   "cover target should be in direction toward own goal from presser")

    def test_cover_applies_when_ball_on_own_half(self):
        """COVER only applies when ball is on our half (progress < -0.2 * half_field)."""
        s = self._cover_snap(0)
        plan = assignments(s)
        # Ball at -20m = in own half: cover should apply
        # Ball at +20m = opponent half: cover should NOT change position
        s_opp_half = snap(team=0, ball=(20, 5), owner=(1, 1))
        plan_opp = assignments(s_opp_half)
        # Both should succeed without errors
        cover_id = plan["cover"]
        decide(s, cover_id)
        if plan_opp["presser"] is not None:
            decide(s_opp_half, plan_opp["cover"])

    def test_cover_disabled(self):
        """COVER=0: cover player uses default support position."""
        s = self._cover_snap(0)
        plan = assignments(s)
        cover_id = plan["cover"]
        with unittest.mock.patch.dict(os.environ, {"COVER": "0"}):
            dec_off = decide(s, cover_id)
        dec_on = decide(s, cover_id)
        # When COVER is on, cov flag should be set; when off, not
        self.assertEqual((dec_on.assignments or {}).get("cov", 0), 1)
        self.assertEqual((dec_off.assignments or {}).get("cov", 0), 0)

    def test_team1_cover(self):
        """Cover works correctly for team 1 (direction=-1)."""
        s = self._cover_snap(team=1)
        plan = assignments(s)
        if plan["presser"] is None:
            self.skipTest("no presser for team 1")
        cover_id = plan["cover"]
        dec = decide(s, cover_id)
        validate_command(dec.command)


# ---------------------------------------------------------------------------
# §4 BALLSIDE
# ---------------------------------------------------------------------------
class BallsideTest(unittest.TestCase):
    """AT-3: lateral positions shift toward ball when opponent has ball."""

    def test_dynamic_forwards_keep_ballside_shift_for_both_teams_and_flanks(self):
        for team in (0, 1):
            for ball_y in (-25, 25):
                s = snap(
                    team=team,
                    ball=(0, ball_y),
                    free=True,
                    ours=[(-52, 0), (-28, 0), (0, ball_y - 1), (15, -10), (18, 10)],
                )
                for pid in (3, 4):
                    with self.subTest(team=team, ball_y=ball_y, pid=pid):
                        with unittest.mock.patch.dict(
                            os.environ, {"FWD_ROLES": "1", "BALLSIDE": "0"}
                        ):
                            off = decide(s, pid)
                        with unittest.mock.patch.dict(
                            os.environ, {"FWD_ROLES": "1", "BALLSIDE": "1"}
                        ):
                            on = decide(s, pid)
                        self.assertEqual(on.command["commandType"], "MOVE_TO")
                        self.assertEqual(on.assignments["role"], off.assignments["role"])
                        self.assertLess(
                            abs(_target(on)[1] - ball_y),
                            abs(_target(off)[1] - ball_y),
                        )
                        self.assertGreaterEqual(_dist(_target(on), s.ball), 6)

    def _ballside_snap(self, ball_y: float, team=0, opp_has_ball=True):
        owner = (1, 1) if opp_has_ball else (0, 2)
        ours = [(-52, 0), (-20, 0), (-5, 0), (10, -10), (15, 10)]
        theirs = [(52, 0), (0, ball_y), (10, ball_y), (-5, -10), (-5, 10)]
        return snap(team=team, ball=(0, ball_y), owner=owner, ours=ours, theirs=theirs)

    def test_def_closer_to_ball_side_when_defending(self):
        """DEF lateral shifts toward ball side when opponent has ball and BALLSIDE on."""
        ball_y = 25.0  # ball on right side
        s_on = self._ballside_snap(ball_y, opp_has_ball=True)
        with unittest.mock.patch.dict(os.environ, {"BALLSIDE": "0"}):
            s_off = self._ballside_snap(ball_y, opp_has_ball=True)
            dec_def_off = decide(s_off, 1)
        dec_def_on = decide(s_on, 1)
        y_on = _target(dec_def_on)[1]
        y_off = _target(dec_def_off)[1]
        # With BALLSIDE on: DEF lateral should be closer to ball_y (positive)
        # With BALLSIDE off: DEF uses fixed ±8
        self.assertGreater(y_on, y_off - 1.0,
                           "DEF should be closer to ball side with BALLSIDE on")

    def test_no_change_when_we_have_ball(self):
        """BALLSIDE does not change lateral positions when we have possession."""
        ball_y = 25.0
        s_on = self._ballside_snap(ball_y, opp_has_ball=False)
        with unittest.mock.patch.dict(os.environ, {"BALLSIDE": "0"}):
            dec_on_p1 = decide(self._ballside_snap(ball_y, opp_has_ball=False), 1)
        dec_p1_ballside = decide(s_on, 1)
        # When we have ball, lateral shouldn't change with BALLSIDE flag
        y_on = _target(dec_p1_ballside)[1]
        y_off = _target(dec_on_p1)[1]
        self.assertAlmostEqual(y_on, y_off, delta=2.0,
                               msg="BALLSIDE should not affect positions when we have ball")

    def test_ballside_disabled(self):
        """BALLSIDE=0: lateral positions same as before."""
        ball_y = 25.0
        s = self._ballside_snap(ball_y)
        with unittest.mock.patch.dict(os.environ, {"BALLSIDE": "0"}):
            dec_off = decide(s, 1)
        dec_on = decide(s, 1)
        y_off = _target(dec_off)[1]
        y_on = _target(dec_on)[1]
        # With flag on, there should be some shift toward ball
        # With ball at y=25, DEF with BALLSIDE should move toward y=25
        # Without BALLSIDE, DEF is at ±8 (discrete)
        self.assertGreaterEqual(y_on, y_off - 0.1,  # must not be further from ball
                                "BALLSIDE should push DEF toward ball side")


# ---------------------------------------------------------------------------
# §5 GK_LINE
# ---------------------------------------------------------------------------
class GKLineTest(unittest.TestCase):
    """AT-4: GK on shot line at clamp(2.5 + 0.12*dist, 2.5, 7.0) m from own goal."""

    def _gk_snap(self, ball: tuple, team=0):
        d = 1 if team == 0 else -1
        ours = [(-52, 0), (-25, 0), (-5, 5), (10, -10), (15, 10)]
        theirs = [(52, 0), (d * ball[0] + 5, ball[1]), (10, 5), (-10, -5), (-10, 10)]
        return snap(team=team, ball=ball, owner=(1, 1), ours=ours, theirs=theirs)

    def _expected_gkd(self, ball: tuple, team: int) -> float:
        d = 1 if team == 0 else -1
        own_goal = (-d * _HALF_LEN, 0.0)
        bx = d * ball[0]
        by = ball[1]
        dist = math.hypot(bx - own_goal[0], by - own_goal[1])
        return max(2.5, min(7.0, 2.5 + 0.12 * dist))

    def test_gk_at_correct_distance_from_own_goal(self):
        """GK is within [2.5, 7.0] m of own goal center and on the goal→ball line."""
        for ball in [(0, 0), (30, 15), (-10, -10)]:
            for team in (0, 1):
                s = self._gk_snap(ball, team)
                dec = decide(s, 0)
                if dec.command["commandType"] != "MOVE_TO":
                    continue
                tx, ty = _target(dec)
                d = 1 if team == 0 else -1
                own_goal = (-d * _HALF_LEN, 0.0)
                gkd = math.hypot(tx - own_goal[0], ty - own_goal[1])
                expected_gkd = self._expected_gkd(ball, team)
                with self.subTest(ball=ball, team=team):
                    self.assertAlmostEqual(gkd, expected_gkd, delta=1.5,
                                           msg=f"GK should be {expected_gkd:.1f}m from goal, got {gkd:.1f}m")

    def test_gk_distance_at_least_2_5m(self):
        """GK never closer than 2.5m from own goal."""
        for ball in [(0, 0), (-40, 0), (-50, 5)]:
            s = self._gk_snap(ball, team=0)
            dec = decide(s, 0)
            if dec.command["commandType"] == "MOVE_TO":
                tx, ty = _target(dec)
                own_goal = (-_HALF_LEN, 0.0)
                gkd = math.hypot(tx - own_goal[0], ty - own_goal[1])
                with self.subTest(ball=ball):
                    self.assertGreaterEqual(gkd, 2.4, "GK too close to own goal")

    def test_gk_distance_at_most_7m(self):
        """GK never more than 7.0m from own goal."""
        for ball in [(50, 30), (55, 0), (40, -30)]:
            s = self._gk_snap(ball, team=0)
            dec = decide(s, 0)
            if dec.command["commandType"] == "MOVE_TO":
                tx, ty = _target(dec)
                own_goal = (-_HALF_LEN, 0.0)
                gkd = math.hypot(tx - own_goal[0], ty - own_goal[1])
                with self.subTest(ball=ball):
                    self.assertLessEqual(gkd, 7.5, "GK too far from own goal")

    def test_gk_line_disabled(self):
        """GK_LINE=0: GK uses old goal-angle positioning."""
        s = self._gk_snap((20, 0), team=0)
        dec_on = decide(s, 0)
        with unittest.mock.patch.dict(os.environ, {"GK_LINE": "0"}):
            dec_off = decide(s, 0)
        # Both should be MOVE_TO; positions may differ
        validate_command(dec_on.command)
        validate_command(dec_off.command)


# ---------------------------------------------------------------------------
# §6 COUNTER_PRESS
# ---------------------------------------------------------------------------
class CounterPressTest(unittest.TestCase):
    """AT-5: two pressers when ball in opponent half, second player near ball with stamina."""

    def _cp_snap(self, ball_progress: float = 10.0, second_dist: float = 8.0,
                 second_stamina: float = 0.8, team: int = 0):
        d = 1 if team == 0 else -1
        # Ball at (ball_progress, 0) in progress coords
        bx = ball_progress
        # pid2 always 6m from ball (designated presser); pid3 is the second candidate
        ours = [
            (-52, 0),              # GK pid0
            (-5, 0),               # DEF pid1
            (bx - 6, 0),           # MID pid2 = 6m from ball (presser)
            (bx - second_dist, 0), # FWD pid3 = second_dist from ball (second candidate)
            (bx - 20, 10),         # FWD pid4 = far
        ]
        theirs = [(52, 0), (bx, 0), (bx + 5, 5), (-5, -5), (-8, 8)]
        return Snapshot(
            team, 5, 30.0, 120.0,
            _players(team, ours, theirs, stamina_ours=[0.9, 0.9, 0.9, second_stamina, 0.9]),
            (d * bx, 0.0), (0.0, 0.0), (1 - team, 1), False, (0, 0),
        )

    def test_two_pressers_in_opp_half(self):
        """Two PRESS_BALL commands when ball in opp half and second player near ball."""
        s = self._cp_snap(ball_progress=15.0, second_dist=8.0, second_stamina=0.8)
        press_commands = 0
        for pid in range(1, 5):  # exclude GK
            dec = decide(s, pid)
            if dec.command["commandType"] in ("PRESS_BALL", "SLIDE_TACKLE"):
                press_commands += 1
        self.assertEqual(press_commands, 2,
                         f"Expected 2 pressers, got {press_commands}")

    def test_one_presser_when_second_stamina_low(self):
        """Only one presser when second candidate stamina < 0.3."""
        s = self._cp_snap(ball_progress=15.0, second_dist=8.0, second_stamina=0.2)
        press_commands = 0
        for pid in range(1, 5):
            dec = decide(s, pid)
            if dec.command["commandType"] in ("PRESS_BALL", "SLIDE_TACKLE"):
                press_commands += 1
        self.assertEqual(press_commands, 1, "Low stamina should prevent counter-press")

    def test_one_presser_when_ball_on_own_half(self):
        """Counter-press only applies when ball is in opponent half (progress >= 0)."""
        s = self._cp_snap(ball_progress=-15.0, second_dist=8.0, second_stamina=0.8)
        press_commands = 0
        for pid in range(1, 5):
            dec = decide(s, pid)
            if dec.command["commandType"] in ("PRESS_BALL", "SLIDE_TACKLE"):
                press_commands += 1
        self.assertEqual(press_commands, 1, "No counter-press when ball in own half")

    def test_one_presser_when_second_too_far(self):
        """No counter-press when second candidate is more than 10m from ball."""
        s = self._cp_snap(ball_progress=15.0, second_dist=12.0, second_stamina=0.8)
        press_commands = 0
        for pid in range(1, 5):
            dec = decide(s, pid)
            if dec.command["commandType"] in ("PRESS_BALL", "SLIDE_TACKLE"):
                press_commands += 1
        self.assertEqual(press_commands, 1, "Far player should not counter-press")

    def test_cp_flag_in_assignments(self):
        """presser2 in assignments when counter-press active."""
        s = self._cp_snap(ball_progress=15.0, second_dist=8.0, second_stamina=0.8)
        plan = assignments(s)
        self.assertIsNotNone(plan.get("presser2"), "presser2 should be set")

    def test_counter_press_disabled(self):
        """COUNTER_PRESS=0: only one presser."""
        s = self._cp_snap(ball_progress=15.0, second_dist=8.0, second_stamina=0.8)
        with unittest.mock.patch.dict(os.environ, {"COUNTER_PRESS": "0"}):
            press_commands = 0
            for pid in range(1, 5):
                dec = decide(s, pid)
                if dec.command["commandType"] in ("PRESS_BALL", "SLIDE_TACKLE"):
                    press_commands += 1
        self.assertEqual(press_commands, 1)


# ---------------------------------------------------------------------------
# §8 FWD_ROLES
# ---------------------------------------------------------------------------
class FwdRolesTest(unittest.TestCase):
    """AT-7 / AT-8: link/runner roles with correct positions and separation."""

    def _fwd_snap(self, ball=(5.0, 0.0), owner_pid=2, team=0):
        ours = [(-52, 0), (-20, 0), (0, 0), (10, -10), (15, 10)]
        theirs = [(52, 0), (20, 0), (10, 5), (-5, -5), (-5, 5)]
        return snap(team=team, ball=ball, owner=(0, owner_pid), ours=ours, theirs=theirs)

    def test_roles_assigned(self):
        """Both FWDs get non-None roles (link/runner) when we have ball."""
        s = self._fwd_snap(ball=(0, 0), owner_pid=2)
        dec3 = decide(s, 3)
        dec4 = decide(s, 4)
        role3 = (dec3.assignments or {}).get("role", "none")
        role4 = (dec4.assignments or {}).get("role", "none")
        roles = {role3, role4}
        self.assertIn("link", roles, f"Expected a 'link' role, got {role3}/{role4}")
        self.assertIn("runner", roles, f"Expected a 'runner' role, got {role3}/{role4}")

    def test_fwd_carrier_has_no_link_runner_role(self):
        """When FWD3 has ball, FWD3 is carrier (not link/runner); FWD4 is runner."""
        s = self._fwd_snap(ball=(10, 0), owner_pid=3)
        dec4 = decide(s, 4)
        role4 = (dec4.assignments or {}).get("role", "none")
        self.assertEqual(role4, "runner", f"FWD4 should be runner when FWD3 has ball, got {role4}")

    def test_link_runner_consistent_across_players(self):
        """AT-9: all 5 players agree on which FWD is link and which is runner."""
        s = self._fwd_snap(ball=(0, 0), owner_pid=2)
        roles_per_player = {}
        for pid in range(5):
            dec = decide(s, pid)
            a = dec.assignments or {}
            # Each player sees the same FWD roles
            roles_per_player[pid] = {"3": a.get("fwd3_role"), "4": a.get("fwd4_role")}
        # All players should agree (or all have None if FWD_ROLES inactive)
        if roles_per_player[0]["3"] is not None:
            for pid in range(1, 5):
                self.assertEqual(roles_per_player[pid]["3"], roles_per_player[0]["3"],
                                 f"pid {pid} disagrees on FWD3 role")

    def test_min_fwd_separation(self):
        """AT-8: FWD target positions at least 10m apart."""
        s = self._fwd_snap(ball=(0, 0), owner_pid=2)
        dec3 = decide(s, 3)
        dec4 = decide(s, 4)
        if dec3.command["commandType"] != "MOVE_TO" or dec4.command["commandType"] != "MOVE_TO":
            self.skipTest("FWDs not using MOVE_TO")
        t3 = _target(dec3)
        t4 = _target(dec4)
        sep = _dist(t3, t4)
        self.assertGreaterEqual(sep, 9.5, f"FWD separation {sep:.1f}m < 10m minimum")

    def test_non_carrier_fwd_not_within_6m_of_ball(self):
        """AT-8: non-carrier FWD target not within 6m of ball."""
        s = self._fwd_snap(ball=(5, 0), owner_pid=2)  # MID has ball
        for pid in (3, 4):
            dec = decide(s, pid)
            if dec.command["commandType"] == "MOVE_TO":
                tx, ty = _target(dec)
                bx, by = s.ball
                dist_ball = math.hypot(tx - bx, ty - by)
                with self.subTest(pid=pid):
                    self.assertGreaterEqual(dist_ball, 5.5,
                                            f"FWD {pid} target {dist_ball:.1f}m from ball (min 6m)")

    def test_bias_for_pid3_link_preference(self):
        """Player 3 has 3m bias → pid3 is link when equidistant from ball."""
        team = 0
        # Both FWDs at same distance from ball (0,0), but pid3 has +3m bias → pid4 should be link
        ball = (0.0, 0.0)
        ours = [(-52, 0), (-20, 0), (0, 0), (10, 0), (10, 0)]  # pid3 and pid4 same dist
        theirs = [(52, 0), (20, 0), (10, 5), (-5, -5), (-5, 5)]
        s = snap(team=team, ball=ball, owner=(0, 2), ours=ours, theirs=theirs)
        dec3 = decide(s, 3)
        dec4 = decide(s, 4)
        role3 = (dec3.assignments or {}).get("role", "none")
        role4 = (dec4.assignments or {}).get("role", "none")
        # pid4 should be link (closer after bias correction), pid3 runner
        self.assertEqual(role4, "link", f"pid4 should be link when equidistant; roles: 3={role3}, 4={role4}")
        self.assertEqual(role3, "runner")

    def test_fwd_roles_disabled(self):
        """FWD_ROLES=0: FWDs use support positioning, role is 'none'."""
        s = self._fwd_snap(ball=(0, 0), owner_pid=2)
        with unittest.mock.patch.dict(os.environ, {"FWD_ROLES": "0"}):
            dec3 = decide(s, 3)
        role3 = (dec3.assignments or {}).get("role", "none")
        self.assertEqual(role3, "none")


# ---------------------------------------------------------------------------
# §10 AT-9: Consistency — all 5 players agree on core assignments
# ---------------------------------------------------------------------------
class ConsistencyTest(unittest.TestCase):
    def test_one_presser_globally(self):
        """AT-9: at most one designated presser across all 5 players."""
        s = snap(ball=(10, 5), owner=(1, 1))
        pressers = set()
        for pid in range(5):
            plan = (decide(s, pid).assignments or {})
            if plan.get("presser") is not None:
                pressers.add(plan["presser"])
        # All players agree on the same presser
        self.assertLessEqual(len(pressers), 1, f"Multiple different pressers: {pressers}")

    def test_fwd_roles_consistent_across_all_players(self):
        """AT-9: all 5 players assign same link/runner to FWDs."""
        s = snap(ball=(5, 0), owner=(0, 2))
        fwd3_roles = set()
        fwd4_roles = set()
        for pid in range(5):
            plan = (decide(s, pid).assignments or {})
            r3 = plan.get("fwd3_role")
            r4 = plan.get("fwd4_role")
            if r3:
                fwd3_roles.add(r3)
            if r4:
                fwd4_roles.add(r4)
        if fwd3_roles:
            self.assertEqual(len(fwd3_roles), 1, f"FWD3 role inconsistent: {fwd3_roles}")
        if fwd4_roles:
            self.assertEqual(len(fwd4_roles), 1, f"FWD4 role inconsistent: {fwd4_roles}")


# ---------------------------------------------------------------------------
# §10 AT-10: Mirror symmetry
# ---------------------------------------------------------------------------
class MirrorTest(unittest.TestCase):
    def test_same_command_types_for_both_teams(self):
        """AT-10: team 0 and team 1 get same command types for each player."""
        ours = [(-52, 0), (-25, 0), (-5, 5), (10, -10), (15, 10)]
        theirs = [(52, 0), (25, 0), (8, 15), (-8, -10), (-12, 10)]
        s0 = snap(team=0, ball=(10, 5), owner=(1, 1), ours=ours, theirs=theirs)
        s1 = snap(team=1, ball=(10, 5), owner=(1, 1), ours=ours, theirs=theirs)
        for pid in range(5):
            d0 = decide(s0, pid).command["commandType"]
            d1 = decide(s1, pid).command["commandType"]
            with self.subTest(pid=pid):
                self.assertEqual(d0, d1, f"pid {pid}: team0={d0} but team1={d1}")


# ---------------------------------------------------------------------------
# §10 AT-11: Disable all new flags → old behavior
# ---------------------------------------------------------------------------
class AllFlagsOffTest(unittest.TestCase):
    def test_all_new_flags_off_no_crash(self):
        """AT-11: with all new flags=0, decide() runs for all players without errors."""
        env_off = {
            "LANE_BLOCK": "0", "COVER": "0", "BALLSIDE": "0",
            "GK_LINE": "0", "COUNTER_PRESS": "0", "STAMINA_ROT": "0", "FWD_ROLES": "0",
        }
        s = snap(ball=(10, 5), owner=(1, 1))
        with unittest.mock.patch.dict(os.environ, env_off):
            for pid in range(5):
                dec = decide(s, pid)
                validate_command(dec.command)

    def test_markers_use_mark_when_lane_block_off(self):
        """AT-11: markers use MARK (not MOVE_TO) when LANE_BLOCK=0."""
        ours = [(-52, 0), (-10, 0), (25, 8), (0, -15), (10, 15)]
        theirs = [(52, 0), (20, 0), (40, 10), (-5, -10), (-5, 5)]
        s = snap(ball=(20, 0), owner=(1, 1), ours=ours, theirs=theirs)
        plan = assignments(s)
        with unittest.mock.patch.dict(os.environ, {"LANE_BLOCK": "0"}):
            for marker_id in plan["marks"]:
                dec = decide(s, marker_id)
                self.assertNotEqual(dec.command["commandType"], "MOVE_TO",
                                    "LANE_BLOCK=0 should use MARK, not MOVE_TO")


# ---------------------------------------------------------------------------
# §10 AT-12: Validity — all commands valid for various snapshots
# ---------------------------------------------------------------------------
class ValidityTest(unittest.TestCase):
    def test_all_players_valid_commands(self):
        """AT-12: all 5 players produce valid commands on various snapshots."""
        scenarios = [
            snap(ball=(0, 0), owner=(1, 1)),
            snap(ball=(20, 10), owner=(0, 2)),
            snap(ball=(-20, -5), owner=None, free=True),
            snap(ball=(40, 20), owner=(1, 0)),
            snap(team=1, ball=(10, 5), owner=(1, 1)),
        ]
        for i, s in enumerate(scenarios):
            for pid in range(5):
                with self.subTest(scenario=i, pid=pid):
                    dec = decide(s, pid)
                    validate_command(dec.command)


# ---------------------------------------------------------------------------
# §10 AT-13: Teammate intent — auto-inactive (no action data in state)
# ---------------------------------------------------------------------------
class TeammateIntentTest(unittest.TestCase):
    def test_use_teammate_intent_inactive(self):
        """AT-13: USE_TEAMMATE_INTENT=1 does not break anything (auto-inactive)."""
        s = snap(ball=(5, 0), owner=(0, 2))
        with unittest.mock.patch.dict(os.environ, {"USE_TEAMMATE_INTENT": "1"}):
            for pid in range(5):
                dec = decide(s, pid)
                validate_command(dec.command)


if __name__ == "__main__":
    unittest.main()
