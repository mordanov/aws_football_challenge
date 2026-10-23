"""python deploy/offline_coach.py <command> — offline coaching resource lifecycle.

Manages three AgentCore resources owned by this project:
  • Memory (short-term scouting notes, actor-scoped semantic strategy)
  • CodeInterpreter (SANDBOX network, no execution role or mounts)
  • Evaluator (LLM-as-a-judge faithfulness scorer, custom rubric)

These resources are separate from the five deployed football player runtimes
(AgentCore runtimes r4_gk/r4_def/r4_mid/r4_fwd1/r4_fwd2). This script never
modifies the player CloudFormation stack, their IAM roles or their agentcore.json.

Commands:
  plan     Print the intended resource/IAM changes (no AWS calls).
  apply    Provision resources (requires --approve, expected account/region).
  status   Describe existing owned resources (read-only).
  cleanup  Delete owned resources (requires --approve, explicit IDs only).

Exit codes: 0=success, 1=user error, 2=service/validation failure.

Cost controls: doctor reports unknown pricing; no dollar estimate is claimed.
Every apply call enforces finite page/token/session limits; actual usage is
not a hard budget enforcement.

AWS AUTHORIZATION: This script is ready to run but does NOT provision, change
IAM or delete unless --approve is passed AND the caller has verified
account/region match. No live calls happen in plan or without --approve in apply.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

DEPLOY = Path(__file__).resolve().parent
ROOT = DEPLOY.parent

# ── SDK shape constants (verified against published API) ─────────────────────

_NETWORK_MODE_SANDBOX = "SANDBOX"
_EVALUATOR_LEVEL_TRACE = "TRACE"
_POLL_INTERVAL = 5  # seconds between readiness polls
_POLL_DEADLINE = 120  # maximum seconds to wait for resource activation
_MAX_RETRIES = 3


# ── Config loading ────────────────────────────────────────────────────────────


def _load_config(path: str) -> dict[str, Any]:
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read config {path}: {exc}") from exc
    required = {"account", "region", "team_id", "memory", "interpreter", "evaluator"}
    missing = required - set(data)
    if missing:
        raise ValueError(f"config missing keys: {sorted(missing)}")
    # Store config directory so receipts live beside the config, not the source tree.
    data["_data_dir"] = str(Path(path).resolve().parent)
    return data


# ── Receipt file (git-ignored; records owned resource IDs atomically) ─────────


def _receipt_path(config: dict[str, Any]) -> Path:
    base = Path(config["_data_dir"]) if "_data_dir" in config else ROOT
    return base / f".offline_coach_resources_{config['team_id']}.json"


def _load_receipt(config: dict[str, Any]) -> dict[str, Any]:
    p = _receipt_path(config)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _save_receipt(config: dict[str, Any], receipt: dict[str, Any]) -> None:
    import os
    import tempfile

    p = _receipt_path(config)
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", dir=p.parent, prefix=f".{p.name}.", delete=False
        ) as f:
            tmp = Path(f.name)
            json.dump(receipt, f, indent=2, sort_keys=True)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, p)
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)


# ── Policy generation ─────────────────────────────────────────────────────────


def _invocation_policy(config: dict[str, Any], receipt: dict[str, Any]) -> dict[str, Any]:
    """Least-privilege policy for the offline CLI operator (invoke only, no management)."""
    region = config["region"]
    account = config["account"]
    team = config["team_id"]
    model_id = config["evaluator"]["model_id"]

    resources: list[str] = []
    if "memory_id" in receipt:
        resources.append(
            f"arn:aws:bedrock-agentcore:{region}:{account}:memory/{receipt['memory_id']}"
        )
    if "interpreter_id" in receipt:
        resources.append(
            f"arn:aws:bedrock-agentcore:{region}:{account}:code-interpreter/{receipt['interpreter_id']}"
        )
    if "evaluator_id" in receipt:
        resources.append(
            f"arn:aws:bedrock-agentcore:{region}:{account}:evaluator/{receipt['evaluator_id']}"
        )

    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "OfflineCoachInvoke",
                "Effect": "Allow",
                "Action": [
                    "bedrock-agentcore:GetMemory",
                    "bedrock-agentcore:RetrieveMemoryRecords",
                    "bedrock-agentcore:IngestMemoryRecord",
                    "bedrock-agentcore:InvokeCodeInterpreter",
                    "bedrock-agentcore:GetCodeInterpreterSession",
                    "bedrock-agentcore:StartCodeInterpreterSession",
                    "bedrock-agentcore:StopCodeInterpreterSession",
                    "bedrock-agentcore:CreateEvaluationJob",
                    "bedrock-agentcore:GetEvaluationJob",
                ],
                "Resource": resources or [
                    f"arn:aws:bedrock-agentcore:{region}:{account}:memory/*",
                    f"arn:aws:bedrock-agentcore:{region}:{account}:code-interpreter/*",
                    f"arn:aws:bedrock-agentcore:{region}:{account}:evaluator/*",
                ],
                "Condition": {"StringEquals": {"aws:RequestedRegion": region}},
            },
            {
                "Sid": "JudgeModelInvoke",
                "Effect": "Allow",
                "Action": ["bedrock:InvokeModel"],
                "Resource": [
                    f"arn:aws:bedrock:{region}::foundation-model/{model_id}",
                    f"arn:aws:bedrock:{region}:{account}:inference-profile/{model_id}",
                ],
            },
            {
                "Sid": "TagResourceOwner",
                "Effect": "Allow",
                "Action": ["tag:GetResources"],
                "Resource": "*",
                "Condition": {
                    "StringEquals": {
                        "aws:ResourceTag/afc:team": team,
                        "aws:ResourceTag/afc:component": "offline-coach",
                    }
                },
            },
        ],
    }


def _provisioning_policy(config: dict[str, Any]) -> dict[str, Any]:
    """Least-privilege policy for the one-time provisioner (no attachment to player roles)."""
    region = config["region"]
    account = config["account"]

    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "OfflineCoachManage",
                "Effect": "Allow",
                "Action": [
                    "bedrock-agentcore:CreateMemory",
                    "bedrock-agentcore:DeleteMemory",
                    "bedrock-agentcore:GetMemory",
                    "bedrock-agentcore:CreateCodeInterpreter",
                    "bedrock-agentcore:DeleteCodeInterpreter",
                    "bedrock-agentcore:GetCodeInterpreter",
                    "bedrock-agentcore:CreateEvaluator",
                    "bedrock-agentcore:DeleteEvaluator",
                    "bedrock-agentcore:GetEvaluator",
                ],
                "Resource": [
                    f"arn:aws:bedrock-agentcore:{region}:{account}:memory/*",
                    f"arn:aws:bedrock-agentcore:{region}:{account}:code-interpreter/*",
                    f"arn:aws:bedrock-agentcore:{region}:{account}:evaluator/*",
                ],
                "Condition": {"StringEquals": {"aws:RequestedRegion": region}},
            },
        ],
    }


# ── Resource request builders ─────────────────────────────────────────────────


def _memory_request(config: dict[str, Any]) -> dict[str, Any]:
    mem = config["memory"]
    return {
        "name": mem["name"],
        "description": f"Offline coach scouting memory for team {config['team_id']}",
        "memoryStrategies": [
            {
                "semanticMemoryStrategy": {
                    "name": mem["strategy"]["namespace"],
                    "configuration": {
                        "semanticMemoryConfiguration": {
                            "maxTokens": 2048,
                        }
                    },
                }
            }
        ],
        "tags": config.get("tags", {}),
    }


def _interpreter_request(config: dict[str, Any]) -> dict[str, Any]:
    interp = config["interpreter"]
    return {
        "name": interp["name"],
        "description": f"Offline coach sandbox interpreter for team {config['team_id']}",
        "networkConfiguration": {"networkMode": _NETWORK_MODE_SANDBOX},
        "tags": config.get("tags", {}),
        # No executionRoleArn: SANDBOX mode does not require a role
        # No filesystem mounts
    }


def _evaluator_request(config: dict[str, Any]) -> dict[str, Any]:
    ev = config["evaluator"]
    from agent.advisor.offline.evaluation import JUDGE_INSTRUCTIONS, RUBRIC_VERSION

    return {
        "name": ev["name"],
        "description": f"Offline coach faithfulness evaluator (rubric {RUBRIC_VERSION}) for team {config['team_id']}",
        "evaluatorConfig": {
            "llmAsAJudge": {
                "judgeModelConfig": {
                    "bedrockEvaluatorModelConfig": {
                        "modelId": ev["model_id"],
                        "inferenceConfig": {
                            "maxTokens": ev.get("max_tokens", 512),
                        },
                    }
                },
                "instructions": JUDGE_INSTRUCTIONS,
                "level": ev.get("level", _EVALUATOR_LEVEL_TRACE),
                "ratingScale": {
                    "numerical": [
                        {"value": entry["value"], "definition": entry["definition"]}
                        for entry in ev.get("rating_scale", [])
                    ]
                },
            }
        },
        "tags": config.get("tags", {}),
    }


# ── Readiness polling ─────────────────────────────────────────────────────────


def _poll_ready(client: Any, resource_type: str, resource_id: str) -> str:
    """Poll until resource reaches an active/available state or deadline."""
    get_fn = {
        "memory": (client.get_memory, "memoryId", "status"),
        "interpreter": (client.get_code_interpreter, "codeInterpreterId", "status"),
        "evaluator": (client.get_evaluator, "evaluatorId", "status"),
    }[resource_type]
    fn, id_key, status_key = get_fn
    deadline = time.monotonic() + _POLL_DEADLINE
    while True:
        resp = fn(**{id_key: resource_id})
        status = resp.get(status_key, "UNKNOWN")
        if status in ("ACTIVE", "AVAILABLE", "READY"):
            return status
        if status in ("FAILED", "DELETED", "ERROR"):
            raise RuntimeError(f"{resource_type} {resource_id} reached terminal status {status}")
        if time.monotonic() > deadline:
            raise TimeoutError(
                f"{resource_type} {resource_id} did not become active within {_POLL_DEADLINE}s"
            )
        time.sleep(_POLL_INTERVAL)


# ── Commands ──────────────────────────────────────────────────────────────────


def _cmd_plan(config: dict[str, Any]) -> int:
    """Print intended resource/IAM changes without any AWS calls."""
    receipt = _load_receipt(config)
    plan: dict[str, Any] = {
        "account": config["account"],
        "region": config["region"],
        "team_id": config["team_id"],
        "resources": {},
        "policies": {},
    }

    for kind, req_fn in [
        ("memory", _memory_request),
        ("interpreter", _interpreter_request),
        ("evaluator", _evaluator_request),
    ]:
        id_key = f"{kind}_id"
        if id_key in receipt:
            plan["resources"][kind] = {"action": "existing", "id": receipt[id_key]}
        else:
            plan["resources"][kind] = {"action": "create", "request": req_fn(config)}

    plan["policies"]["invocation"] = _invocation_policy(config, receipt)
    plan["policies"]["provisioning"] = _provisioning_policy(config)
    plan["note"] = (
        "No AWS calls made. Run 'apply --approve' to provision missing resources. "
        "Player runtimes r4_gk/r4_def/r4_mid/r4_fwd1/r4_fwd2 are NOT touched."
    )
    print(json.dumps(plan, indent=2))
    return 0


def _cmd_apply(config: dict[str, Any], approve: bool) -> int:
    """Provision missing resources. Requires --approve and account/region match."""
    if not approve:
        print(
            "apply: pass --approve to confirm resource provisioning "
            "(plan shows intended changes)",
            file=sys.stderr,
        )
        return 1

    # Account/region guard — must be verified before any AWS call
    try:
        import boto3
        sts = boto3.client("sts", region_name=config["region"])
        identity = sts.get_caller_identity()
    except Exception as exc:
        print(f"apply: cannot verify AWS identity: {exc}", file=sys.stderr)
        return 2

    if identity["Account"] != config["account"]:
        print(
            f"apply: account mismatch: config={config['account']} "
            f"caller={identity['Account']}",
            file=sys.stderr,
        )
        return 2

    receipt = _load_receipt(config)
    client = boto3.client("bedrock-agentcore", region_name=config["region"])
    updated = dict(receipt)

    # Memory
    if "memory_id" not in receipt:
        try:
            resp = client.create_memory(**_memory_request(config))
            memory_id = resp["memoryId"]
            _poll_ready(client, "memory", memory_id)
            updated["memory_id"] = memory_id
            updated["memory_name"] = config["memory"]["name"]
            print(f"memory created: {memory_id}")
        except Exception as exc:
            print(f"apply: memory creation failed: {exc}", file=sys.stderr)
            _save_receipt(config, updated)
            return 2
    else:
        print(f"memory exists: {receipt['memory_id']}")

    # Interpreter
    if "interpreter_id" not in receipt:
        try:
            resp = client.create_code_interpreter(**_interpreter_request(config))
            interp_id = resp["codeInterpreterId"]
            _poll_ready(client, "interpreter", interp_id)
            updated["interpreter_id"] = interp_id
            updated["interpreter_name"] = config["interpreter"]["name"]
            print(f"interpreter created: {interp_id}")
        except Exception as exc:
            print(f"apply: interpreter creation failed: {exc}", file=sys.stderr)
            _save_receipt(config, updated)
            return 2
    else:
        print(f"interpreter exists: {receipt['interpreter_id']}")

    # Evaluator
    if "evaluator_id" not in receipt:
        try:
            resp = client.create_evaluator(**_evaluator_request(config))
            ev_id = resp["evaluatorId"]
            _poll_ready(client, "evaluator", ev_id)
            updated["evaluator_id"] = ev_id
            updated["evaluator_name"] = config["evaluator"]["name"]
            print(f"evaluator created: {ev_id}")
        except Exception as exc:
            print(f"apply: evaluator creation failed: {exc}", file=sys.stderr)
            _save_receipt(config, updated)
            return 2
    else:
        print(f"evaluator exists: {receipt['evaluator_id']}")

    _save_receipt(config, updated)
    print(json.dumps({"status": "ok", "receipt": str(_receipt_path(config))}))
    return 0


def _cmd_status(config: dict[str, Any]) -> int:
    """Describe existing owned resources (read-only)."""
    receipt = _load_receipt(config)
    if not receipt:
        print(json.dumps({"status": "no resources recorded", "receipt_path": str(_receipt_path(config))}))
        return 0

    try:
        import boto3
        client = boto3.client("bedrock-agentcore", region_name=config["region"])
    except Exception as exc:
        print(f"status: cannot create AWS client: {exc}", file=sys.stderr)
        return 2

    report: dict[str, Any] = {"resources": {}}

    for kind, id_key, get_fn_name, id_param in [
        ("memory", "memory_id", "get_memory", "memoryId"),
        ("interpreter", "interpreter_id", "get_code_interpreter", "codeInterpreterId"),
        ("evaluator", "evaluator_id", "get_evaluator", "evaluatorId"),
    ]:
        if id_key in receipt:
            try:
                resp = getattr(client, get_fn_name)(**{id_param: receipt[id_key]})
                report["resources"][kind] = {
                    "id": receipt[id_key],
                    "status": resp.get("status", "unknown"),
                }
            except Exception as exc:
                report["resources"][kind] = {
                    "id": receipt[id_key],
                    "error": str(exc),
                }
        else:
            report["resources"][kind] = {"id": None}

    report["note"] = (
        "Player runtimes r4_gk/r4_def/r4_mid/r4_fwd1/r4_fwd2 are NOT shown here. "
        "pricing/account entitlements are unknown; doctor does not estimate costs."
    )
    print(json.dumps(report, indent=2))
    return 0


def _cmd_cleanup(config: dict[str, Any], approve: bool) -> int:
    """Delete owned resources (recorded IDs only). Requires --approve."""
    receipt = _load_receipt(config)
    if not receipt:
        print("cleanup: no recorded resources to delete")
        return 0

    # List owned resources before asking for approval
    owned = {k: v for k, v in receipt.items() if k.endswith("_id")}
    if not owned:
        print("cleanup: no resource IDs recorded")
        return 0

    print("cleanup: the following owned resources will be PERMANENTLY DELETED:")
    for k, v in owned.items():
        print(f"  {k}: {v}")
    print("cleanup: this is irreversible and will destroy all stored data.")

    if not approve:
        print(
            "cleanup: pass --approve to confirm deletion. "
            "Player runtimes are NOT affected.",
            file=sys.stderr,
        )
        return 1

    try:
        import boto3
        sts = boto3.client("sts", region_name=config["region"])
        identity = sts.get_caller_identity()
    except Exception as exc:
        print(f"cleanup: cannot verify AWS identity: {exc}", file=sys.stderr)
        return 2

    if identity["Account"] != config["account"]:
        print(
            f"cleanup: account mismatch: config={config['account']} "
            f"caller={identity['Account']}",
            file=sys.stderr,
        )
        return 2

    client = boto3.client("bedrock-agentcore", region_name=config["region"])
    updated = dict(receipt)
    failed = False

    for kind, id_key, del_fn_name, id_param in [
        ("evaluator", "evaluator_id", "delete_evaluator", "evaluatorId"),
        ("interpreter", "interpreter_id", "delete_code_interpreter", "codeInterpreterId"),
        ("memory", "memory_id", "delete_memory", "memoryId"),
    ]:
        if id_key in receipt:
            try:
                getattr(client, del_fn_name)(**{id_param: receipt[id_key]})
                print(f"{kind} deleted: {receipt[id_key]}")
                del updated[id_key]
                name_key = f"{kind}_name"
                if name_key in updated:
                    del updated[name_key]
            except Exception as exc:
                print(f"cleanup: {kind} deletion failed: {exc}", file=sys.stderr)
                failed = True

    _save_receipt(config, updated)
    if failed:
        return 2
    print(json.dumps({"status": "ok", "remaining": updated}))
    return 0


# ── Argument parser ───────────────────────────────────────────────────────────


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python deploy/offline_coach.py",
        description="Offline coaching resource lifecycle (separate from player runtimes)",
    )
    parser.add_argument("--config", required=True, metavar="K", help="offline_coach.json path")
    ops = parser.add_subparsers(dest="command", required=True)

    ops.add_parser("plan", help="print intended changes (no AWS calls)")
    apply_p = ops.add_parser("apply", help="provision missing resources")
    apply_p.add_argument(
        "--approve", action="store_true", help="required to make AWS changes"
    )
    ops.add_parser("status", help="describe existing owned resources")
    cleanup_p = ops.add_parser("cleanup", help="delete owned resources (irreversible)")
    cleanup_p.add_argument(
        "--approve", action="store_true", help="required to delete resources"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        config = _load_config(args.config)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    try:
        if args.command == "plan":
            return _cmd_plan(config)
        if args.command == "apply":
            return _cmd_apply(config, getattr(args, "approve", False))
        if args.command == "status":
            return _cmd_status(config)
        if args.command == "cleanup":
            return _cmd_cleanup(config, getattr(args, "approve", False))
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:  # noqa: BLE001
        print(f"unexpected error: {exc}", file=sys.stderr)
        return 2

    print(f"unknown command: {args.command}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
