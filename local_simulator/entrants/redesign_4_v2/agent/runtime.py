from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import replace

from .coach import choose_preset
from .model import Profile
from .profile import effective_settings, parse_profile
from .protocol import decode, envelope, snapshot_dict, validate_command
from .tactics import decide
from .telemetry import DecisionLog


def emit_json(record: dict) -> None:
    print(json.dumps(record, sort_keys=True, allow_nan=False), flush=True)


class Runtime:
    def __init__(
        self,
        profile: Profile | None = None,
        *,
        match_id: str | None = None,
        opponent_id: str | None = None,
        stamina_scale: float = 1.0,
        emit: Callable[[dict], None] | None = None,
    ):
        self.profile = parse_profile((profile if profile is not None else Profile()).to_dict())
        self.settings = effective_settings(self.profile, opponent_id)
        self.match_id = match_id
        self.opponent_id = opponent_id
        if type(stamina_scale) not in (float, int) or stamina_scale not in (1.0, 100.0):
            raise ValueError("stamina_scale must be 1 or 100")
        self.stamina_scale = stamina_scale
        self._log = DecisionLog(emit if emit is not None else emit_json)

    @property
    def log_status(self) -> dict:
        return self._log.status

    def flush_logs(self, timeout: float = 1.0) -> bool:
        return self._log.flush(timeout)

    def close(self, timeout: float = 1.0) -> bool:
        return self._log.close(timeout)

    def handle(self, payload: dict) -> str:
        start = time.perf_counter()
        data, team, pid = envelope(payload)
        record: dict
        context = {
            "match_id": self.match_id,
            "opponent_id": self.opponent_id,
            "profile_version": self.profile.version,
            "team_id": team,
            "player_id": pid,
        }
        try:
            snapshot, _ = decode(payload, stamina_scale=self.stamina_scale)
            if self.profile.coach_policy == "rules":
                snapshot = replace(snapshot, coach=choose_preset(snapshot))
            decision = decide(snapshot, pid, self.settings, self.profile.style)
            selected = decision.command
            validate_command(selected)
        except (ValueError, KeyError, TypeError, StopIteration, ArithmeticError) as exc:
            selected = {
                "commandType": "SET_STANCE",
                "teamId": team,
                "playerId": pid,
                "parameters": {"stance": 0},
                "duration": 0,
            }
            validate_command(selected)
            raw_state = data.get("gameState")
            tick = raw_state.get("tick") if isinstance(raw_state, dict) else None
            if type(tick) is not int or not 0 <= tick <= 10**12:
                tick = None
            record = {
                "type": "error",
                **context,
                "tick": tick,
                "error": f"{type(exc).__name__}: {exc}",
                "command": selected,
            }
        else:
            record = {
                "type": "decision",
                **context,
                "snapshot": snapshot_dict(snapshot),
                "command": selected,
                "reason": decision.reason,
                "alternatives": decision.alternatives,
                "assignments": decision.assignments,
                "latency_ms": round((time.perf_counter() - start) * 1000, 3),
                "learning_context": "identified"
                if self.match_id and self.opponent_id
                else "missing",
                "warnings": ["unresolved_possession"]
                if snapshot.owner is None and not snapshot.free
                else [],
            }
        response = json.dumps([selected], allow_nan=False)
        self._log.submit(record)
        return response
