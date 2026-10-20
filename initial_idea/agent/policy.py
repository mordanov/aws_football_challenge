"""Deterministic decision policy: World -> ranked Candidates -> Decision.

Rule of thumb borrowed from teams who've played the real thing: the LLM reads
the game, the code pulls the trigger. So:
  * clear-cut situations (open shot, tackle range, keeper work, off-ball
    shape) are decided by code in microseconds and never touch the LLM;
  * only genuinely close ball-carrier choices (which pass? carry or release?)
    are optionally handed to the LLM, as a pick-one-of-N among candidates that
    are already valid, so it cannot emit an illegal command.
"""
from __future__ import annotations

from typing import Callable, Optional

from . import commands as C
from . import tactics as T
from .model import Candidate, Decision, World

DECISIVE_MARGIN = 8.0
LLMChooser = Callable[[World, list[Candidate]], Optional[int]]


def decide(w: World, llm: Optional[LLMChooser] = None) -> Decision:
    post = T.posture(w)
    cands = _candidates(w, post)
    cands.sort(key=lambda c: -c.score)
    top = cands[0]
    decisive = top.forced or len(cands) == 1 or top.score - cands[1].score >= DECISIVE_MARGIN
    if not decisive and llm is not None:
        try:
            idx = llm(w, cands[:4])
        except Exception:  # never let the brain crash the body
            idx = None
        if isinstance(idx, int) and 0 <= idx < min(4, len(cands)):
            return Decision(cands[idx].cmd, cands[idx].why, "llm", {"post": post})
    return Decision(top.cmd, top.why, "rules", {"post": post})


def _candidates(w: World, post: str) -> list[Candidate]:
    me = w.me
    if me.id == 0:
        return _goalkeeper(w, post)
    if w.i_have_ball:
        return _carrier(w, post)
    if w.they_have_ball:
        return _defend(w, post)
    return _loose(w, post)


# ---------------------------------------------------------------- with ball
def _carrier(w: World, post: str) -> list[Candidate]:
    me, pid = w.me, w.me.id
    pos = (me.x, me.y)
    rng = T.shoot_range(w, post)
    _, nearest_opp = T.nearest(pos, w.opps)
    pressed = nearest_opp < 5.0
    out: list[Candidate] = []

    d_goal = T.dist(pos, w.opp_goal)
    aim, clear = T.best_aim(w, pos)
    if d_goal <= rng:
        if clear >= 2.2:
            out.append(Candidate(C.shoot(pid, aim, 1.0), 100, f"shot {aim} clr={clear:.1f}", forced=True))
        elif d_goal <= 0.5 * rng and clear >= 1.2:
            out.append(Candidate(C.shoot(pid, aim, 1.0), 80, "point-blank", forced=True))

    on_counter = T.counter_on(w)
    for m in w.mates:
        sc = T.pass_score(w, m, post, rng)
        if sc is None:
            continue
        why = f"pass->{m.id}"
        if on_counter and m.id in (3, 4) and T.runner_open_ahead(w, m):
            sc += 30
            why += " (counter through-ball)"
        out.append(Candidate(C.pass_to(pid, m.id), sc, why))

    # Carry: only into space.
    tx = me.x + w.s * 8.0
    ty = max(-0.42 * w.width, min(0.42 * w.width, me.y))
    _, ahead_d = T.nearest((tx, ty), w.opps)
    if ahead_d > 6.0:
        out.append(Candidate(C.dribble(pid, tx, ty), 28 + (6 if pid in (3, 4) else 0), "carry into space"))

    if pressed and pid in (1, 2) and not out:
        out.append(Candidate(C.clear(pid), 38, "pressed, no outlet: clear"))
    if all(c.score < 20 for c in out) and d_goal <= rng:
        # lanes shut near goal: cut back / recycle rather than burn the chance
        out.append(Candidate(C.dribble(pid, me.x - w.s * 4, me.y), 22, "cut back"))
    if not out:
        out.append(Candidate(C.clear(pid) if pressed else C.dribble(pid, tx, ty), 5, "fallback"))
    return out


# --------------------------------------------------------------- goalkeeper
def _goalkeeper(w: World, post: str) -> list[Candidate]:
    me = w.me
    pos = (me.x, me.y)
    if w.i_have_ball:
        best, best_sc = None, -1e9
        for m in w.mates:
            if T.dist(pos, m) < 3:
                continue
            clear = T.lane_clearance(pos, (m.x, m.y), w.opps)
            if clear < 3.0:
                continue
            sc = 2.0 * min(clear, 8) + 0.1 * (w.prog(m.x) + w.length / 2)
            if sc > best_sc:
                best, best_sc = m, sc
        if best is not None:
            return [Candidate(C.gk_distribute(me.id, best.id), 90, f"distribute->{best.id}", forced=True)]
        return [Candidate(C.clear(me.id), 60, "keeper clearance", forced=True)]
    if T.interceptor_id(w) == me.id:
        return [Candidate(C.intercept(me.id), 95, "keeper smothers loose ball", forced=True)]
    if w.they_have_ball and T.dist(me, w.ball) < 6 and w.prog(w.ball[0]) < -0.75 * w.length / 2:
        return [Candidate(C.press_ball(me.id), 85, "keeper closes down", forced=True)]
    x, y = T.anchor(w, 0, post)
    return [Candidate(C.move_to(me.id, x, y), 70, "keeper tracks ball", forced=True)]


# --------------------------------------------------------- opponent has ball
def _defend(w: World, post: str) -> list[Candidate]:
    me = w.me
    pid = me.id
    if T.presser_id(w) == pid and me.stamina >= 0.12:
        d = T.dist(me, w.ball)
        if d < 2.4:
            return [Candidate(C.tackle(pid), 95, "tackle", forced=True)]
        return [Candidate(C.press_ball(pid), 90, "designated presser", forced=True)]
    marks = T.marker_assignments(w)
    if pid in marks and post != "chase":
        return [Candidate(C.mark(pid, marks[pid]), 85, f"mark opp {marks[pid]}", forced=True)]
    x, y = T.anchor(w, pid, post)
    return [Candidate(C.move_to(pid, x, y), 70, f"hold shape ({post})", forced=True)]


# ------------------------------------------------------------------ loose ball
def _loose(w: World, post: str) -> list[Candidate]:
    pid = w.me.id
    if T.interceptor_id(w) == pid:
        return [Candidate(C.intercept(pid), 92, "nearest to loose ball", forced=True)]
    x, y = T.anchor(w, pid, post)
    return [Candidate(C.move_to(pid, x, y), 70, f"shape ({post})", forced=True)]
