"""Champion / Challenger: the trainer tries one small change at a time and keeps it
only if results prove it.

league.json:
  {"next_version", "next": "champion"|"challenger", "processed": [...],
   "champion":   {"version", "results": [{"match","gd","gf","ga"}]},
   "challenger": null | {"version", "results", "lessons", "proposals", "changes", "champ_from"},
   "log": [...]}
"""
from __future__ import annotations

from typing import Optional

from agent.playbook import KNOBS, Playbook

from . import lessons as L

TRIAL_MATCHES = 3
MAX_KNOBS = 3
BREAKER_GA = 4


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def load(store) -> tuple[dict, Playbook, Optional[Playbook]]:
    lg = store.get_json("league.json") or {"next_version": 1, "next": "champion", "processed": [], "log": [],
                                            "champion": {"version": 0, "results": []}, "challenger": None}
    champ = Playbook.from_json(store.get_json("playbooks/champion.json") or Playbook().to_json())
    champ.name = "champion"
    chal = None
    if lg.get("challenger"):
        cj = store.get_json("playbooks/challenger.json")
        chal = Playbook.from_json(cj) if cj else None
        if chal is None:
            lg["challenger"] = None
    return lg, champ, chal


def plan_challenger(champ: Playbook, book: dict, proposals: list[dict]) -> tuple[dict, list[str], list[dict]]:
    """Return (knob deltas, lesson ids used, proposals used); <= MAX_KNOBS, <= one step each."""
    deltas: dict[str, float] = {}
    used_l, used_p = [], []

    def fits(d: dict) -> bool:
        new = set(deltas) | set(d)
        return len(new) <= MAX_KNOBS and not (set(d) & set(deltas))

    def effective(k: str, d: float) -> float:
        step = KNOBS[k].step
        d = max(-step, min(step, d))
        cur = champ.get(k)
        return Playbook({**champ.values, k: cur + d}).get(k) - cur

    for lid in L.ready(book):
        d = {k: effective(k, v) for k, v in L.BY_ID[lid].delta.items()}
        d = {k: v for k, v in d.items() if abs(v) > 1e-9}
        if d and fits(d):
            deltas.update(d)
            used_l.append(lid)
    for p in proposals:
        d = {p["knob"]: effective(p["knob"], p["delta"])}
        if abs(d[p["knob"]]) > 1e-9 and fits(d):
            deltas.update(d)
            used_p.append(p)
    return deltas, used_l, used_p


def record_and_plan(store, book: dict, match_id: str, played: tuple[str, int], gf: int, ga: int,
                    proposals: list[dict]) -> dict:
    lg, champ, chal = load(store)
    res = {"match": match_id, "gd": gf - ga, "gf": gf, "ga": ga}
    name, ver = played
    decision: dict = {"match": match_id, "played": f"{name}:{ver}", "result": res}
    if name == "challenger" and chal is not None and ver == lg["challenger"]["version"]:
        lg["challenger"]["results"].append(res)
    elif name == "champion" and ver == lg["champion"]["version"]:
        lg["champion"]["results"] = (lg["champion"]["results"] + [res])[-30:]
    else:
        decision["note"] = "result from a stale playbook; not counted"

    # ---- judge the running trial
    if chal is not None:
        c = lg["challenger"]
        verdict = None
        if name == "challenger" and ver == c["version"] and ga >= BREAKER_GA:
            verdict = ("discard", f"circuit breaker: conceded {ga}")
        elif len(c["results"]) >= TRIAL_MATCHES:
            ch_res = lg["champion"]["results"][c.get("champ_from", 0):] or lg["champion"]["results"][-TRIAL_MATCHES:]
            c_gd, h_gd = _mean([r["gd"] for r in c["results"]]), _mean([r["gd"] for r in ch_res])
            c_gf, h_gf = _mean([r["gf"] for r in c["results"]]), _mean([r["gf"] for r in ch_res])
            if c_gd > h_gd or (c_gd == h_gd and c_gf > h_gf):
                verdict = ("promote", f"challenger gd {c_gd:+.2f} (gf {c_gf:.2f}) vs champion {h_gd:+.2f} ({h_gf:.2f})")
            else:
                verdict = ("discard", f"challenger gd {c_gd:+.2f} (gf {c_gf:.2f}) vs champion {h_gd:+.2f} ({h_gf:.2f})")
        if verdict:
            kind, why = verdict
            decision["verdict"] = {"kind": kind, "why": why, "version": c["version"], "changes": c["changes"]}
            if kind == "promote":
                champ = Playbook(dict(chal.values), c["version"], "champion",
                                 (chal.notes + [f"promoted after {match_id}: {why}"])[-20:])
                lg["champion"] = {"version": c["version"], "results": list(c["results"])}
                L.mark(book, c["lessons"], "adopted")
                store.put_json("playbooks/champion.json", champ.to_json())
                store.put_json(f"playbooks/history/v{champ.version:04d}-champion.json", champ.to_json())
            else:
                L.mark(book, c["lessons"], "hurt")
            lg["challenger"], chal = None, None

    # ---- open a new trial when idle
    if chal is None:
        deltas, used_l, used_p = plan_challenger(champ, book, proposals)
        if deltas:
            ver = lg["next_version"]
            lg["next_version"] = ver + 1
            notes = [f"lesson {i}: {L.BY_ID[i].title}" for i in used_l] + [f"analyst: {p['knob']} {p['delta']:+} {p['reason']}" for p in used_p]
            chal = champ.with_nudges(deltas)
            chal.name, chal.version, chal.notes = "challenger", ver, notes
            changes = {k: [champ.get(k), chal.get(k)] for k in deltas}
            lg["challenger"] = {"version": ver, "results": [], "lessons": used_l, "proposals": used_p,
                                "changes": changes, "champ_from": len(lg["champion"]["results"])}
            L.mark(book, used_l, "trial", ver)
            store.put_json("playbooks/challenger.json", chal.to_json())
            store.put_json(f"playbooks/history/v{ver:04d}-challenger.json", chal.to_json())
            decision["new_challenger"] = {"version": ver, "changes": changes, "notes": notes}
            lg["next"] = "challenger"
        else:
            lg["next"] = "champion"
    else:
        lg["next"] = "champion" if name == "challenger" else "challenger"

    nxt = chal if lg["next"] == "challenger" and chal is not None else champ
    store.put_json("active.json", {"name": nxt.name, "version": nxt.version, "playbook": nxt.to_json()})
    if store.get_json("playbooks/champion.json") is None:
        store.put_json("playbooks/champion.json", champ.to_json())
    decision["next"] = f"{nxt.name}:{nxt.version}"
    lg["processed"] = (lg.get("processed", []) + [match_id])[-300:]
    lg["log"] = (lg.get("log", []) + [decision])[-50:]
    store.put_json("league.json", lg)
    return decision
