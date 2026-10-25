"""Trusted count parsing shared by local advice preparation and sandbox scripts."""

from __future__ import annotations

import inspect
from typing import Any

from .contracts import EvidencePacket

ANALYSIS_WRAPPER = (
    "\n\nimport json as _afc_json\n"
    'with open("evidence.json", encoding="utf-8") as _afc_file:\n'
    "    _afc_evidence = _afc_json.load(_afc_file)\n"
    "_afc_result = analyze(_afc_evidence)\n"
    "if not isinstance(_afc_result, dict):\n"
    '    raise ValueError("Analysis must return a JSON object")\n'
    "print(_afc_json.dumps(_afc_result, allow_nan=False))\n"
)


def analyze_shooting_counts(evidence: dict[str, Any]) -> dict[str, Any]:
    """Parse canonical command-count claims, not free-form tactical narratives."""
    import re

    claims = evidence.get("claims")
    if not isinstance(claims, list):
        raise ValueError("INVALID_COUNT_EVIDENCE")
    result: dict[str, Any] = {}
    for kind, prefix, pattern in (
        ("observed", "SHOOT command issued", r"SHOOT command issued ([0-9]{1,10}) times"),
        ("report_claim", "Report records SHOOT", r"Report records SHOOT=([0-9]{1,10})"),
    ):
        found = []
        for claim in claims:
            if not isinstance(claim, dict) or not isinstance(claim.get("text"), str):
                raise ValueError("INVALID_COUNT_EVIDENCE")
            text = claim["text"]
            if claim.get("kind") != kind or not text.startswith(prefix):
                continue
            match = re.fullmatch(pattern + r"(?:; [^\r\n]*| \([^\r\n]*\))?", text)
            if match is None or int(match.group(1)) > 1_000_000_000:
                raise ValueError("MALFORMED_SHOOT_COUNT")
            sources = claim.get("source_ids")
            if (
                not isinstance(claim.get("id"), str)
                or not claim["id"]
                or not isinstance(sources, list)
                or not sources
                or any(not isinstance(source, str) or not source for source in sources)
            ):
                raise ValueError("INVALID_COUNT_PROVENANCE")
            found.append(
                {
                    "status": "found",
                    "kind": kind,
                    "count": int(match.group(1)),
                    "claim_id": claim["id"],
                    "source_ids": list(sources),
                }
            )
        if len(found) > 1:
            raise ValueError("AMBIGUOUS_SHOOT_COUNT")
        result[kind] = (
            found[0]
            if found
            else {
                "status": "missing",
                "kind": kind,
                "count": None,
                "claim_id": None,
                "source_ids": [],
            }
        )
    observed = result["observed"]["count"]
    reported = result["report_claim"]["count"]
    result["observed_minus_report"] = (
        observed - reported if observed is not None and reported is not None else None
    )
    result["limitation"] = (
        "Issued commands and report claims, not confirmed actions, goals or causality."
    )
    return result


def build_count_script(
    packet: EvidencePacket,
    *,
    max_output_bytes: int = 16_384,
) -> dict[str, Any]:
    """Export the same reviewed source, not a second parser or a canned result."""
    counts = analyze_shooting_counts(packet.model_dump(mode="json"))
    code = (
        "from typing import Any\n\n"
        + inspect.getsource(analyze_shooting_counts)
        + "\nanalyze = analyze_shooting_counts\n"
        + ANALYSIS_WRAPPER
    )
    if len(code.encode()) > max_output_bytes:
        raise ValueError("OUTPUT_LIMIT")
    return {
        "code": code,
        "evidence_digest": packet.digest,
        "origin": "deterministic_shooting_counts_v1",
        "usage": None,
        "counts": counts,
    }
