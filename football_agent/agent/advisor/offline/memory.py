"""Exact AgentCore Memory events, scoped by trusted team and opponent identity.

The caller supplies the SDK runtime client. A canonical note digest and its
source packet travel together; these are integrity checks, not signatures.
IAM must restrict event writers. Optional semantic extraction is deliberately
excluded: an extracted record cannot prove its text came from a validated note.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any, Protocol

from pydantic import ValidationError

from .contracts import (
    EvidencePacket,
    OfflineConfig,
    OfflineServiceError,
    ScoutingContext,
    ScoutingNote,
)


class _MemoryClient(Protocol):
    def create_event(self, **kwargs: Any) -> dict[str, Any]: ...

    def list_events(self, **kwargs: Any) -> dict[str, Any]: ...


_MAX_EVENT_BYTES = 100_000
_MAX_PAGES = 10


def _safe_component(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]", "_", value.lower())[:32].strip("_") or "id"
    return f"{slug}_{hashlib.sha256(value.encode()).hexdigest()}"


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _note_digest(note: ScoutingNote) -> str:
    return hashlib.sha256(_canonical(note.model_dump(mode="json")).encode()).hexdigest()


def _validate_note(note: ScoutingNote, packet: EvidencePacket) -> None:
    for field in ("team_id", "opponent_id", "match_id", "profile_version", "played_at"):
        if getattr(note, field) != getattr(packet, field):
            raise ValueError(f"note {field} does not match packet")
    if note.packet_digest != packet.digest:
        raise ValueError("note packet digest does not match packet")
    if not packet.played_at < note.expires_at <= packet.expires_at:
        raise ValueError("note expiry is outside packet validity")
    if not packet.complete:
        raise ValueError("incomplete packet is not scouting evidence")
    if any(claim not in packet.claims for claim in note.claims):
        raise ValueError("note claims do not match packet evidence")
    if any(sid not in packet.source_digests for c in note.claims for sid in c.source_ids):
        raise ValueError("note claim has an unknown source")


class AgentCoreMemory:
    """Publish and retrieve bounded exact events in a deterministic opponent session.

    Existing match-specific sessions are not scanned or silently trusted.
    Retrieval follows at most ten pages and reports any excluded/truncated data.
    """

    def __init__(
        self, client: _MemoryClient, config: OfflineConfig, *, max_records: int = 20
    ) -> None:
        if not 1 <= max_records <= 100:
            raise ValueError("max_records must be between 1 and 100")
        self._client = client
        self._config = config
        self._max_records = max_records

    def publish(self, note: ScoutingNote, packet: EvidencePacket) -> str:
        """Validate against the caller's packet before any service request."""
        try:
            _validate_note(note, packet)
            digest = _note_digest(note)
            payload = _canonical(
                {
                    "note": note.model_dump(mode="json"),
                    "note_digest": digest,
                    "packet": packet.model_dump(mode="json"),
                }
            )
            if len(payload.encode()) > _MAX_EVENT_BYTES:
                raise ValueError("note envelope exceeds event byte limit")
        except ValueError as exc:
            raise OfflineServiceError(
                "Invalid scouting note or packet",
                service="memory",
                stage="publish_validate",
                error_code="INVALID_NOTE",
            ) from exc

        try:
            self._client.create_event(
                memoryId=self._config.memory_store_id,
                actorId=_safe_component(packet.team_id),
                sessionId=_safe_component(packet.opponent_id),
                eventTimestamp=packet.played_at,
                payload=[{"conversational": {"role": "USER", "content": {"text": payload}}}],
                clientToken=digest,
            )
        except Exception as exc:
            raise OfflineServiceError(
                f"Memory publish failed: {type(exc).__name__}",
                service="memory",
                stage="publish",
                outcome="unknown",
            ) from exc
        return note.note_id

    def retrieve(self, packet: EvidencePacket) -> ScoutingContext:
        """List payloads for this team's opponent session, checking every envelope."""
        return self._retrieve(
            packet.team_id,
            packet.opponent_id,
            before=packet.played_at if not packet.complete else None,
            excluded_match_id=packet.match_id if not packet.complete else None,
        )

    def retrieve_before(self, team_id: str, opponent_id: str, before: datetime) -> ScoutingContext:
        """Retrieve past scouting only; the cutoff is exclusive."""
        if before.tzinfo is None:
            raise ValueError("Memory cutoff requires a timezone")
        return self._retrieve(team_id, opponent_id, before=before)

    def _retrieve(
        self,
        team_id: str,
        opponent_id: str,
        *,
        before: datetime | None = None,
        excluded_match_id: str | None = None,
    ) -> ScoutingContext:
        actor_id = _safe_component(team_id)
        session_id = _safe_component(opponent_id)
        now = datetime.now(timezone.utc)
        notes: dict[str, ScoutingNote] = {}
        excluded = ["semantic_retrieval_disabled:no_trusted_record_link"]
        token: str | None = None
        seen_tokens: set[str] = set()
        for _ in range(_MAX_PAGES):
            params: dict[str, Any] = {
                "memoryId": self._config.memory_store_id,
                "actorId": actor_id,
                "sessionId": session_id,
                "includePayloads": True,
                "maxResults": self._max_records,
            }
            if token is not None:
                params["nextToken"] = token
            try:
                response = self._client.list_events(**params)
            except Exception as exc:
                raise OfflineServiceError(
                    f"Memory list_events failed: {type(exc).__name__}",
                    service="memory",
                    stage="retrieve_list",
                    outcome="unknown",
                ) from exc
            events = response.get("events")
            if not isinstance(events, list) or len(events) > self._max_records:
                raise OfflineServiceError(
                    "Malformed Memory event page",
                    service="memory",
                    stage="retrieve_list",
                )
            for event in events:
                try:
                    note, digest = self._read_event(event, actor_id, session_id)
                    if note.team_id != team_id or note.opponent_id != opponent_id:
                        raise ValueError("event does not match requested identity")
                    if note.expires_at <= max(now, before or now):
                        raise ValueError("event expired")
                    if note.match_id == excluded_match_id:
                        excluded.append("upcoming_match_excluded")
                        continue
                    if before is not None and note.played_at >= before:
                        excluded.append("not_before_cutoff")
                        continue
                    notes[digest] = note
                except (ValueError, KeyError, TypeError, ValidationError, RecursionError):
                    excluded.append("invalid_or_expired_event")
            token = response.get("nextToken")
            if not token:
                break
            if not isinstance(token, str) or token in seen_tokens:
                raise OfflineServiceError(
                    "Invalid Memory pagination token",
                    service="memory",
                    stage="retrieve_list",
                )
            seen_tokens.add(token)
        else:
            excluded.append("event_page_limit")

        ordered = sorted(notes.values(), key=lambda n: (n.played_at, n.note_id), reverse=True)
        if len(ordered) > self._max_records:
            excluded.append("note_limit")
        return ScoutingContext(
            notes=tuple(ordered[: self._max_records]),
            extracted_text=(),
            excluded=tuple(excluded),
        )

    def _read_event(self, event: Any, actor_id: str, session_id: str) -> tuple[ScoutingNote, str]:
        if not isinstance(event, dict):
            raise ValueError("event is not an object")
        if (
            event.get("memoryId") != self._config.memory_store_id
            or event.get("actorId") != actor_id
            or event.get("sessionId") != session_id
        ):
            raise ValueError("event service identity mismatch")
        payload = event["payload"]
        if not isinstance(payload, list) or len(payload) != 1:
            raise ValueError("unexpected event payload")
        conversational = payload[0]["conversational"]
        if conversational["role"] != "USER":
            raise ValueError("unexpected event role")
        raw = conversational["content"]["text"]
        if not isinstance(raw, str) or len(raw.encode()) > _MAX_EVENT_BYTES:
            raise ValueError("event envelope too large")
        env = json.loads(raw)
        if not isinstance(env, dict) or set(env) != {"note", "note_digest", "packet"}:
            raise ValueError("invalid event envelope")
        note = ScoutingNote.model_validate(env["note"])
        source_packet = EvidencePacket.model_validate(env["packet"])
        _validate_note(note, source_packet)
        digest = _note_digest(note)
        if env["note_digest"] != digest or event["eventTimestamp"] != source_packet.played_at:
            raise ValueError("event digest or timestamp mismatch")
        return note, digest
