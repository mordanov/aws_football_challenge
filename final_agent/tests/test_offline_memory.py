"""SDK-shaped Memory events: trusted identity, integrity, expiry and pagination."""

from __future__ import annotations

import copy
import json
import unittest
from datetime import datetime, timedelta, timezone
from typing import Any

from agent.advisor.offline.contracts import OfflineServiceError
from agent.advisor.offline.memory import AgentCoreMemory
from tests.offline_support import make_config, make_note, make_packet


class FakeMemoryClient:
    """The actual event shape, including session-scoped, paginated payloads."""

    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []
        self.listed: list[dict[str, Any]] = []
        self.events: list[dict[str, Any]] = []
        self.failure: Exception | None = None

    def create_event(self, **kwargs: Any) -> dict[str, Any]:
        if self.failure:
            raise self.failure
        self.created.append(kwargs)
        event = {k: v for k, v in kwargs.items() if k != "clientToken"}
        self.events.append(copy.deepcopy(event))
        return {"event": event}

    def list_events(self, **kwargs: Any) -> dict[str, Any]:
        if self.failure:
            raise self.failure
        self.listed.append(kwargs)
        events = [
            event for event in self.events
            if event["actorId"] == kwargs["actorId"] and event["sessionId"] == kwargs["sessionId"]
        ]
        start = int(kwargs.get("nextToken", "0"))
        end = start + kwargs["maxResults"]
        response: dict[str, Any] = {"events": events[start:end]}
        if end < len(events):
            response["nextToken"] = str(end)
        return response


class MemoryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.client = FakeMemoryClient()
        self.memory = AgentCoreMemory(self.client, make_config())
        self.packet = make_packet()
        self.note = make_note(packet=self.packet)

    def test_round_trip_and_explicit_semantic_exclusion(self) -> None:
        self.assertEqual(self.memory.publish(self.note, self.packet), self.note.note_id)
        context = self.memory.retrieve(self.packet)
        self.assertEqual(context.notes, (self.note,))
        self.assertEqual(context.extracted_text, ())
        self.assertIn("semantic_retrieval_disabled:no_trusted_record_link", context.excluded)
        self.assertTrue(self.client.listed[0]["includePayloads"])

    def test_opponent_session_shared_across_matches_not_across_opponents(self) -> None:
        packets = [
            self.packet, make_packet(match_id="second-match"),
            make_packet(opponent_id="other-opponent"),
        ]
        for packet in packets:
            self.memory.publish(make_note(packet=packet), packet)
        self.assertEqual(self.client.created[0]["sessionId"], self.client.created[1]["sessionId"])
        self.assertNotEqual(self.client.created[0]["sessionId"], self.client.created[2]["sessionId"])
        self.assertEqual(len(self.memory.retrieve(self.packet).notes), 2)

    def test_note_digest_is_idempotency_token_and_duplicates_are_deduplicated(self) -> None:
        self.memory.publish(self.note, self.packet)
        self.memory.publish(self.note, self.packet)
        self.assertEqual(self.client.created[0]["clientToken"], self.client.created[1]["clientToken"])
        self.assertEqual(self.memory.retrieve(self.packet).notes, (self.note,))

    def test_rejects_untrusted_note_identity_digest_claims_and_extended_expiry(self) -> None:
        for update in (
            {"team_id": "other"}, {"opponent_id": "other"}, {"match_id": "other"},
            {"profile_version": "other"}, {"packet_digest": "0" * 64},
            {"played_at": self.note.played_at - timedelta(seconds=1)},
            {"expires_at": self.packet.expires_at + timedelta(days=1)},
            {"claims": (self.note.claims[0].model_copy(update={"text": "invented"}),)},
        ):
            with self.subTest(update=update), self.assertRaises(OfflineServiceError):
                self.memory.publish(self.note.model_copy(update=update), self.packet)
        self.assertEqual(self.client.created, [])

    def test_invalid_and_expired_events_are_excluded_without_aborting_page(self) -> None:
        self.memory.publish(self.note, self.packet)
        good = copy.deepcopy(self.client.events[0])
        mutations = [
            lambda e: e.update(memoryId="another-memory"),
            lambda e: e.update(eventTimestamp=self.packet.played_at + timedelta(seconds=1)),
            lambda e: e.update(payload=[]),
            lambda e: e.update(payload=[{"conversational": {"role": "USER",
                                                           "content": {"text": "[]"}}}]),
        ]
        for mutate in mutations:
            event = copy.deepcopy(good)
            mutate(event)
            self.client.events.append(event)
        for key, value in (
            ("note_digest", "0" * 64),
            ("note", {"expires_at": "2027-01-01"}),
            ("packet", {"match_id": "tampered"}),
        ):
            event = copy.deepcopy(good)
            content = event["payload"][0]["conversational"]["content"]
            env = json.loads(content["text"])
            if isinstance(value, dict):
                env[key].update(value)
            else:
                env[key] = value
            content["text"] = json.dumps(env)
            self.client.events.append(event)
        now = datetime.now(timezone.utc)
        expired = self.packet.model_copy(update={
            "played_at": now - timedelta(days=2), "expires_at": now - timedelta(days=1),
        })
        self.memory.publish(make_note(packet=expired), expired)
        context = self.memory.retrieve(self.packet)
        self.assertEqual(context.notes, (self.note,))
        self.assertEqual(context.excluded.count("invalid_or_expired_event"), 8)

    def test_pagination_finds_note_after_expired_page_and_bounds_results(self) -> None:
        memory = AgentCoreMemory(self.client, make_config(), max_records=1)
        now = datetime.now(timezone.utc)
        expired = self.packet.model_copy(update={
            "played_at": now - timedelta(days=2), "expires_at": now - timedelta(days=1),
        })
        memory.publish(make_note(packet=expired), expired)
        memory.publish(self.note, self.packet)
        self.assertEqual(memory.retrieve(self.packet).notes, (self.note,))
        self.assertEqual(self.client.listed[1]["nextToken"], "1")

    def test_repeating_token_fails_and_page_limit_is_reported(self) -> None:
        class Pages(FakeMemoryClient):
            def list_events(self, **kwargs: Any) -> dict[str, Any]:
                self.listed.append(kwargs)
                return {"events": [], "nextToken": str(len(self.listed))}

        pages = Pages()
        memory = AgentCoreMemory(pages, make_config())
        self.assertIn("event_page_limit", memory.retrieve(self.packet).excluded)
        self.assertEqual(len(pages.listed), 10)

        class Repeated(FakeMemoryClient):
            def list_events(self, **kwargs: Any) -> dict[str, Any]:
                return {"events": [], "nextToken": "same"}

        with self.assertRaises(OfflineServiceError):
            AgentCoreMemory(Repeated(), make_config()).retrieve(self.packet)

    def test_service_failures_are_explicit_with_cause(self) -> None:
        self.client.failure = PermissionError("denied")
        with self.assertRaises(OfflineServiceError) as publish:
            self.memory.publish(self.note, self.packet)
        with self.assertRaises(OfflineServiceError) as retrieve:
            self.memory.retrieve(self.packet)
        for error in (publish.exception, retrieve.exception):
            self.assertIs(error.__cause__, self.client.failure)
            self.assertEqual(error.service, "memory")
