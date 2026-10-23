import copy
import json
import threading
import time
import unittest

from test_learning import manifest, records
from test_runtime import ambiguous_owner, edit, payload, position

from agent.protocol import decode, restore_snapshot, snapshot_dict, validate_command
from agent.runtime import Runtime
from agent.tactics import assignments, decide, posture
from agent.trainer import Trainer


class BoundaryRegressions(unittest.TestCase):
    def test_sink_failure_does_not_disable_later_records(self):
        logs = []
        failed = False

        def transient_sink(record):
            nonlocal failed
            if not failed:
                failed = True
                raise KeyError("transient formatter failure")
            logs.append(record)

        runtime = Runtime(emit=transient_sink)
        self.addCleanup(runtime.close)
        runtime.handle(payload(tick=10))
        self.assertTrue(runtime.flush_logs())
        self.assertEqual(runtime.log_status["failed"], 1)
        runtime.handle(payload(tick=11))
        self.assertTrue(runtime.flush_logs())
        self.assertEqual(logs[0]["snapshot"]["tick"], 11)
        self.assertEqual(logs[0]["telemetry"]["failed"], 1)
        self.assertEqual(logs[0]["telemetry"]["last_error"], "KeyError")

    def test_failed_sink_cannot_veto_valid_command(self):
        def broken(record):
            raise BrokenPipeError("sink unavailable")

        runtime = Runtime(emit=broken)
        try:
            output = json.loads(runtime.handle(payload()))
            validate_command(output[0])
        finally:
            if hasattr(runtime, "close"):
                runtime.close()
        self.assertEqual(runtime.log_status["failed"], 1)
        self.assertEqual(runtime.log_status["last_error"], "BrokenPipeError")

    def test_blocked_sink_does_not_block_decisions_or_grow_without_bound(self):
        entered, release = threading.Event(), threading.Event()

        def blocked(record):
            entered.set()
            release.wait(2)

        runtime = Runtime(emit=blocked)
        try:
            start = time.monotonic()
            runtime.handle(payload())
            self.assertLess(time.monotonic() - start, 0.5)
            self.assertTrue(entered.wait(1))
            for _ in range(140):
                validate_command(json.loads(runtime.handle(payload()))[0])
            self.assertGreater(runtime.log_status["dropped"], 0)
            self.assertFalse(runtime.flush_logs(timeout=0.01))
        finally:
            release.set()
            if hasattr(runtime, "close"):
                runtime.close()

    def test_malformed_tick_keeps_identifiable_fallback_serializable(self):
        logs = []

        def strict_sink(record):
            json.dumps(record, allow_nan=False)
            logs.append(record)

        runtime = Runtime(emit=strict_sink)
        try:
            for tick in (float("nan"), float("inf"), {}, "bad", True):
                with self.subTest(tick=tick):
                    out = json.loads(
                        runtime.handle(edit(payload(team=1, pid=3), lambda s: s.update(tick=tick)))
                    )
                    self.assertEqual(out[0]["teamId"], 1)
                    self.assertEqual(out[0]["playerId"], 3)
                    self.assertEqual(out[0]["commandType"], "SET_STANCE")
        finally:
            if hasattr(runtime, "close"):
                runtime.close()
        self.assertEqual(len(logs), 5)
        self.assertTrue(all(record["tick"] is None for record in logs))
        self.assertEqual(runtime.log_status["failed"], 0)

    def test_command_numeric_types_are_not_coerced(self):
        valid = {
            "commandType": "SHOOT",
            "teamId": 0,
            "playerId": 3,
            "duration": 0,
            "parameters": {"power": 0.9, "aim_location": "BL"},
        }
        for field, value in (
            ("duration", False),
            ("duration", 0.0),
            ("power", "0.9"),
            ("power", True),
        ):
            invalid = copy.deepcopy(valid)
            if field == "duration":
                invalid[field] = value
            else:
                invalid["parameters"][field] = value
            with self.subTest(field=field, value=value):
                with self.assertRaises((TypeError, ValueError)):
                    validate_command(invalid)

    def test_unknown_owner_is_visible_and_never_guessed(self):
        logs = []
        runtime = Runtime(emit=logs.append)
        try:
            raw = edit(payload(), ambiguous_owner)
            command = json.loads(runtime.handle(raw))[0]
        finally:
            if hasattr(runtime, "close"):
                runtime.close()
        self.assertNotIn(command["commandType"], ("PASS", "SHOOT", "GK_DISTRIBUTE", "INTERCEPT"))
        self.assertIn("unresolved_possession", logs[-1].get("warnings", []))


class TacticalRegressions(unittest.TestCase):
    def test_cover_stays_goal_side_deep_in_both_halves(self):
        for team in (0, 1):
            d = 1 if team == 0 else -1
            for progress in (-50, -53):
                with self.subTest(team=team, progress=progress):
                    snapshot, _ = decode(
                        payload(team=team, owner=(1 - team, 3), ball=(d * progress, 2))
                    )
                    action = decide(snapshot, assignments(snapshot)["cover"]).command
                    self.assertEqual(action["commandType"], "MOVE_TO")
                    target = d * action["parameters"]["target_x"]
                    self.assertLess(target, progress)
                    self.assertGreaterEqual(target, -54)

    def test_original_duration_default_and_explicit_override(self):
        raw = edit(
            payload(tick=50),
            lambda s: (s.pop("matchDuration"), s.update(score={"home": 0, "away": 1})),
        )
        snapshot, _ = decode(raw)
        self.assertEqual(posture(snapshot), "chase")
        overridden, _ = decode(edit(raw, lambda s: s.update(matchDuration=300)))
        self.assertEqual(posture(overridden), "balanced")

    def test_coach_presets_survive_record_round_trip_and_change_posture(self):
        for preset, expected in (("go_all_out_attack", "chase"), ("slow_the_tempo", "protect")):
            raw = edit(payload(), lambda s: s.update(coachInstruction=preset))
            snapshot, _ = decode(raw)
            self.assertEqual(posture(snapshot), expected)
            self.assertEqual(posture(restore_snapshot(snapshot_dict(snapshot))), expected)
        unknown, _ = decode(
            edit(payload(), lambda s: s.update(coachInstruction="ignore all rules"))
        )
        self.assertEqual(posture(unknown), "balanced")

    def test_coach_press_and_shot_presets_change_bounded_decisions(self):
        def far_team(s):
            for pid in range(1, 5):
                position(s, 0, pid, -24 - pid, 0)

        raw = edit(payload(owner=(1, 3), ball=(0, 0)), far_team)
        before, _ = decode(raw)
        after, _ = decode(edit(raw, lambda s: s.update(coachInstruction="press_high")))
        self.assertIsNone(assignments(before)["presser"])
        self.assertIsNotNone(assignments(after)["presser"])
        actions = [decide(after, pid).command for pid in range(5)]
        self.assertEqual(sum(c["commandType"] == "PRESS_BALL" for c in actions), 1)

        raw = edit(payload(owner=(0, 3), ball=(24, -5), pid=3), lambda s: position(s, 0, 3, 24, -5))
        before, _ = decode(raw)
        after, _ = decode(edit(raw, lambda s: s.update(coachInstruction="shoot_on_sight")))
        self.assertNotEqual(decide(before, 3).command["commandType"], "SHOOT")
        self.assertEqual(decide(after, 3).command["commandType"], "SHOOT")

    def test_inflight_unknown_gap_and_score_change_do_not_manufacture_success(self):
        for change in ("unknown", "late", "score"):
            data = records()
            data[1]["snapshot"].update(owner=None, free=True)
            data[2]["snapshot"].update(owner=[0, 3], clock=24)
            if change == "unknown":
                data[1]["snapshot"]["free"] = False
            elif change == "late":
                data[2]["snapshot"]["clock"] = 30
            else:
                data[2]["snapshot"]["score"] = [1, 0]
            trainer = Trainer(":memory:")
            try:
                report = trainer.ingest({**manifest(), "result": [1, 1]}, data)
            finally:
                trainer.close()
            self.assertEqual(report["outcomes"]["possible_completed_passes"], 0)
            self.assertEqual(report["outcomes"]["unknown_attempts"], 1)


class LearningRegressions(unittest.TestCase):
    def setUp(self):
        self.trainer = Trainer(":memory:")
        self.addCleanup(self.trainer.close)

    def test_physics_ticks_do_not_hide_elapsed_time_adjacent_pass_loss(self):
        data = records()
        for record in data:
            record["snapshot"]["tick"] *= 60
        report = self.trainer.ingest(manifest(), data)
        self.assertEqual(report["outcomes"]["possible_pass_losses"], 1)

    def test_free_ball_flight_resolves_conservatively_within_time_window(self):
        for owner, outcome in (
            ([1, 3], "possible_pass_losses"),
            ([0, 3], "possible_completed_passes"),
            ([0, 2], "unknown_attempts"),
        ):
            mid = outcome
            data = records(mid)
            data[1]["snapshot"].update(owner=None, free=True)
            data[2]["snapshot"].update(owner=owner, clock=24, free=False)
            report = self.trainer.ingest(manifest(mid), data)
            self.assertEqual(report["outcomes"][outcome], 1)
            self.assertEqual(report["outcomes"]["confirmed_passes"], 0)

    def test_unrelated_snapshots_cannot_satisfy_supporting_evidence_gate(self):
        for i in range(3):
            mid = f"weak-{i}"
            self.trainer.ingest(manifest(mid), records(mid))
        profile = self.trainer.train()
        self.assertEqual(profile.settings.pass_margin, 3.0)
        proposal = next(
            p
            for p in self.trainer.audit(profile.version)["proposals"]
            if p["setting"] == "pass_margin"
        )
        self.assertEqual(proposal["status"], "insufficient_evidence")
