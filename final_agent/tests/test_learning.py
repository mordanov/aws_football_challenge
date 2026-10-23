import copy
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from test_runtime import edit, payload, position

from agent.model import Profile, Settings, Style
from agent.profile import effective_settings, load_profile, save_profile
from agent.protocol import decode, snapshot_dict
from agent.runtime import Runtime
from agent.tactics import decide
from agent.trainer import Trainer

PLAYED_AT = (
    datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
)


def manifest(mid="m1", opponent="high-line", complete=True):
    return {
        "match_id": mid,
        "opponent_id": opponent,
        "team_id": 0,
        "complete": complete,
        "result": [0, 1] if complete else None,
        "played_at": PLAYED_AT,
    }


def records(mid="m1", opponent="high-line", gap=1):
    out = []
    for tick, owner in ((10, (0, 2)), (10 + gap, (1, 3)), (12 + gap, (0, 1))):
        raw = payload(owner=owner, tick=tick)

        def high_line(s):
            for i in range(1, 5):
                position(s, 1, i, -10, (-1 if i % 2 else 1) * 15)

        snapshot, _ = decode(edit(raw, high_line))
        out.append(
            {
                "type": "decision",
                "match_id": mid,
                "opponent_id": opponent,
                "profile_version": "baseline",
                "team_id": 0,
                "player_id": 2,
                "snapshot": snapshot_dict(snapshot),
                "command": {
                    "commandType": "PASS" if tick == 10 else "MOVE_TO",
                    "teamId": 0,
                    "playerId": 2,
                    "duration": 0,
                    "parameters": {"target_player_id": 3, "type": "GROUND"}
                    if tick == 10
                    else {"target_x": -10, "target_y": 0, "sprint": False},
                },
                "reason": "test",
                "alternatives": [],
            }
        )
    return out


def supporting_records(mid="m1", opponent="high-line"):
    result = []
    for attempt in range(3):
        episode = records(mid, opponent)
        for record in episode:
            record["snapshot"]["tick"] += attempt * 10
            record["snapshot"]["clock"] += attempt * 20
        result.extend(episode)
    return result


class LearningTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name)
        self.trainer = Trainer(self.path / "coach.db")

    def tearDown(self):
        self.trainer.close()
        self.tmp.cleanup()

    def test_train_keeps_base_style_and_coach_policy(self):
        self.trainer.ingest(manifest(), records())
        base = Profile(style=Style(0.1, 1, 0.4, 0.3, "2-1-1"), coach_policy="rules")
        plain = self.trainer.train()
        kept = self.trainer.train(base=base)
        self.assertEqual((plain.style, plain.coach_policy), (Profile().style, "none"))
        self.assertEqual((kept.style, kept.coach_policy), (base.style, base.coach_policy))
        self.assertEqual(kept.settings, plain.settings)
        self.assertNotEqual(kept.version, plain.version)
        self.trainer.export(kept.version, self.path / "kept.json")
        self.assertEqual(load_profile(self.path / "kept.json"), kept)

    def test_cli_base_profile(self):
        from contextlib import redirect_stdout
        from io import StringIO

        from agent.__main__ import main

        self.trainer.ingest(manifest(), records())
        self.trainer.close()
        base = Profile(style=Style(0.5, 1, 0.2, 0.0, "1-2-1"))
        save_profile(base, self.path / "base.json")
        out = self.path / "trained.json"
        with redirect_stdout(StringIO()):
            code = main(
                [
                    "train",
                    "--db",
                    str(self.path / "coach.db"),
                    "--output",
                    str(out),
                    "--base-profile",
                    str(self.path / "base.json"),
                ]
            )
        self.trainer = Trainer(self.path / "coach.db")
        self.assertEqual(code, 0)
        self.assertEqual(load_profile(out).style, base.style)

    def test_cli_learn_bad_base_profile_writes_nothing(self):
        from contextlib import redirect_stderr
        from io import StringIO

        from agent.__main__ import main

        (self.path / "match.json").write_text(json.dumps(manifest()))
        (self.path / "records.jsonl").write_text("\n".join(json.dumps(r) for r in records()) + "\n")
        database = self.path / "fresh.db"
        with redirect_stderr(StringIO()):
            code = main(
                [
                    "learn",
                    "--db",
                    str(database),
                    "--manifest",
                    str(self.path / "match.json"),
                    "--records",
                    str(self.path / "records.jsonl"),
                    "--output",
                    str(self.path / "trained.json"),
                    "--base-profile",
                    str(self.path / "missing.json"),
                ]
            )
        self.assertEqual(code, 2)
        self.assertFalse(database.exists())
        self.assertFalse((self.path / "trained.json").exists())

    def test_idempotent_sorted_import_survives_fresh_process(self):
        data = records()
        first = self.trainer.ingest(manifest(), list(reversed(data)))
        second = self.trainer.ingest(manifest(), data + data)
        self.assertEqual(first, second)
        self.assertEqual(first["observations"], 3)
        self.trainer.close()
        self.trainer = Trainer(self.path / "coach.db")
        self.assertEqual(first, self.trainer.report("m1"))

    def test_five_player_logs_do_not_multiply_scouting_samples(self):
        data = records()
        expanded = []
        for record in data:
            for pid in range(5):
                r = copy.deepcopy(record)
                r["player_id"] = pid
                r["command"] = {
                    "commandType": "SET_STANCE",
                    "teamId": 0,
                    "playerId": pid,
                    "duration": 0,
                    "parameters": {"stance": 0},
                }
                expanded.append(r)
        report = self.trainer.ingest(manifest(), expanded)
        self.assertEqual(report["observations"], 3)
        self.assertEqual(report["scouting"]["samples"], 3)

    def test_conflicting_duplicate_rolls_back_entire_import(self):
        data = records()
        conflicting = copy.deepcopy(data[0])
        conflicting["snapshot"]["ball"] = [1, 1]
        with self.assertRaises(ValueError):
            self.trainer.ingest(manifest(), data + [conflicting])
        with self.assertRaises(KeyError):
            self.trainer.report("m1")
        self.trainer.ingest(manifest(), data)
        with self.assertRaises(ValueError):
            self.trainer.ingest({**manifest(), "opponent_id": "other"}, data)

    def test_pass_loss_is_an_inference_not_a_claim_of_interception(self):
        report = self.trainer.ingest(manifest(), records())
        lesson = next(x for x in report["lessons"] if x["kind"] == "risky_outlet")
        self.assertEqual(lesson["confidence"], "inferred")
        self.assertEqual(lesson["role"], "MID")
        self.assertIn("remedy", lesson)
        self.assertEqual(report["outcomes"]["confirmed_passes"], 0)
        self.assertEqual(report["outcomes"]["possible_pass_losses"], 1)

    def test_sparse_gap_does_not_attribute_pass_failure(self):
        report = self.trainer.ingest(manifest(), records(gap=9))
        self.assertEqual(report["outcomes"]["possible_pass_losses"], 0)
        self.assertEqual(report["outcomes"]["unknown_attempts"], 1)

    def test_missing_match_identity_and_profile_mismatch_are_rejected(self):
        for field in ("match_id", "opponent_id", "played_at"):
            bad = manifest()
            del bad[field]
            with self.assertRaises(ValueError):
                self.trainer.ingest(bad, records())
        bad = records()
        bad[1]["profile_version"] = "other"
        with self.assertRaises(ValueError):
            self.trainer.ingest(manifest(), bad)
        bad = records()
        bad[0]["team_id"] = 1
        with self.assertRaises(ValueError):
            self.trainer.ingest(manifest(), bad)

    def test_partial_match_teaches_but_does_not_count_as_a_loss(self):
        report = self.trainer.ingest(manifest(complete=False), records())
        self.assertTrue(report["lessons"])
        self.assertIsNone(report["result"])
        self.assertFalse(report["complete"])
        self.assertIn("insufficient", report["evaluation"])

    def test_opponent_scouting_and_bounded_learning_export(self):
        for i in range(4):
            mid = f"m{i}"
            self.trainer.ingest(manifest(mid), supporting_records(mid))
        profile = self.trainer.train()
        self.assertGreater(profile.settings.pass_margin, Settings().pass_margin)
        self.assertLessEqual(profile.settings.pass_margin, 4.5)
        self.assertIn("high-line", profile.opponents)
        self.assertEqual(self.trainer.train().version, profile.version)
        settings = effective_settings(profile, "high-line")
        self.assertGreaterEqual(settings.support_width, profile.settings.support_width)
        self.assertEqual(effective_settings(profile, "unseen"), profile.settings)
        self.trainer.export(profile.version, self.path / "profile.json")
        loaded = load_profile(self.path / "profile.json")
        self.assertEqual(loaded, profile)
        self.assertEqual(loaded.version, profile.version)

    def test_single_match_is_a_proposal_not_an_automatic_global_change(self):
        self.trainer.ingest(manifest(), records())
        self.assertEqual(self.trainer.train().settings, Settings())
        self.assertEqual(self.trainer.report("m1")["promotion"], "proposal_only")

    def test_old_opponent_history_is_weaker_than_fresh_history(self):
        for i in range(4):
            mid = f"old{i}"
            self.trainer.ingest(
                {**manifest(mid, "old"), "played_at": "2025-01-01T00:00:00+00:00"},
                records(mid, "old"),
            )
        for i in range(4):
            mid = f"fresh{i}"
            self.trainer.ingest(manifest(mid, "fresh"), records(mid, "fresh"))
        profile = self.trainer.train()
        self.assertLess(profile.opponents["old"]["weight"], profile.opponents["fresh"]["weight"])

    def test_sparse_observations_cannot_claim_a_shot_scored(self):
        data = records(gap=8)
        data[0]["command"] = {
            "commandType": "SHOOT",
            "teamId": 0,
            "playerId": 2,
            "duration": 0,
            "parameters": {"aim_location": "CENTER", "power": 1},
        }
        data[-1]["snapshot"]["score"] = [1, 0]
        report = self.trainer.ingest({**manifest(), "result": [1, 1]}, data)
        self.assertEqual(report["outcomes"]["confirmed_goals"], 0)
        self.assertEqual(report["outcomes"]["unknown_attempts"], 1)

    def test_error_records_are_counted_and_not_interpreted_as_success(self):
        data = records()
        error = {
            "type": "error",
            "match_id": "m1",
            "opponent_id": "high-line",
            "profile_version": "baseline",
            "team_id": 0,
            "player_id": 1,
            "tick": 9,
            "error": "invalid state",
        }
        report = self.trainer.ingest(manifest(), data + [error])
        self.assertEqual(report["errors"], 1)
        self.assertTrue(any(x["kind"] == "runtime_error" for x in report["lessons"]))

    def test_export_unknown_version_and_corrupt_profiles_fail_explicitly(self):
        with self.assertRaises(KeyError):
            self.trainer.export("not-real", self.path / "profile.json")
        (self.path / "bad.json").write_text(
            json.dumps(
                {"version": "bad", "settings": {"pass_margin": float("nan")}, "opponents": {}}
            )
        )
        with self.assertRaises(ValueError):
            load_profile(self.path / "bad.json")
        save_profile(Profile(version="baseline"), self.path / "baseline.json")
        self.assertEqual(load_profile(self.path / "baseline.json").settings, Settings())

    def test_runtime_logs_are_directly_ingestible(self):
        logs = []
        runtime = Runtime(match_id="m1", opponent_id="high-line", emit=logs.append)
        self.addCleanup(runtime.close)
        for pid in range(5):
            runtime.handle(payload(pid=pid))
        self.assertTrue(runtime.flush_logs())
        report = self.trainer.ingest(manifest(complete=False), logs)
        self.assertEqual(report["observations"], 1)
        self.assertEqual(report["decisions"], 5)

    def test_learned_correction_changes_risky_midfield_outlet_not_keeper_shape(self):
        for i in range(3):
            mid = f"m{i}"
            self.trainer.ingest(manifest(mid), supporting_records(mid))
        learned = self.trainer.train()

        def setup(s):
            position(s, 0, 2, 0, 0)
            position(s, 0, 3, 20, -10)
            position(s, 0, 4, 20, 10)
            position(s, 1, 2, 10, -1.5)
            position(s, 1, 3, 10, 1.5)

        snapshot, _ = decode(edit(payload(ball=(0, 0)), setup))
        before = decide(snapshot, 2)
        after = decide(snapshot, 2, learned.settings)
        self.assertNotEqual(before.command, after.command)
        self.assertEqual(before.command["commandType"], "PASS")
        self.assertEqual(decide(snapshot, 0).command, decide(snapshot, 0, learned.settings).command)

    def test_partial_match_can_be_finalized_without_double_counting_evidence(self):
        partial = self.trainer.ingest(manifest(complete=False), records())
        final = self.trainer.ingest(manifest(), records())
        self.assertFalse(partial["complete"])
        self.assertTrue(final["complete"])
        self.assertEqual(final["observations"], partial["observations"])
        self.assertEqual(final["result"], [0, 1])
        with self.assertRaises(ValueError):
            self.trainer.ingest(manifest(complete=False), records())

    def test_unconfirmed_possession_attempt_cannot_tune_passing_safety(self):
        for i in range(4):
            mid = f"m{i}"
            data = records(mid)
            data[0]["snapshot"]["owner"] = None
            self.trainer.ingest(manifest(mid), data)
        self.assertEqual(self.trainer.train().settings.pass_margin, Settings().pass_margin)

    def test_score_change_is_an_attribution_boundary(self):
        data = records()
        for record in data[1:]:
            record["snapshot"]["score"] = [1, 0]
        report = self.trainer.ingest({**manifest(), "result": [1, 0]}, data)
        self.assertEqual(report["outcomes"]["possible_pass_losses"], 0)
        self.assertEqual(report["outcomes"]["unknown_attempts"], 1)
        other = records("m2")
        other[0]["snapshot"]["owner"] = [1, 3]
        other[0]["command"] = {
            "commandType": "SET_STANCE",
            "teamId": 0,
            "playerId": 2,
            "duration": 0,
            "parameters": {"stance": 0},
        }
        for record in other[1:]:
            record["snapshot"]["owner"] = [0, 2]
            record["snapshot"]["score"] = [1, 0]
        report = self.trainer.ingest({**manifest("m2"), "result": [1, 0]}, other)
        self.assertEqual(report["scouting"]["possible_opponent_losses"], [])

    def test_impossible_final_score_rejects_both_home_and_away_transactionally(self):
        for team in (0, 1):
            mid = f"bad{team}"
            data = records(mid)
            for record in data:
                record["team_id"] = team
                record["command"]["teamId"] = team
                record["snapshot"]["team"] = team
                record["snapshot"]["score"] = [2, 2]
            with self.assertRaises(ValueError):
                self.trainer.ingest({**manifest(mid), "team_id": team}, data)
            with self.assertRaises(KeyError):
                self.trainer.report(mid)

    def test_recent_clean_observations_do_not_revive_old_opponent_errors(self):
        for i in range(3):
            mid = f"old{i}"
            self.trainer.ingest(
                {**manifest(mid), "played_at": "2000-01-01T00:00:00+00:00"}, records(mid)
            )
        for i in range(2):
            mid = f"clean{i}"
            data = records(mid)
            for record in data:
                record["snapshot"]["owner"] = [0, 2]
            self.trainer.ingest(manifest(mid), data)
        scout = self.trainer.train().opponents["high-line"]
        self.assertEqual(scout["settings"]["press_radius"], Settings().press_radius)

    def test_weak_proposal_and_explicit_promotion_history_survive_restart(self):
        self.trainer.ingest(manifest(), records())
        profile = self.trainer.train()
        audit = self.trainer.audit(profile.version)
        proposal = next(p for p in audit["proposals"] if p["setting"] == "pass_margin")
        self.assertEqual(proposal["status"], "insufficient_evidence")
        self.assertEqual(proposal["evidence"], ["m1"])
        self.assertEqual(audit["promotions"], [])
        self.trainer.promote(profile.version, "approved for controlled practice")
        self.trainer.close()
        self.trainer = Trainer(self.path / "coach.db")
        promotion = self.trainer.audit(profile.version)["promotions"][0]
        self.assertEqual(promotion["note"], "approved for controlled practice")
        self.assertEqual(promotion["status"], "operator_approved")
        with self.assertRaises(KeyError):
            self.trainer.promote("nonexistent", "no")
