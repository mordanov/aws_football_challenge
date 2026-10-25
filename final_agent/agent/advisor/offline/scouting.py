"""Deterministic scouting from normalized telemetry; no AWS or model calls."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ...model import Profile, Snapshot
from ...protocol import restore_snapshot, snapshot_dict
from ...trainer import checked_manifest, nonempty, timestamp
from .contracts import (
    EvidenceClaim,
    EvidencePacket,
    ScoutingContext,
    ScoutingNote,
    _canonical_digest,
)

_MAX_SAMPLES = 256
_Metric = Literal["keeper_advanced", "all_players_attacking", "opponent_possession_dominance"]
_HYPOTHESES: dict[_Metric, str] = {
    "keeper_advanced": (
        "Test quicker safe counterattacks when the opponent keeper is advanced; "
        "retain live shot range, angle and lane guards."
    ),
    "all_players_attacking": (
        "Test a safe outlet after regaining possession against an all-player attack; "
        "retain defensive cover and compare against the unchanged profile."
    ),
    "opponent_possession_dominance": (
        "Test a deeper defensive shape against opponents who control possession; "
        "compare park_the_bus and balanced archetypes in possession-dominated matches."
    ),
}
_LIMITATIONS = (
    "Sparse attempted-decision snapshots are not execution acknowledgements or time-weighted rates.",
    "Repeated game clocks count once; sampled positions do not establish tactical intent.",
    "Opponent shots, shot accuracy and causal match benefits are not inferred.",
    "Identity is operator-bound; digests are integrity checks, not source authentication.",
    "Hypotheses require offline evaluation and explicit approval; no profile is changed.",
)


class ScoutingObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal["cloudwatch_scout_v1"] = "cloudwatch_scout_v1"
    metric: _Metric
    samples: int = Field(ge=1, le=_MAX_SAMPLES)
    positive: int = Field(ge=0, le=_MAX_SAMPLES)
    sampled_ticks: list[int]
    positive_ticks: list[int]

    @model_validator(mode="after")
    def _counts_match_ticks(self) -> Self:
        if (
            len(self.sampled_ticks) != self.samples
            or len(set(self.sampled_ticks)) != self.samples
            or len(self.positive_ticks) != self.positive
            or len(set(self.positive_ticks)) != self.positive
            or not set(self.positive_ticks) <= set(self.sampled_ticks)
            or any(t < 0 for t in self.sampled_ticks)
        ):
            raise ValueError("scouting counts must agree with distinct supporting ticks")
        return self


def prepare_scouting(
    manifest: dict[str, Any],
    records: list[dict[str, Any]],
    *,
    team_id: str,
    profile: Profile,
    expires_at: datetime,
) -> tuple[EvidencePacket, ScoutingNote]:
    """Bind one complete match to an explicitly confirmed opponent.

    Records must already be standalone application JSON, not CloudWatch wrappers.
    Missing opponent IDs may be bound by the manifest; conflicting IDs fail closed.
    """
    checked = checked_manifest(manifest)
    nonempty(team_id, "team")
    if not checked["complete"]:
        raise ValueError("scouting publication requires a complete match manifest")
    now = datetime.now(timezone.utc)
    if timestamp(checked["played_at"]) > now:
        raise ValueError("future matches cannot supply scouting evidence")
    if expires_at.tzinfo is None:
        raise ValueError("scouting expiry requires a timezone")
    if expires_at <= now:
        raise ValueError("scouting evidence is already expired")
    if not isinstance(records, list) or len(records) > 20_000:
        raise ValueError("telemetry must be an array of at most 20000 records")
    snapshots: dict[int, Snapshot] = {}
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("telemetry record must be an object")
        if record.get("type") not in ("decision", "error"):
            raise ValueError("expected standalone decision/error telemetry records")
        if record.get("match_id") != checked["match_id"]:
            raise ValueError("telemetry match identity mismatch")
        if record.get("opponent_id") not in (None, checked["opponent_id"]):
            raise ValueError("telemetry opponent identity mismatch")
        if record.get("team_id") != checked["team_id"]:
            raise ValueError("telemetry team identity mismatch")
        if record.get("profile_version") != profile.version:
            raise ValueError("telemetry profile identity mismatch")
        if record["type"] == "error":
            continue
        if record.get("input_format") not in ("compact_match", "sample"):
            raise ValueError("unrecognized telemetry coordinate contract")
        s = restore_snapshot(record.get("snapshot"))
        if s.team != checked["team_id"]:
            raise ValueError("snapshot team identity mismatch")
        if any(abs(p.x) > 55 or abs(p.y) > 35 for p in s.players):
            raise ValueError("snapshot is outside canonical pitch bounds")
        own_score = s.score if s.team == 0 else s.score[::-1]
        if any(a > b for a, b in zip(own_score, checked["result"], strict=True)):
            raise ValueError("snapshot score exceeds manifest final result")
        if s.tick in snapshots and snapshots[s.tick] != s:
            raise ValueError("conflicting snapshots at one tick")
        snapshots[s.tick] = s
    if not snapshots:
        raise ValueError("scouting needs decision snapshots")
    observations: list[Snapshot] = []
    seen_clocks: set[float] = set()
    previous = -1.0
    for s in sorted(snapshots.values(), key=lambda s: s.tick):
        if s.clock < previous:
            raise ValueError("game clock decreased across ticks")
        previous = s.clock
        if s.clock not in seen_clocks:
            observations.append(s)
            seen_clocks.add(s.clock)
    if len(observations) > _MAX_SAMPLES:
        raise ValueError(f"scouting exceeds {_MAX_SAMPLES} distinct game clocks")
    sources = {"manifest": _canonical_digest(checked)}
    sources.update(
        {f"snapshot-{s.tick}": _canonical_digest(snapshot_dict(s)) for s in observations}
    )
    claims = []
    for metric in _HYPOTHESES:
        if metric == "keeper_advanced":
            samples = observations
        elif metric == "opponent_possession_dominance":
            samples = [s for s in observations if s.owner is not None]
        else:  # all_players_attacking
            samples = [s for s in observations if s.owner is not None and s.owner[0] != s.team]
        if not samples:
            continue
        if metric == "keeper_advanced":
            positives = [
                s.tick for s in samples
                if s.direction * next(p for p in s.opponents if p.id == 0).x <= 30
            ]
        elif metric == "opponent_possession_dominance":
            positives = [s.tick for s in samples if s.owner[0] != s.team]
        else:  # all_players_attacking
            positives = [s.tick for s in samples if all(s.direction * p.x < 0 for p in s.opponents)]
        observation = ScoutingObservation(
            metric=metric,
            samples=len(samples),
            positive=len(positives),
            sampled_ticks=[s.tick for s in samples],
            positive_ticks=positives,
        )
        claims.append(
            EvidenceClaim(
                id=f"scout-{metric}",
                kind="observed",
                text=observation.model_dump_json(),
                source_ids=("manifest", *(f"snapshot-{s.tick}" for s in samples)),
            )
        )
    packet = EvidencePacket(
        team_id=team_id,
        opponent_id=checked["opponent_id"],
        match_id=checked["match_id"],
        profile_version=profile.version,
        played_at=timestamp(checked["played_at"]),
        expires_at=expires_at,
        complete=True,
        result=tuple(checked["result"]),
        claims=tuple(claims),
        source_digests=sources,
    )
    note = ScoutingNote(
        note_id=f"scout-{packet.digest}",
        packet_digest=packet.digest,
        **{
            k: getattr(packet, k)
            for k in (
                "team_id",
                "opponent_id",
                "match_id",
                "profile_version",
                "played_at",
                "expires_at",
                "claims",
            )
        },
    )
    return packet, note


def build_brief(
    context: ScoutingContext,
    *,
    team_id: str,
    opponent_id: str,
    match_id: str,
    profile: Profile,
    kickoff: datetime,
) -> tuple[dict[str, Any], EvidencePacket]:
    """Produce review-only hypotheses from historical, exact, source-bound notes."""
    for label, value in (("team", team_id), ("opponent", opponent_id), ("match", match_id)):
        nonempty(value, label)
    if kickoff.tzinfo is None:
        raise ValueError("kickoff requires a timezone")
    cutoff = max(kickoff, datetime.now(timezone.utc))
    excluded = list(context.excluded)
    if context.extracted_text:
        excluded.append("semantic_text_not_used")
    by_match: dict[str, ScoutingNote] = {}
    for note in context.notes:
        if (
            note.team_id != team_id
            or note.opponent_id != opponent_id
            or note.match_id == match_id
            or note.played_at >= kickoff
            or note.expires_at <= cutoff
        ):
            excluded.append(f"ineligible_note:{note.note_id}")
            continue
        previous = by_match.get(note.match_id)
        if previous is not None and previous != note:
            raise ValueError("conflicting scouting notes for one match")
        by_match[note.match_id] = note
    grouped: dict[_Metric, list[tuple[ScoutingNote, ScoutingObservation]]] = {
        metric: [] for metric in _HYPOTHESES
    }
    for note in sorted(by_match.values(), key=lambda n: (n.played_at, n.match_id)):
        seen = set()
        for claim in note.claims:
            if not claim.id.startswith("scout-") or claim.kind != "observed":
                excluded.append(f"unsupported_claim:{note.note_id}:{claim.id}")
                continue
            observation = ScoutingObservation.model_validate_json(claim.text)
            if (
                claim.id != f"scout-{observation.metric}"
                or observation.metric in seen
                or set(claim.source_ids)
                != {"manifest", *(f"snapshot-{tick}" for tick in observation.sampled_ticks)}
            ):
                raise ValueError("invalid scouting observation references")
            seen.add(observation.metric)
            grouped[observation.metric].append((note, observation))
    hypotheses = []
    claims = []
    sources = {}
    for metric, items in grouped.items():
        positive = sum(o.positive for _, o in items)
        samples = sum(o.samples for _, o in items)
        matches_observed = sum(o.positive > 0 for _, o in items)
        eligible = matches_observed >= 2 and positive >= 3 and positive >= samples * 0.2
        hypotheses.append(
            {
                "metric": metric,
                "positive": positive,
                "samples": samples,
                "matches_observed": matches_observed,
                "eligible_for_review": eligible,
                "recommendation": _HYPOTHESES[metric]
                if eligible
                else "Collect more independent evidence.",
                "evidence": [
                    {
                        "note_id": n.note_id,
                        "match_id": n.match_id,
                        "packet_digest": n.packet_digest,
                        "profile_version": n.profile_version,
                        "played_at": n.played_at.isoformat(),
                        "expires_at": n.expires_at.isoformat(),
                        "observation": o.model_dump(),
                    }
                    for n, o in items
                ],
            }
        )
        if eligible:
            refs = []
            for note, _ in items:
                source = f"memory-note-{_canonical_digest(note.model_dump(mode='json'))}"
                sources[source] = _canonical_digest(note.model_dump(mode="json"))
                refs.append(source)
            claims.append(
                EvidenceClaim(
                    id=f"prematch-{metric}",
                    kind="hypothesis",
                    source_ids=tuple(refs),
                    text=(
                        f"{_HYPOTHESES[metric]} Historical sampled observations: "
                        f"{positive}/{samples}, positive in {matches_observed} distinct matches. "
                        "Not a time-weighted rate, proven counter-tactic or outcome prediction."
                    ),
                )
            )
    brief = {
        "status": "review_required" if claims else "insufficient_evidence",
        "team_id": team_id,
        "opponent_id": opponent_id,
        "match_id": match_id,
        "kickoff": kickoff.isoformat(),
        "current_profile_version": profile.version,
        "distinct_matches": len(by_match),
        "hypotheses": hypotheses,
        "excluded": excluded,
        "limitations": list(_LIMITATIONS),
        "profile_changed": False,
    }
    packet = EvidencePacket(
        team_id=team_id,
        opponent_id=opponent_id,
        match_id=match_id,
        profile_version=profile.version,
        played_at=kickoff,
        expires_at=kickoff + timedelta(days=1),
        complete=False,
        result=None,
        claims=tuple(claims),
        source_digests=sources,
    )
    return brief, packet
