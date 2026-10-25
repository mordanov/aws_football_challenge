"""Coalesce log deliveries into a retryable, quiet-period learning invocation."""

from __future__ import annotations

import json
from typing import Any, Callable, Protocol
from uuid import uuid4

from .profile_registry import canonical, error_code


class TriggerStore(Protocol):
    def get_object(self, **kwargs: Any) -> dict: ...

    def put_object(self, **kwargs: Any) -> dict: ...


class S3LearningTrigger:
    def __init__(self, client: TriggerStore, *, bucket: str, team_id: str, owner_account: str):
        self.client = client
        self.request = {
            "Bucket": bucket,
            "Key": f"final-agent-learning/{team_id}/work.json",
            "ExpectedBucketOwner": owner_account,
        }

    def read(self) -> tuple[dict, str | None]:
        try:
            response = self.client.get_object(**self.request)
        except Exception as exc:
            if error_code(exc) in ("NoSuchKey", "404"):
                return {"revision": 0, "latest_ms": 0, "token": None, "expires_ms": 0}, None
            raise
        raw = response["Body"].read(16_001)
        if len(raw) > 16_000:
            raise ValueError("oversized S3 learning trigger")
        state = json.loads(raw)
        if (
            type(state.get("revision")) is not int
            or type(state.get("latest_ms")) is not int
            or type(state.get("expires_ms")) is not int
            or not (state.get("token") is None or isinstance(state["token"], str))
        ):
            raise ValueError("invalid S3 learning trigger")
        return state, response["ETag"]

    def _write(self, state: dict, etag: str | None) -> bool:
        try:
            self.client.put_object(
                **self.request,
                Body=canonical(state).encode(),
                ServerSideEncryption="AES256",
                ContentType="application/json",
                **({"IfMatch": etag} if etag else {"IfNoneMatch": "*"}),
            )
        except Exception as exc:
            if error_code(exc) in (
                "PreconditionFailed",
                "ConditionalRequestConflict",
                "412",
                "409",
            ):
                return False
            # A lost acknowledgement is safe only when the exact state won.
            actual, _ = self.read()
            if actual == state:
                return True
            raise
        return True

    def signal(self, *, now_ms: int, invoke: Callable[[str], None]) -> str:
        for _ in range(20):
            state, etag = self.read()
            active = state["token"] is not None and state["expires_ms"] > now_ms
            pending = active and not state.get("dispatched", False)
            next_state = {
                **state,
                "revision": state["revision"] + 1,
                "latest_ms": max(now_ms, state["latest_ms"]),
            }
            if not active:
                next_state.update(token=uuid4().hex, expires_ms=now_ms + 900_000, dispatched=False)
            if not self._write(next_state, etag):
                continue
            if not active or pending:
                invoke(next_state["token"])
                self.dispatched(next_state["token"])
                return "dispatched"
            return "coalesced"
        raise RuntimeError("S3 learning signal contention exceeded limit")

    def dispatched(self, token: str) -> None:
        for _ in range(20):
            state, etag = self.read()
            if state["token"] != token or state.get("dispatched", False):
                return
            if self._write({**state, "dispatched": True}, etag):
                return
        raise RuntimeError("S3 learning dispatch acknowledgement contention")

    def settle(self, token: str, revision: int) -> bool:
        state, etag = self.read()
        if state["token"] != token:
            return True
        if state["revision"] != revision:
            return False
        return self._write({**state, "token": None, "expires_ms": 0, "dispatched": False}, etag)

    def renew(self, token: str, *, now_ms: int) -> bool:
        for _ in range(20):
            state, etag = self.read()
            if state["token"] != token:
                return False
            if self._write({**state, "expires_ms": now_ms + 900_000}, etag):
                return True
        raise RuntimeError("S3 learning continuation contention")
