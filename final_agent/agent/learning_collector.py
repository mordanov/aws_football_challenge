"""CloudWatch subscription boundary and durable retry-safe provisional Memory outbox."""

from __future__ import annotations

import base64
import gzip
import hashlib
import io
import json
import os
import re
from datetime import datetime, timezone
from typing import Any, Protocol

from .learning_observations import Observation, prepare_observation
from .profile_registry import canonical, error_code


class OutboxClient(Protocol):
    def get_item(self, **kwargs: Any) -> dict: ...

    def put_item(self, **kwargs: Any) -> dict: ...

    def update_item(self, **kwargs: Any) -> dict: ...


class MemoryClient(Protocol):
    def create_event(self, **kwargs: Any) -> dict: ...


class Outbox(Protocol):
    @property
    def team_id(self) -> str: ...

    def publish(self, observation: Observation) -> bool: ...


class ObservationOutbox:
    def __init__(
        self,
        dynamo: OutboxClient,
        memory: MemoryClient,
        *,
        table: str,
        memory_id: str,
        team_id: str,
    ):
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", team_id):
            raise ValueError("invalid collector team identity")
        self.dynamo, self.memory = dynamo, memory
        self.table, self.memory_id, self.team_id = table, memory_id, team_id

    def publish(self, observation: Observation) -> bool:
        if observation.content["team_id"] != self.team_id:
            raise ValueError("outbox team identity mismatch")
        document = canonical(
            {
                "observation_id": observation.observation_id,
                "content": observation.content,
                "source": observation.source,
            }
        )
        key = f"TEAM#{self.team_id}#MEMORY#{self.memory_id}#OBS#{observation.observation_id}"
        item = {
            "pk": {"S": key},
            "document": {"S": document},
            "status": {"S": "PENDING"},
            "team": {"S": self.team_id},
            "received": {"N": str(observation.source["timestamp_ms"])},
        }
        try:
            self.dynamo.put_item(
                TableName=self.table, Item=item, ConditionExpression="attribute_not_exists(pk)"
            )
        except Exception as exc:
            try:
                item = self.dynamo.get_item(
                    TableName=self.table, Key={"pk": {"S": key}}, ConsistentRead=True
                ).get("Item", {})
            except Exception as read_exc:
                raise RuntimeError(f"outbox recovery failed: {error_code(read_exc)}") from read_exc
            if not item:
                raise RuntimeError(f"outbox create failed: {error_code(exc)}") from exc
        try:
            document = item["document"]["S"]
            saved = json.loads(document)
            if (
                saved["observation_id"] != observation.observation_id
                or saved["content"] != observation.content
                or item["status"]["S"] not in ("PENDING", "SENT")
            ):
                raise ValueError("outbox document conflicts")
            if item["status"]["S"] == "SENT":
                return False
            timestamp = datetime.fromtimestamp(saved["source"]["timestamp_ms"] / 1000, timezone.utc)
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError("corrupt observation outbox record") from exc
        content = saved["content"]
        try:
            self.memory.create_event(
                memoryId=self.memory_id,
                actorId="team_" + self.team_id,
                sessionId=f"observations_v1_{content['match_id']}_{content['team_side']}",
                eventTimestamp=timestamp,
                clientToken=observation.observation_id,
                extractionMode="SKIP",
                payload=[{"conversational": {"role": "USER", "content": {"text": document}}}],
            )
        except Exception as exc:
            raise RuntimeError(f"Memory observation publish failed: {error_code(exc)}") from exc
        try:
            self.dynamo.update_item(
                TableName=self.table,
                Key={"pk": {"S": key}},
                UpdateExpression="SET #status = :sent",
                ConditionExpression="#document = :document",
                ExpressionAttributeNames={"#status": "status", "#document": "document"},
                ExpressionAttributeValues={":sent": {"S": "SENT"}, ":document": {"S": document}},
            )
        except Exception as exc:
            raise RuntimeError(f"outbox acknowledgement failed: {error_code(exc)}") from exc
        return True


def collect(event: dict, outbox: Outbox, *, log_groups: tuple[str, ...]) -> dict:
    data = event["awslogs"]["data"]
    if not isinstance(data, str) or len(data) > 6_000_000:
        raise ValueError("invalid subscription envelope size")
    with gzip.GzipFile(fileobj=io.BytesIO(base64.b64decode(data, validate=True))) as stream:
        raw = stream.read(8_000_001)
    if len(raw) > 8_000_000:
        raise ValueError("subscription payload exceeds decompressed limit")
    batch = json.loads(raw)
    if batch.get("messageType") == "CONTROL_MESSAGE":
        return {"published": 0, "duplicates": 0, "ignored": 0}
    if batch.get("messageType") != "DATA_MESSAGE":
        raise ValueError("unknown subscription message type")
    group = batch.get("logGroup")
    if len(log_groups) != 5 or len(set(log_groups)) != 5 or group not in log_groups:
        raise ValueError("untrusted subscription log group")
    events = batch.get("logEvents")
    if not isinstance(events, list) or len(events) > 10_000:
        raise ValueError("invalid subscription event batch")
    # Validate the whole batch before committing any evidence from it.
    observations = []
    result = {"published": 0, "duplicates": 0, "ignored": 0}
    for row in events:
        message, event_id, ts = row["message"], row["id"], row["timestamp"]
        if (
            not isinstance(message, str)
            or len(message.encode()) > 100_000
            or not isinstance(event_id, str)
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,256}", event_id)
            or type(ts) is not int
            or not 0 <= ts <= 10**13
        ):
            raise ValueError("invalid subscription event metadata")
        try:
            record = json.loads(message)
        except json.JSONDecodeError:
            result["ignored"] += 1
            continue
        if not isinstance(record, dict):
            result["ignored"] += 1
            continue
        observation = prepare_observation(
            record,
            team_id=outbox.team_id,
            source={
                "log_group": group,
                "event_id": event_id,
                "timestamp_ms": ts,
                "message_digest": hashlib.sha256(message.encode()).hexdigest(),
            },
        )
        if observation is None:
            result["ignored"] += 1
        else:
            if observation.content["player_id"] != log_groups.index(group):
                raise ValueError("observation player does not match trusted log group")
            observations.append(observation)
    for observation in observations:
        result["published" if outbox.publish(observation) else "duplicates"] += 1
    return result


_outbox: ObservationOutbox | None = None


def handler(event: dict, context) -> dict:
    global _outbox
    if _outbox is None:
        import boto3
        from botocore.config import Config

        config = Config(connect_timeout=2, read_timeout=3, retries={"total_max_attempts": 2})
        _outbox = ObservationOutbox(
            boto3.client("dynamodb", config=config),
            boto3.client("bedrock-agentcore", config=config),
            table=os.environ["AFC_OBSERVATION_TABLE"],
            memory_id=os.environ["AFC_MEMORY_ID"],
            team_id=os.environ["AFC_TEAM_ID"],
        )
    result = collect(event, _outbox, log_groups=tuple(json.loads(os.environ["AFC_LOG_GROUPS"])))
    print(canonical({"type": "learning_collection", **result}), flush=True)
    return result
