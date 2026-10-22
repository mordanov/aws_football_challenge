"""Pure tactical math. No I/O, no randomness, no LLM.

Key design idea: the five players run as five INDEPENDENT stateless agents
that all see the same snapshot. They cannot talk to each other, so team
coordination (who presses, who marks whom, who intercepts) is done by every
agent evaluating the same deterministic function on the same input and
reaching the same answer. See `presser_id`, `marker_assignments`.
"""
from __future__ import annotations

import math
from typing import Optional

from .model import Pl, World

AIM_LABELS = {-1: "LEFT", 0: "CENTER", 1: "RIGHT"}  # ASSUMPTION: verify label->side
GOAL_HALF_WIDTH = 3.0
OUTFIELD_IDS = (1, 2, 3, 4)


# ---------------------------------------------------------------- geometry
def dist(a, b) -> float:
    ax, ay = (a.x, a.y) if isinstance(a, Pl) else a
    bx, by = (b.x, b.y) if isinstance(b, Pl) else b
    return math.hypot(ax - bx, ay - by)


def seg_dist(p, a, b) -> float:
    """Distance from point p to segment a-b."""
    px, py = p
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    if L2 == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L2))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def lane_clearance(src, dst, opps: list[Pl], ignore=None) -> float:
    """Smallest distance from any opponent to the src->dst segment."""
    ds = [seg_dist((o.x, o.y), src, dst) for o in opps if o.id != ignore]
    return min(ds) if ds else 99.0


def nearest(pt, players: list[Pl]) -> tuple[Optional[Pl], float]:
    best, bd = None, 1e9
    for p in players:
        d = dist(pt, p)
        if d < bd:
            best, bd = p, d
    return best, bd


# ------------------------------------------------------------------ posture
_opp_line_ema: Optional[float] = None


def reset_memory() -> None:
    global _opp_line_ema
    _opp_line_ema = None


def opp_line(w: World) -> float:
    """Mean progress of opposing outfielders (in OUR attack frame).
    Low/negative = they are pushed up into our half (high line)."""
    global _opp_line_ema
    outf = [o for o in w.opps if o.id != 0] or w.opps
    cur = sum(w.prog(o.x) for o in outf) / max(1, len(outf))
    # Warm runtimes smooth it; cold ones just use the current tick.
    _opp_line_ema = cur if _opp_line_ema is None else 0.7 * _opp_line_ema + 0.3 * cur
    return _opp_line_ema


def coach_flags(w: World) -> dict:
    c = w.coach.lower()
    return {
        "chase": "all_out" in c or "go_all" in c,
        "protect": "slow_the_tempo" in c or "slow the tempo" in c,
        "press": "press_high" in c or "press high" in c,
        "shoot": "shoot_on_sight" in c or "shoot on sight" in c,
    }


def posture(w: World) -> str:
    """protect | chase | counter | build  (chosen identically by all 5 agents)."""
    diff = w.score_us - w.score_them
    late = (w.match_len - w.clock) < 0.25 * w.match_len
    cf = coach_flags(w)
    if cf["chase"] or (diff < 0 and late):
        return "chase"
    if cf["protect"] or (diff > 0 and late):
        return "protect"
    # Opponent pushed up -> space behind them -> counter. Deep block -> build.
    return "counter" if opp_line(w) < -0.05 * w.length / 2 else "build"


def shoot_range(w: World, post: str) -> float:
    r = 0.30 * w.length  # ~33 m: evidence says 45 m+ hoofs just donate possession
    if post == "chase":
        r *= 1.25
    if coach_flags(w)["shoot"]:
        r *= 1.3
    return r


# ------------------------------------------------------------------ shooting
def best_aim(w: World, src: tuple[float, float]) -> tuple[str, float]:
    """Pick the corner with the most free space; return (label, clearance)."""
    gx, _ = w.opp_goal
    best = ("CENTER", -1.0)
    for k, label in AIM_LABELS.items():
        tgt = (gx, k * GOAL_HALF_WIDTH * 0.8)
        c = lane_clearance(src, tgt, w.opps)
        # tiny preference for the centre: highest-percentage, avoids label risk
        c += 0.25 if k == 0 else 0.0
        if c > best[1]:
            best = (label, c)
    return best


# ------------------------------------------------------------------ passing
def pass_score(w: World, m: Pl, post: str, rng: float) -> Optional[float]:
    me = w.me
    d = dist(me, m)
    if d < 3.0 or d > 0.55 * w.length:
        return None
    clear = lane_clearance((me.x, me.y), (m.x, m.y), w.opps)
    need = 2.0 if post == "chase" else 3.2
    if clear < need:
        return None
    _, open_d = nearest((m.x, m.y), w.opps)
    gain = (w.prog(m.x) - w.prog(me.x)) / w.length
    gain_w = 22 if post == "protect" else 45
    score = gain_w * gain + 2.2 * min(open_d, 12) + 0.4 * min(clear, 8)
    if dist((m.x, m.y), w.opp_goal) <= rng:
        _, c = best_aim(w, (m.x, m.y))
        if c >= 2.2:
            score += 18  # receiver would have a shot
    if m.id == 0:
        score -= 25  # back-pass to keeper
    return score


def counter_on(w: World) -> bool:
    """3+ opponent outfielders sitting in our half -> space in behind them."""
    return sum(1 for o in w.opps if o.id != 0 and w.prog(o.x) < 0) >= 3


def runner_open_ahead(w: World, m: Pl) -> bool:
    """No opponent (bar one, their keeper) is goal-side of this teammate."""
    ahead = sum(1 for o in w.opps if w.prog(o.x) > w.prog(m.x) - 1.0)
    return ahead <= 1


# ---------------------------------------------------------------- team roles
def _eligible_press(w: World) -> list[Pl]:
    out = []
    for p in w.all_ours:
        if p.id not in OUTFIELD_IDS:
            continue
        if p.id == 1 and w.prog(w.ball[0]) > 0.15 * w.length / 2:
            continue  # last defender stays home when the ball is upfield
        if p.stamina < 0.12:
            continue
        out.append(p)
    return out


def presser_id(w: World):
    """Everyone computes this identically -> exactly one presser, no mobbing."""
    pool = _eligible_press(w) or [p for p in w.all_ours if p.id in OUTFIELD_IDS]
    if not pool:
        return None
    return min(pool, key=lambda p: (dist(p, w.ball), str(p.id))).id


def interceptor_id(w: World):
    pool = [p for p in w.all_ours if p.id in OUTFIELD_IDS and p.stamina >= 0.1]
    gk = next((p for p in w.all_ours if p.id == 0), None)
    if gk is not None and dist(gk, w.ball) < 10 and w.prog(w.ball[0]) < -0.7 * w.length / 2:
        pool.append(gk)  # keeper smothers loose balls in our box
    if not pool:
        return None
    return min(pool, key=lambda p: (dist(p, w.ball), str(p.id))).id


def marker_assignments(w: World) -> dict:
    """{our_id: opponent_id}. DEF and MID mark the two most dangerous
    off-ball opponents; forwards stay high for the counter."""
    pid = presser_id(w)
    owner_id = w.owner[1] if w.they_have_ball else None
    threats = sorted(
        (o for o in w.opps if o.id != 0 and o.id != owner_id),
        key=lambda o: w.prog(o.x),
    )
    markers = [i for i in (1, 2) if i != pid and any(p.id == i for p in w.all_ours)]
    return {m: t.id for m, t in zip(markers, threats)}


# ------------------------------------------------------------------- anchors
_BASE = {  # (progress as fraction of half-length, lateral as fraction of width)
    0: (-0.92, 0.0), 1: (-0.45, 0.0), 2: (-0.08, 0.0), 3: (0.42, -0.22), 4: (0.42, 0.22),
}
_SHIFT = {"protect": -0.18, "counter": -0.06, "build": 0.04, "chase": 0.20}


def anchor(w: World, pid, post: str) -> tuple[float, float]:
    L, W = w.length, w.width
    bp = w.prog(w.ball[0]) / (L / 2)
    if pid == 0:
        x = w.own_goal[0] + w.s * 2.5
        y = max(-6.0, min(6.0, w.ball[1] * 0.35))
        return x, y
    fx, fy = _BASE.get(pid, (0.0, 0.0))
    fx += _SHIFT[post]
    if coach_flags(w)["press"]:
        fx += 0.10
    follow = 0.15 if pid in (3, 4) else 0.25
    fx += follow * (bp - fx)
    fx = max(-0.85, min(0.85, fx))
    y = fy * W + (0.30 if pid in (1, 2) else 0.12) * w.ball[1]
    y = max(-0.45 * W, min(0.45 * W, y))
    return w.s * fx * L / 2, y
