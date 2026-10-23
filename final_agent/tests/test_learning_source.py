from __future__ import annotations

import copy
import json
from datetime import datetime, timezone

import pytest
from test_learning_policy import evidence

from agent.learning_source import MemoryObservationSource
from agent.profile_registry import canonical


class Index:
    def __init__(self, documents):
        self.rows = [
            {
                "document": {"S": canonical(doc)},
                "status": {"S": "SENT"},
                "team": {"S": "our_team"},
                "received": {"N": str(doc["source"]["timestamp_ms"])},
            }
            for doc in documents
        ]

    def query(self, **request):
        assert request["IndexName"] == "TeamReceived"
        assert request["ExpressionAttributeValues"][":team"] == {"S": "our_team"}
        return {"Items": copy.deepcopy(self.rows)}


class Memory:
    def __init__(self, documents):
        self.documents = documents
        self.pages = False
        self.bad_token = False

    def list_events(self, **request):
        suffix = request["sessionId"].removeprefix("observations_v1_")
        mid, side = suffix.rsplit("_", 1)
        docs = [
            d
            for d in self.documents
            if d["content"]["match_id"] == mid and str(d["content"]["team_side"]) == side
        ]
        events = [
            {
                "memoryId": request["memoryId"],
                "actorId": request["actorId"],
                "sessionId": request["sessionId"],
                "eventTimestamp": datetime.fromtimestamp(
                    doc["source"]["timestamp_ms"] / 1000, timezone.utc
                ),
                "payload": [
                    {"conversational": {"role": "USER", "content": {"text": canonical(doc)}}}
                ],
            }
            for doc in docs
        ]
        if self.bad_token:
            return {"events": events, "nextToken": "same"}
        if self.pages and "nextToken" not in request:
            return {"events": events[:2], "nextToken": "second"}
        return {"events": events[2:] if self.pages else events}


def setup():
    docs = evidence()
    index, memory = Index(docs), Memory(docs)
    source = MemoryObservationSource(
        index, memory, table="observations", memory_id="Memory-123", team_id="our_team"
    )
    return source, index, memory, docs


def test_learner_uses_exact_memory_events_not_only_local_outbox():
    source, _, memory, docs = setup()
    memory.pages = True
    assert source.load(now_ms=200_000) == sorted(docs, key=lambda d: d["observation_id"])


def test_recent_game_is_not_analysis_ready_and_pending_is_not_acknowledged():
    source, index, _, docs = setup()
    index.rows[0]["status"] = {"S": "PENDING"}
    assert len(source.load(now_ms=200_000)) == len(docs) - 5
    assert source.load(now_ms=100_000) == []


@pytest.mark.parametrize("damage", ["missing", "tampered", "foreign", "loop"])
def test_missing_or_invalid_memory_evidence_fails_closed(damage):
    source, _, memory, _ = setup()
    if damage == "missing":
        memory.documents = memory.documents[1:]
    elif damage == "tampered":
        memory.documents = copy.deepcopy(memory.documents)
        memory.documents[0]["content"]["snapshot"]["ball"] = [0, 0]
    elif damage == "foreign":
        memory.documents = copy.deepcopy(memory.documents)
        memory.documents[0]["content"]["team_id"] = "foreign"
    else:
        memory.bad_token = True
    with pytest.raises(RuntimeError, match="Memory"):
        source.load(now_ms=200_000)


def test_old_or_future_events_are_not_eligible():
    source, index, _, _ = setup()
    for row in index.rows:
        doc = json.loads(row["document"]["S"])
        doc["source"]["timestamp_ms"] = 999_999
        row["document"]["S"] = canonical(doc)
    assert source.load(now_ms=200_000) == []


def test_fractional_timestamp_roundtrip_keeps_original_millisecond():
    source, index, memory, _ = setup()
    for row, doc in zip(index.rows, memory.documents, strict=True):
        doc["source"]["timestamp_ms"] = 1001
        row["document"]["S"] = canonical(doc)
    assert len(source.load(now_ms=200_000)) == 15


def test_truncated_interleaved_index_cannot_hide_errors_from_retained_matches():
    source, index, _, _ = setup()
    index.rows = (index.rows * 200)[:3000]
    index.query = lambda **request: {
        "Items": index.rows,
        "LastEvaluatedKey": {"pk": {"S": "older-observation"}},
    }
    with pytest.raises(RuntimeError, match="truncated"):
        source.load(now_ms=200_000)


def test_unindexed_memory_observation_cannot_be_silently_omitted():
    source, index, _, _ = setup()
    index.rows = index.rows[1:]
    with pytest.raises(RuntimeError, match="Memory"):
        source.load(now_ms=200_000)
