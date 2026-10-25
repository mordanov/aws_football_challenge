"""Provisional team evidence, deliberately separate from completed opponent scouting."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from .profile_registry import RegistryError, canonical, checked_match
from .protocol import restore_snapshot, snapshot_dict, validate_command

SCHEMA = "provisional_team_observation_v1"


@dataclass(frozen=True)
class Observation:
    observation_id: str
    content: dict
    source: dict


def prepare_observation(record: dict, *, team_id: str, source: dict) -> Observation | None:
    """Normalize only allowlisted evidence; null result/opponent are intentional."""
    kind = record.get("type")
    if kind not in ("decision", "error", "profile_selection_error"):
        return None
    if record.get("match_id") is None:
        return None
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", team_id):
        raise ValueError("invalid observation team identity")
    side, mid, player = record.get("team_id"), record.get("match_id"), record.get("player_id")
    if not isinstance(mid, str) or type(side) is not int:
        raise ValueError("invalid observation match identity")
    try:
        checked_match(mid, side)
    except RegistryError as exc:
        raise ValueError("invalid observation match identity") from exc
    if type(player) is not int or not 0 <= player <= 4:
        raise ValueError("invalid observation player identity")
    version = record.get("profile_version")
    if not (
        kind == "profile_selection_error"
        and version is None
        or isinstance(version, str)
        and re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", version)
    ):
        raise ValueError("invalid observation profile version")
    digest, generation = record.get("profile_digest"), record.get("code_generation")
    if (
        (digest is None) != (generation is None)
        or digest is not None
        and (
            not isinstance(digest, str)
            or not re.fullmatch(r"[0-9a-f]{64}", digest)
            or not isinstance(generation, str)
            or not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", generation)
        )
    ):
        raise ValueError("invalid observation profile binding")
    content = {
        "schema_version": SCHEMA,
        "team_id": team_id,
        "match_id": mid,
        "team_side": side,
        "player_id": player,
        "kind": kind,
        "profile_version": version,
        "profile_digest": digest,
        "code_generation": generation,
        "opponent_id": None,
        "final_result": None,
        "evidence_status": "provisional",
    }
    if kind == "decision":
        snapshot = restore_snapshot(record.get("snapshot"))
        if snapshot.team != side:
            raise ValueError("observation snapshot identity mismatch")
        if any(abs(p.x) > 55 or abs(p.y) > 35 for p in snapshot.players):
            raise ValueError("observation snapshot outside canonical pitch")
        contract, fmt = record.get("input_contract"), record.get("input_format")
        if not isinstance(contract, dict) or fmt not in ("sample", "compact_match"):
            raise ValueError("unknown observation input contract")
        expected_scale = 8.0 if fmt == "compact_match" else 1.0
        if (
            type(contract.get("coordinate_scale")) not in (int, float)
            or contract["coordinate_scale"] != expected_scale
            or type(contract.get("stamina_scale")) not in (int, float)
            or contract["stamina_scale"] not in (1.0, 100.0)
            or fmt == "compact_match"
            and contract["stamina_scale"] != 1.0
            or contract.get("ball_lateral_axis") not in ("y", "z")
        ):
            raise ValueError("invalid observation input contract")
        command = record.get("command")
        if not isinstance(command, dict):
            raise ValueError("observation command must be an object")
        validate_command(command)
        if command["teamId"] != side or command["playerId"] != player:
            raise ValueError("observation command identity mismatch")
        content.update(
            snapshot=snapshot_dict(snapshot),
            command={
                k: command[k]
                for k in ("commandType", "teamId", "playerId", "parameters", "duration")
            },
            input_format=fmt,
            input_contract={
                k: contract[k] for k in ("coordinate_scale", "stamina_scale", "ball_lateral_axis")
            },
            # Frozen clocks and multiple players are not independent support.
            support_key=f"{mid}:{side}:{snapshot.clock}",
        )
    else:
        tick = record.get("tick")
        if tick is not None and (type(tick) is not int or not 0 <= tick <= 10**12):
            raise ValueError("invalid observation error tick")
        raw_error = record.get("error")
        error = raw_error.split(":", 1)[0] if isinstance(raw_error, str) else ""
        content.update(
            tick=tick,
            error_class=error
            if error
            in (
                "ValueError",
                "KeyError",
                "TypeError",
                "StopIteration",
                "ZeroDivisionError",
                "OverflowError",
                "RegistryError",
            )
            else "redacted",
        )
    telemetry = record.get("telemetry")
    if isinstance(telemetry, dict):
        counts = {k: telemetry.get(k) for k in ("dropped", "failed")}
        if any(type(v) is not int or v < 0 for v in counts.values()):
            raise ValueError("invalid observation telemetry counters")
        content["telemetry"] = counts
    observation_id = hashlib.sha256(canonical(content).encode()).hexdigest()
    if len(canonical({"content": content, "source": source}).encode()) > 32_000:
        raise ValueError("observation exceeds byte limit")
    return Observation(observation_id, content, source)
