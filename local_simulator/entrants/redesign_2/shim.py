#!/usr/bin/env python3
"""Per-player JSONL shim for redesign_2.
One Runtime instance per player; handle() returns a JSON string.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from agent.runtime import Runtime  # noqa: E402


_runtimes = {pid: Runtime(emit=lambda _: None) for pid in range(5)}


for raw in sys.stdin:
    raw = raw.strip()
    if not raw:
        continue
    msg = json.loads(raw)
    if msg.get("_command") == "stop":
        break
    if msg.get("_command") == "reset":
        # recreate runtimes for clean per-match state
        _runtimes = {pid: Runtime(emit=lambda _: None) for pid in range(5)}
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
