"""Build a deterministic EvidencePacket from a match manifest and coach report.

No AWS clients, boto3 or GenAI instrumentation are imported here.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from .contracts import EvidenceClaim, EvidencePacket, JsonValue, _canonical_digest

# ── Constants ─────────────────────────────────────────────────────────────────

_MAX_INPUT_BYTES = 128 * 1024  # 128 KB — combined manifest + report byte cap
_MAX_CLAIM_TEXT = 4000  # matches EvidenceClaim.text max_length

# Fields included in the canonical digest for each source.
# Anything outside the allowlist is excluded — keeps arbitrary operator-added
# keys and raw framework payloads out of the evidence record.
_MANIFEST_ALLOWLIST = frozenset(
    {
        "complete",
        "decisions",
        "errors",
        "limitations",
        "match_id",
        "observations",
        "opponent_id",
        "outcomes",
        "played_at",
        "result",
    }
)
_REPORT_ALLOWLIST = frozenset(
    {
        "coach_notes",
        "complete",
        "match_id",
        "opponent_coach_advice",
        "opponent_id",
        "outcomes",
        "own_coach_advice",
        "result",
        "score",
        "source_id",
    }
)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _require_str(d: dict[str, Any], key: str, label: str = "manifest") -> str:
    value = d.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} {key!r} must be a nonempty string")
    return value


# ── Public API ────────────────────────────────────────────────────────────────


def prepare_evidence(
    manifest: dict[str, JsonValue],
    report: dict[str, JsonValue],
    coach_claims: tuple[EvidenceClaim, ...],
    *,
    team_id: str,
    expires_at: datetime,
    profile_version: str = "unknown",
) -> EvidencePacket:
    """Assemble an EvidencePacket from a match manifest and coach report.

    Claim kinds preserve provenance:
    - Manifest outcome counts → ``observed`` (telemetry; execution not confirmed)
    - Report outcome counts  → ``report_claim`` (coach interpretation; may differ)
    - Advice excerpts        → ``report_claim`` / ``inferred`` per convention
    - ``coach_claims``       → appended unchanged (operator-supplied labels)

    A SHOOT contradiction (manifest=3, report=0) yields two separately-labelled
    claims; they are never merged. Claim IDs are deterministic per source+field.
    """
    if not isinstance(manifest, dict):
        raise ValueError("manifest must be a dict")
    if not isinstance(report, dict):
        raise ValueError("report must be a dict")

    # ── Byte cap — checked before any AWS or model call ───────────────────────
    total_bytes = len(json.dumps(manifest, allow_nan=False).encode()) + len(
        json.dumps(report, allow_nan=False).encode()
    )
    if total_bytes > _MAX_INPUT_BYTES:
        raise ValueError(
            f"combined manifest+report exceeds {_MAX_INPUT_BYTES}-byte cap "
            f"({total_bytes} bytes)"
        )

    # ── Manifest validation ───────────────────────────────────────────────────
    match_id = _require_str(manifest, "match_id")
    opponent_id = _require_str(manifest, "opponent_id")

    played_at_raw = _require_str(manifest, "played_at")
    try:
        played_at = datetime.fromisoformat(played_at_raw)
    except (ValueError, TypeError) as exc:
        raise ValueError(
            f"manifest played_at is not a valid ISO timestamp: {played_at_raw!r}"
        ) from exc
    if played_at.tzinfo is None:
        raise ValueError("manifest played_at must be timezone-aware")

    complete = manifest.get("complete")
    if not isinstance(complete, bool):
        raise ValueError("manifest 'complete' must be a boolean")

    result_raw = manifest.get("result")
    if complete:
        if result_raw is None:
            raise ValueError("complete manifest must provide a result")
        if not (isinstance(result_raw, (list, tuple)) and len(result_raw) == 2):
            raise ValueError("manifest result must be a two-element list")
        packet_result: tuple[int, int] | None = (int(result_raw[0]), int(result_raw[1]))
    else:
        if result_raw is not None:
            raise ValueError("partial (incomplete) match must not declare a result")
        packet_result = None

    if expires_at.tzinfo is None:
        raise ValueError("expires_at must be timezone-aware")

    # ── Report identity cross-check ───────────────────────────────────────────
    if "match_id" in report and report["match_id"] != match_id:
        raise ValueError(
            f"report match_id {report['match_id']!r} != manifest match_id {match_id!r}"
        )
    if "opponent_id" in report and report["opponent_id"] != opponent_id:
        raise ValueError(
            f"report opponent_id {report['opponent_id']!r} != manifest opponent_id {opponent_id!r}"
        )

    # ── Source IDs and digests ────────────────────────────────────────────────
    manifest_src_id = f"manifest-{match_id}"
    # Prefer an explicit source_id embedded in the report for traceability.
    report_src_raw = report.get("source_id")
    report_src_id = (
        report_src_raw
        if isinstance(report_src_raw, str) and report_src_raw
        else f"report-{match_id}"
    )
    # Guarantee no collision: if the report source_id happens to match the
    # manifest source_id (injection attempt), prefix the report copy.
    if report_src_id == manifest_src_id:
        report_src_id = f"report-{report_src_id}"

    manifest_content = {k: manifest[k] for k in sorted(manifest) if k in _MANIFEST_ALLOWLIST}
    report_content = {k: report[k] for k in sorted(report) if k in _REPORT_ALLOWLIST}
    source_digests = {
        manifest_src_id: _canonical_digest(manifest_content),
        report_src_id: _canonical_digest(report_content),
    }

    # ── Claims ────────────────────────────────────────────────────────────────
    claims: list[EvidenceClaim] = []

    # Manifest outcome counts → observed (telemetry; not confirmed execution)
    m_outcomes = manifest.get("outcomes")
    if isinstance(m_outcomes, dict):
        for cmd in sorted(m_outcomes):
            count = m_outcomes[cmd]
            if isinstance(cmd, str) and isinstance(count, (int, float)):
                claims.append(
                    EvidenceClaim(
                        id=f"manifest-outcome-{cmd.lower()}",
                        text=(
                            f"{cmd} command issued {int(count)} times "
                            "(manifest decision count; not confirmed execution)"
                        ),
                        kind="observed",
                        source_ids=(manifest_src_id,),
                    )
                )

    # Report outcome counts → report_claim (coach interpretation; may differ)
    r_outcomes = report.get("outcomes")
    if isinstance(r_outcomes, dict):
        for cmd in sorted(r_outcomes):
            count = r_outcomes[cmd]
            if isinstance(cmd, str) and isinstance(count, (int, float)):
                claims.append(
                    EvidenceClaim(
                        id=f"report-outcome-{cmd.lower()}",
                        text=(
                            f"Report records {cmd}={int(count)} "
                            "(coach report interpretation; may differ from telemetry)"
                        ),
                        kind="report_claim",
                        source_ids=(report_src_id,),
                    )
                )

    # Match result → observed (confirms end state; not causal attribution)
    if complete and packet_result is not None:
        claims.append(
            EvidenceClaim(
                id="manifest-result",
                text=(
                    f"Match result: {list(packet_result)} "
                    "(complete match; confirms end state, not causal attribution)"
                ),
                kind="observed",
                source_ids=(manifest_src_id,),
            )
        )

    # Report text fields — lengths bounded to EvidenceClaim.text max
    _text_claims = [
        ("coach_notes", "report-coach-notes", "report_claim"),
        ("own_coach_advice", "report-own-coach-advice", "report_claim"),
        ("opponent_coach_advice", "report-opponent-coach-advice", "inferred"),
    ]
    for field, claim_id, kind in _text_claims:
        value = report.get(field)
        if isinstance(value, str) and value.strip():
            claims.append(
                EvidenceClaim(
                    id=claim_id,
                    text=value[:_MAX_CLAIM_TEXT],
                    kind=kind,  # type: ignore[arg-type]
                    source_ids=(report_src_id,),
                )
            )

    # Operator coach claims appended unchanged (pre-labelled by caller)
    claims.extend(coach_claims)

    return EvidencePacket(
        team_id=team_id,
        opponent_id=opponent_id,
        match_id=match_id,
        profile_version=profile_version,
        played_at=played_at,
        expires_at=expires_at,
        complete=complete,
        result=packet_result,
        claims=tuple(claims),
        source_digests=source_digests,
    )
