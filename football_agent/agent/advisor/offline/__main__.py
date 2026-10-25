"""python -m agent.advisor.offline <command> — offline coaching operator CLI.

Commands:
  prepare           Build an EvidencePacket locally (no AWS calls).
  doctor            Check config and report service accessibility (no paid calls).
  run               Run the full offline coaching workflow.
  generate-analysis Generate a Python analysis script (one model call, no execution).
  analyze           Execute a previously generated script in the sandbox.
  evaluate          Score an existing run artifact with the evaluator.
  publish           Publish a scouting note to shared Memory.
  scout             Prepare a source-bound scouting packet/note from saved telemetry.
  prematch          Retrieve historical scouting and export review-only hypotheses.

Exit codes:
  0  Completed proposal / service operation succeeded.
  2  Validation, service or filesystem failure.
  3  Evaluation completed; advice rejected (score below minimum threshold).
"""

from __future__ import annotations

import argparse
import hashlib
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


def _aws_client(config: Any, service: str, *, retry_reads: bool = False) -> Any:
    import boto3
    from botocore.config import Config

    return boto3.client(
        service,
        region_name=config.region,
        config=Config(
            connect_timeout=min(10, config.timeout_seconds),
            read_timeout=config.timeout_seconds,
            retries=(
                {"mode": "standard", "total_max_attempts": 3}
                if retry_reads
                else {"max_attempts": 0}
            ),
        ),
    )


def _real_scouts(config: Any) -> Any:

    from .memory import AgentCoreMemory

    client = _aws_client(config, "bedrock-agentcore")
    return AgentCoreMemory(client, config)


def _real_advisor(config: Any) -> Any:
    from ...archetypes import ARCHETYPES
    from .bedrock import GroundedAdvisor

    client = _aws_client(config, "bedrock-runtime", retry_reads=True)
    return GroundedAdvisor(client, config, ARCHETYPES)


def _real_evaluator(config: Any) -> Any:
    from .evaluation import AgentCoreEvaluator

    client = _aws_client(config, "bedrock-agentcore", retry_reads=True)
    return AgentCoreEvaluator(client, config)


def _real_sandbox(config: Any) -> Any:
    from .interpreter import AgentCoreInterpreter

    client = _aws_client(config, "bedrock-agentcore")
    control = _aws_client(config, "bedrock-agentcore-control")
    return AgentCoreInterpreter(client, config, control_client=control)


# ── Sub-command implementations ───────────────────────────────────────────────


def _cmd_prepare(args: argparse.Namespace) -> int:
    """Build EvidencePacket locally from manifest + report + coach-claims + profile."""
    from datetime import datetime

    from .contracts import EvidenceClaim
    from .evidence import prepare_evidence

    try:
        manifest = _load_json(args.manifest)
        report = _load_json(args.report)
        coach_claims_data = _load_json(args.coach_claims)
        profile = load_profile(args.profile)
        expires_at = datetime.fromisoformat(args.expires_at)

        # Build claims from manifest and report
        claims: list[EvidenceClaim] = []
        for raw in coach_claims_data.get("claims", []):
            claims.append(EvidenceClaim.model_validate(raw))

        packet = prepare_evidence(
            manifest,
            report,
            tuple(claims),
            team_id=args.team,
            profile_version=profile.version,
            expires_at=expires_at,
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
    from .contracts import AnalysisResult, OfflineServiceError
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
        analysis_result = (
            AnalysisResult.model_validate(_load_json(args.analysis)) if args.analysis else None
        )

        artifact = run_offline(
            packet,
            profile,
            config,
            scouts=scouts,
            advisor=advisor,
            evaluator=evaluator,
            sandbox=sandbox,
            analysis_result=analysis_result,
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


def _cmd_count_analysis(args: argparse.Namespace) -> int:
    """Export the trusted deterministic counter locally, without model calls."""
    from .counts import build_count_script

    try:
        packet = _load_packet(args.evidence)
        artifact = build_count_script(packet)
        write_atomic(Path(args.output), json.dumps(artifact, indent=2, sort_keys=True) + "\n")
    except (ValueError, OSError) as exc:
        print(f"count-analysis error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"script": args.output, "executed": False, "model_called": False}))
    return 0


def _cmd_generate_analysis(args: argparse.Namespace) -> int:
    """Generate a Python analysis script via one bounded model call (no execution)."""
    from .contracts import OfflineServiceError

    try:
        config = _load_config(args.config)
        packet = _load_packet(args.evidence)
        advisor = _real_advisor(config)
        try:
            artifact = advisor.generate_analysis(packet, args.instruction)
        finally:
            if advisor.last_generation_diagnostics is not None:
                write_atomic(
                    Path(args.output + ".diagnostics.json"),
                    json.dumps(advisor.last_generation_diagnostics, indent=2, sort_keys=True)
                    + "\n",
                )
        write_atomic(Path(args.output), json.dumps(artifact, indent=2, sort_keys=True) + "\n")
    except (ValueError, OSError, OfflineServiceError) as exc:
        print(f"generate-analysis error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"script": args.output, "executed": False}))
    return 0


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
        if packet.digest != expected_digest:
            print(
                f"analyze: evidence digest mismatch (expected {expected_digest})",
                file=sys.stderr,
            )
            return 2

        request = AnalysisRequest(
            code=script_data["code"],
            data_json=json.dumps(packet.model_dump(mode="json"), sort_keys=True),
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

    try:
        config = _load_config(args.config)
        run_data = _load_json(args.run)
        run_artifact = RunArtifact.model_validate(run_data)

        if run_artifact.advice is None:
            print("evaluate: run artifact has no advice to evaluate", file=sys.stderr)
            return 2

        spans = run_artifact.evaluation_spans
        if not spans:
            raise ValueError(
                "run artifact has no preserved evaluation evidence; rerun with evidence"
            )

        evaluator = (
            services.evaluator if services.evaluator is not None else _real_evaluator(config)
        )
        evaluation = evaluator.evaluate(spans)
        candidate = run_artifact.proposed_profile or run_artifact.candidate_profile
        status = "proposal"
        failure = None
        if evaluation.status != "ok" or "faithfulness" not in evaluation.scores:
            status, failure = "failed", "evaluation has no complete faithfulness score"
        elif evaluation.scores["faithfulness"] < config.judge_min_score:
            status, failure = "rejected", "faithfulness below configured minimum"
        elif candidate is None:
            status, failure = "failed", "no preserved candidate profile; rerun advice with evidence"

        updated = RunArtifact.model_validate(
            {
                **run_artifact.model_dump(),
                "evaluation": evaluation,
                "config_digest": hashlib.sha256(config.model_dump_json().encode()).hexdigest(),
                "candidate_profile": candidate if status == "proposal" else None,
                "status": status,
                "failure": failure,
            }
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


def _cmd_scout(args: argparse.Namespace) -> int:
    from datetime import datetime

    from .scouting import prepare_scouting

    try:
        path = Path(args.records)
        with path.open("rb") as stream:
            raw = stream.read(16 * 1024 * 1024 + 1)
        if len(raw) > 16 * 1024 * 1024:
            raise ValueError("telemetry exceeds 16 MiB")
        text = raw.decode("utf-8")
        records = (
            json.loads(text)
            if text.lstrip().startswith("[")
            else [json.loads(line) for line in text.splitlines() if line.strip()]
        )
        packet, note = prepare_scouting(
            _load_json(args.manifest),
            records,
            team_id=args.team,
            profile=load_profile(args.profile),
            expires_at=datetime.fromisoformat(args.expires_at),
        )
        write_atomic(Path(args.output), packet.model_dump_json(indent=2) + "\n")
        write_atomic(Path(args.note_output), note.model_dump_json(indent=2) + "\n")
    except (ValueError, OSError, TypeError) as exc:
        print(f"scout error: {exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "evidence": args.output,
                "note": args.note_output,
                "digest": packet.digest,
                "published": False,
                "model_called": False,
            }
        )
    )
    return 0


def _cmd_prematch(args: argparse.Namespace, services: Services) -> int:
    from datetime import datetime

    from .contracts import OfflineServiceError
    from .scouting import build_brief

    try:
        config = _load_config(args.config)
        kickoff = datetime.fromisoformat(args.kickoff)
        profile = load_profile(args.profile)
        scouts = services.scouts if services.scouts is not None else _real_scouts(config)
        context = scouts.retrieve_before(args.team, args.opponent, kickoff)
        brief, evidence = build_brief(
            context,
            team_id=args.team,
            opponent_id=args.opponent,
            match_id=args.match_id,
            profile=profile,
            kickoff=kickoff,
        )
        write_atomic(Path(args.output), json.dumps(brief, indent=2, sort_keys=True) + "\n")
        write_atomic(Path(args.evidence_output), evidence.model_dump_json(indent=2) + "\n")
    except (ValueError, OSError, OfflineServiceError) as exc:
        print(f"prematch error: {exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "status": brief["status"],
                "brief": args.output,
                "evidence": args.evidence_output,
                "profile_changed": False,
                "model_called": False,
            }
        )
    )
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
        "--coach-claims",
        required=True,
        metavar="C",
        dest="coach_claims",
        help="coach claims JSON path",
    )
    prep.add_argument("--profile", required=True, metavar="P", help="player profile JSON path")
    prep.add_argument("--team", required=True, help="team identifier")
    prep.add_argument(
        "--expires-at",
        required=True,
        metavar="UTC",
        dest="expires_at",
        help="ISO-8601 UTC expiry datetime",
    )
    prep.add_argument("--output", required=True, metavar="E", help="output evidence JSON path")

    # doctor
    doc = ops.add_parser("doctor", help="check config and report service IDs (no paid calls)")
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

    counter = ops.add_parser(
        "count-analysis", help="prepare trusted shooting-count script locally (no model call)"
    )
    counter.add_argument("--evidence", required=True, metavar="E")
    counter.add_argument("--output", required=True, metavar="S")

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
        "--execute-generated-code",
        action="store_true",
        dest="execute_generated_code",
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

    scout = ops.add_parser("scout", help="prepare scouting from saved application telemetry")
    scout.add_argument("--manifest", required=True)
    scout.add_argument("--records", required=True, help="application JSON array or JSONL")
    scout.add_argument("--profile", required=True)
    scout.add_argument("--team", required=True)
    scout.add_argument("--expires-at", required=True)
    scout.add_argument("--output", required=True, help="evidence packet path")
    scout.add_argument("--note-output", required=True)

    prematch = ops.add_parser("prematch", help="read past opponent notes; never modify a profile")
    prematch.add_argument("--config", required=True)
    prematch.add_argument("--team", required=True)
    prematch.add_argument("--opponent", required=True)
    prematch.add_argument("--match-id", required=True)
    prematch.add_argument("--kickoff", required=True, help="timezone-aware cutoff (exclusive)")
    prematch.add_argument("--profile", required=True)
    prematch.add_argument("--output", required=True, help="operator brief path")
    prematch.add_argument("--evidence-output", required=True, help="offline advisor input path")

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
        if cmd == "count-analysis":
            return _cmd_count_analysis(args)
        if cmd == "analyze":
            return _cmd_analyze(args, services)
        if cmd == "evaluate":
            return _cmd_evaluate(args, services)
        if cmd == "publish":
            return _cmd_publish(args, services)
        if cmd == "scout":
            return _cmd_scout(args)
        if cmd == "prematch":
            return _cmd_prematch(args, services)
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        print(f"unexpected error: {exc}", file=sys.stderr)
        return 2

    print(f"unknown command: {cmd}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
