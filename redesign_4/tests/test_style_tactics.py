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
    return Snapshot(
        team,
        10,
        10.0,
        120.0,
        players,
        (m * ball[0], ball[1]),
        (0.0, 0.0),
        owner,
        owner is None,
        (0, 0),
    )


def target(decision):
    return decision.command["parameters"]["target_x"], decision.command["parameters"]["target_y"]


class RoleAnchorTest(unittest.TestCase):
    def test_roles_by_formation(self):
        self.assertEqual(
            [role(p) for p in (1, 2, 3, 4)],
            [("DEF", False), ("MID", False), ("LINK", False), ("FWD", False)],
        )
        self.assertEqual(role(2, "2-1-1"), ("DEF", True))
        self.assertEqual(role(3, "1-2-1"), ("MID", True))

    def test_formation_anchors(self):
        s = snap(ball=(0, 0), owner=(0, 4), ours=[*OURS[:4], (0, 0)])
        plan = assignments(s)
        self.assertEqual(plan["cover"], 1)
        cases = [
            (2, "1-1-2", (-9, -9.75)),
            (2, "2-1-1", (-18, -8)),
            (3, "1-2-1", (-9, 9.75)),
            (3, "2-1-1", (-9, -9.75)),
            (3, "1-1-2", (5, -15)),
        ]
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
        self.assertEqual(
            [attack_cap(attacking, p, one) for p in (1, 2, 3, 4)], [0.0, 0.0, 0.0, None]
        )
        self.assertEqual([attack_cap(attacking, p) for p in (1, 2, 3, 4)], [0.0, None, None, None])

    def test_deep_block_keeps_outfield_behind_cap(self):
        style = Style(
            block_depth=0.1,
            attackers_committed=1,
            tackle_aggression=0.0,
            tempo=0.5,
            formation="2-1-1",
        )
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
        s = snap(
            team=team,
            ours=[*OURS[:3], (x, 0), OURS[4]],
            theirs=[THEIRS[0], (10, 0), *THEIRS[2:]],
            ball=(10, 0),
            owner=(1, 1),
        )
        return s, tackle_ok(s, s.player(3), replace(DEFAULT_STYLE, tackle_aggression=aggression))

    def test_tackle_ok_distance_and_from_behind(self):
        cases = [
            (8.5, 1.0, True),
            (13.0, 1.0, True),
            (14.0, 1.0, False),
            (11.5, 0.6, False),
            (8.5, 0.6, True),
            (9.5, 0.0, False),
        ]
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
        return next(
            a["score"] for a in decision.alternatives if a["command"]["commandType"] == "SET_STANCE"
        )

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
            d = decide(
                snap(theirs=theirs), 2, DEFAULT_SETTINGS, replace(DEFAULT_STYLE, carry=carry)
            )
            return next(
                a["score"] for a in d.alternatives if a["command"]["commandType"] == "MOVE_TO"
            )

        self.assertAlmostEqual(carry_score(self.PRESSED, 0.5), carry_score(self.PRESSED, 1.0) / 2)
        self.assertGreater(carry_score(THEIRS, 1.0), carry_score(self.PRESSED, 1.0))

    def test_keeper_never_carries(self):
        s = snap(ball=(-51, 0), owner=(0, 0))
        d = decide(s, 0, DEFAULT_SETTINGS, replace(DEFAULT_STYLE, carry=1.0))
        self.assertNotIn("MOVE_TO", self.kinds(d))

    def test_carry_conserves_depleted_stamina_for_both_teams(self):
        style = replace(DEFAULT_STYLE, carry=1.0)
        for team in (0, 1):
            for stamina, sprint in (
                (0.0, False),
                (0.11782325, False),
                (0.349, False),
                (0.35, True),
                (0.9, True),
            ):
                with self.subTest(team=team, stamina=stamina):
                    s = snap(team=team, theirs=self.PRESSED)
                    s = replace(
                        s,
                        players=tuple(
                            replace(p, stamina=stamina) if p.team == team and p.id == 2 else p
                            for p in s.players
                        ),
                    )
                    action = decide(s, 2, DEFAULT_SETTINGS, style).command
                    self.assertEqual(action["commandType"], "MOVE_TO")
                    self.assertEqual(action["parameters"]["sprint"], sprint)
                    self.assertGreater(s.direction * action["parameters"]["target_x"], 40)

    def test_carry_does_not_sprint_when_already_near_its_target(self):
        for team in (0, 1):
            s = snap(team=team, ball=(49, 0), ours=[*OURS[:2], (49, 0), *OURS[3:]])
            d = decide(s, 2, DEFAULT_SETTINGS, replace(DEFAULT_STYLE, carry=1.0))
            carry = next(
                a["command"] for a in d.alternatives if a["command"]["commandType"] == "MOVE_TO"
            )
            self.assertFalse(carry["parameters"]["sprint"])


class StrandedKeeperTest(unittest.TestCase):
    def captured_counter(self, team=0):
        # Complete canonical player frame at tick 4927, before the 24 s shot.
        rows = (
            (-48.085928, 1.02638296, 0, 0, 1),
            (-4.4620424, 0.46488824, 11.989228, -0.50834544, 0.7826151),
            (-1.5583532, -8.6595032, 11.605272, -3.05248448, 0.7826151),
            (2.75604416, -15.1187656, 11.7724496, -2.3258184, 0.89195365),
            (19.734632, 5.390496, 11.962156, -0.952263472, 0.6390664),
            (-5.1729912, -0.63238544, 7.9270432, -1.0779552, 1),
            (6.2920752, 7.0319056, 7.99372672, 0.31675932, 0),
            (7.9372248, 3.98139072, 7.9186632, 1.13788344, 0),
            (51.2, -0.63447312, 7.9999896, 0.0130657392, 0),
            (12.3388904, 2.6149152, 6.6133224, 4.501552, 0.120704696),
        )
        d = 1 if team == 0 else -1
        return Snapshot(
            team,
            4927,
            22.400322,
            120,
            tuple(
                Player(team if i < 5 else 1 - team, i % 5, d * x, y, d * vx, vy, stamina)
                for i, (x, y, vx, vy, stamina) in enumerate(rows)
            ),
            (d * 21.5673256, 5.2723008),
            (d * 11.962156, -0.952263472),
            (team, 4),
            False,
            (0, 1) if team == 0 else (1, 0),
        )

    def test_captured_counter_takes_clear_shot_before_keeper_can_recover(self):
        for team in (0, 1):
            with self.subTest(team=team):
                s = self.captured_counter(team)
                decision = decide(s, 4, DEFAULT_SETTINGS, replace(DEFAULT_STYLE, carry=1))
                self.assertEqual(decision.command["commandType"], "SHOOT")
                self.assertIn("stranded keeper", decision.reason)
                home_keeper = replace(
                    s,
                    players=tuple(
                        replace(p, x=s.direction * 51, y=0, vx=0, vy=0)
                        if p.team != team and p.id == 0
                        else p
                        for p in s.players
                    ),
                )
                self.assertNotEqual(decide(home_keeper, 4).command["commandType"], "SHOOT")

    def test_stranded_keeper_does_not_override_range_lane_or_recovery_guards(self):
        for case in ("distant", "blocked", "recovering"):
            s = self.captured_counter()
            if case == "distant":
                s = replace(
                    s,
                    ball=(0, 0),
                    players=tuple(
                        replace(p, x=0, y=0) if p.team == 0 and p.id == 4 else p for p in s.players
                    ),
                )
            elif case == "blocked":
                s = replace(
                    s,
                    players=tuple(
                        replace(p, x=40, y=-5 + 2 * p.id, vx=0, vy=0)
                        if p.team == 1 and p.id != 0
                        else p
                        for p in s.players
                    ),
                )
            else:
                s = replace(
                    s,
                    players=tuple(
                        replace(p, x=s.player(4).x - 9, vx=12, vy=0)
                        if p.team == 1 and p.id == 0
                        else p
                        for p in s.players
                    ),
                )
            with self.subTest(case=case):
                self.assertNotEqual(decide(s, 4).command["commandType"], "SHOOT")
