from __future__ import annotations

import math
import os

from .model import DEFAULT_SETTINGS, DEFAULT_STYLE, Decision, Player, Settings, Snapshot, Style
from .protocol import MATCH_COORDINATE_SCALE, MATCH_Y_LIMIT


def distance(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def point(p: Player, horizon: float = 0.0) -> tuple[float, float]:
    return p.x + p.vx * horizon, p.y + p.vy * horizon


def segment_distance(
    p: tuple[float, float], a: tuple[float, float], b: tuple[float, float]
) -> float:
    dx, dy = b[0] - a[0], b[1] - a[1]
    scale = dx * dx + dy * dy
    t = max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / scale)) if scale else 0
    return distance(p, (a[0] + dx * t, a[1] + dy * t))


def clearance(
    src: tuple[float, float], dst: tuple[float, float], players: tuple[Player, ...]
) -> float:
    # Conservatively consider both current and short-horizon lane occupancy.
    return min(
        (segment_distance(point(p, t), src, dst) for p in players for t in (0.0, 0.8)), default=99.0
    )


def arrival(p: Player, target: tuple[float, float]) -> float:
    return distance(point(p, 0.8), target) / (4.0 + 2.0 * p.stamina)


def command(s: Snapshot, pid: int, kind: str, **params) -> dict:
    return {
        "commandType": kind,
        "teamId": s.team,
        "playerId": pid,
        "parameters": params,
        "duration": 0,
    }


def movement(
    s: Snapshot,
    p: Player,
    x: float,
    y: float,
    urgent: bool = False,
    *,
    sprint_distance: float = 5.0,
) -> dict:
    x, y = max(-54.0, min(54.0, x)), max(-34.0, min(34.0, y))
    sprint = urgent and p.stamina >= 0.35 and distance(point(p), (x, y)) > sprint_distance
    return command(s, p.id, "MOVE_TO", target_x=round(x, 2), target_y=round(y, 2), sprint=sprint)


# Outfield roles per formation: (role, mirrored). LINK is the withdrawn forward.
ROLES = {
    "1-1-2": {1: ("DEF", False), 2: ("MID", False), 3: ("LINK", False), 4: ("FWD", False)},
    "2-1-1": {1: ("DEF", False), 2: ("DEF", True), 3: ("MID", False), 4: ("FWD", False)},
    "1-2-1": {1: ("DEF", False), 2: ("MID", False), 3: ("MID", True), 4: ("FWD", False)},
}


def role(pid: int, formation: str = "1-1-2") -> tuple[str, bool]:
    return ROLES[formation][pid]


def attack_cap(s: Snapshot, pid: int, style: Style = DEFAULT_STYLE) -> float | None:
    """Furthest attack-direction x the style allows this outfield player to move to."""
    if pid == 0:
        return None
    if s.owner is not None and s.owner[0] == s.team:
        return None if pid in (4, 3, 2)[: style.attackers_committed] else 0.0
    return -50 + 96 * style.block_depth if style.block_depth < 1 else None


def capped(s: Snapshot, x: float, cap: float | None) -> float:
    return x if cap is None else s.direction * min(s.direction * x, cap)


def tackle_ok(s: Snapshot, p: Player, style: Style = DEFAULT_STYLE) -> bool:
    if style.tackle_aggression <= 0 or s.owner is None or s.owner[0] == s.team:
        return False
    carrier = next(o for o in s.opponents if o.id == s.owner[1])
    dx, dy = p.x - carrier.x, p.y - carrier.y
    gap = math.hypot(dx, dy)
    if gap > 1 + 2.5 * style.tackle_aggression:
        return False
    # Same test as the simulator: within 60 degrees of the carrier's backward direction.
    behind = gap > 0 and s.direction * dx / gap > 0.5
    return not behind or style.tackle_aggression >= 0.7


def posture(s: Snapshot) -> str:
    diff = s.score[s.team] - s.score[1 - s.team]
    late = s.clock >= 0.75 * s.duration
    if s.coach == "go_all_out_attack" or (late and diff < 0):
        return "chase"
    return "protect" if s.coach == "slow_the_tempo" or (late and diff > 0) else "balanced"


def assignments(snapshot: Snapshot, settings: Settings = DEFAULT_SETTINGS) -> dict:
    s = snapshot
    outfield = tuple(p for p in s.ours if p.id != 0)
    stamina_rot = _int_env("STAMINA_ROT", 1)
    min_stamina = 0.12 if stamina_rot else 0.15
    active = tuple(p for p in outfield if p.stamina >= min_stamina) or outfield
    predicted = (s.ball[0] + 0.8 * s.velocity[0], s.ball[1] + 0.8 * s.velocity[1])
    presser = None
    if s.owner is not None and s.owner[0] != s.team:
        radius = min(30, settings.press_radius + (5 if s.coach == "press_high" else 0))
        if stamina_rot:
            best = min(active, key=lambda p: (distance(point(p), s.ball) + 15.0 * (1.0 - p.stamina), p.id))
        else:
            best = min(active, key=lambda p: (arrival(p, s.ball), p.id))
            if s.direction * s.ball[0] > -30 and distance(point(best), s.ball) > 3:
                fresh = tuple(
                    p for p in active
                    if p.stamina >= best.stamina + 0.2
                    and arrival(p, s.ball) <= arrival(best, s.ball) + 0.35
                    and distance(point(p), s.ball) <= radius
                )
                if fresh:
                    best = min(fresh, key=lambda p: (-p.stamina, arrival(p, s.ball), p.id))
        if distance(point(best), s.ball) <= radius:
            presser = best.id
    cover = 2 if presser == 1 else 1
    interceptor = None
    if s.free:
        interceptor = min(active, key=lambda p: (arrival(p, predicted), p.id)).id
        if interceptor == cover:
            cover = 2 if interceptor == 1 else 1
    threats = sorted(
        (p for p in s.opponents if p.id != 0 and (p.team, p.id) != s.owner),
        key=lambda p: (s.direction * p.x + 0.35 * abs(p.y), p.id),
    )
    available = [p for p in outfield if p.id not in (presser, cover)]
    marks = {}
    if s.owner is not None and s.owner[0] != s.team:
        for threat in threats[: len(available)]:
            marker = min(available, key=lambda p: (distance(point(p), point(threat)), p.id))
            marks[marker.id] = threat.id
            available.remove(marker)
    # §6 COUNTER_PRESS: second presser when ball in opp half, closest non-presser within 10m, stamina >= 0.3
    presser2 = None
    if _int_env("COUNTER_PRESS", 1) and presser is not None and s.direction * s.ball[0] >= 0:
        candidates = [
            p for p in active
            if p.id != presser
            and p.stamina >= 0.3
            and distance(point(p), s.ball) <= 10.0
        ]
        if candidates:
            presser2 = min(candidates, key=lambda p: (distance(point(p), s.ball), p.id)).id
    # §3 COVER: cover player 6m behind presser toward own goal when ball in own half (progress < -11)
    cover_pos = None
    if _int_env("COVER", 1) and presser is not None and s.direction * s.ball[0] < -11.0:
        presser_p = next((p for p in s.ours if p.id == presser), None)
        if presser_p is not None and distance(point(presser_p), s.ball) <= 12.0:
            own_goal = (-s.direction * 55.0, 0.0)
            dx = own_goal[0] - presser_p.x
            dy = own_goal[1] - presser_p.y
            dist_to_goal = math.hypot(dx, dy)
            if dist_to_goal > 0.5:
                ux, uy = dx / dist_to_goal, dy / dist_to_goal
                cx = presser_p.x + 6.0 * ux
                cy = presser_p.y + 6.0 * uy
                cy = max(presser_p.y - 4.0, min(presser_p.y + 4.0, cy))
                cover_pos = (cx, cy)
    # §8 FWD_ROLES: assign link/runner to players 3 and 4
    fwd3_role = "none"
    fwd4_role = "none"
    if _int_env("FWD_ROLES", 1):
        p3 = next((p for p in s.ours if p.id == 3), None)
        p4 = next((p for p in s.ours if p.id == 4), None)
        if p3 is not None and p4 is not None:
            carrier_id = s.owner[1] if s.owner is not None and s.owner[0] == s.team else None
            if carrier_id == 3:
                fwd3_role, fwd4_role = "carrier", "runner"
            elif carrier_id == 4:
                fwd3_role, fwd4_role = "runner", "carrier"
            else:
                # pid3 has 3m runner bias: subtract 3 from its effective distance
                dist3_adj = distance(point(p3), s.ball) - 3.0
                dist4_adj = distance(point(p4), s.ball)
                if dist3_adj >= dist4_adj:
                    fwd3_role, fwd4_role = "link", "runner"
                else:
                    fwd3_role, fwd4_role = "runner", "link"
    return {
        "presser": presser, "cover": cover, "interceptor": interceptor, "marks": marks,
        "presser2": presser2, "cover_pos": cover_pos,
        "fwd3_role": fwd3_role, "fwd4_role": fwd4_role,
    }


def shot_option(s: Snapshot, p: Player, settings: Settings) -> tuple[float, dict, str] | None:
    goal = (s.direction * 55, 0.0)
    gap = 55 - s.direction * p.x
    limit = settings.shot_range + (3 if posture(s) == "chase" else 0)
    if s.coach == "shoot_on_sight":
        limit = min(34, limit + 3)
    keeper = next(o for o in s.opponents if o.id == 0)
    stranded = s.direction * (p.x - point(keeper, 0.8)[0]) > 8
    if stranded:
        limit = min(50, limit + 18)
    if gap <= 0 or distance(point(p), goal) > limit:
        return None
    angle = abs(math.atan2(3 - p.y, gap) - math.atan2(-3 - p.y, gap))
    if angle < 0.12:
        return None
    choices = []
    targets: tuple[tuple[float, str], ...] = ((-2.6, "BL"), (2.6, "BR"))
    if stranded:
        targets = (*targets, (0.0, "CENTER"))
    for y, aim in targets:
        target = (goal[0], y)
        lane = clearance(point(p), target, s.opponents)
        if lane >= 1.8:
            keeper_gap = abs(y - (keeper.y + 0.25 * keeper.vy))
            quality = 66 + 35 * angle + min(keeper_gap, 6) * 2 - 0.25 * gap
            choices.append(
                (
                    quality,
                    command(s, p.id, "SHOOT", aim_location=aim, power=0.9),
                    f"open shot; angle={angle:.2f}, lane={lane:.1f}"
                    + ("; stranded keeper" if stranded else ""),
                )
            )
    return (
        max(
            choices,
            key=lambda item: (
                stranded and item[1]["parameters"]["aim_location"] == "CENTER",
                item[0],
                item[1]["parameters"]["aim_location"],
            ),
        )
        if choices
        else None
    )


def carry_option(s: Snapshot, p: Player, style: Style) -> tuple[float, dict, str]:
    """Reward an unpressured clear run without overriding shots or passing under pressure."""
    ahead = [distance(point(p), point(o)) for o in s.opponents if s.direction * (o.x - p.x) > 0]
    space = min([12.0, *ahead])
    urgent = (
        p.stamina >= 0.55
        or min(distance(point(p, t), point(o, t)) for o in s.opponents for t in (0.0, 0.8)) <= 8
        or distance(point(p), (s.direction * 55.0, 0.0)) <= 29
    )
    move = movement(s, p, s.direction * 50.0, max(-10.0, min(10.0, p.y * 0.6)), urgent=urgent)
    target = (move["parameters"]["target_x"], move["parameters"]["target_y"])
    length = distance(point(p), target)
    fraction = min(1.0, 12.0 / length) if length else 0.0
    next_point = (p.x + (target[0] - p.x) * fraction, p.y + (target[1] - p.y) * fraction)
    lane = clearance(point(p), next_point, s.opponents)
    escape = False
    if lane < 3:
        y_limit = MATCH_Y_LIMIT * MATCH_COORDINATE_SCALE
        routes = [
            (
                max(-50.0, min(50.0, p.x + s.direction * dx)),
                max(-y_limit, min(y_limit, p.y + dy)),
            )
            for dx in (8, 0)
            for dy in (-8, 8)
        ]
        routes = [xy for xy in routes if 5 <= distance(point(p), xy) <= 12]
        route = max(
            routes,
            default=target,
            key=lambda xy: (
                clearance(point(p), xy, s.opponents),
                s.direction * (xy[0] - p.x),
                -abs(xy[1]),
            ),
        )
        route_lane = clearance(point(p), route, s.opponents)
        if route_lane >= 3 and route_lane >= lane + 1:
            target, lane = route, route_lane
            length = distance(point(p), target)
            move = movement(s, p, target[0], target[1], urgent=urgent)
            escape = True
    clear_run = (
        length > 5
        and s.direction * (target[0] - p.x) > 0
        and lane >= 6
        and style.tempo >= 0.5
        and posture(s) != "protect"
    )
    return (
        style.carry * (30 + space + (24 if clear_run else 0)),
        move,
        f"carry: {space:.1f} m free ahead; lane={lane:.1f}"
        + ("; clear forward run" if clear_run else "")
        + ("; escape blocked route" if escape else ""),
    )


def carrier_options(
    s: Snapshot,
    p: Player,
    settings: Settings,
    style: Style = DEFAULT_STYLE,
    *,
    gk_as_field: bool = False,
) -> list[tuple[float, dict, str]]:
    options = []
    keeper_outlets = []
    is_gk = p.id == 0 and not gk_as_field
    if style.carry > 0 and not is_gk:
        options.append(carry_option(s, p, style))
    shot = shot_option(s, p, settings)
    if shot is not None and not is_gk:
        options.append(shot)
    for mate in s.ours:
        if mate.id == p.id:
            continue
        length = distance(point(p), point(mate))
        if not is_gk and (length < 3 or length > 60):
            continue
        lane = clearance(point(p), point(mate), s.opponents)
        pressure = min(distance(point(mate, 0.5), point(o, 0.5)) for o in s.opponents)
        safe = 3 <= length <= 60 and min(lane, pressure) >= settings.pass_margin
        if not safe and not is_gk:
            continue
        gain = s.direction * (mate.x - p.x)
        score = 22 + min(pressure, 12) * 2 + 0.6 * gain + min(lane, 8)
        if style.tempo != 0.5:
            score += (style.tempo - 0.5) * 1.2 * gain
        if mate.id == 0:
            score -= 18
        if p.id == 1 and abs(mate.y) > 8:
            score += 5
        if p.id == 2 and p.y * mate.y < 0:
            score += 5
        if posture(s) == "protect":
            score += min(pressure, 12) - max(0, gain) * 0.3
        high_line = sum(s.direction * o.x < 0 for o in s.opponents if o.id != 0) >= 3
        through = high_line and mate.id in (3, 4) and gain > 10 and pressure > 6
        if through:
            score += 12
        kind = "GK_DISTRIBUTE" if is_gk else "PASS"
        params: dict[str, int | str] = {"target_player_id": mate.id}
        if is_gk:
            params["method"] = "THROW" if safe and length < 25 else "KICK"
        else:
            params["type"] = (
                "THROUGH" if through else ("AERIAL" if length > 35 and lane > 5 else "GROUND")
            )
        candidate = (
            score,
            command(s, p.id, kind, **params),
            f"{'safe counter' if through else 'safe'} outlet to {mate.id}; lane={lane:.1f}"
            if safe
            else f"contested keeper outlet to {mate.id}; lane={lane:.1f}, pressure={pressure:.1f}",
        )
        if safe:
            options.append(candidate)
        elif is_gk:
            keeper_outlets.append(candidate)
    calm = min(distance(point(p), point(o)) for o in s.opponents) > 8
    hold = max(0.0, 0.5 - style.tempo) * 120 if calm else 0.0
    safe_keeper_outlet = is_gk and bool(options)
    if is_gk:
        options = options or keeper_outlets
    else:
        options.append(
            (
                hold,
                command(s, p.id, "SET_STANCE", stance=0),
                "hold: slow tempo, no opponent within 8 m"
                if hold
                else "hold balanced: no safe supported possession action",
            )
        )
    return sorted(
        options,
        key=lambda item: (
            -s.direction * s.player(item[1]["parameters"]["target_player_id"]).x
            if safe_keeper_outlet
            else 0,
            -item[0],
            item[1]["commandType"],
            item[1]["parameters"].get("target_player_id", -1),
        ),
    )


def support(
    s: Snapshot, p: Player, settings: Settings, plan: dict, formation: str = "1-1-2"
) -> tuple[float, float]:
    d = s.direction
    bp = d * s.ball[0]
    shift = -6 if posture(s) == "protect" else 5 if posture(s) == "chase" else 0
    defending = s.owner is None or s.owner[0] != s.team
    ballside = _int_env("BALLSIDE", 1) and defending
    if p.id == plan["cover"]:
        return d * max(-54, min(-8, bp - 9)), max(-10, min(10, s.ball[1] * 0.25))
    kind, mirrored = role(p.id, formation)
    flip = -1 if mirrored else 1
    if kind == "DEF":
        base_y = flip * (-8 if s.ball[1] > 0 else 8)
        if ballside:
            base_y += 0.50 * (s.ball[1] - base_y)
        return d * max(-42, min(-10, bp - 18)), base_y
    if kind == "MID":
        base_y = flip * (-1 if s.ball[1] >= 0 else 1) * settings.support_width * 0.65
        if ballside:
            base_y += 0.50 * (s.ball[1] - base_y)
        return d * max(-30, min(23 if defending else 40, bp - 9 + shift)), base_y
    link = kind == "LINK"
    progress = bp + (5 if link else 17) + shift
    if defending:
        progress = min(progress, bp + 5)
    y = (-1 if link else 1) * settings.support_width
    if ballside:
        y += 0.20 * (s.ball[1] - y)
    return d * max(-20, min(46, progress)), y


def _gk_line_pos(s: Snapshot) -> tuple[float, float]:
    """GK on line from own goal center to ball at clamp(2.5 + 0.12*dist, 2.5, 7.0)m.
    Tracks projected ball position (0.5s lookahead) to follow moving balls."""
    d = s.direction
    own_goal = (-d * _HALF_LENGTH, 0.0)
    bx = s.ball[0] + 0.5 * s.velocity[0] if s.free else s.ball[0]
    by = s.ball[1] + 0.5 * s.velocity[1] if s.free else s.ball[1]
    bx = max(-_HALF_LENGTH, min(_HALF_LENGTH, bx))
    by = max(-_HALF_WIDTH, min(_HALF_WIDTH, by))
    dx, dy = bx - own_goal[0], by - own_goal[1]
    dist_ball = math.hypot(dx, dy)
    gkd = max(2.5, min(7.0, 2.5 + 0.12 * dist_ball))
    if dist_ball > 0.5:
        return own_goal[0] + gkd * dx / dist_ball, own_goal[1] + gkd * dy / dist_ball
    return own_goal[0] + gkd * d, own_goal[1]


_HALF_LENGTH = 55.0
_HALF_WIDTH = 34.0


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (ValueError, TypeError):
        return default


def _tactical_context(s: Snapshot) -> tuple[int, int]:
    """(rush: 0/1, stage: 0/1/2). Pure function of snapshot + env vars; no side effects."""
    rush_s = _int_env("RUSH_S", 15)
    desperate_s = _int_env("DESPERATE_S", 30)
    gk_up_s = _int_env("GK_UP_S", 10)
    remaining = s.duration - s.clock
    our_score = s.score[s.team]
    opp_score = s.score[1 - s.team]
    losing = opp_score > our_score
    ball_not_at_opp = s.owner is None or s.owner[0] == s.team
    rush = int(
        bool(_int_env("RUSH_ENABLED", 1))
        and s.clock < rush_s
        and our_score == 0 and opp_score == 0
        and ball_not_at_opp
    )
    if losing and remaining <= desperate_s:
        stage = 2 if (remaining <= gk_up_s and bool(_int_env("DRAW_AT_ANY_COST", 0))) else 1
    else:
        stage = 0
    return rush, stage


def decide(
    snapshot: Snapshot,
    player_id: int,
    settings: Settings = DEFAULT_SETTINGS,
    style: Style = DEFAULT_STYLE,
    *,
    max_move_x: float = 54.0,
) -> Decision:
    s, p = snapshot, snapshot.player(player_id)
    rush, stage = _tactical_context(s)
    plan = {**assignments(s, settings), "rush": rush, "stage": stage}
    # Per-player link/runner role for telemetry and positioning
    plan["role"] = plan.get("fwd3_role", "none") if player_id == 3 else (
        plan.get("fwd4_role", "none") if player_id == 4 else "none"
    )
    if s.owner == (s.team, player_id):
        choices = (
            carrier_options(s, p, settings, style, gk_as_field=True)
            if player_id == 0 and stage == 2
            else carrier_options(s, p, settings, style)
        )
        _, selected, reason = choices[0]
        alternatives = tuple(
            {"score": round(score, 3), "command": cmd, "reason": why} for score, cmd, why in choices
        )
        return Decision(selected, reason, alternatives, plan)
    if player_id == 0:
        if stage == 2:
            x = s.direction * 0.10 * _HALF_LENGTH
            y = max(-6.0, min(6.0, s.ball[1] * 0.3))
            return Decision(movement(s, p, x, y, urgent=True), "stage 2: GK advances", assignments=plan)
        d = s.direction
        x_goal = -d * min(52.0, max_move_x)
        depth = 55 + d * x_goal
        y = s.ball[1] * depth / max(depth, d * s.ball[0] + 55)
        incoming = False
        if s.free and d * s.velocity[0] < -0.5:
            t = (x_goal - s.ball[0]) / s.velocity[0]
            if 0 < t < 3:
                y = s.ball[1] + s.velocity[1] * t
                goal_t = (-d * 55 - s.ball[0]) / s.velocity[0]
                goal_y = s.ball[1] + s.velocity[1] * goal_t
                incoming = abs(y) <= 2.8 or (0 < goal_t < 3 and abs(goal_y) <= 3)
        y = max(-2.8, min(2.8, y))
        if _int_env("GK_LINE", 1) and not incoming:
            # Exception: ball free in own penalty area and GK closest → old goal-line behavior
            _PENALTY_DEPTH = 16.5
            in_own_penalty = d * s.ball[0] < -(_HALF_LENGTH - _PENALTY_DEPTH)
            if not (s.free and in_own_penalty and all(
                distance(point(q), s.ball) >= distance(point(p), s.ball)
                for q in s.players if q.team == s.team and q.id != 0
            )):
                lx, ly = _gk_line_pos(s)
                own_goal = (-d * _HALF_LENGTH, 0.0)
                plan["gkd"] = round(math.hypot(lx - own_goal[0], ly - own_goal[1]), 2)
                return Decision(
                    movement(s, p, lx, ly, urgent=False, sprint_distance=0.0),
                    "keeper covers goal angle / projected shot",
                    assignments=plan,
                )
        return Decision(
            movement(s, p, x_goal, y, urgent=incoming, sprint_distance=0.0),
            "keeper covers goal angle / projected shot",
            assignments=plan,
        )
    if s.free and plan["interceptor"] == player_id:
        return Decision(
            command(s, player_id, "INTERCEPT", aggressive=p.stamina > 0.4),
            "best predicted outfield arrival",
            assignments=plan,
        )
    if plan["presser"] == player_id:
        if s.owner is not None and tackle_ok(s, p, style):
            return Decision(
                command(s, player_id, "SLIDE_TACKLE", target_player_id=s.owner[1]),
                f"slide tackle; aggression={style.tackle_aggression:.1f}",
                assignments=plan,
            )
        return Decision(
            command(s, player_id, "PRESS_BALL", intensity=0.8 if p.stamina >= 0.35 else 0.5),
            "designated press; teammate retains cover",
            assignments=plan,
        )
    # §6 COUNTER_PRESS: second presser also presses
    if plan.get("presser2") == player_id:
        if s.owner is not None and tackle_ok(s, p, style):
            return Decision(
                command(s, player_id, "SLIDE_TACKLE", target_player_id=s.owner[1]),
                "counter-press slide tackle",
                assignments={**plan, "cp": 1},
            )
        return Decision(
            command(s, player_id, "PRESS_BALL", intensity=0.8 if p.stamina >= 0.35 else 0.5),
            "counter-press second presser",
            assignments={**plan, "cp": 1},
        )
    cap = attack_cap(s, player_id, style)
    if player_id in plan["marks"]:
        threat = next(o for o in s.opponents if o.id == plan["marks"][player_id])
        if cap is not None and s.direction * threat.x > cap:
            return Decision(
                movement(s, p, capped(s, threat.x, cap), threat.y, urgent=True),
                "hold block line; runner beyond cap",
                assignments=plan,
            )
        # §2 LANE_BLOCK: move to intercept point on ball→receiver lane
        if _int_env("LANE_BLOCK", 1):
            bx, by = s.ball
            tx, ty = threat.x, threat.y
            marker_dist = distance(point(p), point(threat))
            if marker_dist > 2.5:
                ix = bx + 0.65 * (tx - bx)
                iy = by + 0.65 * (ty - by)
                if math.hypot(ix - bx, iy - by) >= 4.0:
                    return Decision(
                        movement(s, p, ix, iy, urgent=True),
                        "lane block intercept",
                        assignments={**plan, "lb": 1},
                    )
        return Decision(
            command(
                s,
                player_id,
                "MARK",
                target_player_id=plan["marks"][player_id],
                tightness="TIGHT" if posture(s) != "chase" else "LOOSE",
            ),
            "distinct reachable runner assignment",
            assignments=plan,
        )
    x, y = support(s, p, settings, plan, style.formation)
    if rush and player_id == 2:
        x, y = s.direction * 0.30 * _HALF_LENGTH, 0.0
    elif rush and player_id in (3, 4):
        lat = -0.22 * _HALF_WIDTH if player_id == 3 else 0.22 * _HALF_WIDTH
        x, y = s.direction * 0.62 * _HALF_LENGTH, lat
    if stage >= 1 and player_id == 1:
        x, y = s.direction * (-0.08) * _HALF_LENGTH, 0.0
    elif stage >= 1 and player_id == 2:
        x, y = s.direction * 0.45 * _HALF_LENGTH, 0.0
    # §8 FWD_ROLES: position link/runner when not in rush/stage
    if not rush and stage == 0 and _int_env("FWD_ROLES", 1) and player_id in (3, 4):
        fwd_role = plan.get("role", "none")
        if fwd_role in ("link", "runner"):
            d = s.direction
            bp = d * s.ball[0]
            defending = s.owner is None or s.owner[0] != s.team
            shift = -6 if posture(s) == "protect" else 5 if posture(s) == "chase" else 0
            sw = settings.support_width
            if fwd_role == "link":
                progress = min(bp + 5 + shift, bp + 5) if defending else bp + 5 + shift
                x, y = d * max(-20, min(46, progress)), -sw
            else:
                progress = min(bp + 17 + shift, bp + 5) if defending else bp + 17 + shift
                x, y = d * max(-20, min(46, progress)), sw
            bx, by = s.ball
            dist_ball = math.hypot(x - bx, y - by)
            if 0 < dist_ball < 6.0:
                x = bx + 6.0 * (x - bx) / dist_ball
                y = by + 6.0 * (y - by) / dist_ball
            elif dist_ball < 0.01:
                x = bx + d * 6.0
    # §3 COVER: override cover player position when cover_pos is set
    cover_pos = plan.get("cover_pos")
    if player_id == plan["cover"] and cover_pos is not None and _int_env("COVER", 1):
        x, y = cover_pos
        plan = {**plan, "cov": 1}
    x = capped(s, x, cap)
    return Decision(
        movement(s, p, x, y, urgent=s.owner is not None and s.owner[0] != s.team),
        "central cover" if player_id == plan["cover"] else "diagonal / depth support",
        assignments=plan,
    )
