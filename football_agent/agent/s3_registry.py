"""S3 conditional coordination, immutable match pins and auditable profile promotion.

A pin uses a validated ACTIVE snapshot, not a transaction with ACTIVE: a concurrent
promotion may leave a new match on the previous generation. Its winning pin never
changes. Generation objects are proposals until reachable from ACTIVE; their
predecessor chain proves a successful CAS even after a lost acknowledgement and
subsequent promotions. No listing or locally configured fallback selects a profile.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import OrderedDict
from threading import Lock
from typing import Any, Protocol
from uuid import uuid4

from .model import Profile
from .profile import parse_profile
from .profile_registry import (
    CODE_GENERATION,
    ActiveProfile,
    RegistryError,
    SelectedProfile,
    canonical,
    checked_match,
    error_code,
    profile_document,
    replace_settings_only,
)


class S3Client(Protocol):
    def get_object(self, **kwargs: Any) -> dict: ...

    def put_object(self, **kwargs: Any) -> dict: ...

    def list_objects_v2(self, **kwargs: Any) -> dict: ...


class S3Conflict(RegistryError):
    """A conditional write definitively lost to another object version."""


def _uncertain(exc: Exception) -> bool:
    response = getattr(exc, "response", {})
    status = response.get("ResponseMetadata", {}).get("HTTPStatusCode", 0)
    return (
        isinstance(exc, (TimeoutError, ConnectionError))
        or error_code(exc)
        in {
            "ReadTimeoutError",
            "ConnectTimeoutError",
            "EndpointConnectionError",
            "ConnectionClosedError",
            "RequestTimeout",
            "InternalError",
            "ServiceUnavailable",
            "SlowDown",
        }
        or (isinstance(status, int) and status >= 500)
    )


class S3ObjectStore:
    """Canonical JSON boundary. Only conditional/ambiguous writes use readback.

    ``create`` returns the immutable winner, including a different writer's value.
    ``compare_swap`` returns this document or raises; it never returns a losing
    document as success. An ambiguous CAS may require application-level ancestry
    recovery when a subsequent writer has already replaced the value.
    """

    def __init__(self, client: S3Client, bucket: str, *, owner_account: str):
        if not isinstance(bucket, str) or not re.fullmatch(
            r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", bucket
        ):
            raise ValueError("invalid S3 bucket")
        if not isinstance(owner_account, str) or not re.fullmatch(r"[0-9]{12}", owner_account):
            raise ValueError("invalid S3 expected owner account")
        self.client = client
        self.bucket = bucket
        self.owner_account = owner_account

    def _request(self) -> dict:
        return {"Bucket": self.bucket, "ExpectedBucketOwner": self.owner_account}

    def get(self, key: str) -> tuple[dict, str] | None:
        try:
            response = self.client.get_object(**self._request(), Key=key)
        except Exception as exc:
            if error_code(exc) in {"NoSuchKey", "404", "NotFound"}:
                return None
            raise RegistryError(f"S3 read failed: {error_code(exc)}") from exc
        body = response.get("Body")
        try:
            if body is None:
                raise ValueError("missing response body")
            raw = body.read()
            document = json.loads(raw)
            etag = response["ETag"]
            if not isinstance(document, dict) or not isinstance(etag, str) or not etag:
                raise ValueError("invalid JSON object or ETag")
            if raw != canonical(document).encode():
                raise ValueError("noncanonical JSON object")
            return document, etag
        except Exception as exc:
            raise RegistryError(f"S3 object is invalid: {error_code(exc)}") from exc
        finally:
            if body is not None and hasattr(body, "close"):
                body.close()

    def _write(self, key: str, document: dict, condition: dict) -> None:
        if not isinstance(document, dict):
            raise ValueError("S3 document must be an object")
        self.client.put_object(
            **self._request(),
            Key=key,
            Body=canonical(document).encode(),
            ContentType="application/json",
            ServerSideEncryption="AES256",
            **condition,
        )

    def create(self, key: str, document: dict) -> dict:
        try:
            self._write(key, document, {"IfNoneMatch": "*"})
        except Exception as exc:
            if error_code(exc) in {
                "PreconditionFailed",
                "412",
                "ConditionalRequestConflict",
                "409",
            } or _uncertain(exc):
                existing = self.get(key)
                if existing is not None:
                    return existing[0]
            raise RegistryError(f"S3 create failed: {error_code(exc)}") from exc
        return json.loads(canonical(document))

    def compare_swap(self, key: str, document: dict, etag: str) -> dict:
        try:
            self._write(key, document, {"IfMatch": etag})
        except Exception as exc:
            conflict = error_code(exc) in {
                "PreconditionFailed",
                "412",
                "ConditionalRequestConflict",
                "409",
            }
            if conflict or _uncertain(exc):
                existing = self.get(key)
                if existing is not None and existing[0] == document:
                    return existing[0]
                if conflict:
                    raise S3Conflict("S3 compare-and-swap lost") from exc
            raise RegistryError(f"S3 compare-and-swap failed: {error_code(exc)}") from exc
        return json.loads(canonical(document))

    def list_keys(self, prefix: str, *, limit: int = 1000) -> list[str]:
        """Return at most ``limit`` keys (hard cap 10,000), paginating only as needed."""
        if type(limit) is not int or not 1 <= limit <= 10_000:
            raise ValueError("S3 listing limit must be in [1, 10000]")
        keys: list[str] = []
        token = None
        seen = set()
        while len(keys) < limit:
            request = {**self._request(), "Prefix": prefix, "MaxKeys": min(1000, limit - len(keys))}
            if token is not None:
                request["ContinuationToken"] = token
            try:
                response = self.client.list_objects_v2(**request)
            except Exception as exc:
                raise RegistryError(f"S3 list failed: {error_code(exc)}") from exc
            for item in response.get("Contents", []):
                key = item.get("Key")
                if not isinstance(key, str) or not key.startswith(prefix):
                    raise RegistryError("S3 listing returned invalid key")
                keys.append(key)
                if len(keys) == limit:
                    return keys
            if not response.get("IsTruncated"):
                return keys
            token = response.get("NextContinuationToken")
            if not isinstance(token, str) or not token or token in seen:
                raise RegistryError("S3 listing returned invalid continuation token")
            seen.add(token)
        return keys


class S3ProfileRegistry:
    def __init__(
        self,
        client: S3Client,
        bucket: str,
        team_id: str,
        *,
        owner_account: str,
        prefix: str = "final-agent-learning",
    ):
        if not isinstance(team_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", team_id):
            raise ValueError("invalid registry team_id")
        if not isinstance(prefix, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", prefix):
            raise ValueError("invalid registry prefix")
        self.store = S3ObjectStore(client, bucket, owner_account=owner_account)
        self.prefix = f"{prefix}/{team_id}/registry/"
        self._cache: OrderedDict[str, tuple[str, str]] = OrderedDict()
        self._lock = Lock()

    def _key(self, suffix: str) -> str:
        return self.prefix + suffix

    def _required(self, key: str) -> dict:
        stored = self.store.get(key)
        if stored is None:
            raise RegistryError(f"missing registry object: {key}")
        return stored[0]

    def _immutable(self, key: str, document: dict) -> str:
        if self.store.create(key, document) != document:
            raise RegistryError("immutable registry document conflicts")
        return key

    def _artifact(self, kind: str, document: dict) -> str:
        digest = hashlib.sha256(canonical(document).encode()).hexdigest()
        return self._immutable(self._key(f"{kind}/{digest}.json"), document)

    def _load(self, digest: str) -> SelectedProfile:
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise RegistryError("invalid profile digest in registry")
        document = self._required(self._key(f"profiles/{digest}.json"))
        try:
            profile = parse_profile(document["profile"])
            expected_digest, raw = profile_document(profile)
            if expected_digest != digest or canonical(document) != raw:
                raise ValueError("profile content or code generation mismatch")
        except (KeyError, TypeError, ValueError) as exc:
            raise RegistryError("missing, incompatible or corrupt profile document") from exc
        return SelectedProfile(profile, digest, CODE_GENERATION)

    def _commit(self, key: str) -> dict:
        if not isinstance(key, str) or not re.fullmatch(
            re.escape(self._key("generations/")) + r"[0-9a-f]{64}\.json", key
        ):
            raise RegistryError("invalid generation reference")
        document = self._required(key)
        digest = hashlib.sha256(canonical(document).encode()).hexdigest()
        if key != self._key(f"generations/{digest}.json"):
            raise RegistryError("generation content digest mismatch")
        return document

    def _head(self) -> tuple[dict, str]:
        stored = self.store.get(self._key("ACTIVE.json"))
        if stored is None:
            raise RegistryError("ACTIVE is missing or invalid")
        head, etag = stored
        try:
            if type(head["generation"]) is not int or head["generation"] < 1:
                raise ValueError("invalid generation")
            commit = self._commit(head["commit"])
            if commit != {key: value for key, value in head.items() if key != "commit"}:
                raise ValueError("ACTIVE does not match generation commit")
            self._load(head["digest"])
        except (KeyError, TypeError, ValueError) as exc:
            raise RegistryError("ACTIVE is missing or invalid") from exc
        return head, etag

    def bootstrap(self, profile: Profile) -> str:
        """Explicit initialization; never rotate an existing ACTIVE."""
        digest, raw = profile_document(profile)
        self._immutable(self._key(f"profiles/{digest}.json"), json.loads(raw))
        initial = {"digest": digest, "generation": 1, "predecessor": None, "receipt": None}
        key = self._artifact("generations", initial)
        existing = self.store.create(self._key("ACTIVE.json"), dict(initial, commit=key))
        if existing.get("digest") != digest:
            raise RegistryError(
                "ACTIVE already selects another profile; bootstrap cannot rotate it"
            )
        self._head()
        return digest

    def active(self) -> ActiveProfile:
        head, _ = self._head()
        return ActiveProfile(self._load(head["digest"]), head["generation"])

    @staticmethod
    def _validate_candidate(baseline: ActiveProfile, candidate: Profile) -> str:
        digest, _ = profile_document(candidate)
        if (
            type(baseline.generation) is not int
            or baseline.generation < 1
            or profile_document(baseline.selection.profile)[0] != baseline.selection.digest
            or baseline.selection.code_generation != CODE_GENERATION
        ):
            raise RegistryError("invalid evaluation baseline")
        before = baseline.selection.profile
        if (
            candidate.style != before.style
            or candidate.coach_policy != before.coach_policy
            or candidate.opponents != before.opponents
            or not replace_settings_only(before, candidate)
        ):
            raise RegistryError("automatic profile change is outside numeric allowlist")
        return digest

    @staticmethod
    def _attempt(baseline: ActiveProfile, digest: str, evidence_digest: str) -> dict:
        if (
            not isinstance(evidence_digest, str)
            or not evidence_digest
            or len(evidence_digest) > 256
        ):
            raise RegistryError("invalid evidence digest")
        return {
            "baseline_digest": baseline.selection.digest,
            "candidate_digest": digest,
            "generation": baseline.generation,
            "evidence_digest": evidence_digest,
        }

    def begin_attempt(
        self, baseline: ActiveProfile, candidate: Profile, evidence_digest: str
    ) -> bool:
        digest = self._validate_candidate(baseline, candidate)
        expected = self._attempt(baseline, digest, evidence_digest)
        head, _ = self._head()
        if (head["generation"], head["digest"]) != (baseline.generation, baseline.selection.digest):
            return False
        document = dict(expected, owner=uuid4().hex)
        return (
            self.store.create(self._key(f"attempts/{baseline.generation}.json"), document)
            == document
        )

    def _committed(self, head: dict, proposed: str, generation: int) -> bool:
        """Only ACTIVE reachability proves commitment; orphan proposals prove nothing."""
        key = head["commit"]
        current_generation = head["generation"]
        while current_generation >= generation:
            document = self._commit(key)
            if document.get("generation") != current_generation:
                raise RegistryError("broken generation ancestry")
            if key == proposed:
                return True
            if current_generation == generation:
                return False
            key = document.get("predecessor")
            current_generation -= 1
        return False

    def finish_attempt(
        self, baseline: ActiveProfile, candidate: Profile, evidence_digest: str, evaluation: dict
    ) -> dict:
        digest = self._validate_candidate(baseline, candidate)
        expected = self._attempt(baseline, digest, evidence_digest)
        if (
            not isinstance(evaluation, dict)
            or evaluation.get("baseline_digest") != baseline.selection.digest
            or evaluation.get("candidate_digest") != digest
            or type(evaluation.get("accepted")) is not bool
        ):
            raise RegistryError("evaluation is not bound to exact candidate and baseline")
        attempt = self._required(self._key(f"attempts/{baseline.generation}.json"))
        if {k: v for k, v in attempt.items() if k != "owner"} != expected:
            raise RegistryError("evaluation does not match reserved attempt")
        evaluation_ref = self._artifact("evaluations", evaluation)
        proposal = dict(expected, evaluation_ref=evaluation_ref, status="proposed")
        proposal_ref = self._immutable(self._key(f"proposals/{baseline.generation}.json"), proposal)
        result_key = self._key(f"results/{baseline.generation}.json")
        receipt = dict(
            expected, evaluation_ref=evaluation_ref, proposal_ref=proposal_ref, status="rejected"
        )
        existing = self.store.get(result_key)
        if existing is not None:
            if existing[0].get("status") not in {"rejected", "promoted", "stale_baseline"} or {
                k: v for k, v in existing[0].items() if k != "status"
            } != {k: v for k, v in receipt.items() if k != "status"}:
                raise RegistryError("evaluation receipt conflicts")
            return existing[0]
        if evaluation["accepted"]:
            _, raw = profile_document(candidate)
            self._immutable(self._key(f"profiles/{digest}.json"), json.loads(raw))
            head, etag = self._head()
            # Read the original predecessor from the current committed ancestry on replay.
            predecessor = head["commit"]
            original = head
            while original["generation"] > baseline.generation:
                previous_generation = original["generation"] - 1
                predecessor = original["predecessor"]
                original = self._commit(predecessor)
                if original.get("generation") != previous_generation:
                    raise RegistryError("broken generation ancestry")
            if (
                original.get("generation") != baseline.generation
                or original.get("digest") != baseline.selection.digest
            ):
                raise RegistryError("baseline is not in committed generation ancestry")
            commit = {
                "digest": digest,
                "generation": baseline.generation + 1,
                "predecessor": predecessor,
                "receipt": proposal_ref,
                "baseline_digest": baseline.selection.digest,
                "baseline_generation": baseline.generation,
            }
            commit_ref = self._artifact("generations", commit)
            promoted = self._committed(head, commit_ref, baseline.generation + 1)
            if not promoted and head["generation"] == baseline.generation:
                try:
                    self.store.compare_swap(
                        self._key("ACTIVE.json"), dict(commit, commit=commit_ref), etag
                    )
                    promoted = True
                except RegistryError as exc:
                    if not isinstance(exc, S3Conflict) and (
                        not isinstance(exc.__cause__, Exception) or not _uncertain(exc.__cause__)
                    ):
                        raise
                    # Never turn permission/service failures into an apparent promotion
                    # unless a durable ACTIVE ancestry proves that exact CAS succeeded.
                    current, _ = self._head()
                    promoted = self._committed(current, commit_ref, baseline.generation + 1)
                    if not promoted and (
                        not isinstance(exc, S3Conflict)
                        or current["generation"] == baseline.generation
                    ):
                        raise
            receipt["status"] = "promoted" if promoted else "stale_baseline"
        self._immutable(result_key, receipt)
        return receipt

    def resolve(self, match_id: str, side: int) -> SelectedProfile:
        checked_match(match_id, side)
        key = self._key(f"matches/{match_id}/{side}.json")
        with self._lock:
            cached = self._cache.get(key)
            if cached is not None:
                self._cache.move_to_end(key)
        if cached is not None:
            digest, raw = cached
            return SelectedProfile(parse_profile(json.loads(raw)), digest, CODE_GENERATION)
        stored = self.store.get(key)
        if stored is None:
            head, _ = self._head()
            pin = self.store.create(
                key, {"digest": head["digest"], "generation": head["generation"]}
            )
        else:
            pin = stored[0]
        try:
            if type(pin["generation"]) is not int or pin["generation"] < 1:
                raise ValueError("invalid pin generation")
            selected = self._load(pin["digest"])
        except (KeyError, TypeError, ValueError) as exc:
            raise RegistryError("invalid match profile pin") from exc
        raw = canonical(selected.profile.to_dict())
        with self._lock:
            self._cache[key] = selected.digest, raw
            self._cache.move_to_end(key)
            while len(self._cache) > 64:
                self._cache.popitem(last=False)
        return SelectedProfile(
            parse_profile(json.loads(raw)), selected.digest, selected.code_generation
        )
