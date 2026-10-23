"""python -m agent.advisor.offline <command> — offline coaching operator CLI.

Commands:
  prepare           Build an EvidencePacket locally (no AWS calls).
  doctor            Check config and report service accessibility (no paid calls).
  run               Run the full offline coaching workflow.
  generate-analysis Generate a Python analysis script (one model call, no execution).
  analyze           Execute a previously generated script in the sandbox.
  evaluate          Score an existing run artifact with the evaluator.
  publish           Publish a scouting note to shared Memory.

Exit codes:
  0  Completed proposal / service operation succeeded.
  2  Validation, service or filesystem failure.
  3  Evaluation completed; advice rejected (score below minimum threshold).
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ...profile import load_profile
from .. import write_atomic

# ── Injectable services (used by tests to avoid real AWS clients) ─────────────


@dataclass
class Services:
    """Fake-able ports injected in tests; omit for real clients from config."""

    scouts: Any = None
    advisor: Any = None
    evaluator: Any = None
    sandbox: Any = None


# ── Helpers ───────────────────────────────────────────────────────────────────


def _load_json(path_str: str) -> dict[str, Any]:
    try:
        return json.loads(Path(path_str).read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read {path_str}: {exc}") from exc


def _load_config(path_str: str) -> Any:
    from .contracts import OfflineConfig

    data = _load_json(path_str)
    return OfflineConfig.model_validate(data)


def _load_packet(path_str: str) -> Any:
    from .contracts import EvidencePacket

    data = _load_json(path_str)
    return EvidencePacket.model_validate(data)


def _load_note(path_str: str) -> Any:
    from .contracts import ScoutingNote

    data = _load_json(path_str)
    return ScoutingNote.model_validate(data)


def _real_scouts(config: Any) -> Any:
    import boto3

    from .memory import AgentCoreMemory

    client = boto3.client("bedrock-agentcore", region_name=config.region)
    return AgentCoreMemory(client, config)


def _real_advisor(config: Any) -> Any:
    import boto3

    from ..archetypes import ARCHETYPES
    from .bedrock import GroundedAdvisor

    client = boto3.client("bedrock-runtime", region_name=config.region)
    return GroundedAdvisor(client, config, ARCHETYPES)


def _real_evaluator(config: Any) -> Any:
    import boto3

    from .evaluation import AgentCoreEvaluator

    client = boto3.client("bedrock-agentcore", region_name=config.region)
    return AgentCoreEvaluator(client, config)


def _real_sandbox(config: Any) -> Any:
    import boto3

    from .interpreter import AgentCoreInterpreter

    client = boto3.client("bedrock-agentcore", region_name=config.region)
    return AgentCoreInterpreter(client, config)


# ── Sub-command implementations ───────────────────────────────────────────────


def _cmd_prepare(args: argparse.Namespace) -> int:
    """Build EvidencePacket locally from manifest + report + coach-claims + profile."""
    from datetime import datetime, timezone

    from .contracts import EvidenceClaim, EvidencePacket

    try:
        manifest = _load_json(args.manifest)
        report = _load_json(args.report)
        coach_claims_data = _load_json(args.coach_claims)
        profile = load_profile(args.profile)
        expires_at = datetime.fromisoformat(args.expires_at)
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)

        # Build claims from manifest and report
        claims: list[EvidenceClaim] = []
        for raw in coach_claims_data.get("claims", []):
            claims.append(EvidenceClaim.model_validate(raw))

        played_at_str = manifest.get("played_at") or report.get("played_at")
        if not played_at_str:
            raise ValueError("manifest or report must contain played_at")
        played_at = datetime.fromisoformat(played_at_str)
        if played_at.tzinfo is None:
            played_at = played_at.replace(tzinfo=timezone.utc)

        source_digests: dict[str, str] = {}
        for sid, digest in manifest.get("source_digests", {}).items():
            source_digests[sid] = digest
        for sid, digest in report.get("source_digests", {}).items():
            source_digests[sid] = digest

        packet = EvidencePacket(
            team_id=args.team,
            opponent_id=manifest.get("opponent_id", "unknown"),
            match_id=manifest.get("match_id", "unknown"),
            profile_version=profile.version,
            played_at=played_at,
            expires_at=expires_at,
            complete=bool(manifest.get("complete", True)),
            result=tuple(manifest["result"]) if "result" in manifest else None,
            claims=tuple(claims),
            source_digests=source_digests,
        )
        write_atomic(
            Path(args.output),
            json.dumps(packet.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
        )
    except (ValueError, OSError, TypeError) as exc:
        print(f"prepare error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"evidence": args.output, "digest": packet.digest}))
    return 0


def _cmd_doctor(args: argparse.Namespace) -> int:
    """Report config validity and declared service resource IDs (no paid calls)."""
    try:
        config = _load_config(args.config)
    except ValueError as exc:
        print(f"doctor error: {exc}", file=sys.stderr)
        return 2
    report = {
        "config_valid": True,
        "model_id": config.model_id,
        "memory_store_id": config.memory_store_id,
        "interpreter_id": config.interpreter_id,
        "evaluator_id": config.evaluator_id,
        "cache_mode": config.cache_mode,
        "note": (
            "doctor reports declared resource IDs only; "
            "invocation capability is untested unless you call each service."
        ),
    }
    print(json.dumps(report, indent=2))
    return 0


def _cmd_run(args: argparse.Namespace, services: Services) -> int:
    """Run the full offline coaching workflow."""
    from .contracts import OfflineServiceError
    from .workflow import run_offline

    try:
        config = _load_config(args.config)
        packet = _load_packet(args.evidence)
        profile = load_profile(args.profile)

        scouts = services.scouts if services.scouts is not None else _real_scouts(config)
        advisor = services.advisor if services.advisor is not None else _real_advisor(config)
        evaluator = (
            services.evaluator if services.evaluator is not None else _real_evaluator(config)
        )
        sandbox = services.sandbox  # analysis = None → no sandbox execution

        artifact = run_offline(
            packet,
            profile,
            config,
            scouts=scouts,
            advisor=advisor,
            evaluator=evaluator,
            sandbox=sandbox,
        )

        write_atomic(
            Path(args.output),
            json.dumps(artifact.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
        )
    except (ValueError, OSError, OfflineServiceError) as exc:
        print(f"run error: {exc}", file=sys.stderr)
        return 2

    status = artifact.status
    print(json.dumps({"status": status, "run": args.output}))
    if status == "rejected":
        return 3
    if status == "failed":
        return 2
    return 0


def _cmd_generate_analysis(args: argparse.Namespace) -> int:
    """Generate a Python analysis script via one bounded model call (no execution)."""
    # shortcut: generate-analysis not yet implemented; add AnalysisGenerator protocol when needed
    print(
        "generate-analysis: not yet implemented — use the SDK directly to generate a script",
        file=sys.stderr,
    )
    return 2


def _cmd_analyze(args: argparse.Namespace, services: Services) -> int:
    """Execute a previously generated script in the sandbox (matching digests required)."""
    from .contracts import AnalysisRequest, OfflineServiceError

    try:
        config = _load_config(args.config)
        evidence = _load_json(args.evidence)
        script_data = _load_json(args.script)

        if not args.execute_generated_code:
            print(
                "analyze: --execute-generated-code flag is required for sandbox execution",
                file=sys.stderr,
            )
            return 2

        # Verify evidence/script digest match
        from .contracts import EvidencePacket

        packet = EvidencePacket.model_validate(evidence)
        expected_digest = script_data.get("evidence_digest")
        if expected_digest and packet.digest != expected_digest:
            print(
                f"analyze: evidence digest mismatch (expected {expected_digest})",
                file=sys.stderr,
            )
            return 2

        request = AnalysisRequest(
            code=script_data["code"],
            data_json=json.dumps(evidence),
            timeout_seconds=config.timeout_seconds,
        )

        sandbox = services.sandbox if services.sandbox is not None else _real_sandbox(config)
        result = sandbox.execute(request)

        write_atomic(
            Path(args.output),
            json.dumps(result.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
        )
    except (ValueError, OSError, OfflineServiceError) as exc:
        print(f"analyze error: {exc}", file=sys.stderr)
        return 2

    print(json.dumps({"analysis": args.output}))
    return 0


def _cmd_evaluate(args: argparse.Namespace, services: Services) -> int:
    """Score an existing run artifact with the evaluator (recovery path)."""
    from .contracts import OfflineServiceError, RunArtifact
    from .workflow import _build_advisor_span

    try:
        config = _load_config(args.config)
        run_data = _load_json(args.run)
        run_artifact = RunArtifact.model_validate(run_data)

        if run_artifact.advice is None:
            print("evaluate: run artifact has no advice to evaluate", file=sys.stderr)
            return 2

        # Build synthetic span from the run artifact (recovery path, no re-inference)
        spans = _build_advisor_span(
            run_artifact.run_id,
            _synthetic_packet(run_artifact),
            run_artifact.advice,
        )

        evaluator = (
            services.evaluator if services.evaluator is not None else _real_evaluator(config)
        )
        evaluation = evaluator.evaluate(spans)

        updated = RunArtifact(
            run_id=run_artifact.run_id,
            source_digest=run_artifact.source_digest,
            config_digest=run_artifact.config_digest,
            stage_receipts=run_artifact.stage_receipts,
            advice=run_artifact.advice,
            candidate_profile=run_artifact.candidate_profile,
            evaluation=evaluation,
            status=run_artifact.status,
            failure=run_artifact.failure,
        )

        write_atomic(
            Path(args.output),
            json.dumps(updated.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
        )
    except (ValueError, OSError, OfflineServiceError) as exc:
        print(f"evaluate error: {exc}", file=sys.stderr)
        return 2

    status = updated.status
    print(json.dumps({"status": status, "evaluation": args.output}))
    if status == "rejected":
        return 3
    if status == "failed":
        return 2
    return 0


def _synthetic_packet(run_artifact: Any) -> Any:
    """Build a minimal EvidencePacket from run_artifact metadata for span construction."""
    from datetime import datetime, timezone

    from .contracts import EvidencePacket

    now = datetime.now(tz=timezone.utc)
    return EvidencePacket(
        team_id="unknown",
        opponent_id="unknown",
        match_id=run_artifact.run_id,
        profile_version="unknown",
        played_at=now,
        expires_at=now.replace(year=now.year + 1),
        complete=False,
        claims=(),
        source_digests={},
    )


def _cmd_publish(args: argparse.Namespace, services: Services) -> int:
    """Publish a scouting note to shared Memory."""
    from .contracts import OfflineServiceError

    try:
        config = _load_config(args.config)
        note = _load_note(args.note)
        packet = _load_packet(args.evidence)

        scouts = services.scouts if services.scouts is not None else _real_scouts(config)
        note_id = scouts.publish(note, packet)
    except (ValueError, OSError, OfflineServiceError) as exc:
        print(f"publish error: {exc}", file=sys.stderr)
        return 2

    print(json.dumps({"published": note_id}))
    return 0


# ── Argument parser ───────────────────────────────────────────────────────────


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m agent.advisor.offline",
        description="Offline coaching operator CLI (proposals only; deployment stays manual)",
    )
    ops = parser.add_subparsers(dest="command", required=True)

    # prepare
    prep = ops.add_parser("prepare", help="build an EvidencePacket locally (no AWS calls)")
    prep.add_argument("--manifest", required=True, metavar="M", help="match manifest JSON path")
    prep.add_argument("--report", required=True, metavar="R", help="match report JSON path")
    prep.add_argument(
        "--coach-claims", required=True, metavar="C", dest="coach_claims",
        help="coach claims JSON path",
    )
    prep.add_argument("--profile", required=True, metavar="P", help="player profile JSON path")
    prep.add_argument("--team", required=True, help="team identifier")
    prep.add_argument(
        "--expires-at", required=True, metavar="UTC", dest="expires_at",
        help="ISO-8601 UTC expiry datetime",
    )
    prep.add_argument("--output", required=True, metavar="E", help="output evidence JSON path")

    # doctor
    doc = ops.add_parser(
        "doctor", help="check config and report service IDs (no paid calls)"
    )
    doc.add_argument("--config", required=True, metavar="K", help="offline config JSON path")

    # run
    run_p = ops.add_parser("run", help="run the full offline coaching workflow")
    run_p.add_argument("--config", required=True, metavar="K", help="offline config JSON path")
    run_p.add_argument("--evidence", required=True, metavar="E", help="evidence packet JSON path")
    run_p.add_argument("--profile", required=True, metavar="P", help="player profile JSON path")
    run_p.add_argument(
        "--analysis", metavar="A", default=None, help="optional analysis result JSON path"
    )
    run_p.add_argument("--output", required=True, metavar="O", help="output run artifact path")

    # generate-analysis
    gen = ops.add_parser(
        "generate-analysis",
        help="generate a Python analysis script (one model call, no execution)",
    )
    gen.add_argument("--config", required=True, metavar="K")
    gen.add_argument("--evidence", required=True, metavar="E")
    gen.add_argument("--instruction", required=True, metavar="I")
    gen.add_argument("--output", required=True, metavar="S")

    # analyze
    ana = ops.add_parser("analyze", help="execute a script in the sandbox")
    ana.add_argument("--config", required=True, metavar="K")
    ana.add_argument("--evidence", required=True, metavar="E")
    ana.add_argument("--script", required=True, metavar="S")
    ana.add_argument(
        "--execute-generated-code", action="store_true", dest="execute_generated_code",
        help="required explicit flag to permit sandbox execution",
    )
    ana.add_argument("--output", required=True, metavar="A")

    # evaluate
    ev = ops.add_parser("evaluate", help="score an existing run artifact")
    ev.add_argument("--config", required=True, metavar="K")
    ev.add_argument("--run", required=True, metavar="O", help="existing run artifact path")
    ev.add_argument("--output", required=True, metavar="V")

    # publish
    pub = ops.add_parser("publish", help="publish a scouting note to shared Memory")
    pub.add_argument("--config", required=True, metavar="K")
    pub.add_argument("--note", required=True, metavar="N")
    pub.add_argument("--evidence", required=True, metavar="E")

    return parser


def main(argv: list[str] | None = None, *, services: Services | None = None) -> int:
    if services is None:
        services = Services()
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        cmd = args.command
        if cmd == "prepare":
            return _cmd_prepare(args)
        if cmd == "doctor":
            return _cmd_doctor(args)
        if cmd == "run":
            return _cmd_run(args, services)
        if cmd == "generate-analysis":
            return _cmd_generate_analysis(args)
        if cmd == "analyze":
            return _cmd_analyze(args, services)
        if cmd == "evaluate":
            return _cmd_evaluate(args, services)
        if cmd == "publish":
            return _cmd_publish(args, services)
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        print(f"unexpected error: {exc}", file=sys.stderr)
        return 2

    print(f"unknown command: {cmd}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
