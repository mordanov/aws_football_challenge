"""Deep Role-Specific Tactical Decision Engines for GK (0), DEF (1), MID (2), FWD1 (3), FWD2 (4).

Every role implements specialized spatial geometry, off-ball movement, pressing discipline,
and on-ball execution informed by the `FootballTrainer` directive and `Eagle-Eye` scouting.
"""

from __future__ import annotations

import math

from . import commands as cmd
from .model import (
    Command,
    GOAL_HALF_W,
    PENALTY_BOX_HALF_W,
    PENALTY_BOX_X,
    PITCH_HALF_L,
    PITCH_HALF_W,
    Phase,
    Player,
    Role,
    TACKLE_RADIUS,
    Vec2,
    World,
)
from .tactics import (
    DefensiveAssignments,
    MY_GOAL_CENTER,
    OPP_GOAL_CENTER,
    evaluate_pass_options,
    evaluate_shot,
    nearest_opponent_dist,
    opponent_offside_line_x,
    predict_ball_pos,
    should_sprint,
    time_to_point,
)
from .trainer import TrainerDirective


def decide_for_player(
    me: Player,
    world: World,
    directive: TrainerDirective,
    assignments: DefensiveAssignments,
) -> tuple[Command, str]:
    """Dispatch to the specialized tactical engine for `me.role`."""
    if directive.phase == Phase.SET_PIECE:
        return _decide_set_piece(me, world, directive, assignments)

    role = me.role
    if role == Role.GK:
        return _decide_gk(me, world, directive)
    if role == Role.DEF:
        return _decide_def(me, world, directive, assignments)
    if role == Role.MID:
        return _decide_mid(me, world, directive, assignments)
    if role == Role.FWD1:
        return _decide_fwd1(me, world, directive, assignments)
    return _decide_fwd2(me, world, directive, assignments)


# ==============================================================================
# ROLE 0: GOALKEEPER (Angle-Bisector Shot-Stopper & Sweeper-Keeper Distributor)
# ==============================================================================
def _decide_gk(
    me: Player,
    world: World,
    directive: TrainerDirective,
) -> tuple[Command, str]:
    tid = world.my_team_id
    sign = world.attack_sign
    w = directive.weights
    ball_pred = predict_ball_pos(world, horizon_ticks=1.2)

    # 1. GK Has Possession -> Smart Distribution (`GK_DISTRIBUTE` or safe `PASS`)
    if me.has_ball:
        passes = evaluate_pass_options(me, world, directive)
        if passes:
            # If Trainer detects high buildup turnover risk or counter opportunity, prefer long KICK
            if w.gk_distribute_long_bias >= 0.55 or directive.scouting.press_mob_count >= 2:
                long_cands = [
                    c for c in passes if c.target.id in (int(Role.FWD1), int(Role.FWD2), int(Role.MID))
                    and c.lead_pos.x > -10.0
                ]
                if long_cands:
                    best_long = long_cands[0]
                    return (
                        cmd.gk_distribute(me.id, best_long.target.id, method="KICK", team_id=tid),
                        f"gk_kick_counter_to_p{best_long.target.id}",
                    )
            best = passes[0]
            method = "KICK" if me.pos.dist(best.target.pos) > 22.0 else "THROW"
            return (
                cmd.gk_distribute(me.id, best.target.id, method=method, team_id=tid),
                f"gk_{method.lower()}_to_p{best.target.id}",
            )

        # Fallback outlet: throw to widest defender/midfielder or kick to FWD1
        outfield = [p for p in world.me if p.id != 0]
        if outfield:
            safest = max(
                outfield,
                key=lambda p: nearest_opponent_dist(p.pos, world.opp, ignore_gk=True) + p.pos.x * 0.2,
            )
            method = "KICK" if safest.id in (3, 4) else "THROW"
            return (
                cmd.gk_distribute(me.id, safest.id, method=method, team_id=tid),
                f"gk_fallback_{method.lower()}_p{safest.id}",
            )

    # 2. Incoming Shot Ray-Cast Interception
    if world.ball.vel.x < -2.5 and world.ball.pos.x < -10.0:
        dt_to_line = (-PITCH_HALF_L - world.ball.pos.x) / world.ball.vel.x
        if 0.0 < dt_to_line <= 4.5:
            intersect_y = world.ball.pos.y + world.ball.vel.y * dt_to_line
            if abs(intersect_y) <= GOAL_HALF_W + 2.5:
                save_y = max(-GOAL_HALF_W, min(GOAL_HALF_W, intersect_y))
                return (
                    cmd.move_to(me.id, -54.0, save_y, sprint=True, attack_sign=sign, team_id=tid),
                    "gk_shot_line_save",
                )

    # 3. Sweeper-Keeper Rush on Loose Ball in Penalty Box
    in_my_box = ball_pred.x < -PENALTY_BOX_X and abs(ball_pred.y) < PENALTY_BOX_HALF_W
    if (world.ball.is_free or world.ball.owner_team is None) and in_my_box:
        gk_time = time_to_point(me, ball_pred, sprint=True)
        opp_time = min((time_to_point(o, ball_pred, sprint=True) for o in world.opp), default=99.0)
        if gk_time <= opp_time + 0.35 or me.pos.dist(ball_pred) < 6.0:
            return (
                cmd.intercept(me.id, aggressive=True, duration=1, team_id=tid),
                "gk_sweeper_claim_loose_ball",
            )

    # 4. 1v1 Breakaway Smother Inside Goal Area
    if (
        world.ball.owner_team == "opp"
        and world.ball.pos.x < -45.0
        and abs(world.ball.pos.y) < 8.5
        and me.pos.dist(world.ball.pos) < 5.5
    ):
        return (
            cmd.press_ball(me.id, intensity=0.95, duration=1, team_id=tid),
            "gk_1v1_smother",
        )

    # 5. Angle-Bisector Arc Positioning
    # Step up to -47m as a sweeper when our team attacks deep in opponent half;
    # hold -53.2m .. -50.8m on the ball-to-goal bisector when defending.
    if world.ball.pos.x > 10.0 and world.ball.owner_team == "me":
        arc_x = -47.5
        arc_y = max(-3.0, min(3.0, ball_pred.y * 0.15))
    else:
        depth_ratio = max(0.0, min(1.0, (ball_pred.x + PITCH_HALF_L) / 55.0))
        arc_x = -53.4 + 2.8 * depth_ratio
        arc_y = max(-GOAL_HALF_W + 0.4, min(GOAL_HALF_W - 0.4, ball_pred.y * 0.36))

    target = Vec2(arc_x, arc_y)
    return (
        cmd.move_to(
            me.id,
            target.x,
            target.y,
            sprint=me.pos.dist(target) > 4.0,
            attack_sign=sign,
            team_id=tid,
        ),
        "gk_bisector_arc",
    )


# ==============================================================================
# ROLE 1: DEFENDER (Shadow-Cone Stopper, Penalty-Box Guardian & Buildup Anchor)
# ==============================================================================
def _decide_def(
    me: Player,
    world: World,
    directive: TrainerDirective,
    assignments: DefensiveAssignments,
) -> tuple[Command, str]:
    tid = world.my_team_id
    sign = world.attack_sign
    w = directive.weights
    ball_pred = predict_ball_pos(world, horizon_ticks=1.2)

    # --- A. DEF Has Ball in Possession ---
    if me.has_ball:
        # 1. Rare case: DEF is in shooting range (e.g. corner rebound or sweeper GK open net)
        shot = evaluate_shot(me, world, directive)
        if shot.should_shoot and (shot.xg > 0.40 or shot.reason == "sweeper_gk_open_net_punish"):
            return (
                cmd.shoot(me.id, aim=shot.aim_location, power=shot.power, team_id=tid),
                f"def_shoot_{shot.reason}",
            )

        # 2. Progressive Pass to MID, FWD1, or FWD2
        passes = evaluate_pass_options(me, world, directive)
        if passes:
            best = passes[0]
            return (
                cmd.pass_to(me.id, best.target.id, pass_type=best.pass_type, team_id=tid),
                f"def_{best.reason}",
            )

        # 3. Under heavy pressure in our defensive third with no clean lane -> Safe Aerial Outlet to Forward
        opp_dist = nearest_opponent_dist(me.pos, world.opp, ignore_gk=True)
        if opp_dist < w.dribble_caution_radius and me.pos.x < -12.0:
            fwds = [p for p in world.me if p.id in (int(Role.FWD1), int(Role.FWD2), int(Role.MID))]
            if fwds:
                best_outlet = max(
                    fwds,
                    key=lambda p: p.pos.x + nearest_opponent_dist(p.pos, world.opp, ignore_gk=True),
                )
                return (
                    cmd.pass_to(me.id, best_outlet.id, pass_type="AERIAL", team_id=tid),
                    f"def_press_escape_aerial_p{best_outlet.id}",
                )

        # 4. Carry ball into open wide lane away from nearest opponent
        carry_y = -14.0 if w.preferred_flank <= 0 else 14.0
        if world.opp:
            nearest_opp = min(world.opp, key=lambda o: me.pos.dist(o.pos))
            if abs(nearest_opp.pos.y - me.pos.y) < 5.0:
                carry_y = -16.0 if nearest_opp.pos.y >= me.pos.y else 16.0
        carry_target = Vec2(min(10.0, me.pos.x + 10.0), carry_y).clamp_to_pitch(3.0, 3.0)
        return (
            cmd.dribble_to(me.id, carry_target.x, carry_target.y, sprint=True, attack_sign=sign, team_id=tid),
            "def_progressive_carry",
        )

    # --- B. Loose Ball Claim if DEF is Fastest ---
    if directive.phase == Phase.LOOSE_BALL and assignments.primary_presser_id == me.id:
        return (
            cmd.intercept(me.id, aggressive=True, duration=1, team_id=tid),
            "def_intercept_loose_ball",
        )

    # --- C. Team in Possession (Off-Ball Rest-Defense & Buildup Support) ---
    if directive.phase in (Phase.IN_POSSESSION, Phase.TRANSITION_ATTACK):
        # Provide safe back-pass triangle angle while screening opponent's highest counter-attacker
        opp_cherry_picker = max(
            (o for o in world.opp if o.id != 0),
            key=lambda o: -o.pos.x,  # closest to our goal (-55)
            default=None,
        )
        max_def_x = min(6.0 + w.defensive_line_offset * 0.6, world.ball.pos.x - 12.0)
        base_x = max(-40.0, max_def_x)
        if opp_cherry_picker is not None and opp_cherry_picker.pos.x < 5.0:
            # Shadow-mark the counter-attacking threat on the goal-side
            guard_x = min(base_x, opp_cherry_picker.pos.x - 3.0)
            guard_y = opp_cherry_picker.pos.y * 0.75
            target = Vec2(max(-44.0, guard_x), guard_y).clamp_to_pitch(3.0, 3.0)
            return (
                cmd.move_to(
                    me.id,
                    target.x,
                    target.y,
                    sprint=should_sprint(me, target, urgent=False),
                    attack_sign=sign,
                    team_id=tid,
                ),
                "def_rest_defense_screen",
            )

        support_y = max(-14.0, min(14.0, world.ball.pos.y * 0.45))
        target = Vec2(base_x, support_y).clamp_to_pitch(3.0, 3.0)
        return (
            cmd.move_to(
                me.id,
                target.x,
                target.y,
                sprint=should_sprint(me, target, urgent=False),
                attack_sign=sign,
                team_id=tid,
            ),
            "def_buildup_pivot",
        )

    # --- D. Out of Possession / Defensive Transition ---
    carrier = world.ball_carrier()
    in_my_penalty_box = me.pos.x < -PENALTY_BOX_X and abs(me.pos.y) < PENALTY_BOX_HALF_W

    # 1. Assigned as Primary Presser (Ball in danger zone or 1v1 breakaway)
    if assignments.primary_presser_id == me.id:
        if carrier is not None and carrier.team == "opp":
            d_carrier = me.pos.dist(carrier.pos)
            # Surgical SLIDE_TACKLE only OUTSIDE penalty box (to never concede a penalty!)
            # unless carrier is < 11m from goal about to shoot uncontested.
            emergency_goal_save = carrier.pos.dist(MY_GOAL_CENTER) < 11.5
            if (
                d_carrier <= TACKLE_RADIUS
                and (not in_my_penalty_box or emergency_goal_save)
                and w.slide_tackle_aggression >= 0.35
                and me.yellow_cards == 0
            ):
                return (
                    cmd.slide_tackle(me.id, carrier.id, sprint=True, distance=min(2.2, d_carrier), team_id=tid),
                    f"def_surgical_slide_tackle_p{carrier.id}",
                )

            # Inside the box or when containing: step directly onto the goal-side shot cone!
            if in_my_penalty_box or w.slide_tackle_aggression < 0.25:
                block_pt = carrier.pos.toward(MY_GOAL_CENTER, 1.6)
                return (
                    cmd.move_to(me.id, block_pt.x, block_pt.y, sprint=True, attack_sign=sign, team_id=tid),
                    "def_box_jockey_block",
                )

            return (
                cmd.press_ball(me.id, intensity=w.press_intensity, duration=1, team_id=tid),
                "def_press_carrier",
            )

    # 2. Box Protector / Goal-Side Shadow-Cone Blocker
    # If `box_protector_lock` is active and ball is wide, protect the central penalty spot against cutbacks!
    if w.box_protector_lock and abs(ball_pred.y) > 10.0 and ball_pred.x < -15.0:
        central_threats = [
            o for o in world.opp if o.id != 0 and o.pos.x < -18.0 and abs(o.pos.y) <= 10.0
        ]
        if central_threats:
            danger_man = min(central_threats, key=lambda o: o.pos.dist(MY_GOAL_CENTER))
            guard_pt = danger_man.pos.toward(MY_GOAL_CENTER, 1.8)
            return (
                cmd.move_to(me.id, guard_pt.x, guard_pt.y, sprint=True, attack_sign=sign, team_id=tid),
                f"def_box_lock_cutback_guard_p{danger_man.id}",
            )
        spot_pt = Vec2(max(-46.0, min(-36.0, ball_pred.x - 2.0)), ball_pred.y * 0.28)
        return (
            cmd.move_to(me.id, spot_pt.x, spot_pt.y, sprint=True, attack_sign=sign, team_id=tid),
            "def_box_lock_penalty_spot",
        )

    shadow_pt = assignments.shadow_targets.get(me.id)
    if shadow_pt is not None:
        # Clamp DEF x so we never get dragged above `press_height_x`
        clamped_x = min(shadow_pt.x, max(-18.0, w.defensive_line_offset - 8.0))
        target = Vec2(max(-49.0, clamped_x), max(-18.0, min(18.0, shadow_pt.y)))
        urgent = ball_pred.x < -10.0 or me.pos.dist(target) > 8.0
        return (
            cmd.move_to(
                me.id,
                target.x,
                target.y,
                sprint=should_sprint(me, target, urgent=urgent),
                attack_sign=sign,
                team_id=tid,
            ),
            "def_shadow_cone_block",
        )

    # Fallback zonal anchor
    fallback = Vec2(-34.0 + w.defensive_line_offset * 0.5, ball_pred.y * 0.35).clamp_to_pitch(3.0, 3.0)
    return (
        cmd.move_to(me.id, fallback.x, fallback.y, sprint=False, attack_sign=sign, team_id=tid),
        "def_zonal_anchor",
    )


# ==============================================================================
# ROLE 2: MIDFIELDER (Box-to-Box Pivot, Triangle Hub & Gegenpresser)
# ==============================================================================
def _decide_mid(
    me: Player,
    world: World,
    directive: TrainerDirective,
    assignments: DefensiveAssignments,
) -> tuple[Command, str]:
    tid = world.my_team_id
    sign = world.attack_sign
    w = directive.weights
    ball_pred = predict_ball_pos(world, horizon_ticks=1.2)

    # --- A. MID Has Ball in Possession ---
    if me.has_ball:
        # 1. Check for high-xG shot or Sweeper-GK punish
        shot = evaluate_shot(me, world, directive)
        if shot.should_shoot:
            return (
                cmd.shoot(me.id, aim=shot.aim_location, power=shot.power, team_id=tid),
                f"mid_shoot_{shot.reason}",
            )

        # 2. Evaluate passes (THROUGH balls to forwards, flank switches, or quick triangles)
        passes = evaluate_pass_options(me, world, directive)
        opp_dist = nearest_opponent_dist(me.pos, world.opp, ignore_gk=True)

        if passes:
            best = passes[0]
            # Release immediately if:
            # - A high-value THROUGH ball or cutback is open (score >= 16.0)
            # - An opponent is closing within `dribble_caution_radius`
            # - Or best pass gains > 10m progressively
            if (
                best.pass_type == "THROUGH"
                or best.score >= 16.0
                or opp_dist <= w.dribble_caution_radius
                or best.progressive_gain >= 10.0
            ):
                return (
                    cmd.pass_to(me.id, best.target.id, pass_type=best.pass_type, team_id=tid),
                    f"mid_{best.reason}",
                )

        # 3. Progressive Half-Space Carry (`MOVE_TO`) toward preferred flank or open corridor
        target_y = -9.0 if w.preferred_flank < -0.15 else (9.0 if w.preferred_flank > 0.15 else me.pos.y * 0.6)
        # Steer away from closest opponent
        outfield_opps = [o for o in world.opp if o.id != 0 and o.pos.x > me.pos.x - 3.0]
        if outfield_opps:
            closest_ahead = min(outfield_opps, key=lambda o: me.pos.dist(o.pos))
            if me.pos.dist(closest_ahead.pos) < 8.0:
                target_y = me.pos.y - 7.0 if closest_ahead.pos.y >= me.pos.y else me.pos.y + 7.0

        carry_pt = Vec2(min(38.0, me.pos.x + 11.0), max(-24.0, min(24.0, target_y)))
        return (
            cmd.dribble_to(
                me.id,
                carry_pt.x,
                carry_pt.y,
                sprint=me.stamina > 0.32,
                attack_sign=sign,
                team_id=tid,
            ),
            "mid_halfspace_carry",
        )

    # --- B. Loose Ball Claim ---
    if directive.phase == Phase.LOOSE_BALL:
        if assignments.primary_presser_id == me.id or me.pos.dist(ball_pred) < 10.0:
            return (
                cmd.intercept(me.id, aggressive=True, duration=1, team_id=tid),
                "mid_loose_ball_pounce",
            )

    # --- C. Team in Possession (Off-Ball Triangle Support & Late Box Arrival) ---
    if directive.phase in (Phase.IN_POSSESSION, Phase.TRANSITION_ATTACK):
        carrier = world.ball_carrier()
        cx = carrier.pos.x if carrier else world.ball.pos.x
        cy = carrier.pos.y if carrier else world.ball.pos.y

        # If DEF or GK has the ball in buildup, offer a clean central-wide triangle pivot
        if cx < -10.0:
            pivot_x = max(-22.0, cx + 14.0)
            pivot_y = -10.0 if cy >= 0.0 else 10.0
            if abs(w.preferred_flank) > 0.3:
                pivot_y = 10.0 if w.preferred_flank > 0 else -10.0
            target = Vec2(pivot_x, pivot_y).clamp_to_pitch(3.0, 3.0)
            return (
                cmd.move_to(
                    me.id,
                    target.x,
                    target.y,
                    sprint=should_sprint(me, target, urgent=True),
                    attack_sign=sign,
                    team_id=tid,
                ),
                "mid_buildup_triangle_outlet",
            )

        # If a Forward has the ball in the final third, arrive at the edge of the box ("Zone 14" / cutback arc)
        if cx > 18.0:
            zone14_x = min(35.0, cx - 5.0)
            # Offset laterally from carrier to open a clean pass angle
            zone14_y = -7.5 if cy > 2.0 else (7.5 if cy < -2.0 else 0.0)
            target = Vec2(zone14_x, zone14_y).clamp_to_pitch(3.0, 3.0)
            return (
                cmd.move_to(
                    me.id,
                    target.x,
                    target.y,
                    sprint=should_sprint(me, target, urgent=directive.phase == Phase.TRANSITION_ATTACK),
                    attack_sign=sign,
                    team_id=tid,
                ),
                "mid_zone14_box_support",
            )

        # Midfield progression support
        sup_x = min(28.0, cx + 8.0 + w.defensive_line_offset * 0.3)
        sup_y = -11.0 if cy > 0 else 11.0
        target = Vec2(sup_x, sup_y).clamp_to_pitch(3.0, 3.0)
        return (
            cmd.move_to(
                me.id,
                target.x,
                target.y,
                sprint=should_sprint(me, target, urgent=False),
                attack_sign=sign,
                team_id=tid,
            ),
            "mid_triangle_support",
        )

    # --- D. Out of Possession (Gegenpress, Passing-Lane Screen, or Box Cover) ---
    # 1. If DEF stepped out and MID is designated Box Protector
    if assignments.box_protector_id == me.id:
        shadow_pt = assignments.shadow_targets.get(
            me.id, ball_pred.toward(MY_GOAL_CENTER, 4.0)
        )
        return (
            cmd.move_to(me.id, shadow_pt.x, shadow_pt.y, sprint=True, attack_sign=sign, team_id=tid),
            "mid_cover_for_stepping_def",
        )

    # 2. Assigned Primary or Secondary Presser
    if me.id in (assignments.primary_presser_id, assignments.secondary_presser_id):
        carrier = world.ball_carrier()
        if (
            carrier is not None
            and carrier.team == "opp"
            and me.pos.dist(carrier.pos) <= TACKLE_RADIUS
            and me.pos.x > -PENALTY_BOX_X
            and w.slide_tackle_aggression >= 0.50
            and me.yellow_cards == 0
        ):
            return (
                cmd.slide_tackle(me.id, carrier.id, sprint=True, distance=2.0, team_id=tid),
                f"mid_tackle_carrier_p{carrier.id}",
            )
        return (
            cmd.press_ball(me.id, intensity=w.press_intensity, duration=1, team_id=tid),
            "mid_gegenpress_ball",
        )

    # 3. Cut Central Passing Lane (`INTERCEPT` or shadow-mark opponent midfielder)
    if me.id in assignments.shadow_targets:
        lane_pt = assignments.shadow_targets[me.id]
        return (
            cmd.move_to(
                me.id,
                lane_pt.x,
                lane_pt.y,
                sprint=should_sprint(me, lane_pt, urgent=ball_pred.x < 0.0),
                attack_sign=sign,
                team_id=tid,
            ),
            "mid_screen_passing_lane",
        )

    screen_pt = ball_pred.toward(MY_GOAL_CENTER, 7.0).clamp_to_pitch(3.0, 3.0)
    return (
        cmd.move_to(
            me.id,
            screen_pt.x,
            screen_pt.y,
            sprint=should_sprint(me, screen_pt, urgent=False),
            attack_sign=sign,
            team_id=tid,
        ),
        "mid_central_screen",
    )


# ==============================================================================
# ROLE 3: FORWARD 1 (Spearhead Striker, Blind-Side Shoulder Runner & Press Trap)
# ==============================================================================
def _decide_fwd1(
    me: Player,
    world: World,
    directive: TrainerDirective,
    assignments: DefensiveAssignments,
) -> tuple[Command, str]:
    tid = world.my_team_id
    sign = world.attack_sign
    w = directive.weights
    ball_pred = predict_ball_pos(world, horizon_ticks=1.2)

    # --- A. FWD1 Has Ball in Possession ---
    if me.has_ball:
        shot = evaluate_shot(me, world, directive)
        passes = evaluate_pass_options(me, world, directive)

        # 1. Check for high-value Cutback Pass to FWD2 / MID if we are at a tight angle inside the box
        if passes and abs(me.pos.y) > 10.5 and me.pos.x > 34.0:
            best_pass = passes[0]
            if (
                best_pass.lead_pos.x > 35.0
                and abs(best_pass.lead_pos.y) < 9.0
                and best_pass.safety_ratio >= w.pass_safety_margin
                and (not shot.should_shoot or shot.xg < 0.38)
            ):
                return (
                    cmd.pass_to(me.id, best_pass.target.id, pass_type="GROUND", team_id=tid),
                    f"fwd1_cutback_assist_p{best_pass.target.id}",
                )

        # 2. Clinical Finish (`SHOOT`)
        if shot.should_shoot:
            return (
                cmd.shoot(me.id, aim=shot.aim_location, power=shot.power, team_id=tid),
                f"fwd1_shoot_{shot.aim_location}_{shot.reason}",
            )

        # 3. Under immediate tackle danger outside shooting range -> 1-2 Layoff Pass to MID or FWD2
        opp_dist = nearest_opponent_dist(me.pos, world.opp, ignore_gk=True)
        if passes and (opp_dist <= w.dribble_caution_radius or passes[0].score >= 18.0):
            best = passes[0]
            if best.lead_pos.x >= me.pos.x - 6.0:
                return (
                    cmd.pass_to(me.id, best.target.id, pass_type=best.pass_type, team_id=tid),
                    f"fwd1_combination_{best.reason}",
                )

        # 4. Drive Diagonally into the Penalty Box toward Goal Center (`MOVE_TO`)
        # If an opponent defender is Frozen (`scouting.frozen_opponent_ids`), attack right past them!
        aim_y = 0.0
        if me.pos.y < -4.0:
            aim_y = -3.5
        elif me.pos.y > 4.0:
            aim_y = 3.5
        drive_target = Vec2(min(47.0, me.pos.x + 11.0), aim_y).clamp_to_pitch(2.5, 2.5)
        return (
            cmd.dribble_to(me.id, drive_target.x, drive_target.y, sprint=True, attack_sign=sign, team_id=tid),
            "fwd1_drive_at_goal",
        )

    # --- B. Loose Ball in Attacking Half ---
    if directive.phase == Phase.LOOSE_BALL and (
        assignments.primary_presser_id == me.id or me.pos.dist(ball_pred) < 11.0
    ):
        return (
            cmd.intercept(me.id, aggressive=True, duration=1, team_id=tid),
            "fwd1_attack_loose_ball",
        )

    # --- C. Team in Possession (Off-Ball Shoulder Pinning & Near-Post / Channel Run) ---
    if directive.phase in (Phase.IN_POSSESSION, Phase.TRANSITION_ATTACK):
        offside_x = opponent_offside_line_x(world)
        carrier = world.ball_carrier()
        cx = carrier.pos.x if carrier else world.ball.pos.x
        cy = carrier.pos.y if carrier else world.ball.pos.y

        # Target weakest/tired defender's shoulder if identified by Eagle-Eye Scouting
        target_channel_y = -7.5
        if w.preferred_flank < -0.2:
            target_channel_y = -11.0
        elif w.preferred_flank > 0.2:
            target_channel_y = -3.0

        # When FWD2 or MID carries down the right wing (`cy > 8`), FWD1 crashes the near/central post `(44, -2)`
        if cx > 22.0 and cy > 6.0:
            box_run = Vec2(min(46.0, max(38.0, offside_x + 1.5)), -2.5)
            return (
                cmd.move_to(me.id, box_run.x, box_run.y, sprint=True, attack_sign=sign, team_id=tid),
                "fwd1_crash_central_box",
            )

        # Pin the opponent defensive line (`offside_x`) and sprint in behind on transition
        run_x = min(46.0, max(cx + 12.0, min(offside_x + 1.0, 44.0)))
        run_pt = Vec2(run_x, target_channel_y).clamp_to_pitch(3.0, 3.0)
        urgent = directive.phase == Phase.TRANSITION_ATTACK or cx > 0.0
        return (
            cmd.move_to(
                me.id,
                run_pt.x,
                run_pt.y,
                sprint=should_sprint(me, run_pt, urgent=urgent),
                attack_sign=sign,
                team_id=tid,
            ),
            "fwd1_shoulder_channel_run",
        )

    # --- D. Out of Possession (Pressing Trap Leader vs. Counter-Attack Outlet) ---
    # 1. If ball is in opponent half (`ball_pred.x > 0`) or we are assigned presser: lead the press!
    if assignments.primary_presser_id == me.id or (
        ball_pred.x > 8.0 and me.pos.dist(ball_pred) < 16.0 and me.stamina > 0.35
    ):
        return (
            cmd.press_ball(me.id, intensity=w.press_intensity, duration=1, team_id=tid),
            "fwd1_high_press_trap",
        )

    # 2. When opponent attacks in our half, hold near midfield / opponent CB shoulder as Counter-Attack Spearhead!
    counter_x = max(-6.0, min(16.0, ball_pred.x + 22.0 + w.defensive_line_offset * 0.4))
    counter_y = -8.0 if w.preferred_flank <= 0 else 4.0
    outlet_pt = Vec2(counter_x, counter_y).clamp_to_pitch(3.0, 3.0)
    return (
        cmd.move_to(
            me.id,
            outlet_pt.x,
            outlet_pt.y,
            sprint=False,  # Conserve stamina while waiting to spring the counter-attack!
            attack_sign=sign,
            team_id=tid,
        ),
        "fwd1_counter_attack_spearhead",
    )


# ==============================================================================
# ROLE 4: FORWARD 2 (Shadow Creator, Weak-Side Overload & Far-Post Poacher)
# ==============================================================================
def _decide_fwd2(
    me: Player,
    world: World,
    directive: TrainerDirective,
    assignments: DefensiveAssignments,
) -> tuple[Command, str]:
    tid = world.my_team_id
    sign = world.attack_sign
    w = directive.weights
    ball_pred = predict_ball_pos(world, horizon_ticks=1.2)
    fwd1 = world.my_player(int(Role.FWD1))

    # --- A. FWD2 Has Ball in Possession ---
    if me.has_ball:
        shot = evaluate_shot(me, world, directive)
        passes = evaluate_pass_options(me, world, directive)

        # 1. High-xG Shot (or Sweeper-GK open net punish)
        if shot.should_shoot and (shot.xg >= 0.28 or shot.reason == "sweeper_gk_open_net_punish"):
            return (
                cmd.shoot(me.id, aim=shot.aim_location, power=shot.power, team_id=tid),
                f"fwd2_shoot_{shot.aim_location}_{shot.reason}",
            )

        # 2. Slip Killer Through-Ball or Cutback to FWD1 / MID
        if passes:
            best = passes[0]
            opp_dist = nearest_opponent_dist(me.pos, world.opp, ignore_gk=True)
            if (
                best.target.id == int(Role.FWD1)
                and best.lead_pos.x > me.pos.x + 2.0
                and best.safety_ratio >= w.pass_safety_margin
            ) or opp_dist <= w.dribble_caution_radius or best.score >= 16.5:
                return (
                    cmd.pass_to(me.id, best.target.id, pass_type=best.pass_type, team_id=tid),
                    f"fwd2_create_{best.reason}",
                )

        # 3. Diagonal Half-Space Cut-In Dribble toward Penalty Spot
        cut_in_y = 4.5 if me.pos.y >= 0.0 else -4.5
        drive_pt = Vec2(min(46.0, me.pos.x + 10.5), cut_in_y).clamp_to_pitch(2.5, 2.5)
        return (
            cmd.dribble_to(me.id, drive_pt.x, drive_pt.y, sprint=True, attack_sign=sign, team_id=tid),
            "fwd2_diagonal_cut_in_dribble",
        )

    # --- B. Loose Ball ---
    if directive.phase == Phase.LOOSE_BALL and (
        assignments.primary_presser_id == me.id or me.pos.dist(ball_pred) < 11.0
    ):
        return (
            cmd.intercept(me.id, aggressive=True, duration=1, team_id=tid),
            "fwd2_loose_ball_claim",
        )

    # --- C. Team in Possession (Anti-Clustered Weak-Side Width & Far-Post Cutback Arrival) ---
    if directive.phase in (Phase.IN_POSSESSION, Phase.TRANSITION_ATTACK):
        offside_x = opponent_offside_line_x(world)
        carrier = world.ball_carrier()
        cx = carrier.pos.x if carrier else world.ball.pos.x
        cy = carrier.pos.y if carrier else world.ball.pos.y
        fwd1_y = fwd1.pos.y if fwd1 is not None else -8.0

        # Guarantee >= 12m lateral separation from FWD1 (Anti-Clustering Law)
        if fwd1_y <= 2.0:
            desired_y = max(10.0, fwd1_y + 15.0)
        else:
            desired_y = min(-10.0, fwd1_y - 15.0)

        # When FWD1 or MID drives into the left side of the box (`cx > 25, cy < -4`),
        # FWD2 sprints into the Penalty-Spot / Far-Post Cutback Window `(41, +6)`!
        if cx > 24.0 and cy < -3.0:
            cutback_slot = Vec2(min(44.0, max(37.0, offside_x)), 6.5)
            return (
                cmd.move_to(me.id, cutback_slot.x, cutback_slot.y, sprint=True, attack_sign=sign, team_id=tid),
                "fwd2_far_post_cutback_arrival",
            )

        # Otherwise stretch the weak-side half-space at staggered depth to offer an unpressured switch
        stagger_x = min(43.0, max(cx + 8.0, min(offside_x - 2.0, 38.0)))
        if w.preferred_flank > 0.25:
            stagger_x = min(45.0, stagger_x + 4.0)
        target = Vec2(stagger_x, max(-24.0, min(24.0, desired_y))).clamp_to_pitch(3.0, 3.0)
        urgent = directive.phase == Phase.TRANSITION_ATTACK or cx > 5.0
        return (
            cmd.move_to(
                me.id,
                target.x,
                target.y,
                sprint=should_sprint(me, target, urgent=urgent),
                attack_sign=sign,
                team_id=tid,
            ),
            "fwd2_weak_side_overload",
        )

    # --- D. Out of Possession (Secondary Press & Right Half-Space Compactness) ---
    if me.id in (assignments.primary_presser_id, assignments.secondary_presser_id):
        return (
            cmd.press_ball(me.id, intensity=w.press_intensity, duration=1, team_id=tid),
            "fwd2_press_outlet",
        )

    # If opponent is in buildup (`ball_pred.x > 10`), mark their weak-side defender/midfielder
    if ball_pred.x > 10.0 and me.id in assignments.mark_targets:
        opp_id = assignments.mark_targets[me.id]
        return (
            cmd.mark(me.id, opp_id, tightness="TIGHT", duration=2, team_id=tid),
            f"fwd2_mark_buildup_p{opp_id}",
        )

    # Compact half-space transition springboard
    hold_x = max(-14.0, min(10.0, ball_pred.x + 14.0 + w.defensive_line_offset * 0.4))
    hold_y = 11.0 if (fwd1 is None or fwd1.pos.y <= 0.0) else -11.0
    target = Vec2(hold_x, hold_y).clamp_to_pitch(3.0, 3.0)
    return (
        cmd.move_to(
            me.id,
            target.x,
            target.y,
            sprint=should_sprint(me, target, urgent=False),
            attack_sign=sign,
            team_id=tid,
        ),
        "fwd2_halfspace_trap_cover",
    )


# ==============================================================================
# SET-PIECE CHOREOGRAPHY (KICK_OFF, CORNER, THROW_IN, GOAL_KICK, FREE_KICK, PENALTY)
# ==============================================================================
def _decide_set_piece(
    me: Player,
    world: World,
    directive: TrainerDirective,
    assignments: DefensiveAssignments,
) -> tuple[Command, str]:
    tid = world.my_team_id
    sign = world.attack_sign
    pm = world.play_mode
    my_restart = world.mode_team != "opp"

    # If I have the ball on any set piece -> execute immediate set-piece delivery!
    if me.has_ball:
        if "PENALTY" in pm or (me.pos.dist(OPP_GOAL_CENTER) <= 18.0 and "FREE" in pm):
            shot = evaluate_shot(me, world, directive)
            return (
                cmd.shoot(me.id, aim=shot.aim_location, power=0.92, team_id=tid),
                f"setpiece_shoot_{pm.lower()}",
            )
        passes = evaluate_pass_options(me, world, directive)
        if passes:
            best = passes[0]
            if me.role == Role.GK and "GOAL_KICK" in pm:
                method = "KICK" if me.pos.dist(best.target.pos) > 20.0 else "THROW"
                return (
                    cmd.gk_distribute(me.id, best.target.id, method=method, team_id=tid),
                    f"setpiece_gk_{method.lower()}_p{best.target.id}",
                )
            return (
                cmd.pass_to(me.id, best.target.id, pass_type=best.pass_type, team_id=tid),
                f"setpiece_pass_{best.reason}",
            )
        # Fallback pass to MID (2) or FWD1 (3)
        fallback_target = int(Role.MID) if me.id != int(Role.MID) else int(Role.FWD1)
        return (
            cmd.pass_to(me.id, fallback_target, pass_type="GROUND", team_id=tid),
            f"setpiece_fallback_pass_p{fallback_target}",
        )

    if me.role == Role.GK:
        return _decide_gk(me, world, directive)

    # Attacking Corner Kick Choreography
    if "CORNER" in pm and my_restart:
        corner_sign = 1.0 if world.ball.pos.y >= 0.0 else -1.0
        slots = {
            int(Role.DEF): Vec2(8.0, 0.0),                       # Rest-defense stopper at halfway
            int(Role.MID): Vec2(36.0, 6.0 * corner_sign),        # Short corner / edge of box shooter
            int(Role.FWD1): Vec2(47.0, -3.5 * corner_sign),      # Near/central post header
            int(Role.FWD2): Vec2(45.0, 5.5 * corner_sign),       # Far-post poacher
        }
        pt = slots.get(me.id, Vec2(30.0, 0.0))
        return (
            cmd.move_to(me.id, pt.x, pt.y, sprint=True, attack_sign=sign, team_id=tid),
            f"setpiece_attacking_corner_r{me.id}",
        )

    # Defending Corner Kick Choreography
    if "CORNER" in pm and not my_restart:
        slots = {
            int(Role.DEF): Vec2(-49.0, -2.5),                    # Near-post zonal blocker
            int(Role.MID): Vec2(-46.0, 3.0),                     # Penalty-spot clearer
            int(Role.FWD2): Vec2(-37.0, 0.0),                    # Edge-of-box rebound hunter
            int(Role.FWD1): Vec2(-8.0, 0.0),                     # Fast counter-attack outlet
        }
        pt = slots.get(me.id, Vec2(-45.0, 0.0))
        return (
            cmd.move_to(me.id, pt.x, pt.y, sprint=True, attack_sign=sign, team_id=tid),
            f"setpiece_defending_corner_r{me.id}",
        )

    # Default set-piece positioning: closest outfield player approaches ball if ours, others take structure
    outfield = [p for p in world.me if p.id != 0]
    closest_to_ball = min(outfield, key=lambda p: p.pos.dist(world.ball.pos), default=me)
    if my_restart and closest_to_ball.id == me.id and me.pos.dist(world.ball.pos) > 1.8:
        return (
            cmd.move_to(me.id, world.ball.pos.x, world.ball.pos.y, sprint=True, attack_sign=sign, team_id=tid),
            f"setpiece_taker_approach_{pm.lower()}",
        )

    # Fall back to normal open-play role positioning
    if me.role == Role.DEF:
        return _decide_def(me, world, directive, assignments)
    if me.role == Role.MID:
        return _decide_mid(me, world, directive, assignments)
    if me.role == Role.FWD1:
        return _decide_fwd1(me, world, directive, assignments)
    return _decide_fwd2(me, world, directive, assignments)
