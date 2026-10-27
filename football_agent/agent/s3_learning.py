"""Immutable S3 evidence and acknowledgements; Memory remains the learning authority.

Discovery entries are written before envelopes so an interrupted publish cannot
silently remove support. UTC day partitions bound discovery independently of the
size of the permanent archive. Session reads include all support, not a sample.
Discovery ranks whole sessions by maximum S3 LastModified across eight UTC day
partitions (30,000 references maximum). At most 20 sessions and 3,000 documents
are admitted newest-first; oversized/capacity/expired sessions are excluded whole.
``last_plan`` exposes selection counts. Truncated discovery still fails closed.
"""

from __future__ import annotations

import hashlib
import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

from botocore.exceptions import BotoCoreError, ClientError

from .learning_collector import MemoryClient
from .learning_observations import SCHEMA, Observation
from .learning_source import EvidenceMemory, verify_observation_documents
from .profile_registry import RegistryError, canonical, checked_match

DAY_MS = 86_400_000
MAX_DOCUMENTS = 3000
MAX_SESSIONS = 20
MAX_DISCOVERY_REFERENCES = 30_000


class _EvidenceLimit(RuntimeError):
    pass


class LearningS3(Protocol):
    def get_object(self, **kwargs: Any) -> dict: ...

    def put_object(self, **kwargs: Any) -> dict: ...

    def list_objects_v2(self, **kwargs: Any) -> dict: ...


class _EvidenceObjects:
    def __init__(
        self,
        s3: LearningS3,
        memory: Any,
        *,
        bucket: str,
        memory_id: str,
        team_id: str,
        owner_account: str,
        prefix: str = "final-agent-learning",
    ):
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", team_id):
            raise ValueError("invalid collector team identity")
        if not re.fullmatch(r"\d{12}", owner_account):
            raise ValueError("invalid S3 bucket owner")
        if not prefix or any(p in ("", ".", "..") for p in prefix.split("/")):
            raise ValueError("invalid S3 evidence prefix")
        self.s3, self.memory = s3, memory
        self.bucket, self.owner_account = bucket, owner_account
        self.memory_id, self.team_id = memory_id, team_id
        self.root = f"{prefix}/{team_id}"
        self.last_plan: dict[str, int] = {}

    def _get(self, key: str) -> dict | None:
        try:
            response = self.s3.get_object(
                Bucket=self.bucket, Key=key, ExpectedBucketOwner=self.owner_account
            )
        except ClientError as exc:
            if exc.response["Error"]["Code"] in ("NoSuchKey", "404", "NotFound"):
                return None
            raise
        body = response["Body"]
        try:
            raw = body.read(40_001)
        finally:
            body.close()
        if len(raw) > 40_000:
            raise RuntimeError("S3 evidence object exceeds byte limit")
        try:
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise ValueError("not an object")
            return value
        except (ValueError, TypeError) as exc:
            raise RuntimeError("corrupt S3 evidence JSON") from exc

    def _create(self, key: str, value: dict) -> dict:
        try:
            self.s3.put_object(
                Bucket=self.bucket,
                Key=key,
                Body=canonical(value).encode(),
                ContentType="application/json",
                ExpectedBucketOwner=self.owner_account,
                IfNoneMatch="*",
            )
        except (ClientError, BotoCoreError, OSError) as exc:
            if isinstance(exc, ClientError) and exc.response["Error"]["Code"] not in (
                "PreconditionFailed",
                "ConditionalRequestConflict",
                "412",
                "409",
                "InternalError",
                "ServiceUnavailable",
                "RequestTimeout",
                "500",
                "503",
            ):
                raise
            # The response may have been lost after a successful conditional write.
            # Recovery is success only with a readable, independently validated winner.
            winner = self._get(key)
            if winner is None:
                raise
            return winner
        return value

    def _list(
        self, prefix: str, *, limit: int, modified: dict[str, float] | None = None
    ) -> list[str]:
        keys: list[str] = []
        seen = set()
        request: dict = {
            "Bucket": self.bucket,
            "Prefix": prefix,
            "ExpectedBucketOwner": self.owner_account,
            "MaxKeys": min(1000, limit + 1),
        }
        tokens = set()
        for _ in range(100):
            page = self.s3.list_objects_v2(**request)
            for item in page.get("Contents", []):
                key = item["Key"]
                if not isinstance(key, str) or not key.startswith(prefix) or key in seen:
                    raise RuntimeError("invalid S3 evidence listing")
                seen.add(key)
                keys.append(key)
                if modified is not None:
                    timestamp = item.get("LastModified")
                    if not isinstance(timestamp, datetime):
                        raise RuntimeError("S3 discovery listing lacks LastModified")
                    modified[key] = timestamp.timestamp()
            if len(keys) > limit:
                raise _EvidenceLimit("S3 evidence window exceeds document limit")
            if not page.get("IsTruncated", False):
                return keys
            token = page.get("NextContinuationToken")
            if not isinstance(token, str) or not token or token in tokens:
                raise RuntimeError("S3 evidence pagination truncated or looping")
            tokens.add(token)
            request["ContinuationToken"] = token
        raise RuntimeError("S3 evidence pagination limit exceeded")

    def _session_prefix(self, document: dict) -> str:
        content = document["content"]
        return f"{self.root}/observations/{content['match_id']}/{content['team_side']}/"

    def _key(self, document: dict) -> str:
        return self._session_prefix(document) + document["observation_id"] + ".json"

    def _validate(self, envelope: dict | None, *, key: str) -> dict:
        try:
            if envelope is None:
                raise ValueError("missing envelope")
            doc = envelope["document"]
            content, source = doc["content"], doc["source"]
            identifier = doc["observation_id"]
            checked_match(content["match_id"], content["team_side"])
            timestamp = source["timestamp_ms"]
            if (
                envelope["memory_id"] != self.memory_id
                or content["team_id"] != self.team_id
                or content["schema_version"] != SCHEMA
                or content["evidence_status"] != "provisional"
                or content["final_result"] is not None
                or content["opponent_id"] is not None
                or hashlib.sha256(canonical(content).encode()).hexdigest() != identifier
                or type(timestamp) is not int
                or not 0 <= timestamp <= 10**13
                or self._key(doc) != key
                or len(canonical(doc).encode()) > 40_000
            ):
                raise ValueError("identity or digest mismatch")
            return doc
        except (KeyError, TypeError, ValueError, RegistryError) as exc:
            raise RuntimeError("corrupt S3 observation envelope") from exc

    def _ack(self, envelope: dict) -> dict:
        return {
            "memory_id": self.memory_id,
            "document_digest": hashlib.sha256(canonical(envelope).encode()).hexdigest(),
            "status": "SENT",
        }

    def _ack_key(self, key: str) -> str:
        return key.replace(f"{self.root}/observations/", f"{self.root}/acknowledgements/", 1)

    def _status(self, key: str, envelope: dict) -> str:
        ack = self._get(self._ack_key(key))
        if ack is None:
            return "PENDING"
        if ack != self._ack(envelope):
            raise RuntimeError("corrupt S3 observation acknowledgement")
        return "SENT"

    def _statuses(self, records: list[tuple[str, dict, dict]]) -> list[str]:
        with ThreadPoolExecutor(max_workers=8) as executor:
            return list(executor.map(lambda row: self._status(row[0], row[1]), records))

    def _read_many(self, keys: list[str]) -> list[dict | None]:
        with ThreadPoolExecutor(max_workers=8) as executor:
            return list(executor.map(self._get, keys))

    def _recent_sessions(self, now_ms: int) -> list[tuple[str, list[str]]]:
        if type(now_ms) is not int or now_ms < 0:
            raise ValueError("invalid evidence clock")
        today = datetime.fromtimestamp(now_ms / 1000, timezone.utc).date()
        # Inclusive lower boundary can be in the eighth UTC calendar partition.
        prefixes = [
            f"{self.root}/observations/_days/{(today - timedelta(days=offset)).isoformat()}/"
            for offset in range(8)
        ]
        def partition(prefix: str) -> tuple[list[str], dict[str, float]]:
            modified: dict[str, float] = {}
            return self._list(
                prefix, limit=MAX_DISCOVERY_REFERENCES, modified=modified
            ), modified

        with ThreadPoolExecutor(max_workers=8) as executor:
            partitions = list(executor.map(partition, prefixes))
        references = [key for keys, _ in partitions for key in keys]
        if len(references) > MAX_DISCOVERY_REFERENCES:
            raise RuntimeError("S3 evidence window exceeds document limit")
        modified = {key: value for _, metadata in partitions for key, value in metadata.items()}
        sessions: dict[str, list[str]] = {}
        recent: dict[str, float] = {}
        for reference in references:
            try:
                day, mid, side, identifier = reference.removeprefix(
                    f"{self.root}/observations/_days/"
                ).split("/")
                checked_match(mid, int(side))
                if side not in ("0", "1") or not re.fullmatch(r"[0-9a-f]{64}\.json", identifier):
                    raise ValueError("invalid reference path")
            except (ValueError, RegistryError) as exc:
                raise RuntimeError("corrupt S3 discovery key") from exc
            session = f"{self.root}/observations/{mid}/{side}/"
            sessions.setdefault(session, []).append(reference)
            recent[session] = max(recent.get(session, float("-inf")), modified[reference])
        self.last_plan = {
            "discovery_references": len(references),
            "discovered_sessions": len(sessions),
            "selected_sessions": 0,
            "selected_documents": 0,
            "excluded_oversized": 0,
            "excluded_capacity": 0,
            "excluded_expired": 0,
        }
        return [
            (session, sessions[session])
            for session in sorted(sessions, key=lambda key: (-recent[key], key))
        ]

    def _session_records(
        self, session: str, references: list[str], listed: list[str], *, repair: bool
    ) -> list[tuple[str, dict, dict]]:
        entries = self._read_many(references)
        reference_keys = set()
        for reference, entry in zip(references, entries, strict=True):
            expected_key = session + reference.rsplit("/", 1)[1]
            if (
                entry is None
                or set(entry) != {"key", "memory_id", "envelope"}
                or entry["memory_id"] != self.memory_id
                or entry["key"] != expected_key
            ):
                raise RuntimeError("corrupt S3 evidence discovery entry")
            key = entry["key"]
            self._validate(entry["envelope"], key=key)
            reference_keys.add(key)
        canonical_keys = sorted(set(listed) | reference_keys)
        fetched = dict(zip(canonical_keys, self._read_many(canonical_keys), strict=True))
        for entry in entries:
            if entry is None:
                raise RuntimeError("missing S3 evidence discovery entry")
            key = entry["key"]
            envelope = fetched[key]
            if envelope is None and repair:
                envelope = self._create(key, entry["envelope"])
                fetched[key] = envelope
            doc = self._validate(envelope, key=key)
            if doc["content"] != entry["envelope"]["document"]["content"]:
                raise RuntimeError("S3 discovery content conflicts")
        records = []
        for key in canonical_keys:
            envelope = fetched[key]
            document = self._validate(envelope, key=key)
            if envelope is None:
                raise RuntimeError("missing S3 observation")
            records.append((key, envelope, document))
        return records

    def _window(self, now_ms: int, *, repair: bool = False) -> list[tuple[str, dict, dict]]:
        records: list[tuple[str, dict, dict]] = []
        for session, references in self._recent_sessions(now_ms):
            if self.last_plan["selected_sessions"] >= MAX_SESSIONS:
                self.last_plan["excluded_capacity"] += 1
                continue
            try:
                listed = self._list(session, limit=MAX_DOCUMENTS)
            except _EvidenceLimit:
                self.last_plan["excluded_oversized"] += 1
                continue
            identifiers = set(listed) | {
                session + reference.rsplit("/", 1)[1] for reference in references
            }
            if len(identifiers) > MAX_DOCUMENTS:
                self.last_plan["excluded_oversized"] += 1
                continue
            if len(records) + len(identifiers) > MAX_DOCUMENTS:
                self.last_plan["excluded_capacity"] += 1
                continue
            group = self._session_records(session, references, listed, repair=repair)
            # Expired support excludes the whole session, never individual records.
            if any(doc["source"]["timestamp_ms"] < now_ms - 7 * DAY_MS for _, _, doc in group):
                self.last_plan["excluded_expired"] += 1
                continue
            records.extend(group)
            self.last_plan["selected_sessions"] += 1
            self.last_plan["selected_documents"] = len(records)
        return sorted(records, key=lambda row: (row[2]["source"]["timestamp_ms"], row[0]))


class S3ObservationOutbox(_EvidenceObjects):
    memory: MemoryClient

    def publish(self, observation: Observation) -> bool:
        doc = {
            "observation_id": observation.observation_id,
            "content": observation.content,
            "source": observation.source,
        }
        key = self._key(doc)
        incoming = {"memory_id": self.memory_id, "document": doc}
        self._validate(incoming, key=key)
        day = datetime.fromtimestamp(
            observation.source["timestamp_ms"] / 1000, timezone.utc
        ).date()
        suffix = key.removeprefix(f"{self.root}/observations/")
        reference = f"{self.root}/observations/_days/{day.isoformat()}/{suffix}"
        entry = {"key": key, "memory_id": self.memory_id, "envelope": incoming}
        saved_entry = self._create(reference, entry)
        if (
            saved_entry.get("key") != key
            or saved_entry.get("memory_id") != self.memory_id
            or self._validate(saved_entry.get("envelope"), key=key)["content"]
            != observation.content
        ):
            raise RuntimeError("S3 discovery entry conflicts")
        envelope = self._create(key, saved_entry["envelope"])
        saved = self._validate(envelope, key=key)
        if saved["content"] != observation.content:
            raise RuntimeError("S3 observation conflicts")
        return self._send(key, envelope, saved)

    def _send(self, key: str, envelope: dict, document: dict) -> bool:
        if self._status(key, envelope) == "SENT":
            return False
        content = document["content"]
        try:
            self.memory.create_event(
                memoryId=self.memory_id,
                actorId=f"team_{self.team_id}",
                sessionId=f"observations_v1_{content['match_id']}_{content['team_side']}",
                eventTimestamp=datetime.fromtimestamp(
                    document["source"]["timestamp_ms"] / 1000, timezone.utc
                ),
                clientToken=document["observation_id"],
                extractionMode="SKIP",
                payload=[
                    {"conversational": {"role": "USER", "content": {"text": canonical(document)}}}
                ],
            )
        except ClientError as e:
            if e.response["Error"]["Code"] != "ResourceNotFoundException":
                raise
        ack = self._ack(envelope)
        if self._create(self._ack_key(key), ack) != ack:
            raise RuntimeError("S3 observation acknowledgement conflicts")
        return True

    def recover_pending(self, now_ms: int) -> dict[str, int]:
        """Replay selected whole sessions; last_plan explains exclusions."""
        counts = {"published": 0, "duplicates": 0}
        records = self._window(now_ms, repair=True)
        for (key, envelope, document), status in zip(records, self._statuses(records), strict=True):
            if status == "SENT":
                counts["duplicates"] += 1
            else:
                counts["published" if self._send(key, envelope, document) else "duplicates"] += 1
        return counts


class S3MemoryObservationSource(_EvidenceObjects):
    memory: EvidenceMemory

    def load(self, *, now_ms: int) -> list[dict]:
        records = self._window(now_ms)
        statuses = self._statuses(records)
        return verify_observation_documents(
            self.memory,
            [(row[2], status) for row, status in zip(records, statuses, strict=True)],
            memory_id=self.memory_id,
            team_id=self.team_id,
            now_ms=now_ms,
        )
