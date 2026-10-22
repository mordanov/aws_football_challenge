import unittest
from dataclasses import replace

from agent.model import DEFAULT_SETTINGS, DEFAULT_STYLE, Player, Snapshot, Style
from agent.tactics import assignments, attack_cap, decide, role, support, tackle_ok

OURS = [(-51, 0), (-28, 0), (-5, 0), (20, -15), (23, 15)]
THEIRS = [(51, 0), (30, 0), (8, 20), (-10, -20), (-12, 20)]


def snap(team=0, ours=None, theirs=None, ball=(-5, 0), owner=(0, 2)):
    """Positions are given in team-0 view; team 1 mirrors x so tests stay direction-free."""
    m = 1 if team == 0 else -1
    sides = {team: ours or OURS, 1 - team: theirs or THEIRS}
    players = tuple(
        Player(side, pid, m * x, y, stamina=0.9)
        for side in (0, 1)
        for pid, (x, y) in enumerate(sides[side])
    )
    owner = None if owner is None else (team if owner[0] == 0 else 1 - team, owner[1])
    return Snapshot(team, 10, 10.0, 120.0, players, (m * ball[0], ball[1]), (0.0, 0.0),
                    owner, owner is None, (0, 0))


def target(decision):
    return decision.command["parameters"]["target_x"], decision.command["parameters"]["target_y"]


class RoleAnchorTest(unittest.TestCase):
    def test_roles_by_formation(self):
        self.assertEqual([role(p) for p in (1, 2, 3, 4)],
                         [("DEF", False), ("MID", False), ("LINK", False), ("FWD", False)])
        self.assertEqual(role(2, "2-1-1"), ("DEF", True))
        self.assertEqual(role(3, "1-2-1"), ("MID", True))

    def test_formation_anchors(self):
        s = snap(ball=(0, 0), owner=(0, 4), ours=[*OURS[:4], (0, 0)])
        plan = assignments(s)
        self.assertEqual(plan["cover"], 1)
        cases = [(2, "1-1-2", (-9, -9.75)), (2, "2-1-1", (-18, -8)), (3, "1-2-1", (-9, 9.75)),
                 (3, "2-1-1", (-9, -9.75)), (3, "1-1-2", (5, -15))]
        for pid, formation, expected in cases:
            with self.subTest(pid=pid, formation=formation):
                got = support(s, s.player(pid), DEFAULT_SETTINGS, plan, formation)
                self.assertAlmostEqual(got[0], expected[0])
                self.assertAlmostEqual(got[1], expected[1])


class BlockAndCommitTest(unittest.TestCase):
    def test_attack_cap_values(self):
        defending = snap(owner=(1, 1), ball=(30, 0))
        self.assertIsNone(attack_cap(defending, 3, DEFAULT_STYLE))
        self.assertAlmostEqual(
            attack_cap(defending, 3, replace(DEFAULT_STYLE, block_depth=0.1)), -40.4
        )
        self.assertAlmostEqual(
            attack_cap(defending, 3, replace(DEFAULT_STYLE, block_depth=0.5)), -2.0
        )
        attacking = snap()
        one = replace(DEFAULT_STYLE, attackers_committed=1)
        self.assertEqual([attack_cap(attacking, p, one) for p in (1, 2, 3, 4)],
                         [0.0, 0.0, 0.0, None])
        self.assertEqual([attack_cap(attacking, p) for p in (1, 2, 3, 4)],
                         [0.0, None, None, None])

    def test_deep_block_keeps_outfield_behind_cap(self):
        style = Style(block_depth=0.1, attackers_committed=1, tackle_aggression=0.0,
                      tempo=0.5, formation="2-1-1")
        for team in (0, 1):
            s = snap(team=team, owner=(1, 1), ball=(30, 0))
            moves = 0
            for pid in (1, 2, 3, 4):
                cmd = decide(s, pid, DEFAULT_SETTINGS, style).command
                with self.subTest(team=team, pid=pid, cmd=cmd):
                    self.assertIn(cmd["commandType"], ("MOVE_TO", "PRESS_BALL"))
                    if cmd["commandType"] == "MOVE_TO":
                        moves += 1
                        self.assertLessEqual(s.direction * cmd["parameters"]["target_x"], -40.4)
            self.assertGreaterEqual(moves, 2)

    def test_attackers_committed_holds_link_at_halfway(self):
        s = snap(ours=[OURS[0], OURS[1], (20, 0), OURS[3], OURS[4]], ball=(20, 0))
        self.assertEqual(target(decide(s, 3))[0], 25)
        one = replace(DEFAULT_STYLE, attackers_committed=1)
        self.assertEqual(target(decide(s, 3, DEFAULT_SETTINGS, one))[0], 0)
        self.assertEqual(target(decide(s, 4, DEFAULT_SETTINGS, one))[0], 37)


class TackleTest(unittest.TestCase):
    def tackler(self, x, aggression, team=0):
        s = snap(team=team, ours=[*OURS[:3], (x, 0), OURS[4]],
                 theirs=[THEIRS[0], (10, 0), *THEIRS[2:]], ball=(10, 0), owner=(1, 1))
        return s, tackle_ok(s, s.player(3), replace(DEFAULT_STYLE, tackle_aggression=aggression))

    def test_tackle_ok_distance_and_from_behind(self):
        cases = [(8.5, 1.0, True), (13.0, 1.0, True), (14.0, 1.0, False),
                 (11.5, 0.6, False), (8.5, 0.6, True), (9.5, 0.0, False)]
        for team in (0, 1):
            for x, aggression, expected in cases:
                with self.subTest(team=team, x=x, aggression=aggression):
                    self.assertIs(self.tackler(x, aggression, team)[1], expected)

    def test_presser_slides_only_when_tackle_ok(self):
        s, _ = self.tackler(8.5, 1.0)
        self.assertEqual(assignments(s)["presser"], 3)
        self.assertEqual(decide(s, 3).command["commandType"], "PRESS_BALL")
        slide = decide(s, 3, DEFAULT_SETTINGS, replace(DEFAULT_STYLE, tackle_aggression=1.0))
        self.assertEqual(slide.command["commandType"], "SLIDE_TACKLE")
        self.assertEqual(slide.command["parameters"], {"target_player_id": 1})


class TempoTest(unittest.TestCase):
    def hold_score(self, decision):
        return next(a["score"] for a in decision.alternatives
                    if a["command"]["commandType"] == "SET_STANCE")

    def test_slow_tempo_holds_when_unpressed(self):
        s = snap()
        self.assertEqual(decide(s, 2).command["commandType"], "PASS")
        slow = decide(s, 2, DEFAULT_SETTINGS, replace(DEFAULT_STYLE, tempo=0.0))
        self.assertEqual(slow.command["commandType"], "SET_STANCE")
        self.assertEqual(self.hold_score(slow), 60.0)

    def test_no_hold_bonus_under_pressure(self):
        s = snap(theirs=[*THEIRS[:3], (-3, 2), THEIRS[4]])
        slow = decide(s, 2, DEFAULT_SETTINGS, replace(DEFAULT_STYLE, tempo=0.0))
        self.assertEqual(self.hold_score(slow), 0.0)

    def test_high_tempo_prefers_forward_pass(self):
        s = snap()
        fast = decide(s, 2, DEFAULT_SETTINGS, replace(DEFAULT_STYLE, tempo=1.0))
        forward = [a for a in fast.alternatives if a["command"]["commandType"] == "PASS"]
        self.assertGreater(forward[0]["score"], decide(s, 2).alternatives[0]["score"])


if __name__ == "__main__":
    unittest.main()


class CarryTest(unittest.TestCase):
    PRESSED = [*THEIRS[:3], (-3, 2), THEIRS[4]]

    def kinds(self, decision):
        return {a["command"]["commandType"] for a in decision.alternatives}

    def test_default_style_never_carries(self):
        for theirs in (THEIRS, self.PRESSED):
            with self.subTest(theirs=theirs):
                self.assertNotIn("MOVE_TO", self.kinds(decide(snap(theirs=theirs), 2)))

    def test_carry_drives_toward_goal_for_both_teams(self):
        style = replace(DEFAULT_STYLE, carry=1.0)
        for team, sign in ((0, 1), (1, -1)):
            with self.subTest(team=team):
                d = decide(snap(team=team, theirs=self.PRESSED), 2, DEFAULT_SETTINGS, style)
                self.assertEqual(d.command["commandType"], "MOVE_TO")
                self.assertGreater(sign * d.command["parameters"]["target_x"], 40)
                self.assertTrue(d.command["parameters"]["sprint"])

    def test_carry_score_scales_with_style_and_space(self):
        def carry_score(theirs, carry):
            d = decide(snap(theirs=theirs), 2, DEFAULT_SETTINGS, replace(DEFAULT_STYLE, carry=carry))
            return next(a["score"] for a in d.alternatives
                        if a["command"]["commandType"] == "MOVE_TO")
        self.assertAlmostEqual(carry_score(self.PRESSED, 0.5), carry_score(self.PRESSED, 1.0) / 2)
        self.assertGreater(carry_score(THEIRS, 1.0), carry_score(self.PRESSED, 1.0))

    def test_keeper_never_carries(self):
        s = snap(ball=(-51, 0), owner=(0, 0))
        d = decide(s, 0, DEFAULT_SETTINGS, replace(DEFAULT_STYLE, carry=1.0))
        self.assertNotIn("MOVE_TO", self.kinds(d))

