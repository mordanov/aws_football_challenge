#!/usr/bin/env python3
"""Per-team JSONL shim for redesign_1.
Brain.handle reads myPlayers from payload and returns one command per player.
Passes myPlayers=[0,1,2,3,4] so one call returns all 5 commands.
Brain created with background=False to prevent refresh threads.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from agent.main import handle  # noqa: E402 — module creates Brain(background=False)


for raw in sys.stdin:
    raw = raw.strip()
    if not raw:
        continue
    msg = json.loads(raw)
    if msg.get("_command") == "stop":
        break
    if msg.get("_command") == "reset":
        print(json.dumps({"ok": True}), flush=True)
        continue
    obs = msg["obs"]
    obs["myPlayers"] = list(range(5))
    cmds = handle(obs)
    print(json.dumps(cmds), flush=True)
