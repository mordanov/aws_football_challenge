"""Pure tactical math and implicit team coordination. No I/O.

All five players run these functions on the same snapshot and the same BASE
playbook, so they reach the same answer (one presser, distinct markers, one
interceptor) without messaging. In-match nudges never reach these functions.
"""
from __future__ import annotations

import math
from typing import Optional

from .playbook import Playbook
from .world import GOAL_HALF_WIDTH, HALF_L, HALF_W, OUTFIELD, Pl, World


# ---------------------------------------------------------------- geometry
def dist(a, b) -> float:
    ax, ay = (a.x, a.y) if isinstance(a, Pl) else a
    bx, by = (b.x, b.y) if isinstance(b, Pl) else b
    return math.hypot(ax - bx, ay - by)


def seg_dist(p, a, b) -> float:
    px, py = p
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    l2 = dx * dx + dy * dy
    if l2 == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / l2))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def lane_clearance(src, dst, opps: list[Pl], skip: Optional[int] = None) -> float:
    ds = [seg_dist((o.x, o.y), src, dst) for o in opps if o.id != skip]
    return min(ds) if ds else 99.0


def nearest(pt, players: list[Pl]) -> tuple[Optional[Pl], float]:
    best, bd = None, 1e9
    for p in players:
        d = dist(pt, p)
        if d < bd:
            best, bd = p, d
    return best, bd


def clamp_xy(x: float, y: float, mx: float = 0.95, my: float = 0.95) -> tuple[float, float]:
    return max(-mx * HALF_L, min(mx * HALF_L, x)), max(-my * HALF_W, min(my * HALF_W, y))


def opp_gk(w: World) -> Optional[Pl]:
    return w.opp(0) or (max(w.opps, key=lambda o: o.x) if w.opps else None)


def ball_soon(w: World, secs: float = 1.0) -> tuple[float, float]:
    """Where the ball will roughly be (decisions persist ~2 s)."""
    return clamp_xy(w.ball[0] + w.ball_vel[0] * secs, w.ball[1] + w.ball_vel[1] * secs, 1.0, 1.0)


# ------------------------------------------------------------------ posture
def coach_flags(w: World) -> dict[str, bool]:
    c = w.coach.lower()
    return {
        "chase": any(k in c for k in ("all_out", "all out", "go_all", "attack more", "need a goal")),
        "protect": any(k in c for k in ("slow_the_tempo", "slow the tempo", "protect", "defensive", "possession")),
        "press": any(k in c for k in ("press_high", "press high", "press higher", "win the ball back")),
        "shoot": any(k in c for k in ("shoot_on_sight", "shoot on sight", "shoot more")),
        "counter": "counter" in c,
    }


def opps_in_our_half(w: World) -> int:
    return sum(1 for o in w.opps if o.id != 0 and o.x < 0)


def posture(w: World, pb: Playbook) -> str:
    """protect | chase | counter | build - same answer for all five players."""
    diff = w.score_us - w.score_them
    late = (w.match_len - w.game_time) < 0.2 * w.match_len
    cf = coach_flags(w)
    if cf["chase"] or (diff < 0 and late):
        return "chase"
    if cf["protect"] or (diff > 0 and late):
        return "protect"
    if cf["counter"] or opps_in_our_half(w) >= pb.get("counter_trigger"):
        return "counter"
    return "build"


# --------------------------------------------------------------- team roles
def presser_id(w: World, pb: Playbook) -> Optional[int]:
    target = w.ball
    pool = []
    for p in w.all_ours:
        if p.id not in OUTFIELD or p.stamina < 0.12:
            continue
        if p.id == 1 and target[0] > pb.get("press_height") * HALF_L:
            continue  # last defender stays home when the ball is upfield
        pool.append(p)
    pool = pool or [p for p in w.all_ours if p.id in OUTFIELD]
    if not pool:
        return None
    return min(pool, key=lambda p: (round(dist(p, target), 1), p.id)).id


def interceptor_id(w: World) -> Optional[int]:
    target = ball_soon(w)
    pool = [p for p in w.all_ours if p.id in OUTFIELD and p.stamina >= 0.1]
    gk = w.ours(0)
    if gk is not None and target[0] < -HALF_L + 16.5 and abs(target[1]) < 20:
        pool.append(gk)
    if not pool:
        return None
    return min(pool, key=lambda p: (round(dist(p, target), 1), p.id)).id


def marker_assignments(w: World, pb: Playbook) -> dict[int, int]:
    """{our_id: opp_id}: DEF then MID take the most advanced off-ball threats."""
    pid = presser_id(w, pb)
    carrier = w.owner[1] if w.they_have_ball else None
    threats = sorted((o for o in w.opps if o.id != 0 and o.id != carrier), key=lambda o: (o.x, o.id))
    markers = [i for i in (1, 2) if i != pid and w.ours(i) is not None]
    return {m: t.id for m, t in zip(markers, threats)}


# ------------------------------------------------------------------- anchors
_BASE = {1: (-0.55, 0.0), 2: (-0.10, 0.0), 3: (0.42, -0.40), 4: (0.42, 0.40)}
_SHIFT = {"protect": -0.15, "counter": -0.08, "build": 0.03, "chase": 0.18}


def anchor(w: World, pid: int, pb: Playbook, post: str) -> tuple[float, float]:
    bx, by = w.ball
    if pid == 0:
        return keeper_spot(w)
    fx, fy = _BASE.get(pid, (0.0, 0.0))
    if pid == 1:
        fx = pb.get("def_line")
    fx += _SHIFT[post] + pb.get("shape_shift")
    if coach_flags(w)["press"]:
        fx += 0.10
    follow = 0.2 if pid in (3, 4) else 0.3
    fx += follow * (bx / HALF_L - fx)
    if pid in (1, 2):
        fx = min(fx, bx / HALF_L + 0.05)  # never ahead of the ball when defending
    fx = max(-0.85, min(0.80, fx))
    y = fy * HALF_W + (0.35 if pid in (1, 2) else 0.15) * by
    if pid in (1, 2):
        y += pb.get("lateral_bias") * HALF_W
    if w.we_have_ball and pid in (3, 4):  # attacking runs ahead of the ball
        fx = max(fx, min(0.75, bx / HALF_L + 0.25))
    return clamp_xy(fx * HALF_L, y, 0.9, 0.9)


def keeper_spot(w: World) -> tuple[float, float]:
    gx = -HALF_L
    bx, by = ball_soon(w, 0.5)
    ang = math.atan2(by, bx - gx)
    r = 2.5 if bx > -20 else 4.0
    x, y = gx + r * math.cos(ang), r * math.sin(ang)
    return x, max(-GOAL_HALF_WIDTH, min(GOAL_HALF_WIDTH, y))


# ------------------------------------------------------------------ shooting
def best_aim(w: World, src: tuple[float, float], tick_seed: int = 0) -> tuple[str, float]:
    """(aim label, clearance excluding keeper). Attacker's left = +y (internal frame).

    The keeper is handled by preferring the corner farther from him; top/bottom
    is mixed deterministically so opponents cannot read us (crazy idea D1).
    """
    gk = opp_gk(w)
    field = [o for o in w.opps if gk is None or o.id != gk.id]
    best = ("BL", -1e9, 0.0)
    for side, ty in (("L", GOAL_HALF_WIDTH * 0.7), ("R", -GOAL_HALF_WIDTH * 0.7)):
        clr = lane_clearance(src, (HALF_L, ty), field)
        gk_gap = abs((gk.y if gk else 0.0) - ty)
        score = min(clr, 6.0) + 0.6 * gk_gap
        if score > best[1]:
            best = (side, score, clr)
    side, _, clr = best
    gk_off_line = gk is not None and (HALF_L - gk.x) > 8
    height = "T" if gk_off_line or (tick_seed % 3 == 0) else "B"
    return height + side, clr


def shot_lane_ok(w: World, src: tuple[float, float]) -> float:
    return best_aim(w, src)[1]


# ------------------------------------------------------------------ passing
def runner_open_ahead(w: World, m: Pl) -> bool:
    return sum(1 for o in w.opps if o.x > m.x - 1.0) <= 1


def stamina_ok(p: Pl, pb: Playbook) -> bool:
    return p.stamina >= pb.get("sprint_stamina_min")
