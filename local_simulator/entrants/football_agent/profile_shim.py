#!/usr/bin/env python3
"""Profile shim: football_agent with profile loaded from AFC_PROFILE env var."""
import json
import os
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO / "football_agent"))

from agent.profile import load_profile  # noqa: E402
from agent.runtime import Runtime  # noqa: E402

_profile_path = os.environ.get("AFC_PROFILE")
_rt = Runtime(
    profile=load_profile(_profile_path) if _profile_path else None,
    emit=lambda _: None,
)

for _raw in sys.stdin:
    _raw = _raw.strip()
    if not _raw:
        continue
    _msg = json.loads(_raw)
    if _msg.get("_command") == "stop":
        break
    if _msg.get("_command") == "reset":
        print(json.dumps({"ok": True}), flush=True)
        continue
    _obs = _msg["obs"]
    _cmds: list = []
    for _pid in range(5):
        _payload = {**_obs, "myPlayers": [_pid]}
        _cmds.extend(json.loads(_rt.handle(_payload)))
    print(json.dumps(_cmds), flush=True)
