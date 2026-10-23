"""Deployment staging remains independent of sibling source trees and the cwd."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class DeploymentIsolationTest(unittest.TestCase):
    def test_staging_uses_only_the_standalone_project(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "standalone"
            for name in ("agent", "profiles", "deploy"):
                shutil.copytree(
                    ROOT / name,
                    project / name,
                    ignore=shutil.ignore_patterns(
                        "__pycache__", ".cli", ".cache", "node_modules", "cdk.out"
                    ),
                )
            result = subprocess.run(
                [sys.executable, str(project / "deploy/deploy.py"), "--stage-only"],
                cwd=directory,
                env={**os.environ, "PYTHONPATH": str(project)},
                capture_output=True,
                text=True,
                check=True,
            )
            self.assertIn("staged agent/", result.stdout)
            staged = project / "deploy/app/player"
            self.assertEqual(
                (staged / "profile.json").read_bytes(),
                (project / "profiles/recommended.json").read_bytes(),
            )
            source_files = {
                path.relative_to(project / "agent"): path.read_bytes()
                for path in (project / "agent").rglob("*.py")
            }
            staged_files = {
                path.relative_to(staged / "agent"): path.read_bytes()
                for path in (staged / "agent").rglob("*.py")
            }
            self.assertEqual(staged_files, source_files)
            command = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "agent",
                    "decide",
                    "--input",
                    str(ROOT / "examples/invocation.json"),
                    "--profile",
                    str(staged / "profile.json"),
                ],
                cwd=staged,
                env={**os.environ, "PYTHONPATH": str(staged)},
                capture_output=True,
                text=True,
                check=True,
            )
            self.assertEqual(len(json.loads(command.stdout)), 1)


if __name__ == "__main__":
    unittest.main()
