#!/usr/bin/env python3
"""Per-player JSONL shim for redesign_4 with optional profile injection (M5).

Identical to shim.py except the Profile is loaded from $AFC_PROFILE (validated by the frozen
redesign_4 validator). Without AFC_PROFILE behavior equals shim.py (the default Profile()).
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from agent.model import Profile  # noqa: E402
from agent.profile import load_profile  # noqa: E402
from agent.runtime import Runtime  # noqa: E402

_path = os.getenv("AFC_PROFILE")
_profile = load_profile(_path) if _path else Profile()


def _new_runtimes():
    return {pid: Runtime(_profile, emit=lambda _: None) for pid in range(5)}


_runtimes = _new_runtimes()

for raw in sys.stdin:
    raw = raw.strip()
    if not raw:
        continue
    msg = json.loads(raw)
    if msg.get("_command") == "stop":
        for rt in _runtimes.values():
            rt.close(timeout=1.0)
        break
    if msg.get("_command") == "reset":
        for rt in _runtimes.values():
            rt.close(timeout=1.0)
        _runtimes = _new_runtimes()
        print(json.dumps({"ok": True}), flush=True)
        continue
    obs = msg["obs"]
    all_cmds = []
    for pid in range(5):
        player_obs = dict(obs)
        player_obs["myPlayers"] = [pid]
        result = _runtimes[pid].handle(player_obs)
        cmds = json.loads(result) if isinstance(result, str) else result
        all_cmds.extend(cmds)
    print(json.dumps(all_cmds), flush=True)
