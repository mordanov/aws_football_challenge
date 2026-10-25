"""Deterministic counts are evidence, not model-generated judgments."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from agent.advisor.offline.counts import analyze_shooting_counts, build_count_script
from tests.offline_support import make_packet


class ShootingCountsTest(unittest.TestCase):
    def packet(self):
        return make_packet().model_dump(mode="json")

    def test_counts_depend_on_claims_and_retain_provenance(self):
        for observed, reported in ((3, 0), (17, 9)):
            packet = self.packet()
            packet["claims"][0]["text"] = f"SHOOT command issued {observed} times"
            packet["claims"][1]["text"] = f"Report records SHOOT={reported}"
            result = analyze_shooting_counts(packet)
            self.assertEqual(result["observed"]["count"], observed)
            self.assertEqual(result["report_claim"]["count"], reported)
            self.assertEqual(result["observed_minus_report"], observed - reported)
            self.assertEqual(result["observed"]["claim_id"], packet["claims"][0]["id"])
            self.assertEqual(
                result["report_claim"]["source_ids"], packet["claims"][1]["source_ids"]
            )

    def test_missing_is_not_zero_and_narrative_claims_are_not_counts(self):
        packet = self.packet()
        packet["claims"] = [
            packet["claims"][1],
            {
                "id": "narrative",
                "kind": "report_claim",
                "text": "SHOOT more often",
                "source_ids": ["report"],
            },
            {
                "id": "guess",
                "kind": "inferred",
                "text": "SHOOT command issued 999 times",
                "source_ids": ["report"],
            },
        ]
        result = analyze_shooting_counts(packet)
        self.assertEqual(result["observed"]["status"], "missing")
        self.assertIsNone(result["observed"]["count"])
        self.assertEqual(result["report_claim"]["count"], 0)
        self.assertIsNone(result["observed_minus_report"])

    def test_malformed_and_duplicate_counts_raise_without_raw_text(self):
        for text in (
            "SHOOT command issued -1 times",
            "SHOOT command issued 2.5 times",
            "SHOOT command issued secret times",
            "SHOOT command issued 3 times junk",
            "SHOOT command issued 9999999999999999999999999 times",
            "Report records SHOOT=-2",
            "Report records SHOOT=1.5",
            "Report records SHOOT=secret",
        ):
            with self.subTest(text=text):
                packet = self.packet()
                index = 1 if text.startswith("Report") else 0
                packet["claims"][index]["text"] = text
                with self.assertRaisesRegex(ValueError, "MALFORMED_SHOOT_COUNT") as raised:
                    analyze_shooting_counts(packet)
                self.assertNotIn(text, str(raised.exception))
        for index in (0, 1):
            packet = self.packet()
            packet["claims"].append({**packet["claims"][index], "id": "duplicate"})
            with self.assertRaisesRegex(ValueError, "AMBIGUOUS_SHOOT_COUNT"):
                analyze_shooting_counts(packet)

    def test_trusted_standalone_script_matches_local_analyzer(self):
        packet = make_packet()
        artifact = build_count_script(packet, max_output_bytes=16384)
        self.assertEqual(artifact["evidence_digest"], packet.digest)
        self.assertEqual(artifact["origin"], "deterministic_shooting_counts_v1")
        self.assertIsNone(artifact["usage"])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "analysis.py").write_text(artifact["code"])
            data = packet.model_dump(mode="json")
            for count in (3, 21):
                data["claims"][0]["text"] = f"SHOOT command issued {count} times"
                (root / "evidence.json").write_text(json.dumps(data))
                process = subprocess.run(
                    [sys.executable, "analysis.py"],
                    cwd=root,
                    capture_output=True,
                    text=True,
                    timeout=5,
                    check=True,
                )
                self.assertEqual(json.loads(process.stdout), analyze_shooting_counts(data))
            data["claims"][0]["text"] = "SHOOT command issued invalid times"
            (root / "evidence.json").write_text(json.dumps(data))
            process = subprocess.run(
                [sys.executable, "analysis.py"],
                cwd=root,
                capture_output=True,
                text=True,
                timeout=5,
            )
            self.assertNotEqual(process.returncode, 0)
            self.assertEqual(process.stdout, "")

    def test_script_rejects_bad_input_or_size_before_execution(self):
        with self.assertRaisesRegex(ValueError, "OUTPUT_LIMIT"):
            build_count_script(make_packet(), max_output_bytes=1)
