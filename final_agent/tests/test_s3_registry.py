from __future__ import annotations

import hashlib
import io
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Lock
from uuid import uuid4

import pytest

from agent.model import Profile
from agent.profile_registry import RegistryError, canonical, profile_document
from agent.s3_registry import S3Conflict, S3ObjectStore, S3ProfileRegistry

OWNER = "123456789012"
ROOT = "final-agent-learning/our_team/registry/"
MATCH = "a5ddac07-007f-4d78-8c97-cb7f9b1fcc42"


class S3Error(Exception):
    def __init__(self, code):
        self.response = {"Error": {"Code": code}}


class Bucket:
    """Atomic S3 object boundary; hooks model acknowledgement loss and competing workers."""

    def __init__(self):
        self.items = {}
        self.lock = Lock()
        self.lose_ack = None
        self.before_put = None
        self.after_put = None
        self.denied = False

    def _check(self, request):
        assert request["Bucket"] == "learning-bucket"
        assert request["ExpectedBucketOwner"] == OWNER
        if self.denied:
            raise S3Error("AccessDenied")

    def get_object(self, **request):
        self._check(request)
        with self.lock:
            if request["Key"] not in self.items:
                raise S3Error("NoSuchKey")
            raw, etag = self.items[request["Key"]]
        return {"Body": io.BytesIO(raw), "ETag": etag}

    def put_object(self, **request):
        self._check(request)
        assert request["ServerSideEncryption"] == "AES256"
        assert request["ContentType"] == "application/json"
        assert ("IfMatch" in request) != ("IfNoneMatch" in request)
        raw = request["Body"]
        assert raw == canonical(json.loads(raw)).encode()
        key = request["Key"]
        if self.before_put:
            hook = self.before_put
            self.before_put = None
            hook(request)
        with self.lock:
            old = self.items.get(key)
            if request.get("IfNoneMatch") == "*" and old:
                raise S3Error("PreconditionFailed")
            if "IfMatch" in request and (not old or old[1] != request["IfMatch"]):
                raise S3Error("PreconditionFailed")
            etag = '"' + hashlib.md5(raw).hexdigest() + '"'
            self.items[key] = (raw, etag)
        if self.after_put:
            hook = self.after_put
            self.after_put = None
            hook(request)
        if self.lose_ack == key:
            self.lose_ack = None
            raise TimeoutError("committed; response lost")
        return {"ETag": etag}

    def list_objects_v2(self, **request):
        self._check(request)
        keys = sorted(k for k in self.items if k.startswith(request["Prefix"]))
        start = int(request.get("ContinuationToken", "0"))
        end = start + min(request["MaxKeys"], 2)
        return {
            "Contents": [{"Key": k} for k in keys[start:end]],
            "IsTruncated": end < len(keys),
            "NextContinuationToken": str(end),
        }


def registry(bucket):
    return S3ProfileRegistry(bucket, "learning-bucket", "our_team", owner_account=OWNER)


def setup():
    bucket = Bucket()
    reg = registry(bucket)
    profile = Profile(version="baseline", opponents={})
    digest = reg.bootstrap(profile)
    return bucket, reg, profile, digest


def candidate(profile):
    return replace(
        profile,
        version=profile.version + "-next",
        settings=replace(profile.settings, press_radius=max(12, profile.settings.press_radius - 2)),
    )


def finish(reg, baseline, proposed, *, accepted=True, extra=None):
    evaluation = {
        "baseline_digest": baseline.selection.digest,
        "candidate_digest": profile_document(proposed)[0],
        "accepted": accepted,
        **(extra or {}),
    }
    return reg.finish_attempt(baseline, proposed, "evidence", evaluation)


def promote(reg):
    baseline = reg.active()
    proposed = candidate(baseline.selection.profile)
    assert reg.begin_attempt(baseline, proposed, "evidence")
    return finish(reg, baseline, proposed)


def test_store_conditional_winners_lost_ack_cas_and_bounded_listing():
    bucket = Bucket()
    store = S3ObjectStore(bucket, "learning-bucket", owner_account=OWNER)
    assert store.get("a") is None
    bucket.lose_ack = "a"
    assert store.create("a", {"value": 1}) == {"value": 1}
    assert store.create("a", {"value": 2}) == {"value": 1}
    document, etag = store.get("a")
    bucket.lose_ack = "a"
    assert store.compare_swap("a", {"value": 3}, etag) == {"value": 3}
    with pytest.raises(S3Conflict):
        store.compare_swap("a", document, etag)
    for i in range(5):
        store.create(f"prefix/{i}", {"i": i})
    assert store.list_keys("prefix/", limit=3) == ["prefix/0", "prefix/1", "prefix/2"]
    assert store.get("a")[0] == {"value": 3}


def test_five_parallel_players_share_immutable_pin_across_promotion_and_restart():
    bucket, reg, base, digest = setup()
    selectors = [registry(bucket) for _ in range(5)]
    with ThreadPoolExecutor(max_workers=5) as pool:
        selected = list(pool.map(lambda r: r.resolve(MATCH, 0), selectors))
    assert {s.digest for s in selected} == {digest}
    assert promote(reg)["status"] == "promoted"
    assert reg.active().generation == 2
    for selector in selectors + [registry(bucket)]:
        assert selector.resolve(MATCH, 0).profile == base
        assert selector.resolve(str(uuid4()), 0).profile == candidate(base)
    assert all(key.startswith(ROOT) for key in bucket.items)


def test_pin_uses_validated_active_snapshot_even_when_promotion_races():
    bucket, reg, base, digest = setup()
    active = reg.active()
    proposed = candidate(base)
    assert reg.begin_attempt(active, proposed, "evidence")
    bucket.before_put = lambda request: finish(registry(bucket), active, proposed)
    assert reg.resolve(MATCH, 1).digest == digest
    assert registry(bucket).resolve(MATCH, 1).profile == base
    assert reg.resolve(str(uuid4()), 1).profile == proposed


def test_lost_pin_ack_and_returned_mutation_cannot_rebind_shared_profile():
    bucket, reg, base, digest = setup()
    bucket.lose_ack = ROOT + f"matches/{MATCH}/0.json"
    selection = reg.resolve(MATCH, 0)
    selection.profile.opponents["poison"] = {"anything": 1}
    assert reg.resolve(MATCH, 0).profile == base
    assert registry(bucket).resolve(MATCH, 0).digest == digest
    for _ in range(70):
        reg.resolve(str(uuid4()), 0)
    assert len(reg._cache) <= 64
    assert reg.resolve(MATCH, 0).profile == base


def test_bootstrap_never_rotates_active_and_attempt_owner_does_not_steal():
    bucket, reg, base, digest = setup()
    assert reg.bootstrap(base) == digest
    active = reg.active()
    proposed = candidate(base)
    bucket.lose_ack = ROOT + "attempts/1.json"
    assert reg.begin_attempt(active, proposed, "evidence")
    assert not reg.begin_attempt(active, proposed, "evidence")
    assert not registry(bucket).begin_attempt(active, proposed, "evidence")
    assert finish(reg, active, proposed)["status"] == "promoted"
    with pytest.raises(RegistryError, match="bootstrap"):
        reg.bootstrap(base)
    assert reg.active().selection.profile == proposed


def test_rejected_attempt_is_durable_large_evaluation_is_complete_and_idempotent():
    bucket, reg, base, _ = setup()
    active, proposed = reg.active(), candidate(base)
    assert reg.begin_attempt(active, proposed, "evidence")
    extra = {"report": "detailed evaluation " * 30_000}
    result = finish(reg, active, proposed, accepted=False, extra=extra)
    assert result["status"] == "rejected"
    assert reg.active() == active
    stored = S3ObjectStore(bucket, "learning-bucket", owner_account=OWNER)
    assert stored.get(result["evaluation_ref"])[0]["report"] == extra["report"]
    assert finish(registry(bucket), active, proposed, accepted=False, extra=extra) == result
    assert not reg.begin_attempt(active, proposed, "evidence")


def test_lost_promotion_ack_is_proven_through_successor_generation_chain():
    bucket, reg, base, _ = setup()
    active, proposed = reg.active(), candidate(base)
    assert reg.begin_attempt(active, proposed, "evidence")

    def advance_after_commit(request):
        if request["Key"] == ROOT + "ACTIVE.json":
            promote(registry(bucket))
            raise TimeoutError("first commit succeeded, then successor overtook readback")
        bucket.after_put = advance_after_commit

    bucket.after_put = advance_after_commit
    result = finish(reg, active, proposed)
    assert result["status"] == "promoted"
    assert reg.active().generation == 3
    assert finish(registry(bucket), active, proposed) == result
    assert json.loads(bucket.items[ROOT + "results/1.json"][0]) == result


def test_stale_baseline_never_creates_false_promoted_receipt():
    bucket, reg, base, _ = setup()
    active, proposed = reg.active(), candidate(base)
    assert reg.begin_attempt(active, proposed, "evidence")

    # Another finisher wins with the reserved evaluation before this request's CAS.
    def competing(request):
        if request["Key"] == ROOT + "ACTIVE.json":
            current, etag = reg.store.get(ROOT + "ACTIVE.json")
            changed = dict(current, digest=profile_document(base)[0], generation=2)
            # A valid competing generation may also keep the same profile digest.
            commit = dict(changed)
            commit.pop("commit")
            commit.update(predecessor=current["commit"], receipt=None)
            raw = canonical(commit).encode()
            key = ROOT + "generations/" + hashlib.sha256(raw).hexdigest() + ".json"
            reg.store.create(key, commit)
            changed.update(commit=key, predecessor=current["commit"], receipt=None)
            reg.store.compare_swap(ROOT + "ACTIVE.json", changed, etag)
        else:
            bucket.before_put = competing

    bucket.before_put = competing
    assert finish(reg, active, proposed)["status"] == "stale_baseline"
    assert reg.active().selection.profile == base


def test_concurrent_attempt_reservations_evaluate_only_one_worker():
    bucket, reg, base, _ = setup()
    active, proposed = reg.active(), candidate(base)
    with ThreadPoolExecutor(max_workers=5) as pool:
        winners = list(
            pool.map(
                lambda _: registry(bucket).begin_attempt(active, proposed, "evidence"), range(5)
            )
        )
    assert winners.count(True) == 1
    with pytest.raises(RegistryError, match="reserved"):
        reg.finish_attempt(
            active,
            proposed,
            "different-evidence",
            {
                "baseline_digest": active.selection.digest,
                "candidate_digest": profile_document(proposed)[0],
                "accepted": True,
            },
        )


def test_crash_after_cas_before_result_is_recovered_after_further_promotion():
    bucket, reg, base, _ = setup()
    active, proposed = reg.active(), candidate(base)
    assert reg.begin_attempt(active, proposed, "evidence")

    def crash_before_result(request):
        if request["Key"] == ROOT + "results/1.json":
            raise TimeoutError("process died before writing result")
        bucket.before_put = crash_before_result

    bucket.before_put = crash_before_result
    with pytest.raises(RegistryError):
        finish(reg, active, proposed)
    assert reg.active().generation == 2
    assert ROOT + "results/1.json" not in bucket.items
    promote(registry(bucket))
    assert reg.active().generation == 3
    result = finish(registry(bucket), active, proposed)
    assert result["status"] == "promoted"
    assert json.loads(bucket.items[ROOT + "results/1.json"][0]) == result


def test_uncommitted_generation_proposal_is_not_reported_as_promoted():
    bucket, reg, base, _ = setup()
    active, proposed = reg.active(), candidate(base)
    assert reg.begin_attempt(active, proposed, "evidence")

    def fail_before_cas(request):
        if request["Key"] == ROOT + "ACTIVE.json":
            raise TimeoutError("request never reached S3")
        bucket.before_put = fail_before_cas

    bucket.before_put = fail_before_cas
    with pytest.raises(RegistryError):
        finish(reg, active, proposed)
    assert reg.active() == active
    assert ROOT + "results/1.json" not in bucket.items
    assert finish(registry(bucket), active, proposed)["status"] == "promoted"


def test_transient_conditional_conflict_does_not_persist_a_false_stale_result():
    bucket, reg, base, _ = setup()
    active, proposed = reg.active(), candidate(base)
    assert reg.begin_attempt(active, proposed, "evidence")

    def conflict_before_cas(request):
        if request["Key"] == ROOT + "ACTIVE.json":
            raise S3Error("ConditionalRequestConflict")
        bucket.before_put = conflict_before_cas

    bucket.before_put = conflict_before_cas
    with pytest.raises(RegistryError):
        finish(reg, active, proposed)
    assert reg.active() == active
    assert ROOT + "results/1.json" not in bucket.items
    assert finish(registry(bucket), active, proposed)["status"] == "promoted"


@pytest.mark.parametrize("radius", [13, 12])
def test_press_radius_floor_allows_only_a_strict_decrease(radius):
    bucket = Bucket()
    reg = registry(bucket)
    base = replace(Profile(), settings=replace(Profile().settings, press_radius=radius))
    reg.bootstrap(base)
    active, proposed = reg.active(), candidate(base)
    if radius == 12:
        with pytest.raises(RegistryError, match="allowlist"):
            reg.begin_attempt(active, proposed, "evidence")
    else:
        assert reg.begin_attempt(active, proposed, "evidence")
        assert finish(reg, active, proposed)["status"] == "promoted"
        assert reg.active().selection.profile.settings.press_radius == 12


@pytest.mark.parametrize("damage", ["style", "opponents", "radius", "other_setting", "floor"])
def test_automatic_changes_are_exact_numeric_allowlist(damage):
    _, reg, base, _ = setup()
    if damage == "floor":
        base = replace(base, settings=replace(base.settings, press_radius=12))
    proposed = candidate(base)
    if damage == "style":
        proposed = replace(proposed, style=replace(proposed.style, carry=0.5))
    elif damage == "opponents":
        proposed = replace(
            proposed,
            opponents={
                "rival": {
                    "weight": 0.5,
                    "samples": 2,
                    "settings": base.to_dict()["settings"],
                }
            },
        )
    elif damage == "radius":
        proposed = replace(proposed, settings=replace(proposed.settings, press_radius=15))
    elif damage == "other_setting":
        proposed = replace(proposed, settings=replace(proposed.settings, pass_margin=4))
    with pytest.raises((RegistryError, ValueError)):
        reg.begin_attempt(reg.active(), proposed, "evidence")


@pytest.mark.parametrize("damage", ["baseline", "candidate", "accepted"])
def test_evaluation_requires_exact_digests_and_real_boolean(damage):
    _, reg, base, _ = setup()
    active, proposed = reg.active(), candidate(base)
    assert reg.begin_attempt(active, proposed, "evidence")
    extra = {
        "baseline": {"baseline_digest": "wrong"},
        "candidate": {"candidate_digest": "wrong"},
        "accepted": {"accepted": 1},
    }[damage]
    with pytest.raises(RegistryError, match="evaluation"):
        finish(reg, active, proposed, extra=extra)
    assert reg.active() == active


def test_permission_denied_is_never_not_found_or_winner_success():
    bucket, reg, base, _ = setup()
    bucket.denied = True
    with pytest.raises(RegistryError, match="AccessDenied"):
        reg.resolve(MATCH, 0)
    for call in (
        lambda: reg.store.create(ROOT + "ACTIVE.json", {}),
        lambda: reg.store.compare_swap(ROOT + "ACTIVE.json", {}, '"etag"'),
        lambda: reg.store.list_keys(ROOT),
        lambda: reg.bootstrap(base),
    ):
        with pytest.raises(RegistryError, match="AccessDenied"):
            call()


@pytest.mark.parametrize("damage", ["missing", "digest", "code", "profile"])
def test_corrupt_artifact_cannot_pin_or_fall_back(damage):
    bucket, reg, _, digest = setup()
    key = ROOT + f"profiles/{digest}.json"
    raw, etag = bucket.items[key]
    doc = json.loads(raw)
    if damage == "missing":
        del bucket.items[key]
    else:
        if damage == "digest":
            doc["profile"]["version"] = "tampered"
        elif damage == "code":
            doc["code_generation"] = "future"
        else:
            doc["profile"]["settings"]["press_radius"] = 1000
        bucket.items[key] = (canonical(doc).encode(), etag)
    with pytest.raises(RegistryError):
        reg.resolve(MATCH, 0)
    assert ROOT + f"matches/{MATCH}/0.json" not in bucket.items


def test_s3_requests_conform_to_installed_sdk():
    import botocore.session
    from botocore.validate import validate_parameters

    model = botocore.session.get_session().get_service_model("s3")
    bucket = Bucket()

    class ValidatedClient:
        def __getattr__(self, method):
            operation = {
                "get_object": "GetObject",
                "put_object": "PutObject",
                "list_objects_v2": "ListObjectsV2",
            }[method]

            def call(**request):
                validate_parameters(request, model.operation_model(operation).input_shape)
                return getattr(bucket, method)(**request)

            return call

    reg = registry(ValidatedClient())
    reg.bootstrap(Profile())
    assert promote(reg)["status"] == "promoted"
    reg.resolve(MATCH, 0)
    assert reg.store.list_keys(ROOT, limit=2)
