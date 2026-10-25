#!/usr/bin/env python3
"""Shim: wraps football_agent Runtime for the local_simulator stdin/stdout protocol."""
import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO / "football_agent"))

from agent.runtime import Runtime  # noqa: E402  # pylint: disable=wrong-import-position

_rt = Runtime(emit=lambda _: None)  # suppress telemetry; not useful in local sim

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
