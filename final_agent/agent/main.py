"""Optional AgentCore entrypoint. Local CLI does not require this dependency."""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bedrock_agentcore.runtime import BedrockAgentCoreApp
    from bedrock_agentcore.runtime.context import RequestContext

from .model import Profile
from .profile import load_profile
from .profile_registry import DynamoProfileRegistry, ProfileSelector
from .runtime import Runtime


def with_default_player(payload: dict, default_player: int | None) -> dict:
    """Fill a missing myPlayers with this runtime's seat; explicit routing always wins."""
    if default_player is None or not isinstance(payload, dict):
        return payload
    raw = payload.get("prompt", payload)
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except json.JSONDecodeError:
        return payload
    if not isinstance(data, dict) or "myPlayers" in data:
        return payload
    return {**payload, "prompt": {**data, "myPlayers": [default_player]}}


def create_app(runtime: Runtime, default_player: int | None = None) -> BedrockAgentCoreApp:
    from bedrock_agentcore.runtime import BedrockAgentCoreApp

    app = BedrockAgentCoreApp()

    @app.entrypoint
    def invoke(payload: dict, context: RequestContext):
        yield runtime.handle(
            with_default_player(payload, default_player), session_id=context.session_id
        )

    return app


def main() -> None:
    path = os.getenv("AFC_PROFILE")
    seat = os.getenv("AFC_PLAYER_ID")
    competition = os.getenv("AFC_COMPETITION_ADAPTER", "0")
    if competition not in ("0", "1"):
        raise ValueError("AFC_COMPETITION_ADAPTER must be 0 or 1")
    if seat is not None and seat not in ("0", "1", "2", "3", "4"):
        raise ValueError("AFC_PLAYER_ID must be 0..4")
    table, bucket, team_id = (
        os.getenv("AFC_PROFILE_TABLE"),
        os.getenv("AFC_PROFILE_BUCKET"),
        os.getenv("AFC_TEAM_ID"),
    )
    if (table and bucket) or bool(table or bucket) != bool(team_id):
        raise ValueError("configure one profile backend (table or bucket) and AFC_TEAM_ID together")
    if bucket and not os.getenv("AFC_OWNER_ACCOUNT"):
        raise ValueError("S3 profile selection requires AFC_OWNER_ACCOUNT")
    selector: ProfileSelector | None = None
    if table and team_id:
        import boto3
        from botocore.config import Config

        selector = DynamoProfileRegistry(
            boto3.client(
                "dynamodb",
                config=Config(
                    connect_timeout=0.5, read_timeout=0.5, retries={"total_max_attempts": 1}
                ),
            ),
            table,
            team_id,
        )
    elif bucket and team_id:
        import boto3
        from botocore.config import Config

        from .s3_registry import S3ProfileRegistry

        selector = S3ProfileRegistry(
            boto3.client(
                "s3",
                config=Config(
                    connect_timeout=0.5, read_timeout=0.5, retries={"total_max_attempts": 1}
                ),
            ),
            bucket=bucket,
            team_id=team_id,
            owner_account=os.environ["AFC_OWNER_ACCOUNT"],
        )
    runtime = Runtime(
        load_profile(path) if path else Profile(),
        match_id=os.getenv("AFC_MATCH_ID"),
        opponent_id=os.getenv("AFC_OPPONENT_ID"),
        stamina_scale=float(os.getenv("AFC_STAMINA_SCALE", "1")),
        competition_adapter=competition == "1",
        profile_selector=selector,
    )
    try:
        create_app(runtime, None if seat is None else int(seat)).run()
    finally:
        runtime.close()


if __name__ == "__main__":
    main()
