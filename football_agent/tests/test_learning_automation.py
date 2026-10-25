from __future__ import annotations

import base64
import copy
import gzip
import json

import pytest
from test_profile_registry import Table
from test_runtime import payload

from agent.learning_collector import ObservationOutbox, collect
from agent.profile_registry import canonical
from agent.runtime import Runtime

MATCH = "2d4dd176-ca81-4dd6-88de-e3e99b9a3e97"
GROUPS = tuple(f"/aws/players/seat{i}" for i in range(5))


class OutboxTable(Table):
    def update_item(self, **request):
        key = request["Key"]["pk"]["S"]
        with self.lock:
            assert request["ExpressionAttributeValues"][":sent"] == {"S": "SENT"}
            self.items[key]["status"] = {"S": "SENT"}
        return {}


class Memory:
    def __init__(self):
        self.events = {}
        self.fail = False

    def create_event(self, **request):
        token = request["clientToken"]
        if token in self.events:
            assert self.events[token] == request
        self.events[token] = copy.deepcopy(request)
        if self.fail:
            self.fail = False
            raise TimeoutError("committed but unacknowledged")
        return {"event": {"eventId": token}}


def observation(clock=119.83, tick=101):
    records = []
    runtime = Runtime(emit=records.append)
    try:
        data = json.loads(payload(team=0, pid=2)["prompt"])
        data["gameState"].update(gameTime=clock, tick=tick, matchDuration=120)
        data["gameState"]["score"] = {"home": 1, "away": 1}
        runtime.handle(data, session_id=f"{MATCH}-team0-pos2")
        assert runtime.flush_logs()
    finally:
        runtime.close()
    return records[0]


def event(records, group=GROUPS[2], *, ids=None):
    return {
        "awslogs": {
            "data": base64.b64encode(
                gzip.compress(
                    canonical(
                        {
                            "messageType": "DATA_MESSAGE",
                            "logGroup": group,
                            "logEvents": [
                                {
                                    "id": ids[i] if ids else str(i),
                                    "timestamp": 1792789000000 + i,
                                    "message": canonical(record)
                                    if isinstance(record, dict)
                                    else record,
                                }
                                for i, record in enumerate(records)
                            ],
                        }
                    ).encode()
                )
            ).decode()
        }
    }


def setup():
    table, memory = OutboxTable(), Memory()
    outbox = ObservationOutbox(
        table, memory, table="observations", memory_id="memory-123", team_id="our_team"
    )
    return table, memory, outbox


def documents(memory):
    return [
        json.loads(request["payload"][0]["conversational"]["content"]["text"])
        for request in memory.events.values()
    ]


def test_overtime_is_provisional_and_unknown_opponent_is_not_fabricated():
    _, memory, outbox = setup()
    records = [observation(t, tick=i + 100) for i, t in enumerate((119.83, 121.85, 239.91))]
    result = collect(event(records), outbox, log_groups=GROUPS)
    assert result == {"published": 3, "duplicates": 0, "ignored": 0}
    docs = documents(memory)
    assert [d["content"]["snapshot"]["clock"] for d in docs] == [119.83, 121.85, 239.91]
    for doc in docs:
        assert doc["content"]["evidence_status"] == "provisional"
        assert doc["content"]["opponent_id"] is None
        assert doc["content"]["final_result"] is None
        assert doc["content"]["snapshot"]["duration"] == 120
        assert "observation_id" in doc
        assert "complete" not in doc["content"]


def test_repeated_delivery_and_framework_duplicate_do_not_multiply_memory_evidence():
    _, memory, outbox = setup()
    row = observation()
    batch = event([row, {"message": canonical(row)}, "framework stdout"])
    assert collect(batch, outbox, log_groups=GROUPS)["published"] == 1
    assert collect(batch, outbox, log_groups=GROUPS) == {
        "published": 0,
        "duplicates": 1,
        "ignored": 2,
    }
    # Different CloudWatch transport IDs/timestamps still identify the same observation.
    assert collect(event([row], ids=["new-id"]), outbox, log_groups=GROUPS)["duplicates"] == 1
    assert len(memory.events) == 1


def test_memory_ambiguous_write_keeps_pending_and_retries_exact_persisted_request():
    table, memory, outbox = setup()
    row = observation()
    memory.fail = True
    with pytest.raises(RuntimeError, match="Memory"):
        collect(event([row]), outbox, log_groups=GROUPS)
    assert list(table.items.values())[0]["status"] == {"S": "PENDING"}
    recovered = ObservationOutbox(
        table, memory, table="observations", memory_id="memory-123", team_id="our_team"
    )
    assert (
        collect(event([row], ids=["different-transport"]), recovered, log_groups=GROUPS)[
            "published"
        ]
        == 1
    )
    assert list(table.items.values())[0]["status"] == {"S": "SENT"}
    assert len(memory.events) == 1


def test_conflicting_same_tick_profiles_and_clock_freeze_are_preserved_not_votes():
    _, memory, outbox = setup()
    first = observation(tick=100)
    conflict = copy.deepcopy(first)
    conflict["profile_version"] = "another-profile"
    frozen = observation(tick=101)
    assert collect(event([first, conflict, frozen]), outbox, log_groups=GROUPS)["published"] == 3
    docs = documents(memory)
    assert len({d["content"]["support_key"] for d in docs}) == 1
    assert len({d["observation_id"] for d in docs}) == 3
    assert {d["content"]["profile_version"] for d in docs} == {"baseline", "another-profile"}


def test_redacts_free_text_and_raw_request_but_preserves_source_digest():
    _, memory, outbox = setup()
    row = observation()
    row.update(reason="PRIVATE_TEXT", opponent_id="UNVERIFIED_OPPONENT", prompt="SECRET_REQUEST")
    row["command"]["unrelated_data"] = "PRIVATE_COMMAND_DATA"
    row["snapshot"]["coach"] = "UNKNOWN_COACH_TEXT"
    collect(event([row]), outbox, log_groups=GROUPS)
    raw = canonical(documents(memory))
    for text in (
        "PRIVATE_TEXT",
        "UNVERIFIED_OPPONENT",
        "SECRET_REQUEST",
        "UNKNOWN_COACH_TEXT",
        "PRIVATE_COMMAND_DATA",
    ):
        assert text not in raw
    assert len(documents(memory)[0]["source"]["message_digest"]) == 64


@pytest.mark.parametrize("damage", ["snapshot", "command", "identity", "seat", "input_contract"])
def test_malformed_recognized_records_fail_instead_of_disappearing(damage):
    _, memory, outbox = setup()
    row = observation()
    if damage == "snapshot":
        row["snapshot"]["ball"] = ["invalid", 0]
    elif damage == "command":
        row["command"]["commandType"] = "HACK"
    elif damage == "identity":
        row["snapshot"]["team"] = 1
    elif damage == "seat":
        row["player_id"] = 3
    else:
        row["input_contract"]["coordinate_scale"] = 0
    with pytest.raises(ValueError):
        collect(event([row]), outbox, log_groups=GROUPS)
    assert not memory.events


def test_unknown_identity_is_reported_as_ignored_and_untrusted_group_is_rejected():
    _, memory, outbox = setup()
    row = observation()
    row["match_id"] = None
    assert collect(event([row]), outbox, log_groups=GROUPS)["ignored"] == 1
    with pytest.raises(ValueError, match="log group"):
        collect(event([observation()], group="/untrusted"), outbox, log_groups=GROUPS)
    assert not memory.events


def test_runtime_error_publishes_only_fixed_class_not_exception_or_request():
    _, memory, outbox = setup()
    row = observation()
    row.update(type="error", error="ValueError: PRIVATE_PAYLOAD", tick=10)
    row.pop("snapshot")
    collect(event([row]), outbox, log_groups=GROUPS)
    doc = documents(memory)[0]
    assert doc["content"]["error_class"] == "ValueError"
    assert "PRIVATE_PAYLOAD" not in canonical(doc)
    assert "snapshot" not in doc["content"]


def test_published_events_and_outbox_acknowledgements_match_sdk_shapes():
    import botocore.session
    from botocore.validate import validate_parameters

    session = botocore.session.get_session()
    dynamo_model = session.get_service_model("dynamodb")
    memory_model = session.get_service_model("bedrock-agentcore")
    table, memory = OutboxTable(), Memory()

    class ValidatedClient:
        def __init__(self, client, model):
            self.client, self.model = client, model

        def __getattr__(self, method):
            operation = {
                "get_item": "GetItem",
                "put_item": "PutItem",
                "update_item": "UpdateItem",
                "create_event": "CreateEvent",
            }[method]

            def call(**request):
                validate_parameters(request, self.model.operation_model(operation).input_shape)
                return getattr(self.client, method)(**request)

            return call

    outbox = ObservationOutbox(
        ValidatedClient(table, dynamo_model),
        ValidatedClient(memory, memory_model),
        table="observations",
        memory_id="Our_memory-abcdefghij",
        team_id="our_team",
    )
    assert collect(event([observation()]), outbox, log_groups=GROUPS)["published"] == 1
    assert next(iter(memory.events.values()))["extractionMode"] == "SKIP"
