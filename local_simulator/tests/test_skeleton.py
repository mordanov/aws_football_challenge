import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class SkeletonTests(unittest.TestCase):
    def invoke(self, output, seed="7"):
        return subprocess.run(
            [
                sys.executable,
                "-m",
                "football_lab",
                "--seed",
                seed,
                "--output",
                str(output),
            ],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )

    def test_real_cli_produces_repeatable_semantic_replay(self):
        with tempfile.TemporaryDirectory() as directory:
            first = Path(directory) / "first.json"
            second = Path(directory) / "second.json"
            result = self.invoke(first)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(self.invoke(second).returncode, 0)
            self.assertEqual(first.read_bytes(), second.read_bytes())
            replay = json.loads(first.read_text())
            self.assertEqual(replay["kind"], "wiring-check")
            self.assertEqual(replay["status"], "complete")
            self.assertEqual(replay["seed"], 7)
            self.assertEqual(len(replay["observations"]), 2)
            self.assertEqual(
                replay["observations"][0]["frame"], replay["observations"][1]["frame"]
            )
            self.assertEqual(len(replay["frames"][-1]["players"]), 10)
            self.assertEqual(replay["frames"][-1]["tick"], 1)
            self.assertEqual(
                [[c["team_id"] for c in r["commands"]] for r in replay["replies"]],
                [[0] * 5, [1] * 5],
            )

    def test_existing_artifact_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "replay.json"
            output.write_text("existing evidence")
            result = self.invoke(output)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("failed", result.stderr)
            self.assertEqual(output.read_text(), "existing evidence")
            self.assertEqual(list(Path(directory).iterdir()), [output])

    def test_invalid_seed_reports_failure_without_artifact(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "replay.json"
            for seed in ("-1", str(2**32)):
                with self.subTest(seed=seed):
                    result = self.invoke(output, seed)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("nonnegative", result.stderr)
                    self.assertFalse(output.exists())
