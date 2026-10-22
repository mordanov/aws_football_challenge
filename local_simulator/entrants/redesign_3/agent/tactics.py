"""Physics-aware 2-second horizon geometry, xG shot model, pass evaluator, and defensive assignments."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Iterable

from .model import (
    BASE_MAX_SPEED,
    GOAL_HALF_W,
    PENALTY_BOX_HALF_W,
    PENALTY_BOX_X,
    PITCH_HALF_L,
    PITCH_HALF_W,
    Player,
    Role,
    Vec2,
    World,
)
from .trainer import TrainerDirective

OPP_GOAL_CENTER = Vec2(PITCH_HALF_L, 0.0)
OPP_POST_TOP = Vec2(PITCH_HALF_L, -GOAL_HALF_W)
OPP_POST_BOTTOM = Vec2(PITCH_HALF_L, GOAL_HALF_W)
MY_GOAL_CENTER = Vec2(-PITCH_HALF_L, 0.0)


@dataclass(frozen=True)
class ShotEvaluation:
    should_shoot: bool
    xg: float
    aim_location: str   # "TL" | "TR" | "BL" | "BR" | "CENTER"
    power: float
    reason: str


@dataclass(frozen=True)
class PassCandidate:
    target: Player
    pass_type: str      # "GROUND" | "THROUGH" | "AERIAL"
    lead_pos: Vec2
    score: float
    safety_ratio: float
    progressive_gain: float
    reason: str


@dataclass(frozen=True)
class DefensiveAssignments:
    primary_presser_id: int | None
    secondary_presser_id: int | None
    box_protector_id: int
    mark_targets: dict[int, int]         # my_player_id -> opp_player_id
    shadow_targets: dict[int, Vec2]      # my_player_id -> goal-side blocking point


# ------------------------------------------------------------------
# 1. Physics & 2-Second Horizon Trajectory Primitives
# ------------------------------------------------------------------
def predict_ball_pos(world: World, horizon_ticks: float = 1.5) -> Vec2:
    """Project ball position over `horizon_ticks` with velocity damping and pitch bounds."""
    damping = 0.82
    dx = world.ball.vel.x * horizon_ticks * damping
    dy = world.ball.vel.y * horizon_ticks * damping
    return Vec2(world.ball.pos.x + dx, world.ball.pos.y + dy).clamp_to_pitch(0.5, 0.5)


def time_to_point(player: Player, target: Vec2, sprint: bool = True) -> float:
    """Estimated ticks required for `player` to reach `target`, accounting for stamina."""
    d = player.pos.dist(target)
    if d < 0.4:
        return 0.0
    mult = 1.45 if (sprint and player.stamina > 0.2) else 1.0
    step = max(1.8, BASE_MAX_SPEED * player.speed_factor * mult)
    return d / step


def segment_point_distance(a: Vec2, b: Vec2, p: Vec2) -> tuple[float, float]:
    """Return `(perpendicular_dist, projection_t_in_0_1)` of point `p` onto segment `a -> b`."""
    vx = b.x - a.x
    vy = b.y - a.y
    seg_len_sq = vx * vx + vy * vy
    if seg_len_sq < 1e-6:
        return a.dist(p), 0.0
    t = ((p.x - a.x) * vx + (p.y - a.y) * vy) / seg_len_sq
    t_clamped = max(0.0, min(1.0, t))
    proj = Vec2(a.x + t_clamped * vx, a.y + t_clamped * vy)
    return proj.dist(p), t


def nearest_opponent_dist(pos: Vec2, opponents: Iterable[Player], ignore_gk: bool = False) -> float:
    best = 999.0
    for opp in opponents:
        if ignore_gk and opp.id == 0:
            continue
        d = pos.dist(opp.pos)
        if d < best:
            best = d
    return best


def opponent_offside_line_x(world: World) -> float:
    """Return `x` of the deepest outfield opponent defender (closest to +55)."""
    outfield_xs = [p.pos.x for p in world.opp if p.id != 0]
    if not outfield_xs:
        return 35.0
    return max(outfield_xs)


# ------------------------------------------------------------------
# 2. Expected Goal (xG) & Corner-Targeting Shot Model
# ------------------------------------------------------------------
def evaluate_shot(
    shooter: Player,
    world: World,
    directive: TrainerDirective,
) -> ShotEvaluation:
    """Evaluate shot quality (`xG`), optimal corner (`TL`/`TR`/`BL`/`BR`/`CENTER`), and power."""
    w = directive.weights
    scouting = directive.scouting
    dist_to_goal = shooter.pos.dist(OPP_GOAL_CENTER)

    # Special Case A: Sweeper-GK Punisher!
    # If Eagle-Eye detects opponent GK is >12m off their line, shoot from up to 35m!
    opp_gk = world.opp_gk()
    gk_out_of_net = (
        opp_gk is not None
        and (opp_gk.pos.x < 43.0 or abs(opp_gk.pos.y) > 6.0)
        and shooter.pos.x > 15.0
    )
    if gk_out_of_net and dist_to_goal <= 36.0:
        # Check that no outfield defender is directly in front within 3m
        blocked_close = any(
            o.id != 0
            and segment_point_distance(shooter.pos, OPP_GOAL_CENTER, o.pos)[0] < 1.4
            and 0.05 < segment_point_distance(shooter.pos, OPP_GOAL_CENTER, o.pos)[1] < 0.85
            for o in world.opp
        )
        if not blocked_close:
            aim = "TL" if (opp_gk and opp_gk.pos.y >= 0.0) else "TR"
            return ShotEvaluation(
                should_shoot=True,
                xg=0.68,
                aim_location=aim,
                power=0.95,
                reason="sweeper_gk_open_net_punish",
            )

    # Hard distance & angle gates
    effective_max_dist = w.max_shoot_distance
    if shooter.pos.x < PITCH_HALF_L - effective_max_dist and dist_to_goal > effective_max_dist:
        return ShotEvaluation(False, 0.04, "TR", 0.75, "beyond_max_shoot_distance")

    # Compute visible angle to goal mouth
    a1 = math.atan2(OPP_POST_TOP.y - shooter.pos.y, OPP_POST_TOP.x - shooter.pos.x)
    a2 = math.atan2(OPP_POST_BOTTOM.y - shooter.pos.y, OPP_POST_BOTTOM.x - shooter.pos.x)
    visible_angle = abs(a2 - a1)

    # Check outfield shot-blockers in the shooting cone
    frozen_set = set(scouting.frozen_opponent_ids)
    blocker_penalty = 0.0
    for opp in world.opp:
        if opp.id == 0:
            continue
        perp_d, proj_t = segment_point_distance(shooter.pos, OPP_GOAL_CENTER, opp.pos)
        if 0.04 < proj_t < 0.92:
            block_radius = 1.1 if opp.id in frozen_set else 1.85
            if perp_d < block_radius:
                blocker_penalty += 0.32 * (1.0 - perp_d / block_radius)

    # Base xG from distance and angle
    dist_factor = max(0.0, 1.0 - (dist_to_goal / 28.0) ** 1.35)
    angle_factor = min(1.0, visible_angle / 0.42)
    xg = max(0.0, min(0.98, (0.65 * dist_factor + 0.35 * angle_factor) - blocker_penalty))

    # Select optimal corner farthest from opponent GK position + velocity
    gk_y = opp_gk.pos.y + opp_gk.vel.y * 0.5 if opp_gk is not None else scouting.opp_gk_offset_y
    if gk_y > 0.6:
        # GK shifted toward +y (bottom post) -> shoot at top post (-y: TL or BL)
        aim = "TL" if dist_to_goal > 14.0 else "BL"
    elif gk_y < -0.6:
        # GK shifted toward -y (top post) -> shoot at bottom post (+y: TR or BR)
        aim = "TR" if dist_to_goal > 14.0 else "BR"
    else:
        # GK centered -> shoot across our body to far post
        if shooter.pos.y < -1.5:
            aim = "TR"
        elif shooter.pos.y > 1.5:
            aim = "TL"
        else:
            aim = "TR" if (world.tick % 2 == 0) else "TL"

    # Calibrate shot power: high power from range, controlled placement close-in
    if dist_to_goal <= 11.0:
        power = 0.76
    elif dist_to_goal <= 18.0:
        power = 0.86
    else:
        power = 0.94

    # Check if a teammate has a much higher-xG tap-in (Cutback check)
    tight_angle = abs(shooter.pos.y) > 11.5 and shooter.pos.x > 36.0
    min_xg_threshold = 0.24 if not tight_angle else (0.34 + 0.15 * w.cutback_preference)
    if dist_to_goal <= 12.5 and blocker_penalty < 0.25 and abs(shooter.pos.y) <= 9.5:
        # Clear close-range central shot: always take it!
        return ShotEvaluation(True, max(xg, 0.65), aim, power, "clear_box_finish")

    should_shoot = (xg >= min_xg_threshold) and (dist_to_goal <= effective_max_dist)
    reason = "high_xg_strike" if should_shoot else "low_xg_or_blocked"
    return ShotEvaluation(should_shoot, round(xg, 3), aim, power, reason)


# ------------------------------------------------------------------
# 3. Pass Corridor, Through-Ball & Aerial Switch Evaluator
# ------------------------------------------------------------------
def evaluate_pass_options(
    carrier: Player,
    world: World,
    directive: TrainerDirective,
) -> list[PassCandidate]:
    """Rank all legal pass options (`GROUND`, `THROUGH`, `AERIAL`) for `carrier`."""
    w = directive.weights
    scouting = directive.scouting
    frozen_ids = set(scouting.frozen_opponent_ids)
    offside_x = opponent_offside_line_x(world)
    candidates: list[PassCandidate] = []

    for mate in world.me:
        if mate.id == carrier.id or mate.red_card:
            continue

        # Never pass back to GK unless emergency in deep defense
        if mate.id == int(Role.GK) and carrier.pos.x > -25.0:
            continue

        raw_dist = carrier.pos.dist(mate.pos)
        if raw_dist < 3.0 or raw_dist > 52.0:
            continue

        # Evaluate up to 2 pass types per teammate:
        # 1) Direct to feet (`GROUND` or `AERIAL`)
        # 2) Lead pass into space ahead (`THROUGH`) for forwards/midfielder
        options_to_test: list[tuple[str, Vec2]] = []

        # Standard feet/lead position
        feet_target = Vec2(
            mate.pos.x + mate.vel.x * 0.8,
            mate.pos.y + mate.vel.y * 0.8,
        ).clamp_to_pitch(2.0, 2.0)
        options_to_test.append(("GROUND", feet_target))

        # Long switch or press-bypass (`AERIAL`)
        if raw_dist >= 18.0 or scouting.press_mob_count >= 2:
            options_to_test.append(("AERIAL", feet_target))

        # Through-ball into attacking channel ahead of teammate
        if mate.id in (int(Role.FWD1), int(Role.FWD2), int(Role.MID)) and mate.pos.x > -15.0:
            lead_dx = min(10.0, max(5.0, PITCH_HALF_L - 6.0 - mate.pos.x))
            if lead_dx > 3.5:
                through_target = Vec2(
                    min(PITCH_HALF_L - 4.5, max(mate.pos.x + lead_dx, min(offside_x + 2.0, 48.0))),
                    mate.pos.y * 0.85,
                ).clamp_to_pitch(2.5, 2.5)
                options_to_test.append(("THROUGH", through_target))

        for pass_type, target_pt in options_to_test:
            pass_dist = carrier.pos.dist(target_pt)
            if pass_dist < 3.5 or pass_dist > 50.0:
                continue

            # Compute minimum interception cone ratio along `carrier.pos -> target_pt`
            min_cone_ratio = 1.0
            receiver_space = nearest_opponent_dist(target_pt, world.opp, ignore_gk=True)

            for opp in world.opp:
                perp_d, proj_t = segment_point_distance(carrier.pos, target_pt, opp.pos)
                if 0.05 <= proj_t <= 0.98:
                    # Aerial passes fly over midfield interceptors (only vulnerable near landing `proj_t > 0.78`)
                    if pass_type == "AERIAL" and 0.15 < proj_t < 0.78:
                        continue
                    eff_perp = perp_d * (1.65 if opp.id in frozen_ids else 1.0)
                    travel_to_proj = max(3.5, pass_dist * proj_t)
                    cone_ratio = eff_perp / travel_to_proj
                    if cone_ratio < min_cone_ratio:
                        min_cone_ratio = cone_ratio

            # Compare against Trainer's learned `pass_safety_margin`
            req_margin = w.pass_safety_margin
            if pass_type == "AERIAL":
                req_margin *= 0.78
            elif pass_type == "THROUGH":
                req_margin *= (1.08 - 0.25 * w.through_ball_aggression)

            if min_cone_ratio < req_margin:
                continue

            # Also verify receiver can reach `target_pt` before closest opponent for THROUGH passes
            if pass_type == "THROUGH":
                t_mate = time_to_point(mate, target_pt, sprint=True)
                t_opp = min(
                    (time_to_point(o, target_pt, sprint=True) for o in world.opp),
                    default=99.0,
                )
                if t_mate > t_opp + 0.15:
                    continue

            # Score the pass candidate
            prog_x = target_pt.x - carrier.pos.x
            dist_to_opp_goal = target_pt.dist(OPP_GOAL_CENTER)
            goal_proximity_bonus = max(0.0, (45.0 - dist_to_opp_goal) * 0.45)
            space_bonus = min(12.0, receiver_space) * 1.15
            safety_bonus = min(0.6, min_cone_ratio) * 22.0

            # Flank preference bonus from Trainer + Eagle-Eye Scouting
            flank_align = 0.0
            if w.preferred_flank < -0.15 and target_pt.y < -5.0:
                flank_align = abs(w.preferred_flank) * 4.5
            elif w.preferred_flank > 0.15 and target_pt.y > 5.0:
                flank_align = abs(w.preferred_flank) * 4.5

            # Switch-of-play bonus when escaping a congested flank
            switch_bonus = 0.0
            if (carrier.pos.y * target_pt.y < -40.0) and receiver_space > 8.0:
                switch_bonus = 5.0 + 4.0 * w.long_pass_bias

            type_bonus = 0.0
            if pass_type == "THROUGH":
                type_bonus = 6.5 * w.through_ball_aggression
            elif pass_type == "AERIAL":
                type_bonus = 4.0 * w.long_pass_bias if pass_dist > 20.0 else -2.5

            # Cutback bonus inside final third
            cutback_bonus = 0.0
            if (
                carrier.pos.x > 34.0
                and abs(carrier.pos.y) > 9.0
                and target_pt.x > 34.0
                and abs(target_pt.y) < 9.0
            ):
                cutback_bonus = 12.0 * w.cutback_preference

            total_score = (
                prog_x * 0.55
                + goal_proximity_bonus
                + space_bonus
                + safety_bonus
                + flank_align
                + switch_bonus
                + type_bonus
                + cutback_bonus
            )

            candidates.append(
                PassCandidate(
                    target=mate,
                    pass_type=pass_type,
                    lead_pos=target_pt,
                    score=round(total_score, 2),
                    safety_ratio=round(min_cone_ratio, 3),
                    progressive_gain=round(prog_x, 2),
                    reason=f"{pass_type.lower()}_to_p{mate.id}",
                )
            )

    candidates.sort(key=lambda c: c.score, reverse=True)
    return candidates


# ------------------------------------------------------------------
# 4. Coordinated Team Defensive & Pressing Assignments
# ------------------------------------------------------------------
def compute_defensive_assignments(
    world: World,
    directive: TrainerDirective,
) -> DefensiveAssignments:
    """Coordinate DEF (1), MID (2), FWD1 (3), FWD2 (4) so there is zero double-chasing
    and the central goal-side danger zone is always protected.
    """
    ball_pt = predict_ball_pos(world, horizon_ticks=1.0)
    outfield_me = [p for p in world.me if p.id != int(Role.GK) and not p.red_card]

    # 1. Designate Box Protector (almost always DEF=1, unless DEF is the only player who can stop a 1v1)
    box_protector_id = int(Role.DEF)

    # 2. Rank outfield teammates by time-to-reach predicted ball
    ranked_by_ball = sorted(outfield_me, key=lambda p: time_to_point(p, ball_pt, sprint=True))

    primary_presser_id: int | None = None
    secondary_presser_id: int | None = None

    if ranked_by_ball:
        first = ranked_by_ball[0]
        # If DEF is closest to the ball in midfield/attack, keep DEF on goal-side cover
        # and let MID or FWD press instead, UNLESS ball is deep in our defensive third!
        if (
            first.id == int(Role.DEF)
            and (ball_pt.x > -12.0 or directive.weights.box_protector_lock)
            and len(ranked_by_ball) > 1
        ):
            primary_presser_id = ranked_by_ball[1].id
            if len(ranked_by_ball) > 2 and directive.weights.press_intensity > 0.78:
                secondary_presser_id = ranked_by_ball[2].id
        else:
            primary_presser_id = first.id
            if first.id == int(Role.DEF):
                # Midfielder drops to cover the box when DEF steps out to challenge!
                box_protector_id = int(Role.MID)
            if len(ranked_by_ball) > 1 and directive.weights.press_intensity > 0.80:
                cand_sec = ranked_by_ball[1]
                if cand_sec.id != box_protector_id:
                    secondary_presser_id = cand_sec.id

    # 3. Assign off-ball opponent threats to mark / shadow-block
    carrier = world.ball_carrier()
    opp_threats = [
        o
        for o in world.opp
        if o.id != 0 and (carrier is None or o.id != carrier.id)
    ]
    # Sort opponent threats by proximity to our goal (-55, 0)
    opp_threats.sort(key=lambda o: o.pos.dist(MY_GOAL_CENTER))

    mark_targets: dict[int, int] = {}
    shadow_targets: dict[int, Vec2] = {}

    # Compute goal-side shadow-cone point between ball and our goal center
    ball_to_goal_dist = ball_pt.dist(MY_GOAL_CENTER)
    shadow_offset = min(6.5, max(2.8, ball_to_goal_dist * 0.22))
    primary_shadow_pt = ball_pt.toward(MY_GOAL_CENTER, shadow_offset).clamp_to_pitch(2.0, 2.0)

    shadow_targets[box_protector_id] = primary_shadow_pt

    # Assign remaining non-pressing players to mark the most dangerous opponent runners
    available_markers = [
        p
        for p in outfield_me
        if p.id not in (primary_presser_id, secondary_presser_id)
    ]
    for marker, threat in zip(available_markers, opp_threats):
        mark_targets[marker.id] = threat.id
        # Goal-side midpoint between threat and our goal, biased toward cutting the pass lane from ball
        goal_side_pt = threat.pos.toward(MY_GOAL_CENTER, 2.4)
        lane_pt = threat.pos.lerp(ball_pt, 0.22)
        shadow_targets[marker.id] = goal_side_pt.lerp(lane_pt, 0.35).clamp_to_pitch(2.0, 2.0)

    return DefensiveAssignments(
        primary_presser_id=primary_presser_id,
        secondary_presser_id=secondary_presser_id,
        box_protector_id=box_protector_id,
        mark_targets=mark_targets,
        shadow_targets=shadow_targets,
    )


def should_sprint(player: Player, target: Vec2, urgent: bool = False) -> bool:
    """Stamina-disciplined sprint controller."""
    dist = player.pos.dist(target)
    if dist < 2.2:
        return False
    if urgent and player.stamina > 0.18:
        return True
    if player.stamina > 0.62 and dist > 6.5:
        return True
    if player.stamina > 0.42 and dist > 14.0:
        return True
    return False
