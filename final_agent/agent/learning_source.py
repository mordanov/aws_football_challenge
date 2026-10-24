"""Retrieve exact recently acknowledged observations from Memory, not semantic claims."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from typing import Any, Protocol

from .profile_registry import canonical


class EvidenceIndex(Protocol):
    def query(self, **kwargs: Any) -> dict: ...


class EvidenceMemory(Protocol):
    def list_events(self, **kwargs: Any) -> dict: ...


class MemoryObservationSource:
    def __init__(
        self,
        dynamo: EvidenceIndex,
        memory: EvidenceMemory,
        *,
        table: str,
        memory_id: str,
        team_id: str,
    ):
        self.dynamo, self.memory = dynamo, memory
        self.table, self.memory_id, self.team_id = table, memory_id, team_id

    def load(self, *, now_ms: int) -> list[dict]:
        # Interleaved matches make a truncated time window unsafe for attribution.
        params: dict = {
            "TableName": self.table,
            "IndexName": "TeamReceived",
            "KeyConditionExpression": "#team = :team AND received >= :since",
            "ExpressionAttributeNames": {"#team": "team"},
            "ExpressionAttributeValues": {
                ":team": {"S": self.team_id},
                ":since": {"N": str(max(0, now_ms - 7 * 86_400_000))},
            },
            "ScanIndexForward": False,
            "Limit": 3000,
        }
        rows = []
        for _ in range(30):
            page = self.dynamo.query(**params)
            rows.extend(page.get("Items", []))
            token = page.get("LastEvaluatedKey")
            if len(rows) >= 3000:
                rows = rows[:3000]
                if token:
                    raise RuntimeError("Memory evidence index window is truncated")
                break
            if not token:
                break
            params["ExclusiveStartKey"] = token
            params["Limit"] = 3000 - len(rows)
        else:
            raise RuntimeError("Memory evidence index exceeded pagination limit")
        return verify_observation_documents(
            self.memory,
            [(json.loads(row["document"]["S"]), row["status"]["S"]) for row in rows],
            memory_id=self.memory_id,
            team_id=self.team_id,
            now_ms=now_ms,
        )


def verify_observation_documents(
    memory: EvidenceMemory,
    documents: list[tuple[dict, str]],
    *,
    memory_id: str,
    team_id: str,
    now_ms: int,
) -> list[dict]:
    """Verify exact Memory events against durable documents and acknowledgements."""
    return _DocumentVerifier(memory, memory_id, team_id).load(documents, now_ms=now_ms)


class _DocumentVerifier:
    def __init__(self, memory: EvidenceMemory, memory_id: str, team_id: str):
        self.memory, self.memory_id, self.team_id = memory, memory_id, team_id

    def load(self, documents: list[tuple[dict, str]], *, now_ms: int) -> list[dict]:
        groups: dict[tuple[str, int], list[tuple[dict, str]]] = defaultdict(list)
        for document, status in documents:
            content = document["content"]
            if (
                content["team_id"] != self.team_id
                or hashlib.sha256(canonical(content).encode()).hexdigest()
                != document["observation_id"]
            ):
                raise RuntimeError("Memory evidence index identity/digest mismatch")
            groups[(content["match_id"], content["team_side"])].append(
                (document, status)
            )
        result = {}
        if len(groups) > 20:
            raise RuntimeError("Memory evidence window exceeds match limit")
        for (mid, side), group in sorted(groups.items()):
            latest = max(doc["source"]["timestamp_ms"] for doc, _ in group)
            if (
                latest > now_ms - 120_000
                or latest < now_ms - 7 * 86_400_000
                or any(status != "SENT" for _, status in group)
            ):
                continue
            expected = {doc["observation_id"]: doc for doc, _ in group}
            actor, session = f"team_{self.team_id}", f"observations_v1_{mid}_{side}"
            request: dict = {
                "memoryId": self.memory_id,
                "actorId": actor,
                "sessionId": session,
                "includePayloads": True,
                "maxResults": 100,
            }
            found = {}
            seen_tokens = set()
            for _ in range(30):
                response = self.memory.list_events(**request)
                for event in response.get("events", []):
                    if (
                        event.get("memoryId") != self.memory_id
                        or event.get("actorId") != actor
                        or event.get("sessionId") != session
                    ):
                        raise RuntimeError("Memory observation service identity mismatch")
                    try:
                        payload = event["payload"]
                        if len(payload) != 1 or payload[0]["conversational"]["role"] != "USER":
                            raise ValueError("unexpected payload")
                        raw = payload[0]["conversational"]["content"]["text"]
                        if len(raw.encode()) > 40_000:
                            raise ValueError("oversized event")
                        doc = json.loads(raw)
                        identifier = doc["observation_id"]
                        if identifier not in expected:
                            raise ValueError("event has no acknowledged index observation")
                        if doc != expected[identifier] or (
                            round(event["eventTimestamp"].timestamp() * 1000)
                            != doc["source"]["timestamp_ms"]
                        ):
                            raise ValueError("event does not match acknowledged source")
                        found[identifier] = doc
                    except (KeyError, TypeError, ValueError, AttributeError) as exc:
                        raise RuntimeError(
                            "Memory observation integrity validation failed"
                        ) from exc
                token = response.get("nextToken")
                if not token:
                    break
                if not isinstance(token, str) or token in seen_tokens:
                    raise RuntimeError("Memory observation pagination token loop")
                seen_tokens.add(token)
                request["nextToken"] = token
            else:
                raise RuntimeError("Memory observation page limit exceeded")
            if set(found) != set(expected):
                raise RuntimeError("Memory is missing acknowledged observation evidence")
            result.update(found)
        return [result[key] for key in sorted(result)]
