"""Optional AgentCore entrypoint. Local CLI does not require this dependency."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bedrock_agentcore.runtime import BedrockAgentCoreApp

from .model import Profile
from .profile import load_profile
from .runtime import Runtime


def create_app(runtime: Runtime) -> BedrockAgentCoreApp:
    from bedrock_agentcore.runtime import BedrockAgentCoreApp

    app = BedrockAgentCoreApp()

    @app.entrypoint
    def invoke(payload: dict):
        yield runtime.handle(payload)

    return app


def main() -> None:
    path = os.getenv("AFC_PROFILE")
    runtime = Runtime(
        load_profile(path) if path else Profile(),
        match_id=os.getenv("AFC_MATCH_ID"),
        opponent_id=os.getenv("AFC_OPPONENT_ID"),
        stamina_scale=float(os.getenv("AFC_STAMINA_SCALE", "1")),
    )
    try:
        create_app(runtime).run()
    finally:
        runtime.close()


if __name__ == "__main__":
    main()
