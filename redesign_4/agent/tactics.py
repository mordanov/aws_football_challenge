from __future__ import annotations

import math

from .model import DEFAULT_SETTINGS, DEFAULT_STYLE, Decision, Player, Settings, Snapshot, Style


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
    active = tuple(p for p in outfield if p.stamina >= 0.15) or outfield
    predicted = (s.ball[0] + 0.8 * s.velocity[0], s.ball[1] + 0.8 * s.velocity[1])
    presser = None
    if s.owner is not None and s.owner[0] != s.team:
        best = min(active, key=lambda p: (arrival(p, s.ball), p.id))
        radius = min(30, settings.press_radius + (5 if s.coach == "press_high" else 0))
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
    return {"presser": presser, "cover": cover, "interceptor": interceptor, "marks": marks}


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
    move = movement(s, p, s.direction * 50.0, max(-10.0, min(10.0, p.y * 0.6)), urgent=True)
    target = (move["parameters"]["target_x"], move["parameters"]["target_y"])
    length = distance(point(p), target)
    fraction = min(1.0, 12.0 / length) if length else 0.0
    next_point = (p.x + (target[0] - p.x) * fraction, p.y + (target[1] - p.y) * fraction)
    lane = clearance(point(p), next_point, s.opponents)
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
        + ("; clear forward run" if clear_run else ""),
    )


def carrier_options(
    s: Snapshot, p: Player, settings: Settings, style: Style = DEFAULT_STYLE
) -> list[tuple[float, dict, str]]:
    options = []
    keeper_outlets = []
    if style.carry > 0 and p.id != 0:
        options.append(carry_option(s, p, style))
    shot = shot_option(s, p, settings)
    if shot is not None and p.id != 0:
        options.append(shot)
    for mate in s.ours:
        if mate.id == p.id:
            continue
        length = distance(point(p), point(mate))
        if p.id != 0 and (length < 3 or length > 60):
            continue
        lane = clearance(point(p), point(mate), s.opponents)
        pressure = min(distance(point(mate, 0.5), point(o, 0.5)) for o in s.opponents)
        safe = 3 <= length <= 60 and min(lane, pressure) >= settings.pass_margin
        if not safe and p.id != 0:
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
        kind = "GK_DISTRIBUTE" if p.id == 0 else "PASS"
        params: dict[str, int | str] = {"target_player_id": mate.id}
        if p.id == 0:
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
        elif p.id == 0:
            keeper_outlets.append(candidate)
    calm = min(distance(point(p), point(o)) for o in s.opponents) > 8
    hold = max(0.0, 0.5 - style.tempo) * 120 if calm else 0.0
    safe_keeper_outlet = p.id == 0 and bool(options)
    if p.id == 0:
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
    if p.id == plan["cover"]:
        return d * max(-54, min(-8, bp - 9)), max(-10, min(10, s.ball[1] * 0.25))
    kind, mirrored = role(p.id, formation)
    flip = -1 if mirrored else 1
    if kind == "DEF":
        return d * max(-42, min(-10, bp - 18)), flip * (-8 if s.ball[1] > 0 else 8)
    if kind == "MID":
        return d * max(-30, min(23 if defending else 40, bp - 9 + shift)), flip * (
            -1 if s.ball[1] >= 0 else 1
        ) * settings.support_width * 0.65
    link = kind == "LINK"
    progress = bp + (5 if link else 17) + shift
    if defending:
        progress = min(progress, bp + 5)
    y = (-1 if link else 1) * settings.support_width
    return d * max(-20, min(46, progress)), y


def decide(
    snapshot: Snapshot,
    player_id: int,
    settings: Settings = DEFAULT_SETTINGS,
    style: Style = DEFAULT_STYLE,
    *,
    max_move_x: float = 54.0,
) -> Decision:
    s, p = snapshot, snapshot.player(player_id)
    plan = assignments(s, settings)
    if s.owner == (s.team, player_id):
        choices = carrier_options(s, p, settings, style)
        _, selected, reason = choices[0]
        alternatives = tuple(
            {"score": round(score, 3), "command": cmd, "reason": why} for score, cmd, why in choices
        )
        return Decision(selected, reason, alternatives, plan)
    if player_id == 0:
        d = s.direction
        x = -d * min(52.0, max_move_x)
        depth = 55 + d * x
        y = s.ball[1] * depth / max(depth, d * s.ball[0] + 55)
        incoming = False
        if s.free and d * s.velocity[0] < -0.5:
            t = (x - s.ball[0]) / s.velocity[0]
            if 0 < t < 3:
                y = s.ball[1] + s.velocity[1] * t
                goal_t = (-d * 55 - s.ball[0]) / s.velocity[0]
                goal_y = s.ball[1] + s.velocity[1] * goal_t
                incoming = abs(y) <= 2.8 or (0 < goal_t < 3 and abs(goal_y) <= 3)
        y = max(-2.8, min(2.8, y))
        return Decision(
            movement(s, p, x, y, urgent=incoming, sprint_distance=0.0),
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
    cap = attack_cap(s, player_id, style)
    if player_id in plan["marks"]:
        threat = next(o for o in s.opponents if o.id == plan["marks"][player_id])
        if cap is not None and s.direction * threat.x > cap:
            return Decision(
                movement(s, p, capped(s, threat.x, cap), threat.y, urgent=True),
                "hold block line; runner beyond cap",
                assignments=plan,
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
    x = capped(s, x, cap)
    return Decision(
        movement(s, p, x, y, urgent=s.owner is not None and s.owner[0] != s.team),
        "central cover" if player_id == plan["cover"] else "diagonal / depth support",
        assignments=plan,
    )
