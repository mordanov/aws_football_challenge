from __future__ import annotations

import copy
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Lock

import pytest
from test_runtime import payload

from agent.model import Profile, Settings
from agent.profile_registry import (
    CODE_GENERATION,
    DynamoProfileRegistry,
    RegistryError,
    profile_document,
)
from agent.runtime import Runtime

MATCH = "a5ddac07-007f-4d78-8c97-cb7f9b1fcc42"
NEXT = "2d4dd176-ca81-4dd6-88de-e3e99b9a3e97"


class ConditionalFailure(Exception):
    response = {"Error": {"Code": "ConditionalCheckFailedException"}}


class TransactionConflict(Exception):
    response = {"Error": {"Code": "TransactionCanceledException"}}


class Table:
    """Thread-safe external DynamoDB boundary, including lost write acknowledgements."""

    def __init__(self):
        self.items = {}
        self.lock = Lock()
        self.lose_ack = False
        self.before_transaction = None

    def get_item(self, **request):
        assert request["ConsistentRead"] is True
        with self.lock:
            item = self.items.get(request["Key"]["pk"]["S"])
            return {"Item": copy.deepcopy(item)} if item else {}

    def put_item(self, **request):
        item = request["Item"]
        with self.lock:
            if item["pk"]["S"] in self.items:
                raise ConditionalFailure
            assert request["ConditionExpression"] == "attribute_not_exists(pk)"
            self.items[item["pk"]["S"]] = copy.deepcopy(item)
        return {}

    def transact_write_items(self, **request):
        if self.before_transaction:
            hook, self.before_transaction = self.before_transaction, None
            hook()
        check, write = request["TransactItems"]
        check, write = check["ConditionCheck"], write["Put"]
        with self.lock:
            current = self.items[check["Key"]["pk"]["S"]]
            expected = check["ExpressionAttributeValues"]
            if current["generation"] != expected[":generation"]:
                raise TransactionConflict
            if current["digest"] != expected[":digest"]:
                raise TransactionConflict
            key = write["Item"]["pk"]["S"]
            if key in self.items:
                raise TransactionConflict
            self.items[key] = copy.deepcopy(write["Item"])
            if self.lose_ack:
                self.lose_ack = False
                raise TimeoutError("write succeeded; response lost")
        return {}


def setup():
    table = Table()
    registry = DynamoProfileRegistry(table, "registry", "our_team")
    baseline = Profile(version="baseline-a")
    digest = registry.bootstrap(baseline)
    return table, registry, baseline, digest


def rotate(table, profile):
    digest, document = profile_document(profile)
    table.items[f"TEAM#our_team#PROFILE#{digest}"] = {
        "pk": {"S": f"TEAM#our_team#PROFILE#{digest}"},
        "document": {"S": document},
    }
    active = table.items["TEAM#our_team#ACTIVE"]
    active["generation"] = {"N": str(int(active["generation"]["N"]) + 1)}
    active["digest"] = {"S": digest}
    return digest


def test_five_concurrent_players_keep_one_profile_across_rotation_and_restart():
    table, _, base, digest = setup()
    selectors = [DynamoProfileRegistry(table, "registry", "our_team") for _ in range(5)]
    with ThreadPoolExecutor(max_workers=5) as pool:
        first = list(pool.map(lambda selector: selector.resolve(MATCH, 0), selectors))
    assert {x.digest for x in first} == {digest}
    candidate = replace(base, version="candidate-b", settings=Settings(press_radius=24))
    candidate_digest = rotate(table, candidate)
    for selector in selectors:
        assert selector.resolve(MATCH, 0).profile == base
        assert selector.resolve(NEXT, 0).digest == candidate_digest
    recovered = DynamoProfileRegistry(table, "registry", "our_team")
    assert recovered.resolve(MATCH, 0).digest == digest
    assert recovered.resolve(NEXT, 0).profile == candidate


def test_rotation_between_active_read_and_pin_cannot_commit_stale_pointer():
    table, registry, base, _ = setup()
    candidate = replace(base, version="new")
    table.before_transaction = lambda: rotate(table, candidate)
    selected = registry.resolve(MATCH, 1)
    assert selected.profile == candidate
    assert DynamoProfileRegistry(table, "registry", "our_team").resolve(MATCH, 1) == selected


def test_uncertain_transaction_reloads_winner_without_rebinding():
    table, registry, base, digest = setup()
    table.lose_ack = True
    assert registry.resolve(MATCH, 0).digest == digest
    rotate(table, replace(base, version="later"))
    assert DynamoProfileRegistry(table, "registry", "our_team").resolve(MATCH, 0).profile == base


@pytest.mark.parametrize("damage", ["digest", "code", "invalid_profile", "missing"])
def test_bad_profile_never_returns_local_baseline(damage):
    table, registry, _, digest = setup()
    item = table.items[f"TEAM#our_team#PROFILE#{digest}"]
    if damage == "missing":
        del table.items[f"TEAM#our_team#PROFILE#{digest}"]
    else:
        doc = json.loads(item["document"]["S"])
        if damage == "code":
            doc["code_generation"] = "future"
        elif damage == "invalid_profile":
            doc["profile"]["settings"]["press_radius"] = 1000
        else:
            doc["profile"]["version"] = "tampered"
        item["document"]["S"] = json.dumps(doc)
    with pytest.raises(RegistryError):
        registry.resolve(MATCH, 0)


def test_bootstrap_is_idempotent_but_does_not_reset_existing_active():
    table, registry, base, digest = setup()
    assert registry.bootstrap(base) == digest
    candidate_digest = rotate(table, replace(base, version="different"))
    with pytest.raises(RegistryError):
        registry.bootstrap(base)
    assert registry.resolve(NEXT, 0).digest == candidate_digest


def test_runtime_uses_pinned_profile_without_mutating_other_matches():
    table, registry, base, digest = setup()
    records = []
    runtime = Runtime(base, profile_selector=registry, emit=records.append)
    try:
        runtime.handle(payload(), session_id=f"{MATCH}-team0-pos2")
        candidate = replace(base, version="new", settings=Settings(pass_margin=4.5))
        new_digest = rotate(table, candidate)
        for mid in (NEXT, MATCH):
            runtime.handle(payload(), session_id=f"{mid}-team0-pos2")
        assert runtime.flush_logs()
        assert [r["profile_version"] for r in records] == ["baseline-a", "new", "baseline-a"]
        assert [r["profile_digest"] for r in records] == [digest, new_digest, digest]
        assert all(r["code_generation"] == CODE_GENERATION for r in records)
        assert runtime.profile == base
    finally:
        runtime.close()


def test_unbound_and_misrouted_sessions_fail_but_fitness_keeps_static_profile():
    _, registry, base, _ = setup()
    records = []
    runtime = Runtime(base, profile_selector=registry, emit=records.append)
    try:
        for session in (None, "unknown", f"{MATCH}-team1-pos2", f"{MATCH}-team0-pos3"):
            with pytest.raises(RegistryError):
                runtime.handle(payload(), session_id=session)
        runtime.handle(payload(), session_id="scrimmage-drill-known-pos2")
        assert runtime.flush_logs()
        assert records[-1]["profile_version"] == base.version
        assert all(r["type"] == "profile_selection_error" for r in records[:-1])
    finally:
        runtime.close()


def test_registry_requests_conform_to_real_dynamodb_sdk_shapes():
    import botocore.session
    from botocore.validate import validate_parameters

    model = botocore.session.get_session().get_service_model("dynamodb")
    table = Table()

    class ValidatedClient:
        def __getattr__(self, method):
            operation = {
                "get_item": "GetItem",
                "put_item": "PutItem",
                "transact_write_items": "TransactWriteItems",
            }[method]

            def call(**request):
                validate_parameters(request, model.operation_model(operation).input_shape)
                return getattr(table, method)(**request)

            return call

    registry = DynamoProfileRegistry(ValidatedClient(), "registry", "our_team")
    digest = registry.bootstrap(Profile())
    assert registry.resolve(MATCH, 0).digest == digest


def test_rejected_profile_binding_never_attributes_errors_to_configured_match():
    _, registry, base, _ = setup()
    records = []
    runtime = Runtime(base, match_id=MATCH, profile_selector=registry, emit=records.append)
    try:
        for session in ("unknown", f"{NEXT}-team0-pos2", f"{MATCH}-team1-pos2"):
            with pytest.raises(RegistryError):
                runtime.handle(payload(), session_id=session)
        assert runtime.flush_logs()
        assert [r["match_id"] for r in records] == [None, NEXT, None]
    finally:
        runtime.close()
