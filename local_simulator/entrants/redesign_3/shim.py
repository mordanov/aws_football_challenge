#!/usr/bin/env python3
"""Per-team JSONL shim for redesign_3.
One call to handle() returns all 5 players' commands.
Resets trainer state between matches.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from agent.main import handle, reset_match_state  # noqa: E402


reset_match_state(preserve_cross_match_learning=False)

for raw in sys.stdin:
    raw = raw.strip()
    if not raw:
        continue
    msg = json.loads(raw)
    if msg.get("_command") == "stop":
        break
    if msg.get("_command") == "reset":
        reset_match_state(preserve_cross_match_learning=False)
        print(json.dumps({"ok": True}), flush=True)
        continue
    obs = msg["obs"]
    obs["myPlayers"] = list(range(5))
    cmds = handle(obs)
    print(json.dumps(cmds), flush=True)
