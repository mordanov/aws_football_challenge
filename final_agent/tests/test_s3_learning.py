from __future__ import annotations

import copy
import hashlib
import io
import json
from datetime import datetime, timezone

import pytest
from botocore.exceptions import ClientError
from test_learning_automation import GROUPS, event
from test_learning_automation import observation as logged_observation
from test_learning_policy import evidence
from test_learning_source import Memory

from agent.learning_collector import collect
from agent.learning_observations import Observation
from agent.profile_registry import canonical
from agent.s3_learning import S3MemoryObservationSource, S3ObservationOutbox


class S3:
    """Conditional-write S3 fake; every operation enforces the bucket owner."""

    def __init__(self):
        self.objects = {}
        self.fail_ack = False
        self.lost_ack = False
        self.truncated = False
        self.fail_envelope = False
        self.page_size = 3
        self.reads = []
        self.modified = {}
        self.clock = 0

    def put_object(self, **request):
        assert request["ExpectedBucketOwner"] == "123456789012"
        assert request["IfNoneMatch"] == "*"
        key = request["Key"]
        if key in self.objects:
            raise ClientError({"Error": {"Code": "PreconditionFailed"}}, "PutObject")
        if "/observations/" in key and "/_days/" not in key and self.fail_envelope:
            raise OSError("envelope write unavailable")
        if "/acknowledgements/" in key and self.fail_ack:
            raise OSError("ack write unavailable")
        self.objects[key] = request["Body"]
        self.clock += 1
        self.modified[key] = datetime.fromtimestamp(self.clock, timezone.utc)
        if "/acknowledgements/" in key and self.lost_ack:
            raise OSError("ack response lost")
        return {}

    def get_object(self, **request):
        assert request["ExpectedBucketOwner"] == "123456789012"
        self.reads.append(request["Key"])
        if request["Key"] not in self.objects:
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
        return {"Body": io.BytesIO(self.objects[request["Key"]])}

    def list_objects_v2(self, **request):
        assert request["ExpectedBucketOwner"] == "123456789012"
        keys = sorted(k for k in self.objects if k.startswith(request["Prefix"]))
        start = int(request.get("ContinuationToken", "0"))
        size = min(request["MaxKeys"], self.page_size)
        page = keys[start : start + size]
        response = {
            "Contents": [
                {"Key": k, "LastModified": self.modified.get(k, datetime.fromtimestamp(0, timezone.utc))}
                for k in page
            ],
            "IsTruncated": start + size < len(keys) or self.truncated,
        }
        if response["IsTruncated"] and not self.truncated:
            response["NextContinuationToken"] = str(start + size)
        return response


class WritableMemory(Memory):
    def __init__(self):
        super().__init__([])
        self.requests = {}
        self.fail = False
        self.lost_response = False

    def create_event(self, **request):
        if self.fail:
            raise OSError("Memory unavailable")
        token = request["clientToken"]
        if token in self.requests:
            assert self.requests[token] == request
        else:
            self.requests[token] = copy.deepcopy(request)
            self.documents.append(json.loads(request["payload"][0]["conversational"]["content"]["text"]))
        if self.lost_response:
            self.lost_response = False
            raise OSError("Memory response lost")
        return {}


def setup():
    s3, memory = S3(), WritableMemory()
    kwargs = {
        "bucket": "evidence",
        "memory_id": "Memory-123",
        "team_id": "our_team",
        "owner_account": "123456789012",
    }
    return (
        s3,
        memory,
        S3ObservationOutbox(s3, memory, **kwargs),
        S3MemoryObservationSource(s3, memory, **kwargs),
    )


def observation(doc=None):
    doc = doc or evidence()[0]
    return Observation(doc["observation_id"], doc["content"], doc["source"])


def test_duplicate_transport_keeps_first_envelope_and_exact_memory_timestamp():
    _, memory, outbox, source = setup()
    first = observation()
    assert outbox.publish(first)
    duplicate = Observation(first.observation_id, first.content, {**first.source, "timestamp_ms": 86_401_000})
    assert not outbox.publish(duplicate)
    assert source.load(now_ms=200_000) == memory.documents
    assert memory.documents[0]["source"] == first.source


def test_memory_lost_response_replays_identical_request_from_durable_pending():
    _, memory, outbox, source = setup()
    memory.lost_response = True
    with pytest.raises(OSError, match="response lost"):
        outbox.publish(observation())
    assert source.load(now_ms=200_000) == []
    assert outbox.recover_pending(now_ms=200_000)["published"] == 1
    assert source.load(now_ms=200_000) == memory.documents


def test_ack_failure_retains_pending_and_lost_ack_response_recovers_winner():
    s3, memory, outbox, source = setup()
    s3.fail_ack = True
    with pytest.raises(OSError, match="ack write"):
        outbox.publish(observation())
    assert source.load(now_ms=200_000) == []
    s3.fail_ack, s3.lost_ack = False, True
    assert outbox.recover_pending(now_ms=200_000)["published"] == 1
    assert source.load(now_ms=200_000) == memory.documents


@pytest.mark.parametrize("damage", ["missing", "tampered", "unexpected", "expired"])
def test_exact_memory_evidence_is_required(damage):
    _, memory, outbox, source = setup()
    outbox.publish(observation())
    if damage in ("missing", "expired"):
        memory.documents = []
    elif damage == "tampered":
        memory.documents[0]["content"]["final_result"] = "victory"
    else:
        extra = copy.deepcopy(memory.documents[0])
        extra["observation_id"] = "f" * 64
        memory.documents.append(extra)
    with pytest.raises(RuntimeError, match="Memory"):
        source.load(now_ms=200_000)


def test_pending_blocks_entire_session_and_quiet_window_is_exact():
    _, memory, outbox, source = setup()
    docs = evidence()[:2]
    outbox.publish(observation(docs[0]))
    memory.fail = True
    with pytest.raises(OSError):
        outbox.publish(observation(docs[1]))
    assert source.load(now_ms=200_000) == []
    memory.fail = False
    outbox.recover_pending(now_ms=200_000)
    newest = max(d["source"]["timestamp_ms"] for d in docs)
    assert source.load(now_ms=newest + 119_999) == []
    assert len(source.load(now_ms=newest + 120_000)) == 2


def test_archive_size_does_not_block_recent_window():
    s3, memory, outbox, source = setup()
    outbox.publish(observation())
    now = 20 * 86_400_000
    doc = copy.deepcopy(evidence()[5])
    doc["source"]["timestamp_ms"] = now - 120_000
    outbox.publish(observation(doc))
    for i in range(3100):
        s3.objects[f"final-agent-learning/our_team/observations/_days/1970-01-01/old/{i}.json"] = b"{}"
    assert source.load(now_ms=now) == [memory.documents[-1]]


def test_truncated_listing_without_continuation_fails_closed():
    s3, _, outbox, source = setup()
    outbox.publish(observation())
    s3.truncated = True
    with pytest.raises(RuntimeError, match="pagination|truncated"):
        source.load(now_ms=200_000)


def test_durable_document_tampering_is_not_accepted_as_a_duplicate():
    s3, _, outbox, _ = setup()
    outbox.publish(observation())
    key = next(k for k in s3.objects if "/observations/" in k and "/_days/" not in k)
    envelope = json.loads(s3.objects[key])
    envelope["document"]["content"]["final_result"] = "victory"
    s3.objects[key] = canonical(envelope).encode()
    with pytest.raises(RuntimeError):
        outbox.publish(observation())


def test_recovery_repairs_envelope_write_failure_without_original_subscription():
    s3, memory, outbox, source = setup()
    s3.fail_envelope = True
    with pytest.raises(OSError, match="envelope write"):
        outbox.publish(observation())
    with pytest.raises(RuntimeError, match="envelope"):
        source.load(now_ms=200_000)
    s3.fail_envelope = False
    assert outbox.recover_pending(now_ms=200_000)["published"] == 1
    assert source.load(now_ms=200_000) == memory.documents


def test_subscription_boundary_supports_s3_outbox_without_changing_identity_validation():
    _, memory, outbox, source = setup()
    record = logged_observation()
    assert collect(event([record]), outbox, log_groups=GROUPS)["published"] == 1
    assert collect(event([record], ids=["retry"]), outbox, log_groups=GROUPS)["duplicates"] == 1
    assert source.load(now_ms=1792789120000) == memory.documents
    with pytest.raises(ValueError, match="untrusted"):
        collect(event([record], group="/foreign"), outbox, log_groups=GROUPS)


@pytest.mark.parametrize("damage", ["ack", "envelope", "access"])
def test_invalid_durable_support_and_access_errors_fail_closed(damage):
    s3, _, outbox, source = setup()
    outbox.publish(observation())
    assert len(source.load(now_ms=200_000)) == 1
    if damage == "access":
        def denied(**request):
            raise ClientError({"Error": {"Code": "AccessDenied"}}, "PutObject")
        s3.put_object = denied
        with pytest.raises(ClientError):
            outbox.publish(observation())
        return
    key = next(k for k in s3.objects if "/acknowledgements/" in k)
    if damage == "ack":
        s3.objects[key] = b'{"status":"SENT"}'
    else:
        del s3.objects[key.replace("/acknowledgements/", "/observations/")]
    with pytest.raises(RuntimeError):
        source.load(now_ms=200_000)


def test_overfull_discovery_partition_is_not_silently_sampled(monkeypatch):
    monkeypatch.setattr("agent.s3_learning.MAX_DISCOVERY_REFERENCES", 3000, raising=False)
    s3, _, _, source = setup()
    s3.page_size = 1000
    for i in range(3001):
        s3.objects[f"final-agent-learning/our_team/observations/_days/1970-01-01/{i}.json"] = b"{}"
    with pytest.raises(RuntimeError, match="document limit"):
        source.load(now_ms=200_000)


def test_recent_sessions_are_bounded_whole_sessions_not_an_archive_failure():
    _, _, outbox, source = setup()
    template = evidence()[0]
    for i in range(21):
        doc = copy.deepcopy(template)
        doc["content"]["match_id"] = f"2d4dd176-ca81-4dd6-88de-{i:012d}"
        doc["observation_id"] = hashlib.sha256(canonical(doc["content"]).encode()).hexdigest()
        outbox.publish(observation(doc))
    documents = source.load(now_ms=200_000)
    assert len(documents) == 20
    assert template["content"]["match_id"] not in {doc["content"]["match_id"] for doc in documents}


def test_recent_whole_sessions_fit_evidence_cap_without_global_archive_block(monkeypatch):
    monkeypatch.setattr("agent.s3_learning.MAX_DOCUMENTS", 6)
    _, memory, outbox, source = setup()
    docs = evidence()
    # Eight archived documents exceed the six-document evidence window.
    for i in range(4):
        for base in docs[:2]:
            doc = copy.deepcopy(base)
            doc["content"]["match_id"] = f"2d4dd176-ca81-4dd6-88de-{i:012d}"
            doc["observation_id"] = hashlib.sha256(canonical(doc["content"]).encode()).hexdigest()
            outbox.publish(observation(doc))
    selected = source.load(now_ms=200_000)
    assert len(selected) == 6
    assert len({doc["content"]["match_id"] for doc in selected}) == 3
    assert {doc["observation_id"] for doc in selected} == {
        doc["observation_id"] for doc in memory.documents[2:]
    }
    assert source.last_plan["excluded_capacity"] == 1
    assert source.last_plan["selected_sessions"] == 3


def test_newest_oversized_session_is_excluded_whole_and_older_complete_session_survives(
    monkeypatch,
):
    monkeypatch.setattr("agent.s3_learning.MAX_DOCUMENTS", 3)
    _, memory, outbox, source = setup()
    docs = evidence()
    for doc in docs[5:7] + docs[:4]:
        outbox.publish(observation(doc))
    selected = source.load(now_ms=200_000)
    assert selected == sorted(memory.documents[:2], key=lambda doc: doc["observation_id"])
    assert source.last_plan["excluded_oversized"] == 1
    assert source.last_plan["selected_documents"] == 2


def test_session_crossing_memory_expiry_is_wholly_excluded_not_partially_verified():
    _, memory, outbox, source = setup()
    docs = copy.deepcopy(evidence()[:2])
    docs[0]["source"]["timestamp_ms"] = 1000
    docs[1]["source"]["timestamp_ms"] = 200_000
    for doc in docs:
        outbox.publish(observation(doc))
    # Memory legitimately expires only the older event within this session.
    memory.documents = [doc for doc in memory.documents if doc["source"]["timestamp_ms"] > 1000]
    assert source.load(now_ms=7 * 86_400_000 + 100_000) == []
    recent_session = copy.deepcopy(evidence()[5])
    recent_session["source"]["timestamp_ms"] = 200_000
    outbox.publish(observation(recent_session))
    assert source.load(now_ms=7 * 86_400_000 + 100_000) == [recent_session]


def test_load_does_not_fetch_immutable_envelopes_twice():
    s3, memory, outbox, source = setup()
    for doc in evidence()[:10]:
        outbox.publish(observation(doc))
    s3.reads.clear()
    assert source.load(now_ms=200_000) == sorted(memory.documents, key=lambda d: d["observation_id"])
    canonical_reads = [key for key in s3.reads if "/observations/" in key and "/_days/" not in key]
    assert len(canonical_reads) == len(set(canonical_reads)) == 10


@pytest.mark.parametrize("operation", ["load", "recover_pending"])
def test_independent_s3_reads_overlap_without_unbounded_concurrency(operation):
    import threading
    import time

    s3, memory, outbox, source = setup()
    for doc in evidence()[:10]:
        outbox.publish(observation(doc))
    original = s3.get_object
    lock = threading.Lock()
    active = peak = 0

    def delayed(**request):
        nonlocal active, peak
        if "/acknowledgements/" not in request["Key"]:
            return original(**request)
        with lock:
            active += 1
            peak = max(peak, active)
        try:
            time.sleep(0.005)
            return original(**request)
        finally:
            with lock:
                active -= 1

    s3.get_object = delayed
    if operation == "load":
        assert source.load(now_ms=200_000) == sorted(
            memory.documents, key=lambda d: d["observation_id"]
        )
    else:
        assert outbox.recover_pending(now_ms=200_000) == {"published": 0, "duplicates": 10}
    assert 1 < peak <= 8
