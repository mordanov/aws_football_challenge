import io
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Lock

import pytest
from botocore.exceptions import ClientError

from agent.s3_trigger import S3LearningTrigger


class Objects:
    def __init__(self):
        self.body = None
        self.version = 0
        self.lock = Lock()
        self.lose_ack = False

    def get_object(self, **request):
        with self.lock:
            if self.body is None:
                raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
            return {"Body": io.BytesIO(self.body), "ETag": str(self.version)}

    def put_object(self, **request):
        with self.lock:
            if (request.get("IfNoneMatch") == "*" and self.body is not None) or request.get(
                "IfMatch", str(self.version)
            ) != str(self.version):
                raise ClientError({"Error": {"Code": "PreconditionFailed"}}, "PutObject")
            self.body = request["Body"]
            self.version += 1
            if self.lose_ack:
                self.lose_ack = False
                raise TimeoutError("committed")
            return {"ETag": str(self.version)}


def setup():
    store = Objects()
    return store, S3LearningTrigger(
        store, bucket="our-bucket", team_id="our_team", owner_account="123456789012"
    )


def test_burst_coalesces_without_losing_later_activity():
    _, trigger = setup()
    calls = []
    trigger.signal(now_ms=1000, invoke=calls.append)
    state, _ = trigger.read()
    trigger.signal(now_ms=2000, invoke=calls.append)
    assert len(calls) == 1
    assert not trigger.settle(calls[0], state["revision"])
    state, _ = trigger.read()
    assert state["latest_ms"] == 2000
    assert trigger.settle(calls[0], state["revision"])
    trigger.signal(now_ms=3000, invoke=calls.append)
    assert len(calls) == 2
    assert calls[0] != calls[1]


def test_dispatch_failure_is_retried_on_same_owner_not_silently_parked():
    _, trigger = setup()
    with pytest.raises(TimeoutError):
        trigger.signal(now_ms=1000, invoke=lambda _: (_ for _ in ()).throw(TimeoutError()))
    pending, _ = trigger.read()
    calls = []
    trigger.signal(now_ms=2000, invoke=calls.append)
    assert calls == [pending["token"]]


def test_uncertain_cas_ack_and_expired_owner_recovery():
    store, trigger = setup()
    store.lose_ack = True
    calls = []
    trigger.signal(now_ms=1000, invoke=calls.append)
    old = calls[0]
    trigger.signal(now_ms=902_000, invoke=calls.append)
    assert calls[1] != old
    assert trigger.settle(old, 1)
    assert not trigger.renew(old, now_ms=903_000)
    assert trigger.read()[0]["token"] == calls[1]


def test_parallel_signals_all_record_activity_and_keep_one_owner():
    _, trigger = setup()
    calls = []
    with ThreadPoolExecutor(max_workers=5) as pool:
        list(pool.map(lambda i: trigger.signal(now_ms=i, invoke=calls.append), range(1000, 1005)))
    state, _ = trigger.read()
    assert state["revision"] == 5
    assert state["latest_ms"] == 1004
    assert len(set(calls)) == 1


def test_corrupt_trigger_fails_explicitly():
    store, trigger = setup()
    store.body = json.dumps({"latest_ms": "unknown"}).encode()
    with pytest.raises(ValueError, match="invalid"):
        trigger.read()
