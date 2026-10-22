#!/usr/bin/env python3
"""Per-player JSONL shim for redesign_4.
One Runtime per player; emit=None disables DecisionLog background thread.
close() called on stop for clean teardown.
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
        for rt in _runtimes.values():
            rt.close(timeout=1.0)
        break
    if msg.get("_command") == "reset":
        for rt in _runtimes.values():
            rt.close(timeout=1.0)
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
