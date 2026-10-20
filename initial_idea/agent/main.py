"""AgentCore entrypoint. One deployment per player; the player is fixed by the
MY_PLAYER_ID env var (0=GK, 1=DEF, 2=MID, 3=FWD1, 4=FWD2), as in the AWS
sample agents, falling back to payload['playerId'].

Four degradation layers so a player never stands still:
  rules(+optional LLM) -> rules without LLM -> last-resort SET_STANCE
"""
from __future__ import annotations

import os
import time
from typing import Any

from . import commands as C
from .adapter import parse_world
from .llm_layer import choose
from .policy import decide


def handle(payload: dict, player_id: Any | None = None) -> list[dict]:
    t0 = time.perf_counter()
    pid = player_id if player_id is not None else os.getenv("MY_PLAYER_ID", payload.get("playerId"))
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        pass
    state = payload.get("gameState", payload)
    try:
        w = parse_world(state, pid)
    except Exception as e:  # unparseable state -> safest legal command
        print(f"DECISION pos={pid} source=last-resort err={e!r}")
        return [C.set_stance(pid)]

    try:
        d = decide(w, llm=choose)
    except Exception as e:
        print(f"DECISION pos={pid} source=error-fallback err={e!r}")
        try:
            d = decide(w, llm=None)
        except Exception:
            return [C.set_stance(pid)]

    cmd = C.to_wire(d.cmd, w.offset)
    if not C.is_valid(cmd, pid):
        cmd = C.set_stance(pid)
    ms = (time.perf_counter() - t0) * 1000
    # one structured line per tick -> CloudWatch -> your own analytics
    print(f"DECISION pos={pid} t={w.clock:.0f} src={d.source} post={d.extras.get('post')} "
          f"cmd={cmd['commandType']} why={d.why!r} ms={ms:.1f}")
    return [cmd]


try:  # only needed when deployed on Bedrock AgentCore
    from bedrock_agentcore.runtime import BedrockAgentCoreApp

    app = BedrockAgentCoreApp()

    @app.entrypoint
    def invoke(payload: dict) -> Any:
        return handle(payload)

except ImportError:
    app = None

if __name__ == "__main__" and app is not None:
    app.run()
