"""AgentCore Memory adapter: shared opponent scouting notes.

Writes validated ScoutingNote envelopes as short-term conversational events and
reads them back via exact listing. Semantic context from RetrieveMemoryRecords
is optional: unlinked extracted text is recorded as excluded, not used as evidence.

No AWS credentials are discovered here — callers inject a boto3-like client.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any, Protocol

from .contracts import (
    EvidenceClaim,
    EvidencePacket,
    OfflineConfig,
    OfflineServiceError,
    ScoutingContext,
    ScoutingNote,
)

# ── Memory client protocol ────────────────────────────────────────────────────


class _MemoryClient(Protocol):
    def create_event(self, **kwargs: Any) -> dict[str, Any]: ...

    def list_events(self, **kwargs: Any) -> dict[str, Any]: ...

    def retrieve_memory_records(self, **kwargs: Any) -> dict[str, Any]: ...


# ── Namespace helpers ─────────────────────────────────────────────────────────

_UNSAFE_RE = re.compile(r"[^a-z0-9]")
_MAX_SLUG = 32
_DIGEST_SUFFIX_LEN = 8
_MAX_EXTRACTED_BYTES = 4096


def _safe_component(value: str) -> str:
    """Lowercase slug + 8-char SHA-256 suffix; safe for namespace injection."""
    slug = _UNSAFE_RE.sub("_", value.lower())[:_MAX_SLUG]
    suffix = hashlib.sha256(value.encode()).hexdigest()[:_DIGEST_SUFFIX_LEN]
    return f"{slug}_{suffix}"


# ── AgentCore Memory adapter ──────────────────────────────────────────────────


class AgentCoreMemory:
    """ScoutStore backed by AgentCore Memory.

    publish: writes a JSON event (ScoutingNote fields + packet digest).
    retrieve: lists events for the team's actor, reconstructs notes by
              opponent_id and application expiry; optionally retrieves
              semantic context (unlinked text is excluded, not silently accepted).
    """

    def __init__(
        self,
        client: _MemoryClient,
        config: OfflineConfig,
        *,
        max_records: int = 20,
    ) -> None:
        self._client = client
        self._config = config
        self._max_records = max_records

    # ── publish ───────────────────────────────────────────────────────────────

    def publish(self, note: ScoutingNote, packet: EvidencePacket) -> str:
        """Write note as a short-term memory event; returns note_id on success."""
        actor_id = _safe_component(note.team_id)
        # Deterministic session scopes this note's event for idempotency
        session_id = _safe_component(f"{note.match_id}_{note.note_id}")

        envelope: dict[str, Any] = {
            "note_id": note.note_id,
            "team_id": note.team_id,
            "opponent_id": note.opponent_id,
            "match_id": note.match_id,
            "profile_version": note.profile_version,
            "played_at": note.played_at.isoformat(),
            "expires_at": note.expires_at.isoformat(),
            "packet_digest": note.packet_digest,
            "claims": [
                {
                    "id": c.id,
                    "text": c.text,
                    "kind": c.kind,
                    "source_ids": list(c.source_ids),
                }
                for c in note.claims
            ],
        }
        payload = json.dumps(envelope, sort_keys=True, allow_nan=False)

        try:
            self._client.create_event(
                memoryStoreId=self._config.memory_store_id,
                actorId=actor_id,
                sessionId=session_id,
                messages=[{"role": "user", "content": [{"text": payload}]}],
            )
        except Exception as exc:
            raise OfflineServiceError(
                f"Memory publish failed: {type(exc).__name__}",
                service="memory",
                stage="publish",
            ) from exc

        return note.note_id

    # ── retrieve ──────────────────────────────────────────────────────────────

    def retrieve(self, packet: EvidencePacket) -> ScoutingContext:
        """Return scouting notes and optional semantic text for this packet's opponent.

        Empty context is legitimate after a successful query with no results.
        Service call failure always raises OfflineServiceError.
        Application expiry is enforced even when AWS has not expired the event.
        """
        actor_id = _safe_component(packet.team_id)
        now = datetime.now(tz=timezone.utc)

        # 1. Exact event listing for provenance
        try:
            response = self._client.list_events(
                memoryStoreId=self._config.memory_store_id,
                actorId=actor_id,
                maxResults=self._max_records,
            )
        except Exception as exc:
            raise OfflineServiceError(
                f"Memory list_events failed: {type(exc).__name__}",
                service="memory",
                stage="retrieve_list",
                outcome="unknown",
            ) from exc

        events = response.get("events") or response.get("memoryRecords") or []
        valid_notes: list[ScoutingNote] = []
        valid_digests: set[str] = set()

        for event in events:
            raw = _extract_text(event)
            if not raw:
                continue
            try:
                env = json.loads(raw)
            except (json.JSONDecodeError, ValueError):
                continue

            # Isolate by opponent_id and team_id — never accept another team's data
            if env.get("opponent_id") != packet.opponent_id:
                continue
            if env.get("team_id") != packet.team_id:
                continue

            # Application-level expiry check
            try:
                expires_at = datetime.fromisoformat(env["expires_at"])
            except (KeyError, ValueError):
                continue
            if expires_at <= now:
                continue

            try:
                note = ScoutingNote(
                    note_id=env["note_id"],
                    team_id=env["team_id"],
                    opponent_id=env["opponent_id"],
                    match_id=env["match_id"],
                    profile_version=env["profile_version"],
                    played_at=env["played_at"],
                    expires_at=env["expires_at"],
                    packet_digest=env["packet_digest"],
                    claims=tuple(
                        EvidenceClaim(
                            id=c["id"],
                            text=c["text"],
                            kind=c["kind"],
                            source_ids=tuple(c["source_ids"]),
                        )
                        for c in env.get("claims") or []
                    ),
                )
                valid_notes.append(note)
                valid_digests.add(note.packet_digest)
            except Exception:
                continue

        # 2. Optional semantic extraction — failure degrades to excluded
        extracted: list[str] = []
        excluded: list[str] = []

        try:
            sem_resp = self._client.retrieve_memory_records(
                memoryStoreId=self._config.memory_store_id,
                actorId=actor_id,
                query=f"opponent {packet.opponent_id} scouting",
                maxResults=self._max_records,
            )
            for rec in sem_resp.get("memoryRecords") or []:
                text = (rec.get("content") or {}).get("text") or ""
                if not text:
                    continue
                provenance = (rec.get("metadata") or {}).get("packet_digest") or ""
                if provenance in valid_digests:
                    extracted.append(text[:_MAX_EXTRACTED_BYTES])
                else:
                    excluded.append(f"provenance_unlinked:{provenance[:16]}")
        except Exception as exc:
            excluded.append(f"semantic_retrieval_failed:{type(exc).__name__}")

        return ScoutingContext(
            notes=tuple(valid_notes),
            extracted_text=tuple(extracted),
            excluded=tuple(excluded),
        )


# ── Helpers ───────────────────────────────────────────────────────────────────


def _extract_text(event: dict[str, Any]) -> str:
    """Extract text from a conversational event payload."""
    parts: list[str] = []
    for msg in event.get("messages") or []:
        for block in msg.get("content") or []:
            if "text" in block:
                parts.append(block["text"])
    return "".join(parts)
