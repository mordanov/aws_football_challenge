from __future__ import annotations

import json
import os
import tempfile
from dataclasses import fields
from pathlib import Path

from .model import Profile, Settings
from .protocol import number

BOUNDS = {"pass_margin": (2.5, 4.5), "shot_range": (20.0, 34.0),
          "press_radius": (12.0, 30.0), "support_width": (10.0, 22.0)}


def checked_settings(data: dict) -> Settings:
    if not isinstance(data, dict) or set(data) != {f.name for f in fields(Settings)}:
        raise ValueError("profile must supply all known settings and no unknown settings")
    values = {key: number(value, key) for key, value in data.items()}
    for key, (lo, hi) in BOUNDS.items():
        if not lo <= values[key] <= hi:
            raise ValueError(f"{key} outside safe bounds {lo}..{hi}")
    return Settings(**values)


def parse_profile(data: dict) -> Profile:
    if not isinstance(data, dict) or set(data) != {"version", "settings", "opponents"}:
        raise ValueError("invalid profile fields")
    version = data["version"]
    if not isinstance(version, str) or not version.strip():
        raise ValueError("profile requires a nonempty version")
    settings = checked_settings(data["settings"])
    opponents = data["opponents"]
    if opponents is not None:
        if not isinstance(opponents, dict):
            raise ValueError("opponents must be an object")
        for name, scout in opponents.items():
            if not isinstance(name, str) or not name or not isinstance(scout, dict):
                raise ValueError("invalid opponent scout")
            if not {"weight", "samples", "settings"} <= scout.keys():
                raise ValueError("scout requires weight, samples and settings")
            weight = number(scout["weight"], "scout weight")
            if not 0 <= weight <= 1:
                raise ValueError("invalid scout weight")
            if type(scout["samples"]) is not int or scout["samples"] < 0:
                raise ValueError("invalid scout samples")
            checked_settings(scout["settings"])
    return Profile(version, settings, opponents)


def load_profile(path: str | Path) -> Profile:
    with Path(path).open() as stream:
        return parse_profile(json.load(stream))


def effective_settings(profile: Profile, opponent_id: str | None) -> Settings:
    scout = (profile.opponents or {}).get(opponent_id) if opponent_id else None
    return checked_settings(scout["settings"]) if scout else profile.settings


def save_profile(profile: Profile, path: str | Path) -> None:
    parse_profile(profile.to_dict())
    path = Path(path)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, prefix=f".{path.name}.",
                                         delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(profile.to_dict(), stream, indent=2, sort_keys=True, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
