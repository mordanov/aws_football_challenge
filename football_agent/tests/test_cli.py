import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from test_learning import manifest, records, supporting_records
from test_runtime import payload


def cli(*args):
    return subprocess.run(
        [sys.executable, "-m", "agent", *map(str, args)],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        timeout=10,
        check=False,
    )


class CLITests(unittest.TestCase):
    def test_one_command_learns_exports_and_reloads_after_process_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            database, profile = root / "coach.db", root / "profile.json"
            for i in range(3):
                mid = f"m{i}"
                (root / "match.json").write_text(json.dumps(manifest(mid)))
                (root / "records.jsonl").write_text(
                    "\n".join(json.dumps(r) for r in supporting_records(mid)) + "\n"
                )
                result = cli(
                    "learn",
                    "--db",
                    database,
                    "--manifest",
                    root / "match.json",
                    "--records",
                    root / "records.jsonl",
                    "--output",
                    profile,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
            trained = json.loads(profile.read_text())
            self.assertGreater(trained["settings"]["pass_margin"], 3)
            version = trained["version"]
            reported = cli("report", "--db", database, "--match-id", "m0")
            self.assertEqual(reported.returncode, 0, reported.stderr)
            self.assertIn("risky_outlet", reported.stdout)
            (root / "payload.json").write_text(json.dumps(payload()))
            decision = cli(
                "decide",
                "--input",
                root / "payload.json",
                "--profile",
                profile,
                "--match-id",
                "m4",
                "--opponent-id",
                "high-line",
            )
            self.assertEqual(decision.returncode, 0, decision.stderr)
            self.assertEqual(len(json.loads(decision.stdout)), 1)
            self.assertEqual(json.loads(decision.stderr)["profile_version"], version)
            audit = cli("audit", "--db", database, "--version", version)
            self.assertEqual(audit.returncode, 0, audit.stderr)
            self.assertIn("causality", audit.stdout)
            approval = cli(
                "promote",
                "--db",
                database,
                "--version",
                version,
                "--note",
                "approved for practice only",
            )
            self.assertEqual(approval.returncode, 0, approval.stderr)
            self.assertEqual(json.loads(approval.stdout)["status"], "operator_approved")
            audit = cli("audit", "--db", database, "--version", version)
            self.assertEqual(len(json.loads(audit.stdout)["promotions"]), 1)

    def test_bad_json_fails_without_creating_a_successful_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "bad.json").write_text("{broken")
            result = cli("decide", "--input", root / "bad.json")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("error", result.stderr.lower())
            self.assertEqual(result.stdout, "")

    def test_explicit_binding_allows_unidentified_but_match_scoped_runtime_logs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "manifest.json").write_text(json.dumps(manifest(complete=False)))
            data = records()
            for record in data:
                record["match_id"] = None
                record["opponent_id"] = None
            (root / "records.jsonl").write_text("\n".join(json.dumps(r) for r in data))
            args = (
                "ingest",
                "--db",
                root / "coach.db",
                "--manifest",
                root / "manifest.json",
                "--records",
                root / "records.jsonl",
            )
            rejected = cli(*args)
            self.assertNotEqual(rejected.returncode, 0)
            accepted = cli(*args, "--bind-context")
            self.assertEqual(accepted.returncode, 0, accepted.stderr)
            self.assertEqual(json.loads(accepted.stdout)["observations"], 3)
