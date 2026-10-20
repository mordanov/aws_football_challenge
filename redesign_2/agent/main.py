"""Optional AgentCore entrypoint. Local CLI does not require this dependency."""
from __future__ import annotations

import os

from .model import Profile
from .profile import load_profile
from .runtime import Runtime


def main() -> None:
    from bedrock_agentcore.runtime import BedrockAgentCoreApp

    path = os.getenv("AFC_PROFILE")
    runtime = Runtime(
        load_profile(path) if path else Profile(),
        match_id=os.getenv("AFC_MATCH_ID"),
        opponent_id=os.getenv("AFC_OPPONENT_ID"),
        stamina_scale=float(os.getenv("AFC_STAMINA_SCALE", "1")),
    )
    app = BedrockAgentCoreApp()

    @app.entrypoint
    def invoke(payload: dict):
        yield runtime.handle(payload)

    app.run()


if __name__ == "__main__":
    main()
