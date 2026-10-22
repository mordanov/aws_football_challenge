#!/usr/bin/env python3
"""Per-player JSONL shim for initial_idea.
One process controls all 5 players; iterates player IDs sequentially.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from agent.main import handle  # noqa: E402


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
    # initial_idea adapter reads teamId from inside gameState (not outer dict)
    gs = dict(obs["gameState"])
    gs["teamId"] = obs["teamId"]
    all_cmds = []
    for pid in range(5):
        player_obs = dict(obs)
        player_obs["myPlayers"] = [pid]
        player_obs["gameState"] = gs
        cmds = handle(player_obs, player_id=pid)
        all_cmds.extend(cmds)
    print(json.dumps(all_cmds), flush=True)
