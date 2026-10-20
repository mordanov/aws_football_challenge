from __future__ import annotations

import math

from .model import DEFAULT_SETTINGS, Decision, Player, Settings, Snapshot


def distance(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def point(p: Player, horizon: float = 0.0) -> tuple[float, float]:
    return p.x + p.vx * horizon, p.y + p.vy * horizon


def segment_distance(p: tuple[float, float], a: tuple[float, float],
                     b: tuple[float, float]) -> float:
    dx, dy = b[0] - a[0], b[1] - a[1]
    scale = dx * dx + dy * dy
    t = max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / scale)) if scale else 0
    return distance(p, (a[0] + dx * t, a[1] + dy * t))


def clearance(src: tuple[float, float], dst: tuple[float, float],
              players: tuple[Player, ...]) -> float:
    # Conservatively consider both current and short-horizon lane occupancy.
    return min((segment_distance(point(p, t), src, dst)
                for p in players for t in (0.0, 0.8)), default=99.0)


def arrival(p: Player, target: tuple[float, float]) -> float:
    return distance(point(p, 0.8), target) / (4.0 + 2.0 * p.stamina)


def command(s: Snapshot, pid: int, kind: str, **params) -> dict:
    return {"commandType": kind, "teamId": s.team, "playerId": pid,
            "parameters": params, "duration": 0}


def movement(s: Snapshot, p: Player, x: float, y: float, urgent: bool = False) -> dict:
    x, y = max(-54.0, min(54.0, x)), max(-34.0, min(34.0, y))
    sprint = urgent and p.stamina >= 0.35 and distance(point(p), (x, y)) > 5
    return command(s, p.id, "MOVE_TO", target_x=round(x, 2), target_y=round(y, 2), sprint=sprint)


def posture(s: Snapshot) -> str:
    diff = s.score[s.team] - s.score[1 - s.team]
    late = s.clock >= 0.75 * s.duration
    if late and diff < 0:
        return "chase"
    return "protect" if late and diff > 0 else "balanced"


def assignments(snapshot: Snapshot, settings: Settings = DEFAULT_SETTINGS) -> dict:
    s = snapshot
    outfield = tuple(p for p in s.ours if p.id != 0)
    active = tuple(p for p in outfield if p.stamina >= 0.15) or outfield
    predicted = (s.ball[0] + 0.8 * s.velocity[0], s.ball[1] + 0.8 * s.velocity[1])
    presser = None
    if s.owner is not None and s.owner[0] != s.team:
        eligible = tuple(p for p in active if p.id != 1 or s.direction * s.ball[0] < 5) or active
        best = min(eligible, key=lambda p: (arrival(p, s.ball), p.id))
        if distance(point(best), s.ball) <= settings.press_radius:
            presser = best.id
    cover = 2 if presser == 1 else 1
    interceptor = None
    if s.free:
        pool = list(active)
        keeper = s.player(0)
        opponent_eta = min(arrival(p, predicted) for p in s.opponents)
        if (s.direction * s.ball[0] < -40 and abs(s.ball[1]) < 15
                and arrival(keeper, predicted) + 0.4 < opponent_eta
                and distance(point(keeper), predicted) < 12):
            pool.append(keeper)
        interceptor = min(pool, key=lambda p: (arrival(p, predicted), p.id)).id
        if interceptor == cover:
            cover = 2 if interceptor == 1 else 1
    threats = sorted((p for p in s.opponents if p.id != 0 and (p.team, p.id) != s.owner),
                     key=lambda p: (s.direction * p.x + 0.35 * abs(p.y), p.id))
    available = [p for p in outfield if p.id not in (presser, cover)]
    marks = {}
    if s.owner is not None and s.owner[0] != s.team:
        for threat in threats[:len(available)]:
            marker = min(available, key=lambda p: (distance(point(p), point(threat)), p.id))
            marks[marker.id] = threat.id
            available.remove(marker)
    return {"presser": presser, "cover": cover, "interceptor": interceptor, "marks": marks}


def shot_option(s: Snapshot, p: Player, settings: Settings) -> tuple[float, dict, str] | None:
    goal = (s.direction * 55, 0.0)
    gap = 55 - s.direction * p.x
    limit = settings.shot_range + (3 if posture(s) == "chase" else 0)
    if gap <= 0 or distance(point(p), goal) > limit:
        return None
    angle = abs(math.atan2(3 - p.y, gap) - math.atan2(-3 - p.y, gap))
    if angle < 0.12:
        return None
    keeper = next(o for o in s.opponents if o.id == 0)
    choices = []
    for y, aim in ((-2.6, "BL"), (2.6, "BR")):
        target = (goal[0], y)
        lane = clearance(point(p), target, s.opponents)
        if lane >= 1.8:
            keeper_gap = abs(y - (keeper.y + 0.25 * keeper.vy))
            quality = 66 + 35 * angle + min(keeper_gap, 6) * 2 - 0.25 * gap
            choices.append((quality, command(s, p.id, "SHOOT", aim_location=aim, power=0.9),
                            f"open shot; angle={angle:.2f}, lane={lane:.1f}"))
    return max(choices, key=lambda item: (item[0], item[1]["parameters"]["aim_location"])) if choices else None


def carrier_options(s: Snapshot, p: Player, settings: Settings) -> list[tuple[float, dict, str]]:
    options = []
    shot = shot_option(s, p, settings)
    if shot is not None and p.id != 0:
        options.append(shot)
    for mate in s.ours:
        if mate.id == p.id:
            continue
        length = distance(point(p), point(mate))
        if length < 3 or length > 60:
            continue
        lane = clearance(point(p), point(mate), s.opponents)
        pressure = min(distance(point(mate, 0.5), point(o, 0.5)) for o in s.opponents)
        if lane < settings.pass_margin or pressure < settings.pass_margin:
            continue
        gain = s.direction * (mate.x - p.x)
        score = 22 + min(pressure, 12) * 2 + 0.6 * gain + min(lane, 8)
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
        params = {"target_player_id": mate.id}
        if p.id == 0:
            params["method"] = "THROW" if length < 25 else "KICK"
        else:
            params["type"] = "THROUGH" if through else ("AERIAL" if length > 35 and lane > 5 else "GROUND")
        options.append((score, command(s, p.id, kind, **params),
                        f"safe {'counter ' if through else ''}outlet to {mate.id}; lane={lane:.1f}"))
    options.append((0.0, command(s, p.id, "SET_STANCE", stance=0),
                    "hold balanced: no safe supported possession action"))
    return sorted(options, key=lambda item: (-item[0], item[1]["commandType"],
                                            item[1]["parameters"].get("target_player_id", -1)))


def support(s: Snapshot, p: Player, settings: Settings, plan: dict) -> tuple[float, float]:
    d = s.direction
    bp = d * s.ball[0]
    shift = -6 if posture(s) == "protect" else 5 if posture(s) == "chase" else 0
    defending = s.owner is None or s.owner[0] != s.team
    if p.id == plan["cover"]:
        return d * max(-46, min(-8, bp - 9)), max(-10, min(10, s.ball[1] * 0.25))
    if p.id == 1:
        return d * max(-42, min(-10, bp - 18)), -8 if s.ball[1] > 0 else 8
    if p.id == 2:
        return d * max(-30, min(23, bp - 9 + shift)), (-1 if s.ball[1] >= 0 else 1) * settings.support_width * 0.65
    link = p.id == 3
    progress = bp + (5 if link else 17) + shift
    if defending:
        progress = min(progress, bp + 5)
    y = (-1 if link else 1) * settings.support_width
    return d * max(-20, min(46, progress)), y


def decide(snapshot: Snapshot, player_id: int,
           settings: Settings = DEFAULT_SETTINGS) -> Decision:
    s, p = snapshot, snapshot.player(player_id)
    plan = assignments(s, settings)
    if s.owner == (s.team, player_id):
        choices = carrier_options(s, p, settings)
        _, selected, reason = choices[0]
        alternatives = tuple({"score": round(score, 3), "command": cmd, "reason": why}
                             for score, cmd, why in choices)
        return Decision(selected, reason, alternatives, plan)
    if player_id == 0:
        if s.free and plan["interceptor"] == 0:
            return Decision(command(s, 0, "INTERCEPT", aggressive=False),
                            "sweep: safe first arrival", assignments=plan)
        d = s.direction
        x = d * -52
        y = s.ball[1] * 3 / max(3, s.direction * s.ball[0] + 55)
        if d * s.velocity[0] < -0.5:
            t = (d * -54 - s.ball[0]) / s.velocity[0]
            if 0 < t < 3:
                y = s.ball[1] + s.velocity[1] * t
        y = max(-2.8, min(2.8, y))
        return Decision(movement(s, p, x, y), "keeper covers goal angle / projected shot",
                        assignments=plan)
    if s.free and plan["interceptor"] == player_id:
        return Decision(command(s, player_id, "INTERCEPT", aggressive=p.stamina > 0.4),
                        "best predicted outfield arrival", assignments=plan)
    if plan["presser"] == player_id:
        return Decision(command(s, player_id, "PRESS_BALL",
                                intensity=0.8 if p.stamina >= 0.35 else 0.5),
                        "designated press; teammate retains cover", assignments=plan)
    if player_id in plan["marks"]:
        return Decision(command(s, player_id, "MARK", target_player_id=plan["marks"][player_id],
                                tightness="TIGHT" if posture(s) != "chase" else "LOOSE"),
                        "distinct reachable runner assignment", assignments=plan)
    x, y = support(s, p, settings, plan)
    return Decision(movement(s, p, x, y, urgent=s.owner is not None and s.owner[0] != s.team),
                    "central cover" if player_id == plan["cover"] else "diagonal / depth support",
                    assignments=plan)
