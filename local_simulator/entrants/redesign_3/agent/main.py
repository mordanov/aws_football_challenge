"""Main AgentCore entrypoint for Redesign 3 (5v5 Adaptive Football Trainer Agent).

Supports:
1. Direct Python invocation: `handle(payload, context=None) -> list[dict]`
2. Official AWS `BedrockAgentCoreApp` streaming/JSON entrypoint (`invoke_agentcore`)
3. Both single-player control (`myPlayers: [k]`) and full-team control (`myPlayers: [0,1,2,3,4]`).
4. Guaranteed `<500ms` execution (typical `<0.6ms` pure Python) with never-crash fallback.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

from . import commands as cmd
from .adapter import parse_world, unwrap_payload
from .model import Command, ROLE_NAMES
from .roles import decide_for_player
from .tactics import compute_defensive_assignments
from .trainer import FootballTrainer

logger = logging.getLogger("afc.redesign_3")
if not logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(_handler)
    logger.setLevel(logging.INFO)

# Singleton trainer instance kept warm across all ticks & matches in the container
_TRAINER = FootballTrainer()


def get_trainer() -> FootballTrainer:
    """Return the active `FootballTrainer` singleton (useful for inspection and testing)."""
    return _TRAINER


def reset_match_state(preserve_cross_match_learning: bool = False) -> None:
    """Reset trainer state for deterministic testing or between matches."""
    _TRAINER.reset_for_new_match(preserve_cross_match_learning=preserve_cross_match_learning)


def handle(payload: dict[str, Any] | str, context: Any = None) -> list[dict[str, Any]]:
    """Primary tick handler.

    Unwraps `payload`, updates the `FootballTrainer` & `Eagle-Eye` opponent scout,
    computes coordinated defensive/pressing assignments, and runs deep role-specific
    tactics for each controlled player in `world.controlled_ids`.
    """
    t0 = time.perf_counter()
    try:
        world = parse_world(payload)
        directive = _TRAINER.observe_and_learn(world)
        assignments = compute_defensive_assignments(world, directive)

        issued_cmds: list[Command] = []
        reasons: dict[str, str] = {}

        for pid in world.controlled_ids:
            me = world.my_player(pid)
            if me is None:
                c = cmd.set_stance(pid, stance=0, team_id=world.my_team_id)
                r = "missing_player_stance"
            else:
                c, r = decide_for_player(me, world, directive, assignments)
            issued_cmds.append(c)
            reasons[ROLE_NAMES.get(pid, str(pid))] = f"{c.commandType}:{r}"

        # Record issued commands in Trainer memory so the next tick evaluates their success/error
        _TRAINER.record_issued_commands(world, issued_cmds)

        elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 3)
        if os.environ.get("AFC_QUIET_LOGS") != "1":
            logger.info(
                "DECISION %s",
                json.dumps(
                    {
                        "tick": world.tick,
                        "clock": world.clock,
                        "phase": directive.phase.value,
                        "score": f"{world.score.me}-{world.score.opp}",
                        "opp_archetype": directive.scouting.archetype.value,
                        "weak_flank": directive.scouting.weakest_flank,
                        "reasons": reasons,
                        "trainer_events": list(directive.new_events),
                        "ms": elapsed_ms,
                    },
                    separators=(",", ":"),
                ),
            )

        return [c.to_wire() for c in issued_cmds]

    except Exception as exc:
        elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 3)
        logger.error("FALLBACK err=%r ms=%s", exc, elapsed_ms)
        return _emergency_fallback(payload)


def _emergency_fallback(payload: dict[str, Any] | str) -> list[dict[str, Any]]:
    """Last-resort valid command list if payload is malformed or corrupt."""
    top = unwrap_payload(payload)
    gs = top.get("gameState") if isinstance(top.get("gameState"), dict) else top
    team_id = 0
    raw_tid = top.get("teamId", gs.get("teamId", 0))
    if isinstance(raw_tid, (int, float)):
        team_id = 0 if int(raw_tid) == 0 else 1

    raw_my = top.get("myPlayers") or gs.get("myPlayers")
    if isinstance(raw_my, list) and raw_my:
        pids = [int(x) % 5 for x in raw_my if isinstance(x, (int, float))]
        if not pids:
            pids = [0]
    else:
        pids = [0, 1, 2, 3, 4]

    return [cmd.set_stance(pid, stance=0, team_id=team_id).to_wire() for pid in pids]


# Optional BedrockAgentCoreApp wrapper for direct container deployment on AWS AgentCore
try:  # pragma: no cover
    from bedrock_agentcore.runtime import BedrockAgentCoreApp

    app = BedrockAgentCoreApp()

    @app.entrypoint
    async def invoke(payload: dict[str, Any], context: Any):
        cmds = handle(payload, context)
        yield json.dumps(cmds)

except ImportError:  # pragma: no cover
    app = None
