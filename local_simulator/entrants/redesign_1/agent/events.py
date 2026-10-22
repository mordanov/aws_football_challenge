"""Compact snapshots and event detection, shared by the in-match scout and the
post-match trainer (one implementation, so in-match and post-match learning
agree on what happened).

Snapshot (JSON-friendly, internal frame = we attack +x):
  {"t": gameTime, "k": tick, "su": int, "st": int, "pm": playMode,
   "ball": [x, y], "own": ["us"|"them", id] | None,
   "us":   {"<id>": [x, y, stamina]},
   "them": {"<id>": [x, y, stamina, lastAction, sprinting]},
   "cmd":  {"<id>": [commandType, params]}}       # commands WE issued this tick
"""
from __future__ import annotations

import math
from typing import Any, Optional

from .world import HALF_L, World


def snapshot(w: World, cmds: dict[int, dict]) -> dict:
    r = lambda v: round(v, 1)  # noqa: E731
    return {
        "t": round(w.game_time, 2), "k": w.tick, "su": w.score_us, "st": w.score_them, "pm": w.play_mode,
        "ball": [r(w.ball[0]), r(w.ball[1])],
        "own": list(w.owner) if w.owner else None,
        "us": {str(p.id): [r(p.x), r(p.y), round(p.stamina, 2)] for p in w.all_ours},
        "them": {str(p.id): [r(p.x), r(p.y), round(p.stamina, 2), p.last_action, p.sprinting] for p in w.opps},
        "cmd": {str(k): [c["commandType"], c.get("parameters", {})] for k, c in cmds.items()},
    }


def zone(x: float) -> str:
    return "own" if x < -HALF_L / 3 else "opp" if x > HALF_L / 3 else "mid"


def _seg(p, a, b) -> float:
    (px, py), (ax, ay), (bx, by) = p, a, b
    dx, dy = bx - ax, by - ay
    l2 = dx * dx + dy * dy
    t = 0.0 if l2 == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / l2))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def _pos(snap: dict, side: str, pid: Any) -> Optional[tuple[float, float]]:
    v = snap.get(side, {}).get(str(pid))
    return (v[0], v[1]) if v else None


def _owner(snap: dict) -> Optional[tuple[str, int]]:
    o = snap.get("own")
    return (o[0], int(o[1])) if o else None


def detect(prev: dict, cur: dict) -> list[dict]:
    """Events between two consecutive snapshots (~2 s apart)."""
    ev: list[dict] = []
    po, co = _owner(prev), _owner(cur)
    goal_for = cur.get("su", 0) > prev.get("su", 0)
    goal_against = cur.get("st", 0) > prev.get("st", 0)
    explained = False
    t = cur.get("t", 0)

    if po and po[0] == "us":
        cmd = prev.get("cmd", {}).get(str(po[1]))
        kind = cmd[0] if cmd else None
        src = _pos(prev, "us", po[1]) or tuple(prev["ball"])
        if kind in ("PASS", "GK_DISTRIBUTE"):
            tgt = cmd[1].get("target_player_id")
            dst = _pos(prev, "us", tgt)
            if dst and co is not None:
                opps = [(v[0], v[1]) for v in prev.get("them", {}).values()]
                clr = min((_seg(o, src, dst) for o in opps), default=99.0)
                ok = co[0] == "us"
                ev.append({"type": "PASS_OK" if ok else "PASS_LOST", "t": t, "from": po[1], "to": tgt,
                           "len": round(math.dist(src, dst), 1), "clr": round(clr, 1), "zone": zone(src[0]),
                           "kind": cmd[1].get("type", cmd[1].get("method", "")), "gk": kind == "GK_DISTRIBUTE"})
                explained = not ok
        elif kind == "SHOOT":
            out = "goal" if goal_for else "saved" if co == ("them", 0) else "miss"
            field = [(v[0], v[1]) for k, v in prev.get("them", {}).items() if k != "0"]
            clr = min((_seg(o, src, (HALF_L, 0)) for o in field), default=99.0)
            ev.append({"type": "SHOT", "t": t, "by": po[1], "dist": round(math.dist(src, (HALF_L, 0)), 1),
                       "clr": round(clr, 1), "aim": cmd[1].get("aim_location"), "power": cmd[1].get("power"),
                       "outcome": out})
            explained = True

    if po and co and po[0] != co[0] and not goal_for and not goal_against:
        bx = prev["ball"][0]
        if po[0] == "us" and not explained:
            src = _pos(prev, "us", po[1]) or tuple(prev["ball"])
            near = min((math.dist(src, (v[0], v[1])) for v in prev.get("them", {}).values()), default=99)
            ev.append({"type": "TURNOVER", "t": t, "by": po[1], "zone": zone(bx), "pressed": near <= 3.0})
        elif po[0] == "them":
            ev.append({"type": "WON_BALL", "t": t, "zone": zone(bx), "by": co[1]})

    if goal_for or goal_against:
        ours_up = sum(1 for k, v in prev.get("us", {}).items() if k != "0" and v[0] > 0)
        ev.append({"type": "GOAL_FOR" if goal_for else "GOAL_AGAINST", "t": t,
                   "flank": "L" if prev["ball"][1] > 5 else "R" if prev["ball"][1] < -5 else "C",
                   "counter": goal_against and ours_up >= 3, "ours_up": ours_up})
    return ev


ACTION_FAMILIES = ("win", "mark", "move", "dribble", "ball", "other")


def action_family(a: str) -> str:
    a = (a or "").lower()
    if any(k in a for k in ("press", "intercept", "tackle", "chase")):
        return "win"
    if any(k in a for k in ("mark", "follow")):
        return "mark"
    if "dribble" in a:
        return "dribble"
    if any(k in a for k in ("pass", "shoot", "distribute", "kick", "throw", "clear")):
        return "ball"
    if any(k in a for k in ("move", "roam", "space", "stay", "position", "idle", "return")):
        return "move"
    return "other"


def opp_features(snap: dict) -> dict:
    """Per-tick observations of the opponent (the 'eagle eye')."""
    them = snap.get("them", {})
    outf = [v for k, v in them.items() if k != "0"]
    gk = them.get("0")
    own = _owner(snap)
    f: dict[str, Any] = {
        "line": sum(v[0] for v in outf) / len(outf) if outf else 0.0,
        "gk_off": (HALF_L - gk[0]) if gk else 0.0,
        "sprint": sum(1 for v in them.values() if len(v) > 4 and v[4]) / max(1, len(them)),
        "fam": [action_family(v[3]) for v in them.values() if len(v) > 3],
        "they_have": bool(own and own[0] == "them"),
        "carrier_y": None,
        "pressers": None,
    }
    if own and own[0] == "them":
        p = them.get(str(own[1]))
        f["carrier_y"] = p[1] if p else None
    elif own and own[0] == "us":
        c = _pos(snap, "us", own[1])
        if c:
            f["pressers"] = sum(1 for v in outf if math.dist(c, (v[0], v[1])) <= 4.0)
    return f
