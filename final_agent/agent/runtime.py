from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from dataclasses import replace

from .coach import choose_preset
from .model import Profile
from .profile import effective_settings, parse_profile
from .profile_registry import ProfileSelector, RegistryError
from .protocol import (
    MATCH_COORDINATE_SCALE,
    MATCH_X_LIMIT,
    decode,
    envelope,
    match_command,
    snapshot_dict,
    validate_command,
)
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
        competition_adapter: bool = False,
        emit: Callable[[dict], None] | None = None,
        profile_selector: ProfileSelector | None = None,
    ):
        self.profile = parse_profile((profile if profile is not None else Profile()).to_dict())
        self.settings = effective_settings(self.profile, opponent_id)
        self.match_id = match_id
        self.opponent_id = opponent_id
        if type(stamina_scale) not in (float, int) or stamina_scale not in (1.0, 100.0):
            raise ValueError("stamina_scale must be 1 or 100")
        self.stamina_scale = stamina_scale
        self.competition_adapter = competition_adapter
        self.profile_selector = profile_selector
        self._log = DecisionLog(emit if emit is not None else emit_json)

    @property
    def log_status(self) -> dict:
        return self._log.status

    def flush_logs(self, timeout: float = 1.0) -> bool:
        return self._log.flush(timeout)

    def close(self, timeout: float = 1.0) -> bool:
        return self._log.close(timeout)

    def handle(self, payload: dict, *, session_id: str | None = None) -> str:
        start = time.perf_counter()
        data, team, pid = envelope(payload)
        match = re.fullmatch(
            r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"
            r"-team([01])-pos([0-4])",
            session_id or "",
        )
        compact = self.competition_adapter and match is not None
        record: dict
        context = {
            "match_id": self.match_id or (match.group(1) if match is not None else None),
            "opponent_id": self.opponent_id,
            "profile_version": self.profile.version,
            "team_id": team,
            "player_id": pid,
            "input_format": "compact_match" if compact else "sample",
        }
        profile, settings = self.profile, self.settings
        if self.profile_selector is not None and not (session_id or "").startswith(
            "scrimmage-drill-"
        ):
            try:
                if match is None or (int(match.group(2)), int(match.group(3))) != (team, pid):
                    raise RegistryError("unbound or misrouted match profile request")
                if self.match_id is not None and self.match_id != match.group(1):
                    raise RegistryError("configured match identity conflicts with session")
                selection = self.profile_selector.resolve(match.group(1), team)
                profile = parse_profile(selection.profile.to_dict())
                settings = effective_settings(profile, self.opponent_id)
                context.update(
                    profile_version=profile.version,
                    profile_digest=selection.digest,
                    code_generation=selection.code_generation,
                )
            except RegistryError as exc:
                self._log.submit(
                    {
                        **context,
                        "type": "profile_selection_error",
                        "match_id": match.group(1)
                        if match is not None
                        and (int(match.group(2)), int(match.group(3))) == (team, pid)
                        else None,
                        "profile_version": None,
                        "error": str(exc),
                    }
                )
                raise
        try:
            snapshot, _ = decode(
                payload,
                stamina_scale=1.0 if compact else self.stamina_scale,
                compact_pitch=compact,
            )
            if profile.coach_policy == "rules":
                snapshot = replace(snapshot, coach=choose_preset(snapshot))
            decision = decide(
                snapshot,
                pid,
                settings,
                profile.style,
                max_move_x=MATCH_X_LIMIT * MATCH_COORDINATE_SCALE if compact else 54.0,
            )
            selected = decision.command
            if compact:
                selected = match_command(selected)
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
            ball = data["gameState"]["ball"]
            position = ball["position"]
            warnings = (
                ["unresolved_possession"] if snapshot.owner is None and not snapshot.free else []
            )
            if (
                self.competition_adapter
                and session_id
                and match is None
                and not session_id.startswith("scrimmage-drill-")
            ):
                warnings.append("unrecognized_competition_session")
            record = {
                "type": "decision",
                **context,
                "snapshot": snapshot_dict(snapshot),
                "input_contract": {
                    "coordinate_scale": MATCH_COORDINATE_SCALE if compact else 1.0,
                    "stamina_scale": 1.0 if compact else self.stamina_scale,
                    "ball_lateral_axis": "z" if compact and "z" in position else "y",
                },
                "command": selected,
                "reason": decision.reason,
                "alternatives": tuple(
                    {**option, "command": match_command(option["command"])}
                    for option in decision.alternatives
                )
                if compact
                else decision.alternatives,
                "assignments": decision.assignments,
                "rush": (decision.assignments or {}).get("rush", 0),
                "stage": (decision.assignments or {}).get("stage", 0),
                "latency_ms": round((time.perf_counter() - start) * 1000, 3),
                "learning_context": "identified"
                if self.match_id and self.opponent_id
                else "missing",
                "warnings": warnings,
            }
        response = json.dumps([selected], allow_nan=False)
        self._log.submit(record)
        return response
