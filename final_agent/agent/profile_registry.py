"""Opt-in, match-scoped profile selection; no Memory or model calls."""

from __future__ import annotations

import hashlib
import json
import re
from collections import OrderedDict
from dataclasses import dataclass
from threading import Lock
from typing import Any, Protocol
from uuid import UUID, uuid4

from .model import Profile
from .profile import parse_profile

CODE_GENERATION = "match_profiles_rush_v2"


class RegistryError(RuntimeError):
    """No valid common profile could be established; never select a local fallback."""


@dataclass(frozen=True)
class SelectedProfile:
    profile: Profile
    digest: str
    code_generation: str


@dataclass(frozen=True)
class ActiveProfile:
    selection: SelectedProfile
    generation: int


class ProfileSelector(Protocol):
    def resolve(self, match_id: str, side: int) -> SelectedProfile: ...


class DynamoClient(Protocol):
    def get_item(self, **kwargs: Any) -> dict: ...

    def put_item(self, **kwargs: Any) -> dict: ...

    def transact_write_items(self, **kwargs: Any) -> dict: ...


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def error_code(exc: Exception) -> str:
    response = getattr(exc, "response", None)
    if isinstance(response, dict) and isinstance(response.get("Error"), dict):
        return str(response["Error"].get("Code", "UNKNOWN"))
    return type(exc).__name__


def checked_match(match_id: str, side: int) -> None:
    try:
        valid = str(UUID(match_id)) == match_id
    except (ValueError, TypeError, AttributeError):
        valid = False
    if not valid or type(side) is not int or side not in (0, 1):
        raise RegistryError("profile pin requires a canonical match UUID and team side")


def profile_document(profile: Profile) -> tuple[str, str]:
    checked = parse_profile(profile.to_dict())
    document = canonical({"code_generation": CODE_GENERATION, "profile": checked.to_dict()})
    if len(document.encode()) > 32_000:
        raise RegistryError("profile document exceeds 32000 bytes")
    return hashlib.sha256(document.encode()).hexdigest(), document


class DynamoProfileRegistry:
    def __init__(self, client: DynamoClient, table: str, team_id: str):
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", team_id):
            raise ValueError("invalid registry team_id")
        if not re.fullmatch(r"[A-Za-z0-9_.-]{3,255}", table):
            raise ValueError("invalid registry table")
        self.client = client
        self.table = table
        self.prefix = f"TEAM#{team_id}#"
        self._cache: OrderedDict[str, SelectedProfile] = OrderedDict()
        self._lock = Lock()

    def _read(self, key: str) -> dict:
        try:
            return self.client.get_item(
                TableName=self.table, Key={"pk": {"S": key}}, ConsistentRead=True
            ).get("Item", {})
        except Exception as exc:
            raise RegistryError(f"registry read failed: {error_code(exc)}") from exc

    def _create(self, item: dict) -> dict:
        try:
            self.client.put_item(
                TableName=self.table, Item=item, ConditionExpression="attribute_not_exists(pk)"
            )
        except Exception as exc:
            existing = self._read(item["pk"]["S"])
            if existing:
                return existing
            raise RegistryError(f"registry create failed: {error_code(exc)}") from exc
        return item

    def bootstrap(self, profile: Profile) -> str:
        """Explicit operator initialization only; never rotates an existing pointer."""
        digest, document = profile_document(profile)
        stored = self._create(
            {"pk": {"S": self.prefix + "PROFILE#" + digest}, "document": {"S": document}}
        )
        if stored.get("document") != {"S": document}:
            raise RegistryError("immutable profile document conflicts")
        active = self._create(
            {
                "pk": {"S": self.prefix + "ACTIVE"},
                "digest": {"S": digest},
                "generation": {"N": "1"},
            }
        )
        if active.get("digest") != {"S": digest}:
            raise RegistryError(
                "ACTIVE already selects another profile; bootstrap cannot rotate it"
            )
        return digest

    def active(self) -> ActiveProfile:
        item = self._read(self.prefix + "ACTIVE")
        try:
            generation = int(item["generation"]["N"])
            if generation < 1:
                raise ValueError("invalid generation")
            return ActiveProfile(self._load(item["digest"]["S"]), generation)
        except (KeyError, TypeError, ValueError) as exc:
            raise RegistryError("ACTIVE is missing or invalid") from exc

    def begin_attempt(
        self, baseline: ActiveProfile, candidate: Profile, evidence_digest: str
    ) -> bool:
        """At most one automatic evaluation per active generation, even after failure."""
        digest, _ = profile_document(candidate)
        key = self.prefix + f"ATTEMPT#{baseline.generation}"
        item = {
            "pk": {"S": key},
            "owner": {"S": uuid4().hex},
            "document": {
                "S": canonical(
                    {
                        "baseline_digest": baseline.selection.digest,
                        "candidate_digest": digest,
                        "evidence_digest": evidence_digest,
                        "generation": baseline.generation,
                    }
                )
            },
        }
        try:
            self.client.put_item(
                TableName=self.table, Item=item, ConditionExpression="attribute_not_exists(pk)"
            )
        except Exception as exc:
            existing = self._read(key)
            if existing:
                return existing == item
            raise RegistryError(f"attempt reservation failed: {error_code(exc)}") from exc
        return True

    def finish_attempt(
        self, baseline: ActiveProfile, candidate: Profile, evidence_digest: str, evaluation: dict
    ) -> dict:
        digest, document = profile_document(candidate)
        if (
            candidate.style != baseline.selection.profile.style
            or candidate.coach_policy != baseline.selection.profile.coach_policy
            or candidate.opponents != baseline.selection.profile.opponents
            or replace_settings_only(baseline.selection.profile, candidate) is False
        ):
            raise RegistryError("automatic profile change is outside numeric allowlist")
        if (
            evaluation.get("baseline_digest") != baseline.selection.digest
            or evaluation.get("candidate_digest") != digest
            or type(evaluation.get("accepted")) is not bool
        ):
            raise RegistryError("evaluation is not bound to exact candidate and baseline")
        raw_evaluation = canonical(evaluation)
        stored_evaluation = evaluation
        if len(raw_evaluation.encode()) > 200_000:
            evaluation_digest = hashlib.sha256(raw_evaluation.encode()).hexdigest()
            keys = []
            for offset in range(0, len(raw_evaluation), 100_000):
                chunk_key = self.prefix + f"EVALUATION#{evaluation_digest}#{offset // 100_000}"
                chunk = {
                    "pk": {"S": chunk_key},
                    "document": {"S": raw_evaluation[offset : offset + 100_000]},
                }
                if self._create(chunk) != chunk:
                    raise RegistryError("immutable evaluation chunk conflicts")
                keys.append(chunk_key)
            stored_evaluation = {
                "storage": "immutable_chunks_v1",
                "accepted": evaluation["accepted"],
                "sha256": evaluation_digest,
                "chunk_keys": keys,
            }
        receipt = {
            "baseline_digest": baseline.selection.digest,
            "candidate_digest": digest,
            "generation": baseline.generation,
            "evidence_digest": evidence_digest,
            "evaluation": stored_evaluation,
            "status": "promoted" if evaluation["accepted"] else "rejected",
        }
        key = self.prefix + f"RESULT#{baseline.generation}"
        receipt_item = {"pk": {"S": key}, "document": {"S": canonical(receipt)}}
        if len(receipt_item["document"]["S"].encode()) > 300_000:
            raise RegistryError("evaluation receipt exceeds durable byte limit")
        attempt = self._read(self.prefix + f"ATTEMPT#{baseline.generation}")
        expected = canonical(
            {
                "baseline_digest": baseline.selection.digest,
                "candidate_digest": digest,
                "evidence_digest": evidence_digest,
                "generation": baseline.generation,
            }
        )
        if attempt.get("document") != {"S": expected}:
            raise RegistryError("evaluation does not match reserved attempt")
        if not evaluation["accepted"]:
            stored = self._create(receipt_item)
            if stored != receipt_item:
                raise RegistryError("evaluation receipt conflicts")
            return receipt
        stored = self._create(
            {"pk": {"S": self.prefix + "PROFILE#" + digest}, "document": {"S": document}}
        )
        if stored.get("document") != {"S": document}:
            raise RegistryError("candidate immutable document conflicts")
        try:
            self.client.transact_write_items(
                TransactItems=[
                    {
                        "Update": {
                            "TableName": self.table,
                            "Key": {"pk": {"S": self.prefix + "ACTIVE"}},
                            "UpdateExpression": "SET #digest = :new, #generation = :next",
                            "ConditionExpression": "#digest = :old AND #generation = :generation",
                            "ExpressionAttributeNames": {
                                "#digest": "digest",
                                "#generation": "generation",
                            },
                            "ExpressionAttributeValues": {
                                ":new": {"S": digest},
                                ":old": {"S": baseline.selection.digest},
                                ":generation": {"N": str(baseline.generation)},
                                ":next": {"N": str(baseline.generation + 1)},
                            },
                        }
                    },
                    {
                        "Put": {
                            "TableName": self.table,
                            "Item": receipt_item,
                            "ConditionExpression": "attribute_not_exists(pk)",
                        }
                    },
                ]
            )
        except Exception as exc:
            stored = self._read(key)
            if stored == receipt_item:
                return receipt
            if error_code(exc) == "TransactionCanceledException" and (self.active() != baseline):
                receipt["status"] = "stale_baseline"
                receipt_item["document"] = {"S": canonical(receipt)}
                stored = self._create(receipt_item)
                if stored != receipt_item:
                    raise RegistryError("stale-baseline receipt conflicts") from exc
                return receipt
            raise RegistryError(f"promotion failed: {error_code(exc)}") from exc
        return receipt

    def _load(self, digest: str) -> SelectedProfile:
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise RegistryError("invalid profile digest in registry")
        item = self._read(self.prefix + "PROFILE#" + digest)
        try:
            raw = item["document"]["S"]
            if not isinstance(raw, str) or len(raw.encode()) > 32_000:
                raise ValueError("invalid document size")
            if hashlib.sha256(raw.encode()).hexdigest() != digest:
                raise ValueError("profile digest mismatch")
            document = json.loads(raw)
            if set(document) != {"code_generation", "profile"}:
                raise ValueError("invalid profile document fields")
            if document["code_generation"] != CODE_GENERATION:
                raise ValueError("unsupported code generation")
            profile = parse_profile(document["profile"])
            if profile_document(profile) != (digest, raw):
                raise ValueError("noncanonical profile document")
        except (KeyError, TypeError, ValueError) as exc:
            raise RegistryError("missing, incompatible or corrupt profile document") from exc
        return SelectedProfile(profile, digest, CODE_GENERATION)

    def resolve(self, match_id: str, side: int) -> SelectedProfile:
        checked_match(match_id, side)
        key = self.prefix + f"MATCH#{match_id}#{side}"
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                selected = self._cache[key]
                return SelectedProfile(
                    parse_profile(selected.profile.to_dict()),
                    selected.digest,
                    selected.code_generation,
                )
        pin = self._read(key)
        for _ in range(3):
            if pin:
                break
            active = self._read(self.prefix + "ACTIVE")
            try:
                digest = active["digest"]["S"]
                generation = active["generation"]["N"]
                if not isinstance(generation, str) or not generation.isdecimal():
                    raise ValueError("invalid generation")
                if int(generation) < 1:
                    raise ValueError("invalid generation")
            except (KeyError, TypeError, ValueError) as exc:
                raise RegistryError("ACTIVE is missing or invalid") from exc
            # Validate content before permanently binding a new match.
            self._load(digest)
            proposed = {
                "pk": {"S": key},
                "digest": {"S": digest},
                "generation": {"N": generation},
            }
            try:
                self.client.transact_write_items(
                    TransactItems=[
                        {
                            "ConditionCheck": {
                                "TableName": self.table,
                                "Key": {"pk": {"S": self.prefix + "ACTIVE"}},
                                "ConditionExpression": "#generation = :generation AND #digest = :digest",
                                "ExpressionAttributeNames": {
                                    "#generation": "generation",
                                    "#digest": "digest",
                                },
                                "ExpressionAttributeValues": {
                                    ":generation": {"N": generation},
                                    ":digest": {"S": digest},
                                },
                            }
                        },
                        {
                            "Put": {
                                "TableName": self.table,
                                "Item": proposed,
                                "ConditionExpression": "attribute_not_exists(pk)",
                            }
                        },
                    ]
                )
            except Exception as exc:
                # A timeout can follow a successful commit. Always read the winning pin.
                pin = self._read(key)
                if pin:
                    break
                if error_code(exc) != "TransactionCanceledException":
                    raise RegistryError(f"profile pin failed: {error_code(exc)}") from exc
            else:
                pin = proposed
        if not pin:
            raise RegistryError("profile pin contention exceeded retry limit")
        try:
            selected = self._load(pin["digest"]["S"])
        except (KeyError, TypeError) as exc:
            raise RegistryError("invalid match profile pin") from exc
        with self._lock:
            self._cache[key] = selected
            self._cache.move_to_end(key)
            while len(self._cache) > 64:
                self._cache.popitem(last=False)
        return SelectedProfile(
            parse_profile(selected.profile.to_dict()), selected.digest, selected.code_generation
        )


def replace_settings_only(baseline: Profile, candidate: Profile) -> bool:
    """Initial automated remedy only decreases press radius by a bounded two-unit step."""
    before, after = baseline.to_dict()["settings"], candidate.to_dict()["settings"]
    return (
        after["press_radius"] == max(12.0, before["press_radius"] - 2.0)
        and after["press_radius"] < before["press_radius"]
        and all(before[k] == after[k] for k in before if k != "press_radius")
    )
