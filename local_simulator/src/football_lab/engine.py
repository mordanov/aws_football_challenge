"""M2 match engine: orchestrates a full 120-second simulated match."""

from __future__ import annotations

import random
from dataclasses import asdict
from functools import partial
from pathlib import Path
from typing import Any, Protocol

from football_lab import rules_v2
from football_lab.actions import resolve_command
from football_lab.match_contracts import (
    ActionResult,
    CoachFn,
    ContestHook,
    EngineBallState,
    EnginePlayerState,
    EngineState,
    FrameRecord,
    Intent,
    MatchEvent,
    MatchReplay,
    RulesVersion,
)
from football_lab.physics import (
    STEPS_PER_DECISION,
    step,
)

# ── Constants ─────────────────────────────────────────────────────────────────
TOTAL_TICKS = 2400  # 120 s × 20 ticks/s  (40 steps/window × 60 windows)
DECISION_WINDOWS = TOTAL_TICKS // STEPS_PER_DECISION  # 60

_KICK_OFF_POSITIONS: list[tuple[float, float]] = [
    (-40.0, -20.0),  # GK
    (-25.0, -10.0),  # DEF
    (-10.0, 0.0),  # MID
    (-5.0, -5.0),  # FWD1
    (-5.0, 5.0),  # FWD2
]


# ── Protocols ─────────────────────────────────────────────────────────────────


class EngineAgent(Protocol):
    def decide(self, observation: dict[str, Any]) -> list[dict[str, Any]]: ...


class EngineStore(Protocol):
    def save(self, replay: MatchReplay, path: Path) -> None: ...


# ── Stub agent ────────────────────────────────────────────────────────────────


class StubAgent:
    """Deterministic stub: always returns SET_STANCE for all 5 players."""

    def __init__(self, team_id: int) -> None:
        self._team_id = team_id

    def decide(self, observation: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            {
                "team_id": self._team_id,
                "player_id": i,
                "command_type": "SET_STANCE",
                "parameters": {"stance": 0},
            }
            for i in range(5)
        ]


# ── Helpers ───────────────────────────────────────────────────────────────────


def _initial_state(seed: int) -> EngineState:
    """Build kick-off state. Team 0 attacks +x."""
    players: list[EnginePlayerState] = []
    for player_id, (bx, by) in enumerate(_KICK_OFF_POSITIONS):
        # Team 0: positive side; Team 1: mirrored
        players.append(
            {
                "team_id": 0,
                "player_id": player_id,
                "x": bx,
                "y": by,
                "vx": 0.0,
                "vy": 0.0,
                "stamina": 1.0,
            }
        )
        players.append(
            {
                "team_id": 1,
                "player_id": player_id,
                "x": -bx,
                "y": -by,
                "vx": 0.0,
                "vy": 0.0,
                "stamina": 1.0,
            }
        )

    ball: EngineBallState = {
        "x": 0.0,
        "y": 0.0,
        "z": 0.0,
        "vx": 0.0,
        "vy": 0.0,
        "vz": 0.0,
        "owner_team": None,
        "owner_player": None,
        "in_flight": False,
    }

    return {
        "tick": 0,
        "play_mode": "KICK_OFF",
        "mode_team": 0,
        "players": players,
        "ball": ball,
        "score": [0, 0],
    }


def _build_observation(state: EngineState, team_id: int, seed: int) -> dict[str, Any]:
    """Build observation dict for one team. M0-compatible shape + match fields."""
    b = state["ball"]
    return {
        "schema_version": 1,
        "seed": seed,
        "team_id": team_id,
        "tick": state["tick"],
        "play_mode": state["play_mode"],
        "score": list(state["score"]),
        "players": [
            {
                "team_id": p["team_id"],
                "player_id": p["player_id"],
                "x": p["x"],
                "y": p["y"],
                "vx": p["vx"],
                "vy": p["vy"],
                "stamina": p["stamina"],
            }
            for p in state["players"]
        ],
        "ball": {
            "x": b["x"],
            "y": b["y"],
            "z": b["z"],
            "vx": b["vx"],
            "vy": b["vy"],
            "vz": b["vz"],
            "owner_team": b["owner_team"],
            "owner_player": b["owner_player"],
            "in_flight": b["in_flight"],
        },
    }


def _fallback_commands(team_id: int) -> list[dict[str, Any]]:
    return [
        {
            "team_id": team_id,
            "player_id": i,
            "command_type": "SET_STANCE",
            "parameters": {"stance": 0},
        }
        for i in range(5)
    ]


def _resolve_all(
    cmds_0: list[dict[str, Any]],
    cmds_1: list[dict[str, Any]],
    state: EngineState,
) -> tuple[list[Intent], list[ActionResult]]:
    intents: list[Intent] = []
    results: list[ActionResult] = []
    for cmd in cmds_0 + cmds_1:
        intent, result = resolve_command(cmd, state)
        intents.append(intent)
        results.append(result)
    return intents, results


def _frame(state: EngineState) -> FrameRecord:
    return {
        "tick": state["tick"],
        "players": [dict(p) for p in state["players"]],  # type: ignore[misc]
        "ball": dict(state["ball"]),  # type: ignore[typeddict-item]
        "score": list(state["score"]),
    }


# ── Engine ────────────────────────────────────────────────────────────────────


class MatchEngine:
    def __init__(
        self,
        seed: int,
        team0_agent: EngineAgent,
        team1_agent: EngineAgent,
        store: EngineStore,
        *,
        rules_version: RulesVersion = "v1",
        rules_config: rules_v2.RulesV2Config | None = None,
        coaches: tuple[CoachFn | None, CoachFn | None] = (None, None),
    ) -> None:
        if rules_version not in ("v1", "v2"):
            raise ValueError(f"unknown rules_version: {rules_version!r}")
        if rules_config is not None and rules_version != "v2":
            raise ValueError("rules_config requires rules_version='v2'")
        self._seed = seed
        self._agents = (team0_agent, team1_agent)
        self._store = store
        self._rules_version = rules_version
        self._rules_config = (
            (rules_config or rules_v2.RulesV2Config()) if rules_version == "v2" else None
        )
        self._coaches = coaches

    def run(self, output: Path) -> MatchReplay:
        state = _initial_state(self._seed)
        frames: list[FrameRecord] = [_frame(state)]
        all_results: list[ActionResult] = []
        all_events: list[MatchEvent] = []

        contest: ContestHook | None = None
        if self._rules_config is not None:
            contest = partial(
                rules_v2.contest, rng=random.Random(self._seed), cfg=self._rules_config
            )

        for window in range(DECISION_WINDOWS):
            tick = window * STEPS_PER_DECISION

            # ── Collect decisions ──────────────────────────────────────────
            cmds: list[list[dict[str, Any]]] = []
            for tid, agent in enumerate(self._agents):
                obs = _build_observation(state, tid, self._seed)
                coach = self._coaches[tid]
                if coach is not None:
                    obs["coach"] = coach(state, tid)
                try:
                    raw = agent.decide(obs)
                    # Ensure exactly 5 commands
                    if not isinstance(raw, list) or len(raw) != 5:
                        raise ValueError(
                            f"expected 5 commands, got {len(raw) if isinstance(raw, list) else type(raw)}"
                        )
                    cmds.append(raw)
                except Exception:
                    # Agent crash: use fallback and mark all rejected
                    fallback = _fallback_commands(tid)
                    cmds.append(fallback)
                    for i in range(5):
                        all_results.append(
                            {
                                "tick": tick,
                                "team_id": tid,
                                "player_id": i,
                                "attempted_type": "CRASH",
                                "attempted_params": {},
                                "accepted": False,
                                "rejection_reason": "agent_crash",
                            }
                        )
                    continue

            # Resolve commands for teams whose agents didn't crash
            normal_cmds: list[dict[str, Any]] = []
            for tid, agent_cmds in enumerate(cmds):
                # Check if we already added crash results for this team
                crash_results = [
                    r for r in all_results if r["tick"] == tick and r["team_id"] == tid
                ]
                if not crash_results:
                    normal_cmds.extend(agent_cmds)

            intents, results = _resolve_all(
                [c for c in normal_cmds if c["team_id"] == 0],
                [c for c in normal_cmds if c["team_id"] == 1],
                state,
            )
            # Add SET_STANCE intents for crashed teams
            for r in all_results:
                if r["tick"] == tick:
                    intents.append(
                        {
                            "team_id": r["team_id"],
                            "player_id": r["player_id"],
                            "command_type": "SET_STANCE",
                            "parameters": {"stance": 0},
                            "is_one_shot": False,
                            "executed": False,
                        }
                    )
            all_results.extend(results)

            # ── Run 40 physics steps ───────────────────────────────────────
            for _ in range(STEPS_PER_DECISION):
                state, events = step(
                    state, intents, contest, honour_aim=self._rules_version == "v2"
                )
                all_events.extend(events)
                frames.append(_frame(state))

        replay: MatchReplay = {
            "schema_version": 2,
            "kind": "match-replay",
            "status": "complete",
            "rules_version": self._rules_version,
            "seed": self._seed,
            "duration_ticks": TOTAL_TICKS,
            "action_results": all_results,
            "events": all_events,
            "frames": frames,
        }
        if self._rules_config is not None:
            replay["rules_params"] = asdict(self._rules_config)
        self._store.save(replay, Path(output))
        return replay
