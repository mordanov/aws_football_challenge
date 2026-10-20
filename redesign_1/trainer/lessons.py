"""Lessons: our recurring errors and the playbook change that fixes each one.

A lesson *fires* when its detector passes on a match summary. Lifecycle in
lessons.json (the 'trainer's notebook'):

  watch --(fired in >= 2 matches, or once if strong)--> ready
  ready --(put into a challenger)--> trial
  trial --(challenger promoted)--> adopted   (counter resets; may fire again later)
  trial --(challenger discarded)--> hurt      (suppressed for HURT_MATCHES matches)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from agent.playbook import Playbook

from . import ingest

HURT_MATCHES = 5
READY_AFTER = 2


@dataclass(frozen=True)
class Lesson:
    id: str
    title: str
    delta: dict
    detect: Callable[[dict, list, Playbook], Optional[str]]  # -> evidence or None
    strong: bool = False


def _shots(s):
    return [e for e in s["events"] if e["type"] == "SHOT"]


def _long_shots(s, tl, pb):
    long_ = [e for e in _shots(s) if e["dist"] > pb.get("shoot_range") - 6]
    if len(long_) >= 3 and not any(e["outcome"] == "goal" for e in long_):
        return f"{len(long_)} shots from > {pb.get('shoot_range') - 6:.0f} m, 0 goals"
    return None


def _shots_blocked(s, tl, pb):
    sh = _shots(s)
    bad = [e for e in sh if e["outcome"] != "goal" and e.get("clr", 99) < 2.5]
    if len(sh) >= 3 and len(bad) / len(sh) >= 0.5:
        return f"{len(bad)}/{len(sh)} shots failed through a crowded lane"
    return None


def _passes_intercepted(s, tl, pb):
    k = s["kpis"]
    if k["passes"] >= 6 and k["pass_lost"] / k["passes"] > 0.30:
        return f"lost {k['pass_lost']}/{k['passes']} passes"
    return None


def _passing_too_safe(s, tl, pb):
    k = s["kpis"]
    if k["passes"] >= 10 and k["pass_lost"] / k["passes"] < 0.10 and k["shots"] < 3:
        return f"only {k['pass_lost']}/{k['passes']} passes lost but {k['shots']} shots - too cautious"
    return None


def _too_timid(s, tl, pb):
    near = ingest.carrier_near_goal_ticks(tl, pb.get("shoot_range") + 6)
    if s["kpis"]["shots"] < 2 and near >= 6:
        return f"{near} ticks with the ball near goal, {s['kpis']['shots']} shots"
    return None


def _counter_conceded(s, tl, pb):
    n = s["kpis"]["counter_goals_against"]
    return f"{n} goals conceded on the counter" if n >= 1 else None


def _own_third(s, tl, pb):
    n = s["kpis"]["turnovers_own"] + sum(1 for e in s["events"] if e["type"] == "PASS_LOST" and e["zone"] == "own")
    return f"{n} balls lost in our own third" if n >= 3 else None


def _gk_dist(s, tl, pb):
    n = sum(1 for e in s["events"] if e["type"] == "PASS_LOST" and e.get("gk"))
    return f"{n} keeper distributions lost" if n >= 2 else None


def _stamina(s, tl, pb):
    v = s["kpis"]["late_stamina"]
    return f"mean outfield stamina {v:.2f} in the last minute" if v < 0.25 else None


def _press_futile(s, tl, pb):
    ticks = ingest.opp_half_press_ticks(tl, pb.get("press_height") * 55)
    if s["kpis"]["won_opp_half"] == 0 and ticks >= 8:
        return f"pressed high for {ticks} ticks, won the ball there 0 times"
    return None


def _no_threat(s, tl, pb):
    k = s["kpis"]
    if k["possession"] >= 0.55 and k["shots"] <= 1:
        return f"{k['possession']:.0%} possession but {k['shots']} shots"
    return None


CATALOG: list[Lesson] = [
    Lesson("long_shots_waste", "Shooting from too far", {"shoot_range": -2}, _long_shots),
    Lesson("shots_blocked", "Shooting through bodies", {"shot_clear_min": 0.3}, _shots_blocked),
    Lesson("passes_intercepted", "Passes intercepted", {"pass_lane_min": 0.3, "pass_max_len": -3}, _passes_intercepted),
    Lesson("passing_too_safe", "Passing too safe", {"pass_lane_min": -0.3}, _passing_too_safe),
    Lesson("too_timid", "Not shooting enough", {"shoot_range": 2, "shot_clear_min": -0.3}, _too_timid),
    Lesson("counter_conceded", "Caught on the counter", {"def_line": -0.05, "press_height": -0.1}, _counter_conceded, True),
    Lesson("own_third_turnovers", "Losing the ball at the back", {"pass_lane_min": 0.3, "gk_kick_min": -5}, _own_third),
    Lesson("gk_dist_lost", "Keeper gives the ball away", {"gk_kick_min": 5}, _gk_dist),
    Lesson("stamina_collapse", "Out of gas late", {"sprint_stamina_min": 0.05}, _stamina),
    Lesson("press_futile", "High press wins nothing", {"press_height": -0.1}, _press_futile),
    Lesson("no_threat", "Sterile possession", {"shape_shift": 0.05}, _no_threat),
]
BY_ID = {l.id: l for l in CATALOG}


def evaluate(summary: dict, timeline: list, pb: Playbook) -> list[dict]:
    out = []
    for l in CATALOG:
        try:
            ev = l.detect(summary, timeline, pb)
        except (KeyError, TypeError, ZeroDivisionError):
            ev = None
        if ev:
            out.append({"id": l.id, "title": l.title, "evidence": ev, "delta": l.delta})
    return out


# ------------------------------------------------------------- notebook state
def new_book() -> dict:
    return {"match_count": 0, "lessons": {}}


def record(book: dict, fired: list[dict], match_id: str) -> dict:
    book["match_count"] = book.get("match_count", 0) + 1
    n = book["match_count"]
    for f in fired:
        e = book["lessons"].setdefault(f["id"], {"status": "watch", "fired": 0, "fired_matches": [], "evidence": [],
                                                  "hurt_until": 0, "applied_versions": []})
        e["fired"] += 1
        e["fired_matches"] = (e["fired_matches"] + [match_id])[-20:]
        e["evidence"] = (e["evidence"] + [f["evidence"]])[-5:]
        if e["status"] == "hurt" and n >= e["hurt_until"]:
            e["status"], e["fired"] = "watch", 1
        if e["status"] in ("watch", "adopted") and (e["fired"] >= READY_AFTER or BY_ID[f["id"]].strong):
            e["status"] = "ready"
    return book


def ready(book: dict) -> list[str]:
    """Ready lessons, most-evidenced first."""
    items = [(k, v) for k, v in book.get("lessons", {}).items() if v.get("status") == "ready" and k in BY_ID]
    return [k for k, _ in sorted(items, key=lambda kv: (-kv[1]["fired"], kv[0]))]


def mark(book: dict, ids: list[str], status: str, version: Optional[int] = None) -> None:
    for i in ids:
        e = book["lessons"].get(i)
        if not e:
            continue
        e["status"] = status
        if status == "trial" and version is not None:
            e["applied_versions"] = (e.get("applied_versions", []) + [version])[-10:]
        if status == "hurt":
            e["hurt_until"] = book.get("match_count", 0) + HURT_MATCHES
        if status in ("adopted", "hurt"):
            e["fired"] = 0
