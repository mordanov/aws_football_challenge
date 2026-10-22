"""Deterministic decision policy: World + Playbook -> one command.

`pb` is the effective playbook (base + in-match nudges) and drives individual
choices (shooting, passing, positioning). `team_pb` is the base playbook used
for team coordination so that all five players agree.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from . import commands as C
from . import tactics as T
from .playbook import Playbook
from .world import HALF_L, World, in_own_box


@dataclass
class Decision:
    cmd: dict
    why: str
    extras: dict = field(default_factory=dict)


@dataclass
class _Opt:
    cmd: dict
    score: float
    why: str


def decide(w: World, pb: Playbook, team_pb: Optional[Playbook] = None) -> Decision:
    team_pb = team_pb or pb
    post = T.posture(w, team_pb)
    if w.me.id == 0:
        cmd, why = _goalkeeper(w, pb, team_pb)
    elif w.i_have_ball:
        best = max(_carrier_options(w, pb, post), key=lambda o: o.score)
        cmd, why = best.cmd, best.why
    elif w.they_have_ball:
        cmd, why = _defend(w, pb, team_pb, post)
    elif w.we_have_ball:
        cmd, why = _support(w, pb, team_pb, post)
    else:
        cmd, why = _loose(w, pb, team_pb, post)
    return Decision(cmd, why, {"post": post})


# ---------------------------------------------------------------- with ball
def _carrier_options(w: World, pb: Playbook, post: str) -> list[_Opt]:
    me, pid = w.me, w.me.id
    pos = me.pos
    out: list[_Opt] = []
    rng = pb.get("shoot_range") * (1.25 if post == "chase" else 1.0)
    if T.coach_flags(w)["shoot"]:
        rng *= 1.2
    d_goal = T.dist(pos, w.opp_goal)
    aim, clr = T.best_aim(w, pos, w.tick + pid)
    need = pb.get("shot_clear_min")
    if d_goal <= rng and clr >= need:
        out.append(_Opt(C.shoot(pid, aim, pb.get("shot_power")), 100 - d_goal * 0.3, f"shoot {aim} d={d_goal:.0f} clr={clr:.1f}"))
    elif d_goal <= 0.45 * rng and clr >= 0.6 * need:
        out.append(_Opt(C.shoot(pid, aim, 1.0), 85, f"point-blank {aim} d={d_goal:.0f}"))

    _, near_opp = T.nearest(pos, w.opps)
    pressed = near_opp < 4.0
    counter = post == "counter" or T.opps_in_our_half(w) >= pb.get("counter_trigger")
    opp_line_high = _opp_line(w) < -5
    for m in w.mates:
        o = _pass_option(w, pb, m, rng, counter or opp_line_high)
        if o:
            out.append(o)

    tx, ty = me.x + 9.0, me.y * 0.85
    _, ahead = T.nearest((tx, ty), w.opps)
    if ahead > 6.0 and me.x < HALF_L - 6:
        sprint = T.stamina_ok(me, pb) or counter
        out.append(_Opt(C.move_to(pid, tx, ty, sprint), 30 + (6 if pid in (3, 4) else 0) + min(ahead, 12) * 0.5, "carry into space"))

    if pressed and me.x < -HALF_L / 3:
        fwd = max(w.mates, key=lambda p: p.x, default=None)
        if fwd is not None and fwd.id != 0:
            out.append(_Opt(C.pass_to(pid, fwd.id, "AERIAL"), 18, f"pressed in own third: lift to {fwd.id}"))
    if not out:
        out.append(_Opt(C.move_to(pid, me.x + 5, me.y * 0.8, False), 1, "shield and drift forward"))
    return out


def _pass_option(w: World, pb: Playbook, m, rng: float, through_ok: bool) -> Optional[_Opt]:
    me = w.me
    d = T.dist(me, m)
    if d < 3.0 or d > pb.get("pass_max_len"):
        return None
    clr = T.lane_clearance(me.pos, m.pos, w.opps)
    _, open_d = T.nearest(m.pos, w.opps)
    gain = (m.x - me.x) / (2 * HALF_L)
    base = 45 * gain + 2.2 * min(open_d, 12) + 0.4 * min(clr, 8)
    if T.dist(m.pos, w.opp_goal) <= rng and T.shot_lane_ok(w, m.pos) >= pb.get("shot_clear_min"):
        base += 18
    if m.id == 0:
        base -= 25
    need = pb.get("pass_lane_min")
    if through_ok and m.id in (3, 4) and T.runner_open_ahead(w, m) and clr >= 0.7 * need:
        return _Opt(C.pass_to(me.id, m.id, "THROUGH"), base + pb.get("through_bonus"), f"through ball -> {m.id}")
    if clr >= need:
        return _Opt(C.pass_to(me.id, m.id, "GROUND"), base, f"pass -> {m.id} clr={clr:.1f}")
    if d >= 12 and open_d >= 4.0:
        return _Opt(C.pass_to(me.id, m.id, "AERIAL"), base - 8 + pb.get("aerial_bonus"), f"aerial -> {m.id}")
    return None


def _opp_line(w: World) -> float:
    outf = [o.x for o in w.opps if o.id != 0] or [0.0]
    return sum(outf) / len(outf)


# --------------------------------------------------------------- goalkeeper
def _goalkeeper(w: World, pb: Playbook, team_pb: Playbook) -> tuple[dict, str]:
    me = w.me
    if w.i_have_ball:
        best, best_sc = None, -1e9
        for m in w.mates:
            d = T.dist(me, m)
            clr = T.lane_clearance(me.pos, m.pos, w.opps)
            _, open_d = T.nearest(m.pos, w.opps)
            if clr < 2.5 or d < 4:
                continue
            sc = 1.5 * min(clr, 8) + 1.5 * min(open_d, 10) + 0.15 * (m.x + HALF_L)
            if sc > best_sc:
                best, best_sc = m, sc
        if best is None:
            best = max(w.mates, key=lambda p: p.x)
        method = "THROW" if T.dist(me, best) < pb.get("gk_kick_min") else "KICK"
        return C.gk_distribute(me.id, best.id, method), f"distribute {method} -> {best.id}"
    if not w.we_have_ball and T.interceptor_id(w) == me.id:
        return C.intercept(me.id, True), "keeper claims loose ball"
    if w.they_have_ball:
        car = w.opp(w.owner[1])
        if car is not None and in_own_box(car.x, car.y) and T.dist(me, car) < 9:
            return C.press_ball(me.id, 1.0), "keeper rushes carrier"
    x, y = T.keeper_spot(w)
    return C.move_to(me.id, x, y, False), "keeper angle"


# --------------------------------------------------------- opponent has ball
def _defend(w: World, pb: Playbook, team_pb: Playbook, post: str) -> tuple[dict, str]:
    me, pid = w.me, w.me.id
    if T.presser_id(w, team_pb) == pid:
        car = w.opp(w.owner[1])
        d = T.dist(me, w.ball)
        tr = pb.get("tackle_range")
        if car is not None and tr > 0 and d <= tr and not in_own_box(me.x, me.y):
            return C.slide_tackle(pid, car.id, d), "slide tackle"
        return C.press_ball(pid, pb.get("press_intensity")), "designated presser"
    marks = T.marker_assignments(w, team_pb)
    if pid in marks and post != "chase":
        return C.mark(pid, marks[pid], pb.get("mark_tight") >= 0.5), f"mark {marks[pid]}"
    x, y = T.anchor(w, pid, pb, post)
    sprint = T.dist(me, (x, y)) > 12 and T.stamina_ok(me, pb)
    return C.move_to(pid, x, y, sprint), f"recover shape ({post})"


# ------------------------------------------------------------- we have ball
def _support(w: World, pb: Playbook, team_pb: Playbook, post: str) -> tuple[dict, str]:
    me, pid = w.me, w.me.id
    x, y = T.anchor(w, pid, pb, post)
    if pid == 2:  # MID offers a short angle behind the ball
        bx, by = w.ball
        x, y = bx - 8, by + (8 if by <= 0 else -8)
    x, y = _find_space(w, x, y)
    sprint = pid in (3, 4) and T.stamina_ok(me, pb) and T.dist(me, (x, y)) > 6
    return C.move_to(pid, x, y, sprint), f"support ({post})"


def _find_space(w: World, x: float, y: float) -> tuple[float, float]:
    """Nudge a target away from the nearest opponent (cheap 5-point search)."""
    best, best_d = (x, y), T.nearest((x, y), w.opps)[1]
    for dx, dy in ((0, 5), (0, -5), (4, 0), (-4, 0)):
        c = T.clamp_xy(x + dx, y + dy, 0.9, 0.9)
        d = T.nearest(c, w.opps)[1]
        if d > best_d + 1.0:
            best, best_d = c, d
    return best


# ------------------------------------------------------------------ loose ball
def _loose(w: World, pb: Playbook, team_pb: Playbook, post: str) -> tuple[dict, str]:
    me, pid = w.me, w.me.id
    if T.interceptor_id(w) == pid:
        return C.intercept(pid, True), "nearest to loose ball"
    if not w.open_play and w.mode_team_us:
        taker = min((p for p in w.all_ours if p.id != 0), key=lambda p: (T.dist(p, w.ball), p.id))
        if taker.id == pid:
            return C.move_to(pid, w.ball[0], w.ball[1], True), "take restart"
    x, y = T.anchor(w, pid, pb, post)
    return C.move_to(pid, x, y, False), f"shape ({post})"
