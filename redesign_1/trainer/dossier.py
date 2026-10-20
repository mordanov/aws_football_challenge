"""Opponent dossiers: remember each opponent style and what worked against it.

dossiers/index.json  - compact list the runtimes read: [{key, fp, nudges}]
dossiers/<key>.json  - full record: mean fingerprint, tendencies, results, nudge stats
"""
from __future__ import annotations

from typing import Optional

from agent.scout import NUDGES, fp_distance

MAX_DIST = 0.25
FP_NAMES = ["line_height", "press_rate", "gk_off_line", "left_flank", "carrier_depth", "sprint",
            "act_win", "act_mark", "act_move", "act_dribble", "act_ball", "act_other"]


def tendencies(fp: list[float]) -> list[str]:
    f = dict(zip(FP_NAMES, fp))
    t = []
    if f["line_height"] < 0.45:
        t.append("pushes high up the pitch")
    if f["line_height"] > 0.65:
        t.append("sits in a deep block")
    if f["press_rate"] > 0.5:
        t.append("presses our carrier hard")
    if f["gk_off_line"] > 0.4:
        t.append("keeper leaves his line (shoot early / lob)")
    if f["left_flank"] > 0.65:
        t.append("attacks down their right (our left, +y)")
    if f["left_flank"] < 0.35:
        t.append("attacks down their left (our right, -y)")
    if f["carrier_depth"] > 0.7:
        t.append("carries the ball deep into our half")
    if f["sprint"] > 0.4:
        t.append("sprints a lot (will tire late)")
    if f["act_win"] > 0.35:
        t.append("ball-hunting default AI (press/intercept heavy)")
    return t


def nudge_delta(names: list[str]) -> dict[str, float]:
    out: dict[str, float] = {}
    for n in names:
        for k, v in NUDGES.get(n, {}).items():
            out[k] = round(out.get(k, 0.0) + v, 4)
    return out


def find(index: list[dict], fp: list[float], name: Optional[str] = None) -> Optional[dict]:
    if name:
        for d in index:
            if d.get("name") == name:
                return d
    if not fp:
        return None
    cands = [d for d in index if fp_distance(fp, d.get("fp", [])) < MAX_DIST]
    return min(cands, key=lambda d: fp_distance(fp, d["fp"])) if cands else None


def update(store, fp: Optional[list[float]], gd: int, active: list[str], match_id: str,
           name: Optional[str] = None) -> Optional[str]:
    """Fold one match into the matching dossier (or create one). Returns its key."""
    if not fp:
        return None
    index = store.get_json("dossiers/index.json") or []
    hit = find(index, fp, name)
    if hit is None:
        key = name or f"opp-{len(index) + 1:03d}"
        full = {"key": key, "name": name, "fp": fp, "n": 0, "results": [], "nudge_stats": {}, "matches": []}
        hit = {"key": key, "fp": fp, "nudges": {}}
        index.append(hit)
    else:
        key = hit["key"]
        full = store.get_json(f"dossiers/{key}.json") or {"key": key, "fp": hit["fp"], "n": 0, "results": [],
                                                             "nudge_stats": {}, "matches": []}
    n = full["n"] + 1
    full["fp"] = [round((a * (n - 1) + b) / n, 3) for a, b in zip(full["fp"], fp)]
    full["n"] = n
    full["results"] = (full["results"] + [gd])[-20:]
    full["matches"] = (full["matches"] + [match_id])[-20:]
    for nm in NUDGES:
        st = full["nudge_stats"].setdefault(nm, {"on": 0, "gd_on": 0, "off": 0, "gd_off": 0})
        side = "on" if nm in active else "off"
        st[side] += 1
        st["gd_" + side] += gd
    full["tendencies"] = tendencies(full["fp"])
    chosen = counter_nudges(full)
    full["counter_nudges"] = chosen
    hit.update({"fp": full["fp"], "nudges": nudge_delta(chosen), "n": n, "tendencies": full["tendencies"]})
    store.put_json(f"dossiers/{key}.json", full)
    store.put_json("dossiers/index.json", index)
    return key


def counter_nudges(full: dict) -> list[str]:
    """Pre-apply a scout nudge vs this opponent if it was usually needed and did not hurt."""
    out = []
    for nm, st in full.get("nudge_stats", {}).items():
        total = st["on"] + st["off"]
        if not total or st["on"] / total < 0.5:
            continue
        mean_on = st["gd_on"] / st["on"]
        mean_off = st["gd_off"] / st["off"] if st["off"] else mean_on
        if mean_on >= mean_off - 1:
            out.append(nm)
    return sorted(out)
