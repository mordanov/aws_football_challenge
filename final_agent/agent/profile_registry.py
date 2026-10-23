"""Opt-in, match-scoped profile selection; no Memory or model calls."""

from __future__ import annotations

import hashlib
import json
import re
from collections import OrderedDict
from dataclasses import dataclass
from threading import Lock
from typing import Any, Protocol
from uuid import UUID

from .model import Profile
from .profile import parse_profile

CODE_GENERATION = "match_profiles_v1"


class RegistryError(RuntimeError):
    """No valid common profile could be established; never select a local fallback."""


@dataclass(frozen=True)
class SelectedProfile:
    profile: Profile
    digest: str
    code_generation: str


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
