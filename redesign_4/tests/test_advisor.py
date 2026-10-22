import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from agent.model import Profile

ROOT = Path(__file__).resolve().parents[1]
HAS_LANGCHAIN = importlib.util.find_spec("langchain_core") is not None


class RuntimeGuardTest(unittest.TestCase):
    def test_runtime_never_imports_langchain(self):
        code = (
            "import sys, agent.runtime, agent.__main__, agent.trainer; "
            "bad = sorted(m for m in sys.modules "
            "if m.startswith(('langchain', 'pydantic', 'agent.advisor'))); "
            "print(bad); sys.exit(1 if bad else 0)"
        )
        done = subprocess.run(
            [sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=60
        )
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)


@unittest.skipUnless(HAS_LANGCHAIN, "install the llm extra")
class AdvisorTest(unittest.TestCase):
    def advisor(self, *replies):
        from langchain_core.language_models.fake_chat_models import FakeListChatModel

        from agent.advisor import Advisor
        from agent.archetypes import ARCHETYPES

        return Advisor(FakeListChatModel(responses=list(replies)), ARCHETYPES)

    def test_pick_archetype_returns_validated_profile(self):
        from agent.archetypes import ARCHETYPES

        reply = '{"archetype": "park_the_bus", "coach_policy": "rules", "rationale": "deep"}'
        for text in (reply, f"```json\n{reply}\n```"):
            with self.subTest(text=text):
                advice, profile = self.advisor(text).pick_archetype({"rows": []}, "redesign_3_v1")
                self.assertEqual(advice.archetype, "park_the_bus")
                self.assertEqual(profile.style, ARCHETYPES["park_the_bus"].style)
                self.assertEqual(profile.coach_policy, "rules")
                self.assertEqual(profile.version, "advice-park_the_bus-rules")

    def test_bad_model_output_raises_advisor_error(self):
        from agent.advisor import AdvisorError

        bad = [
            "not json",
            '{"archetype": "nope", "coach_policy": "none", "rationale": "x"}',
            '{"archetype": "balanced", "coach_policy": "llm", "rationale": "x"}',
            '{"archetype": "balanced", "coach_policy": "none", "rationale": "x", "extra": 1}',
        ]
        for text in bad:
            with self.subTest(text=text), self.assertRaises(AdvisorError):
                self.advisor(text).pick_archetype({}, "x")

    def test_model_failure_raises_advisor_error(self):
        from agent.advisor import Advisor, AdvisorError

        class Down:
            def invoke(self, _messages):
                raise RuntimeError("throttled")

        with self.assertRaises(AdvisorError):
            Advisor(Down(), {}).pick_archetype({}, "x")

    def test_review_match_bounds_are_enforced(self):
        from dataclasses import asdict

        from agent.advisor import AdvisorError

        current = Profile(
            version="cur",
            opponents={"o": {"weight": 1.0, "samples": 3, "settings": asdict(Profile().settings)}},
        )
        settings = {**asdict(current.settings), "press_radius": 26.0}
        style = {**asdict(current.style), "tackle_aggression": 0.5}
        ok = json.dumps({"settings": settings, "style": style, "rationale": "press more"})
        advice, profile = self.advisor(ok).review_match({"score": [0, 1]}, current)
        self.assertEqual(profile.settings.press_radius, 26.0)
        self.assertEqual(profile.style.tackle_aggression, 0.5)
        self.assertEqual(profile.opponents, current.opponents)
        self.assertEqual(profile.version, "cur-review")
        too_far = json.dumps(
            {"settings": {**settings, "press_radius": 99.0}, "style": style, "rationale": "x"}
        )
        with self.assertRaises(AdvisorError):
            self.advisor(too_far).review_match({}, current)

    def test_cli_writes_proposal_or_nothing(self):
        from contextlib import redirect_stderr, redirect_stdout
        from io import StringIO

        from langchain_core.language_models.fake_chat_models import FakeListChatModel

        from agent.advisor.__main__ import main

        with tempfile.TemporaryDirectory() as tmp:
            matrix, out = Path(tmp) / "m.json", Path(tmp) / "proposal.json"
            matrix.write_text("{}")
            reply = '{"archetype": "hard_tackle", "coach_policy": "none", "rationale": "x"}'
            argv = ["pick", "--matrix", str(matrix), "--opponent", "o", "--output", str(out)]
            with redirect_stdout(StringIO()):
                self.assertEqual(main(argv, llm=FakeListChatModel(responses=[reply])), 0)
            proposal = json.loads(out.read_text())
            self.assertEqual(proposal["status"], "proposal")
            self.assertEqual(proposal["profile"]["version"], "advice-hard_tackle-none")
            out.unlink()
            with redirect_stderr(StringIO()):
                self.assertEqual(main(argv, llm=FakeListChatModel(responses=["junk"])), 2)
            self.assertFalse(out.exists())


if __name__ == "__main__":
    unittest.main()
