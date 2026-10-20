"""Load a finished match from the store and turn it into a summary (events + KPIs).

Every player runtime records its own file, and each runtime stamps its own match
id, so one real match can appear as several ids. We group ids of the same team
that started within GROUP_WINDOW_S of each other and merge them by tick.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Optional

from agent import events as E
from agent.scout import Scout
from agent.world import HALF_L

GROUP_WINDOW_S = 90
FINISHED_AFTER_S = 120  # no writes for this long -> match is over


def _started(mid: str) -> Optional[datetime]:
    try:
        return datetime.strptime(mid.split("-", 1)[1], "%Y%m%dT%H%M%S")
    except (IndexError, ValueError):
        return None


def match_ids(store) -> list[str]:
    ids = {k.split("/")[1] for k in store.list("matches/") if k.count("/") >= 2}
    return sorted(ids)


def group_matches(ids: list[str]) -> list[list[str]]:
    """[[id, id2, ...], ...]; first id of each group is the canonical one."""
    groups: list[list[str]] = []
    for mid in sorted(ids, key=lambda m: (m.split("-", 1)[0], _started(m) or datetime.min, m)):
        st = _started(mid)
        g = groups[-1] if groups else None
        if g and st and g[0].split("-", 1)[0] == mid.split("-", 1)[0]:
            g_st = _started(g[0])
            if g_st and abs((st - g_st).total_seconds()) <= GROUP_WINDOW_S:
                g.append(mid)
                continue
        groups.append([mid])
    return groups


def load_group(store, ids: list[str]) -> tuple[list[dict], dict]:
    """Merged timeline (sorted by tick, our commands united) and merged meta."""
    by_tick: dict[Any, dict] = {}
    metas: list[dict] = []
    for mid in ids:
        for key in store.list(f"matches/{mid}/"):
            name = key.rsplit("/", 1)[-1]
            if name.endswith(".jsonl"):
                for line in (store.get_text(key) or "").splitlines():
                    try:
                        s = json.loads(line)
                    except ValueError:
                        continue
                    cur = by_tick.get(s.get("k"))
                    if cur is None:
                        by_tick[s.get("k")] = s
                    else:
                        cur.setdefault("cmd", {}).update(s.get("cmd", {}))
            elif name.startswith("meta-") and name.endswith(".json"):
                m = store.get_json(key)
                if isinstance(m, dict):
                    metas.append(m)
    timeline = sorted(by_tick.values(), key=lambda s: (s.get("k") is None, s.get("k") or 0))
    meta: dict = {}
    for m in sorted(metas, key=lambda m: m.get("ticks", 0)):
        meta.update({k: v for k, v in m.items() if v is not None})
    meta["updated"] = max((m.get("updated", 0) for m in metas), default=0)
    return timeline, meta


def summarize(timeline: list[dict]) -> dict:
    """Replay the match through the in-match Scout -> events, KPIs, fingerprint."""
    sc = Scout()
    events: list[dict] = []
    for s in timeline:
        events += sc.observe(s)
    _fix_shot_outcomes(events)
    last = timeline[-1] if timeline else {}
    gf, ga = int(last.get("su", 0)), int(last.get("st", 0))
    cnt = lambda typ, **kw: sum(1 for e in events if e["type"] == typ and all(e.get(k) == v for k, v in kw.items()))  # noqa: E731
    owned = [s.get("own") for s in timeline if s.get("own")]
    poss = sum(1 for o in owned if o[0] == "us") / len(owned) if owned else 0.5
    end_t = last.get("t", 0)
    late = [s for s in timeline if s.get("t", 0) >= end_t - 60]
    late_st = [v[2] for s in late for k, v in s.get("us", {}).items() if k != "0" and len(v) > 2]
    kpis = {
        "gf": gf, "ga": ga, "gd": gf - ga, "ticks": len(timeline), "possession": round(poss, 3),
        "shots": cnt("SHOT"), "shots_on": sum(1 for e in events if e["type"] == "SHOT" and e["outcome"] != "miss"),
        "passes": cnt("PASS_OK") + cnt("PASS_LOST"), "pass_lost": cnt("PASS_LOST"),
        "turnovers": cnt("TURNOVER"), "turnovers_own": cnt("TURNOVER", zone="own"),
        "won_ball": cnt("WON_BALL"), "won_opp_half": cnt("WON_BALL", zone="opp"),
        "counter_goals_against": cnt("GOAL_AGAINST", counter=True),
        "late_stamina": round(sum(late_st) / len(late_st), 3) if late_st else 1.0,
    }
    return {"kpis": kpis, "events": events, "fingerprint": sc.fingerprint(), "scout_nudges": sc.active(),
            "timeline_len": len(timeline)}


def _fix_shot_outcomes(events: list[dict]) -> None:
    """A shot's goal may register one snapshot later (ticks are ~2 s apart)."""
    for i, e in enumerate(events):
        if e["type"] == "SHOT" and e["outcome"] == "miss":
            for nxt in events[i + 1:i + 4]:
                if nxt["type"] == "GOAL_FOR" and nxt["t"] - e["t"] <= 4.5:
                    e["outcome"] = "goal"
                    break


def carrier_near_goal_ticks(timeline: list[dict], within: float) -> int:
    n = 0
    for s in timeline:
        o = s.get("own")
        if o and o[0] == "us":
            p = s.get("us", {}).get(str(o[1]))
            if p and ((HALF_L - p[0]) ** 2 + p[1] ** 2) ** 0.5 <= within:
                n += 1
    return n


def opp_half_press_ticks(timeline: list[dict], press_height_m: float) -> int:
    return sum(1 for s in timeline if s.get("own") and s["own"][0] == "them" and s["ball"][0] > press_height_m)
