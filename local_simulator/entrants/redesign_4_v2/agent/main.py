"""Optional AgentCore entrypoint. Local CLI does not require this dependency."""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bedrock_agentcore.runtime import BedrockAgentCoreApp

from .model import Profile
from .profile import load_profile
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
    def invoke(payload: dict):
        yield runtime.handle(with_default_player(payload, default_player))

    return app


def main() -> None:
    path = os.getenv("AFC_PROFILE")
    seat = os.getenv("AFC_PLAYER_ID")
    if seat is not None and seat not in ("0", "1", "2", "3", "4"):
        raise ValueError("AFC_PLAYER_ID must be 0..4")
    runtime = Runtime(
        load_profile(path) if path else Profile(),
        match_id=os.getenv("AFC_MATCH_ID"),
        opponent_id=os.getenv("AFC_OPPONENT_ID"),
        stamina_scale=float(os.getenv("AFC_STAMINA_SCALE", "1")),
    )
    try:
        create_app(runtime, None if seat is None else int(seat)).run()
    finally:
        runtime.close()


if __name__ == "__main__":
    main()
