"""Tests for AgentCoreMemory: isolation, idempotency, expiry, access denial."""

from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta, timezone
from typing import Any

from tests.offline_support import make_config, make_note, make_packet

_NOW = datetime.now(tz=timezone.utc)
_FUTURE = _NOW + timedelta(days=365)
_EXPIRED = _NOW - timedelta(days=1)


# ── Fake boto3-style AgentCore Memory client ──────────────────────────────────


class FakeMemoryClient:
    """Records create_event/list_events/retrieve_memory_records calls.

    No AWS credentials are discovered, cached or used.
    Published events are stored in memory; list_events returns them filtered
    by actorId. retrieve_memory_records returns configurable semantic records.
    """

    def __init__(
        self,
        *,
        semantic_records: list[dict[str, Any]] | None = None,
        create_side_effect: Exception | None = None,
        list_side_effect: Exception | None = None,
        retrieve_side_effect: Exception | None = None,
    ) -> None:
        self.created: list[dict[str, Any]] = []
        self.listed: list[dict[str, Any]] = []
        self.retrieved_queries: list[dict[str, Any]] = []
        self._semantic_records = semantic_records or []
        self._create_side_effect = create_side_effect
        self._list_side_effect = list_side_effect
        self._retrieve_side_effect = retrieve_side_effect
        self._events: list[dict[str, Any]] = []

    def create_event(self, **kwargs: Any) -> dict[str, Any]:
        if self._create_side_effect:
            raise self._create_side_effect
        self.created.append(kwargs)
        self._events.append(kwargs)
        return {"eventId": f"evt-{len(self._events):04d}"}

    def list_events(self, **kwargs: Any) -> dict[str, Any]:
        if self._list_side_effect:
            raise self._list_side_effect
        self.listed.append(kwargs)
        actor_id = kwargs.get("actorId", "")
        matching = [e for e in self._events if e.get("actorId") == actor_id]
        max_r = kwargs.get("maxResults", 100)
        return {"events": matching[:max_r]}

    def retrieve_memory_records(self, **kwargs: Any) -> dict[str, Any]:
        if self._retrieve_side_effect:
            raise self._retrieve_side_effect
        self.retrieved_queries.append(kwargs)
        return {"memoryRecords": self._semantic_records}


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_memory(client=None, *, max_records: int = 20):
    from agent.advisor.offline.memory import AgentCoreMemory

    cfg = make_config()
    return AgentCoreMemory(client or FakeMemoryClient(), cfg, max_records=max_records)


def _note_with_expiry(expires_at: datetime, *, opponent_id: str = "redesign_3_v1"):
    packet = make_packet(opponent_id=opponent_id)
    # Rebuild note with custom expiry
    from agent.advisor.offline.contracts import ScoutingNote

    base = make_note(packet=packet)
    return ScoutingNote(
        note_id=base.note_id,
        team_id=base.team_id,
        opponent_id=base.opponent_id,
        match_id=base.match_id,
        profile_version=base.profile_version,
        played_at=base.played_at,
        expires_at=expires_at,
        packet_digest=base.packet_digest,
        claims=base.claims,
    )


class PublishTest(unittest.TestCase):
    def test_publish_returns_note_id(self):
        mem = _make_memory()
        note = make_note()
        packet = make_packet()
        result = mem.publish(note, packet)
        self.assertEqual(result, note.note_id)

    def test_publish_calls_create_event_once(self):
        client = FakeMemoryClient()
        mem = _make_memory(client)
        note = make_note()
        packet = make_packet()
        mem.publish(note, packet)
        self.assertEqual(len(client.created), 1)

    def test_publish_uses_memory_store_id_from_config(self):
        client = FakeMemoryClient()
        mem = _make_memory(client)
        note = make_note()
        packet = make_packet()
        mem.publish(note, packet)
        self.assertEqual(client.created[0]["memoryStoreId"], "memory-store-001")

    def test_publish_same_note_uses_same_session_id(self):
        """Same note+match produces same sessionId — idempotency token."""
        client = FakeMemoryClient()
        mem = _make_memory(client)
        note = make_note()
        packet = make_packet()
        mem.publish(note, packet)
        mem.publish(note, packet)
        session_ids = [c["sessionId"] for c in client.created]
        self.assertEqual(session_ids[0], session_ids[1])

    def test_publish_envelope_contains_note_fields(self):
        client = FakeMemoryClient()
        mem = _make_memory(client)
        note = make_note()
        packet = make_packet()
        mem.publish(note, packet)
        messages = client.created[0]["messages"]
        text = messages[0]["content"][0]["text"]
        envelope = json.loads(text)
        self.assertEqual(envelope["note_id"], note.note_id)
        self.assertEqual(envelope["opponent_id"], note.opponent_id)
        self.assertEqual(envelope["packet_digest"], note.packet_digest)

    def test_publish_service_error_raises_offline_service_error(self):
        from agent.advisor.offline.contracts import OfflineServiceError

        client = FakeMemoryClient(create_side_effect=RuntimeError("network"))
        mem = _make_memory(client)
        note = make_note()
        packet = make_packet()
        with self.assertRaises(OfflineServiceError) as ctx:
            mem.publish(note, packet)
        self.assertEqual(ctx.exception.service, "memory")
        self.assertEqual(ctx.exception.stage, "publish")


class RetrieveIsolationTest(unittest.TestCase):
    """Opponent notes must not cross into a different opponent's retrieval."""

    def test_opponent_notes_not_returned_for_different_opponent(self):
        client = FakeMemoryClient()
        mem = _make_memory(client)

        note_a = _note_with_expiry(_FUTURE, opponent_id="opp-a")
        note_b = _note_with_expiry(_FUTURE, opponent_id="opp-b")
        packet_a = make_packet(opponent_id="opp-a")
        packet_b = make_packet(opponent_id="opp-b")

        mem.publish(note_a, packet_a)
        mem.publish(note_b, packet_b)

        # Retrieve for opp-a; must not include opp-b's note
        ctx_a = mem.retrieve(packet_a)
        opponent_ids = {n.opponent_id for n in ctx_a.notes}
        self.assertNotIn("opp-b", opponent_ids)

    def test_retrieve_returns_only_matching_opponent(self):
        client = FakeMemoryClient()
        mem = _make_memory(client)

        note = _note_with_expiry(_FUTURE, opponent_id="opp-target")
        packet = make_packet(opponent_id="opp-target")
        mem.publish(note, packet)

        ctx = mem.retrieve(packet)
        for n in ctx.notes:
            self.assertEqual(n.opponent_id, "opp-target")


class RetrieveExpiryTest(unittest.TestCase):
    def test_expired_note_excluded_from_context(self):
        client = FakeMemoryClient()
        mem = _make_memory(client)

        note = _note_with_expiry(_EXPIRED)
        packet = make_packet()
        mem.publish(note, packet)

        ctx = mem.retrieve(packet)
        self.assertEqual(len(ctx.notes), 0)

    def test_valid_note_included_in_context(self):
        client = FakeMemoryClient()
        mem = _make_memory(client)

        note = _note_with_expiry(_FUTURE)
        packet = make_packet()
        mem.publish(note, packet)

        ctx = mem.retrieve(packet)
        self.assertEqual(len(ctx.notes), 1)


class RetrievePaginationTest(unittest.TestCase):
    def test_max_records_passed_to_list_events(self):
        client = FakeMemoryClient()
        mem = _make_memory(client, max_records=5)
        packet = make_packet()
        mem.retrieve(packet)
        self.assertEqual(client.listed[0]["maxResults"], 5)


class RetrieveAccessDenialTest(unittest.TestCase):
    """Service failure must raise, not return empty context."""

    def test_list_events_failure_raises_offline_service_error(self):
        from agent.advisor.offline.contracts import OfflineServiceError

        client = FakeMemoryClient(list_side_effect=PermissionError("access denied"))
        mem = _make_memory(client)
        packet = make_packet()
        with self.assertRaises(OfflineServiceError) as ctx:
            mem.retrieve(packet)
        self.assertEqual(ctx.exception.service, "memory")
        self.assertIn("retrieve", ctx.exception.stage)


class RetrieveSemanticTest(unittest.TestCase):
    def test_semantic_failure_recorded_as_excluded_not_raised(self):
        """Semantic retrieval failure degrades to excluded, does not abort retrieve."""
        client = FakeMemoryClient(retrieve_side_effect=RuntimeError("timeout"))
        mem = _make_memory(client)
        note = _note_with_expiry(_FUTURE)
        packet = make_packet()
        mem.publish(note, packet)

        ctx = mem.retrieve(packet)
        # Should still return the exact note; semantic failure is excluded
        self.assertEqual(len(ctx.notes), 1)
        self.assertTrue(any("semantic" in e for e in ctx.excluded))

    def test_unlinked_semantic_text_is_excluded_not_accepted(self):
        """Semantic text without a matching packet_digest is excluded."""
        sem = [{"content": {"text": "some extracted text"}, "metadata": {}}]
        client = FakeMemoryClient(semantic_records=sem)
        mem = _make_memory(client)
        note = _note_with_expiry(_FUTURE)
        packet = make_packet()
        mem.publish(note, packet)

        ctx = mem.retrieve(packet)
        self.assertNotIn("some extracted text", ctx.extracted_text)
        self.assertTrue(any("provenance_unlinked" in e for e in ctx.excluded))

    def test_linked_semantic_text_is_included(self):
        """Semantic text linked to a known packet_digest is included."""
        # First publish to get packet_digest
        client = FakeMemoryClient()
        mem = _make_memory(client)
        note = _note_with_expiry(_FUTURE)
        packet = make_packet()
        mem.publish(note, packet)

        # Rebuild client with semantic record that has the right digest
        sem = [
            {
                "content": {"text": "linked scouting insight"},
                "metadata": {"packet_digest": note.packet_digest},
            }
        ]
        client2 = FakeMemoryClient(semantic_records=sem)
        # Inject the already-stored event
        client2._events = client._events[:]
        mem2_cfg = make_config()
        from agent.advisor.offline.memory import AgentCoreMemory

        mem2 = AgentCoreMemory(client2, mem2_cfg)
        ctx = mem2.retrieve(packet)
        self.assertIn("linked scouting insight", ctx.extracted_text)


# Structural check: AgentCoreMemory satisfies ScoutStore at import time.
def _check_protocol() -> None:
    from agent.advisor.offline.contracts import ScoutStore
    from agent.advisor.offline.memory import AgentCoreMemory

    _: ScoutStore = AgentCoreMemory(FakeMemoryClient(), make_config())  # type: ignore[type-abstract]


if __name__ == "__main__":
    unittest.main()
