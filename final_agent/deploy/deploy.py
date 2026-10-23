#!/usr/bin/env python3
"""Stage and deploy the five existing AgentCore runtimes from final_agent.

Usage (AWS credentials already in the environment; region us-east-1 or us-west-2):
    python deploy/deploy.py                 # stage, bootstrap if needed, deploy, print ARNs
    python deploy/deploy.py --stage-only    # only copy agent/ + profile into app/player
    python deploy/deploy.py --status        # print the registered runtime ARNs

Requires Node >= 20 with `agentcore` (npm @aws/agentcore) on PATH, plus uv and the AWS CLI.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

DEPLOY = Path(__file__).resolve().parent
ROOT = DEPLOY.parent
PLAYER = DEPLOY / "app" / "player"
CDK = DEPLOY / "agentcore" / "cdk"
REGIONS = ("us-east-1", "us-west-2")
SEATS = {"r4_gk": "GK", "r4_def": "DEF", "r4_mid": "MID", "r4_fwd1": "FWD", "r4_fwd2": "FWD"}


def run(cmd: list[str], cwd: Path = DEPLOY, capture: bool = False) -> str:
    print("$", " ".join(cmd), flush=True)
    done = subprocess.run(cmd, cwd=cwd, check=True, text=True, capture_output=capture)
    return done.stdout if capture else ""


def stage(profile: Path) -> None:
    sys.path.insert(0, str(ROOT))
    from agent.profile import load_profile

    load_profile(str(profile))  # fail fast on an invalid profile
    shutil.rmtree(PLAYER / "agent", ignore_errors=True)
    shutil.copytree(ROOT / "agent", PLAYER / "agent", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copyfile(profile, PLAYER / "profile.json")
    print(f"staged agent/ and {profile.name} into {PLAYER.relative_to(ROOT)}")


def preflight() -> tuple[str, str]:
    for tool in ("agentcore", "aws", "node", "uv"):
        if not shutil.which(tool):
            sys.exit(f"missing tool on PATH: {tool}")
    major = int(run(["node", "--version"], capture=True).strip().lstrip("v").split(".")[0])
    if major < 20:
        sys.exit("Node >= 20 is required by the AgentCore CLI/CDK")
    region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
    if region not in REGIONS:
        sys.exit(f"set AWS_REGION/AWS_DEFAULT_REGION to one of {REGIONS} (got {region!r})")
    account = run(
        ["aws", "sts", "get-caller-identity", "--query", "Account", "--output", "text"],
        capture=True,
    ).strip()
    return account, region


def bootstrap(account: str, region: str) -> None:
    if not (CDK / "node_modules").exists():
        run(["npm", "install", "--no-fund", "--no-audit"], cwd=CDK)
    probe = subprocess.run(
        ["aws", "cloudformation", "describe-stacks", "--stack-name", "CDKToolkit"],
        capture_output=True,
        text=True,
    )
    if probe.returncode != 0:
        run(["npx", "cdk", "bootstrap", f"aws://{account}/{region}"], cwd=CDK)


def status() -> None:
    raw = run(["agentcore", "status", "--json"], capture=True)
    arns = {
        name: arn
        for name, arn in _find_arns(json.loads(raw))
        if name in SEATS and ":runtime/" in arn
    }
    print("\nRegister in the Player Portal (My Team), one per position:")
    for name, seat in SEATS.items():
        print(f"  {seat:<4} {name:<8} {arns.get(name, '<not deployed>')}")


def _find_arns(node: object, name: str | None = None):
    if isinstance(node, dict):
        name = node.get("name", node.get("runtimeName", name))
        for key, value in node.items():
            if isinstance(value, str) and value.startswith("arn:aws:bedrock-agentcore:"):
                yield (name if key.lower().endswith(("runtimearn", "arn")) else None), value
            else:
                yield from _find_arns(value, name)
    elif isinstance(node, list):
        for item in node:
            yield from _find_arns(item, name)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--profile", type=Path, default=ROOT / "profiles" / "recommended.json")
    parser.add_argument("--stage-only", action="store_true")
    parser.add_argument("--status", action="store_true")
    args = parser.parse_args()
    if args.status:
        status()
        return
    stage(args.profile.resolve())
    if args.stage_only:
        return
    account, region = preflight()
    bootstrap(account, region)
    run(["agentcore", "deploy", "--yes"])
    status()


if __name__ == "__main__":
    main()
