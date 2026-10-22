"""python -m agent.advisor pick|review — writes a proposal file; deployment stays manual."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from ..archetypes import ARCHETYPES
from ..profile import load_profile
from .core import Advisor, AdvisorError, ArchetypeAdvice, SettingsAdvice


def bedrock_llm() -> Any:
    from langchain_aws import ChatBedrockConverse

    model = os.environ.get("AFC_ADVISOR_MODEL")
    if not model:
        raise AdvisorError("set AFC_ADVISOR_MODEL to a Bedrock model id")
    return ChatBedrockConverse(model=model, temperature=0)


def main(argv: list[str] | None = None, llm: Any = None) -> int:
    parser = argparse.ArgumentParser(description="Off-tick strategy advisor (proposals only)")
    ops = parser.add_subparsers(dest="operation", required=True)
    pick = ops.add_parser("pick", help="choose an archetype from a campaign report JSON")
    pick.add_argument("--matrix", required=True)
    pick.add_argument("--opponent", required=True)
    review = ops.add_parser("review", help="propose settings/style after a match report")
    review.add_argument("--report", required=True)
    review.add_argument("--profile", required=True)
    for sub in (pick, review):
        sub.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    try:
        advisor = Advisor(llm if llm is not None else bedrock_llm(), ARCHETYPES)
        advice: ArchetypeAdvice | SettingsAdvice
        if args.operation == "pick":
            matrix = json.loads(Path(args.matrix).read_text())
            advice, profile = advisor.pick_archetype(matrix, args.opponent)
        else:
            report = json.loads(Path(args.report).read_text())
            advice, profile = advisor.review_match(report, load_profile(args.profile))
        proposal = {
            "status": "proposal",
            "advice": advice.model_dump(),
            "profile": profile.to_dict(),
        }
        Path(args.output).write_text(json.dumps(proposal, indent=2, sort_keys=True) + "\n")
    except (AdvisorError, OSError, ValueError, TypeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"proposal": args.output, "version": profile.version}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
