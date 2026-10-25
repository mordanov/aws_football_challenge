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

Cost controls: resource creation bounds memory retention and judge tokens.
This script does not invoke models or start interpreter sessions; configuration
limits are not a hard dollar budget.

AWS AUTHORIZATION: This script is ready to run but does NOT provision, change
IAM or delete unless --approve is passed AND the caller has verified
account/region match. No live calls happen in plan or without --approve in apply.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

DEPLOY = Path(__file__).resolve().parent
ROOT = DEPLOY.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# ── SDK shape constants (verified against published API) ─────────────────────

_NETWORK_MODE_SANDBOX = "SANDBOX"
_EVALUATOR_LEVEL_TRACE = "TRACE"
_POLL_INTERVAL = 5  # seconds between readiness polls
_POLL_DEADLINE = 120  # maximum seconds to wait for resource activation
_RESOURCES = {
    "memory": ("Memory", "memoryId", "memory"),
    "interpreter": ("CodeInterpreter", "codeInterpreterId", "code-interpreter-custom"),
    "evaluator": ("Evaluator", "evaluatorId", "evaluator"),
}


def _validate_request(operation: str, request: dict[str, Any]) -> dict[str, Any]:
    """Validate offline, including patterns/enums which botocore does not enforce."""
    import botocore.session
    from botocore.exceptions import ParamValidationError
    from botocore.validate import validate_parameters

    shape = botocore.session.get_session().get_service_model(
        "bedrock-agentcore-control"
    ).operation_model(operation).input_shape
    try:
        validate_parameters(request, shape)
    except ParamValidationError as exc:
        raise ValueError(f"{operation}: {exc}") from exc

    def check(value: Any, member: Any, path: str) -> None:
        if member.type_name == "structure":
            for key, item in value.items():
                check(item, member.members[key], f"{path}.{key}")
        elif member.type_name == "list":
            for item in value:
                check(item, member.member, path)
        elif member.type_name == "map":
            for key, item in value.items():
                check(key, member.key, f"{path}.key")
                check(item, member.value, path)
        elif member.type_name == "string":
            metadata = member.metadata
            if "pattern" in metadata and not re.fullmatch(metadata["pattern"], value):
                raise ValueError(f"{path} does not match SDK pattern {metadata['pattern']}")
            if "enum" in metadata and value not in metadata["enum"]:
                raise ValueError(f"{path} must be one of {metadata['enum']}")

    check(request, shape, operation)
    return request


def _control_client(config: dict[str, Any]) -> Any:
    import boto3
    from botocore.config import Config

    return boto3.client(
        "bedrock-agentcore-control",
        region_name=config["region"],
        config=Config(retries={"total_max_attempts": 1}),
    )


def _identity(config: dict[str, Any]) -> dict[str, str]:
    return {key: config[key] for key in ("account", "region", "team_id")}


def _validate_identity(config: dict[str, Any]) -> None:
    for key, pattern in [
        ("account", r"[0-9]{12}"),
        ("region", r"[a-z]{2}(?:-[a-z]+)+-[0-9]+"),
        ("team_id", r"[a-zA-Z0-9_]+"),
    ]:
        if not isinstance(config.get(key), str) or not re.fullmatch(pattern, config[key]):
            raise ValueError(f"invalid config {key}")


# ── Config loading ────────────────────────────────────────────────────────────


def _load_config(path: str) -> dict[str, Any]:
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read config {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("config must be a JSON object")
    required = {"account", "region", "team_id", "memory", "interpreter", "evaluator"}
    missing = required - set(data)
    if missing:
        raise ValueError(f"config missing keys: {sorted(missing)}")
    _validate_identity(data)
    # Store config directory so receipts live beside the config, not the source tree.
    data["_data_dir"] = str(Path(path).resolve().parent)
    return data


# ── Receipt file (git-ignored; records owned resource IDs atomically) ─────────


def _receipt_path(config: dict[str, Any]) -> Path:
    _validate_identity(config)
    base = Path(config["_data_dir"]) if "_data_dir" in config else ROOT
    return base / f".offline_coach_resources_{config['team_id']}.json"


def _load_receipt(config: dict[str, Any]) -> dict[str, Any]:
    p = _receipt_path(config)
    if not p.exists():
        return {}
    try:
        receipt = json.loads(p.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read receipt {p}: {exc}; refusing to provision/delete") from exc
    if not isinstance(receipt, dict) or receipt.get("_identity") != _identity(config):
        raise ValueError(f"corrupt or foreign receipt {p}: account/region/team identity mismatch")
    allowed = {"_identity"} | {f"{kind}_{key}" for kind in _RESOURCES for key in ("id", "name")}
    if set(receipt) - allowed:
        raise ValueError(f"corrupt receipt {p}: unexpected fields")
    for kind, (operation, parameter, _) in _RESOURCES.items():
        if f"{kind}_id" in receipt:
            _validate_request(f"Get{operation}", {parameter: receipt[f"{kind}_id"]})
    return receipt


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
            json.dump({**receipt, "_identity": _identity(config)}, f, indent=2, sort_keys=True)
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
    operations = {
        "memory": ["CreateEvent", "ListEvents"],
        "interpreter": [
            "InvokeCodeInterpreter", "GetCodeInterpreter",
            "StartCodeInterpreterSession", "StopCodeInterpreterSession",
        ],
        "evaluator": ["Evaluate"],
    }
    statements = []
    for kind, (_, _, arn_kind) in _RESOURCES.items():
        resource_id = receipt.get(f"{kind}_id", f"{config[kind]['name']}-*")
        statements.append({
            "Sid": f"OfflineCoach{kind.title()}Invoke",
            "Effect": "Allow",
            "Action": [f"bedrock-agentcore:{action}" for action in operations[kind]],
            "Resource": [f"arn:aws:bedrock-agentcore:{region}:{account}:{arn_kind}/{resource_id}"],
            "Condition": {"StringEquals": {"aws:RequestedRegion": region}},
        })
    model_arns = config["evaluator"].get("model_resource_arns", [])
    if model_arns:
        statements.append({
            "Sid": "JudgeModelInvoke",
            "Effect": "Allow",
            "Action": ["bedrock:InvokeModel"],
            "Resource": model_arns,
        })
    return {"Version": "2012-10-17", "Statement": statements}


def _provisioning_policy(config: dict[str, Any]) -> dict[str, Any]:
    """Least-privilege policy for the one-time provisioner (no attachment to player roles)."""
    region = config["region"]
    account = config["account"]

    statements = [{
        "Sid": "OfflineCoachCreate",
        "Effect": "Allow",
        "Action": [f"bedrock-agentcore:Create{operation}"
                   for operation, _, _ in _RESOURCES.values()],
        # Create operations have no resource ID yet and require Resource "*".
        "Resource": "*",
        "Condition": {"StringEquals": {"aws:RequestedRegion": region}},
    }]
    for kind, (operation, _, arn_kind) in _RESOURCES.items():
        statements.append({
            "Sid": f"OfflineCoach{kind.title()}Manage",
            "Effect": "Allow",
            "Action": [
                f"bedrock-agentcore:Get{operation}", f"bedrock-agentcore:Delete{operation}",
                "bedrock-agentcore:TagResource",
            ],
            "Resource": [
                f"arn:aws:bedrock-agentcore:{region}:{account}:{arn_kind}/{config[kind]['name']}-*"
            ],
            "Condition": {"StringEquals": {"aws:RequestedRegion": region}},
        })
    return {"Version": "2012-10-17", "Statement": statements}


# ── Resource request builders ─────────────────────────────────────────────────


def _memory_request(config: dict[str, Any]) -> dict[str, Any]:
    mem = config["memory"]
    if mem["strategy"].get("type") != "SEMANTIC":
        raise ValueError("memory.strategy.type must be SEMANTIC")
    if "{actorId}" not in mem["strategy"]["namespace"]:
        raise ValueError("memory.strategy.namespace must scope records with {actorId}")
    return _validate_request("CreateMemory", {
        "name": mem["name"],
        "description": f"Offline coach scouting memory for team {config['team_id']}",
        "eventExpiryDuration": mem["event_expiry_days"],
        "memoryStrategies": [
            {
                "semanticMemoryStrategy": {
                    "name": mem["strategy"]["name"],
                    "namespaces": [mem["strategy"]["namespace"]],
                }
            }
        ],
        "tags": config.get("tags", {}),
    })


def _interpreter_request(config: dict[str, Any]) -> dict[str, Any]:
    interp = config["interpreter"]
    forbidden = {
        "executionRoleArn", "execution_role_arn", "execution_role", "mounts",
        "filesystemConfigurations", "filesystem_configurations", "certificates",
        "networkConfiguration", "vpcConfig",
    }
    if interp.get("network_mode") != _NETWORK_MODE_SANDBOX or forbidden & set(interp):
        raise ValueError("interpreter requires SANDBOX, no execution role, mounts, or certificates")
    return _validate_request("CreateCodeInterpreter", {
        "name": interp["name"],
        "description": f"Offline coach sandbox interpreter for team {config['team_id']}",
        "networkConfiguration": {"networkMode": _NETWORK_MODE_SANDBOX},
        "tags": config.get("tags", {}),
        # No executionRoleArn: SANDBOX mode does not require a role
        # No filesystem mounts
    })


def _evaluator_request(config: dict[str, Any]) -> dict[str, Any]:
    ev = config["evaluator"]
    from agent.advisor.offline.evaluation import JUDGE_INSTRUCTIONS, RUBRIC_VERSION

    if any("role" in key.lower() for key in ev):
        raise ValueError("installed CreateEvaluator API has no execution role parameter")
    if not isinstance(ev.get("model_id"), str) or not ev["model_id"].strip():
        raise ValueError("evaluator.model_id is required: choose an enabled supported Bedrock model")
    arns = ev.get("model_resource_arns", [])
    if not isinstance(arns, list) or any(
        not isinstance(arn, str) or not re.fullmatch(
            r"arn:aws:bedrock:[a-z0-9-]+:(?:[0-9]{12})?:"
            r"(?:foundation-model|inference-profile|application-inference-profile)/[^*?\s]+", arn
        )
        for arn in arns
    ):
        raise ValueError(
            "evaluator.model_resource_arns must contain only exact Bedrock invoke ARNs"
        )
    if ev.get("level", _EVALUATOR_LEVEL_TRACE) != _EVALUATOR_LEVEL_TRACE:
        raise ValueError("the offline judge rubric requires evaluator.level TRACE")
    return _validate_request("CreateEvaluator", {
        "evaluatorName": ev["name"],
        "level": ev.get("level", _EVALUATOR_LEVEL_TRACE),
        "description": f"Offline coach faithfulness evaluator (rubric {RUBRIC_VERSION}) for team {config['team_id']}",
        "evaluatorConfig": {
            "llmAsAJudge": {
                "modelConfig": {
                    "bedrockEvaluatorModelConfig": {
                        "modelId": ev["model_id"],
                        "inferenceConfig": {
                            "maxTokens": ev.get("max_tokens", 512),
                        },
                    }
                },
                "instructions": JUDGE_INSTRUCTIONS,
                "ratingScale": {
                    "numerical": [
                        {"value": entry["value"], "definition": entry["definition"],
                         "label": entry["label"]}
                        for entry in ev.get("rating_scale", [])
                    ]
                },
            }
        },
        "tags": config.get("tags", {}),
    })


# ── Readiness polling ─────────────────────────────────────────────────────────


def _poll_ready(client: Any, resource_type: str, resource_id: str) -> str:
    """Poll until resource reaches an active/available state or deadline."""
    operation, id_key, _ = _RESOURCES[resource_type]
    method = {"memory": "get_memory", "interpreter": "get_code_interpreter",
              "evaluator": "get_evaluator"}[resource_type]
    request = _validate_request(f"Get{operation}", {id_key: resource_id})
    deadline = time.monotonic() + _POLL_DEADLINE
    while True:
        resp = getattr(client, method)(**request)
        data = resp.get("memory", {}) if resource_type == "memory" else resp
        status = data.get("status", "UNKNOWN")
        if resource_type == "memory":
            strategy_statuses = [strategy.get("status") for strategy in data.get("strategies", [])]
            if any(state not in ("ACTIVE", "CREATING") for state in strategy_statuses):
                raise RuntimeError(f"memory {resource_id} strategy status: {strategy_statuses}")
            if status == "ACTIVE" and "CREATING" in strategy_statuses:
                status = "CREATING"
        if status in ("ACTIVE", "AVAILABLE", "READY"):
            return status
        if status not in ("CREATING", "UPDATING"):
            raise RuntimeError(
                f"{resource_type} {resource_id} reached status {status}: "
                f"{data.get('failureReason', '')}"
            )
        if time.monotonic() > deadline:
            raise TimeoutError(
                f"{resource_type} {resource_id} did not become active within {_POLL_DEADLINE}s"
            )
        time.sleep(_POLL_INTERVAL)


# ── Commands ──────────────────────────────────────────────────────────────────


def _cmd_plan(config: dict[str, Any]) -> int:
    """Print intended resource/IAM changes without any AWS calls."""
    receipt = _load_receipt(config)
    requests = _create_requests(config)
    plan: dict[str, Any] = {
        "account": config["account"],
        "region": config["region"],
        "team_id": config["team_id"],
        "resources": {},
        "policies": {},
    }

    for kind in _RESOURCES:
        id_key = f"{kind}_id"
        if id_key in receipt:
            plan["resources"][kind] = {"action": "existing", "id": receipt[id_key]}
        else:
            plan["resources"][kind] = {"action": "create", "request": requests[kind]}

    plan["policies"]["invocation"] = _invocation_policy(config, receipt)
    plan["policies"]["provisioning"] = _provisioning_policy(config)
    plan["iam_note"] = (
        "Policies are printed only, never attached. Create APIs require region-constrained "
        "creation permissions; tag-on-create requires bedrock-agentcore:TagResource. "
        "Get/Delete/Tag permissions use owned resource name prefixes. STS GetCallerIdentity "
        "verifies the account. The installed CreateEvaluator and Evaluate SDK models do not "
        "accept an execution role; no IAM role is created, passed, or modified. "
        "Judge InvokeModel permissions are omitted unless evaluator.model_resource_arns "
        "explicitly supplies the profile and all routed foundation-model ARNs. "
        "SDK validation does not verify account entitlements or regional service availability."
    )
    plan["note"] = (
        "No AWS calls made. Run 'apply --approve' to provision missing resources. "
        "Player runtimes r4_gk/r4_def/r4_mid/r4_fwd1/r4_fwd2 are NOT touched."
    )
    print(json.dumps(plan, indent=2))
    return 0


def _create_requests(config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    _validate_identity(config)
    return {
        "memory": _memory_request(config),
        "interpreter": _interpreter_request(config),
        "evaluator": _evaluator_request(config),
    }


def _cmd_apply(config: dict[str, Any], approve: bool) -> int:
    """Provision missing resources. Requires --approve and account/region match."""
    if not approve:
        print(
            "apply: pass --approve to confirm resource provisioning "
            "(plan shows intended changes)",
            file=sys.stderr,
        )
        return 1

    # Validate every request and receipt locally before identity lookup or mutation.
    try:
        receipt = _load_receipt(config)
        requests = _create_requests(config)
    except Exception as exc:
        print(f"apply: preflight failed: {exc}", file=sys.stderr)
        return 2

    try:
        import boto3
        from botocore.config import Config

        sts = boto3.client(
            "sts", region_name=config["region"],
            config=Config(retries={"total_max_attempts": 1}),
        )
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

    try:
        client = _control_client(config)
        updated = dict(receipt)
        # Ensure a writable durable receipt before creating any resource.
        _save_receipt(config, updated)
        for kind, method, response_key in [
            ("memory", "create_memory", "id"),
            ("interpreter", "create_code_interpreter", "codeInterpreterId"),
            ("evaluator", "create_evaluator", "evaluatorId"),
        ]:
            id_key = f"{kind}_id"
            if id_key not in updated:
                resp = getattr(client, method)(**requests[kind])
                data = resp["memory"] if kind == "memory" else resp
                resource_id = data[response_key]
                updated[id_key] = resource_id
                updated[f"{kind}_name"] = config[kind]["name"]
                # Never poll or attempt another create before the returned ID is durable.
                try:
                    _save_receipt(config, updated)
                except Exception as exc:
                    raise RuntimeError(
                        f"cannot persist {kind} ID {resource_id}; record it for manual recovery: {exc}"
                    ) from exc
                print(f"{kind} returned ID: {resource_id}", flush=True)
            _poll_ready(client, kind, updated[id_key])
            print(f"{kind} ready: {updated[id_key]}")
    except Exception as exc:
        print(f"apply: provisioning stopped: {exc}", file=sys.stderr)
        return 2

    print(json.dumps({"status": "ok", "receipt": str(_receipt_path(config))}))
    return 0


def _cmd_status(config: dict[str, Any]) -> int:
    """Describe existing owned resources (read-only)."""
    try:
        receipt = _load_receipt(config)
    except ValueError as exc:
        print(f"status: {exc}", file=sys.stderr)
        return 2
    if not any(f"{kind}_id" in receipt for kind in _RESOURCES):
        print(json.dumps({"status": "no resources recorded", "receipt_path": str(_receipt_path(config))}))
        return 0

    try:
        client = _control_client(config)
    except Exception as exc:
        print(f"status: cannot create AWS client: {exc}", file=sys.stderr)
        return 2

    report: dict[str, Any] = {"resources": {}}
    failed = False

    for kind, id_key, get_fn_name, id_param in [
        ("memory", "memory_id", "get_memory", "memoryId"),
        ("interpreter", "interpreter_id", "get_code_interpreter", "codeInterpreterId"),
        ("evaluator", "evaluator_id", "get_evaluator", "evaluatorId"),
    ]:
        if id_key in receipt:
            try:
                operation = _RESOURCES[kind][0]
                request = _validate_request(f"Get{operation}", {id_param: receipt[id_key]})
                resp = getattr(client, get_fn_name)(**request)
                data = resp.get("memory", {}) if kind == "memory" else resp
                status = data.get("status", "UNKNOWN")
                if status not in ("ACTIVE", "READY", "CREATING", "UPDATING"):
                    failed = True
                if kind == "memory" and any(
                    strategy.get("status") not in ("ACTIVE", "CREATING")
                    for strategy in data.get("strategies", [])
                ):
                    failed = True
                report["resources"][kind] = {
                    "id": receipt[id_key],
                    "status": status,
                }
            except Exception as exc:
                failed = True
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
    return 2 if failed else 0


def _cmd_cleanup(config: dict[str, Any], approve: bool) -> int:
    """Delete owned resources (recorded IDs only). Requires --approve."""
    try:
        receipt = _load_receipt(config)
    except ValueError as exc:
        print(f"cleanup: {exc}", file=sys.stderr)
        return 2
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
        from botocore.config import Config

        sts = boto3.client(
            "sts", region_name=config["region"],
            config=Config(retries={"total_max_attempts": 1}),
        )
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

    client = _control_client(config)
    updated = dict(receipt)
    failed = False

    for kind, id_key, del_fn_name, id_param in [
        ("evaluator", "evaluator_id", "delete_evaluator", "evaluatorId"),
        ("interpreter", "interpreter_id", "delete_code_interpreter", "codeInterpreterId"),
        ("memory", "memory_id", "delete_memory", "memoryId"),
    ]:
        if id_key in receipt:
            try:
                operation = _RESOURCES[kind][0]
                request = _validate_request(f"Delete{operation}", {id_param: receipt[id_key]})
                getattr(client, del_fn_name)(**request)
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
