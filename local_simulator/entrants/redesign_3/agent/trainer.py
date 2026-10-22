"""'Learn From Every Match' Football Trainer & Eagle-Eye Opponent Scouting System.

This module acts as an intelligent, real-time and cross-match Football Coach/Trainer:
1. **Self-Error Ledger (Intra-Match & Cross-Match Micro-Learning):**
   Tracks every command we issue on tick `t - 1` and evaluates its causal outcome on
   tick `t`. Automatically detects pass interceptions, buildup turnovers, dribble
   dispossessions, wasted low-xG shots, blocked shots, bypassed presses, and root
   causes of conceded goals — immediately adapting our `TacticalWeights` to fix them.
2. **Eagle-Eye Opponent Scouting Engine:**
   Continuously profiles all 5 opponent players (positions, velocities, stamina,
   frozen/timeout detection, GK sweeping depth, GK lateral bias, pressing mob size,
   flank density, and visible `lastAction` telemetry) and classifies the opponent's
   strategy to activate targeted counter-tactics.
3. **Cross-Match Memory Persistence:**
   Persists learned weights, error ledgers, and opponent dossiers across matches via
   `/tmp/afc_trainer_memory.json` (or `AFC_TRAINER_MEMORY_PATH`) and `tuning.json`.
"""

from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass, field
import json
import os
from pathlib import Path
from typing import Any

from .model import Command, OpponentArchetype, Phase, Player, Role, Vec2, World

DEFAULT_MEMORY_PATH = "/tmp/afc_trainer_memory.json"
DEFAULT_TUNING_PATH = str(Path(__file__).resolve().parent / "tuning.json")


def _clamp(val: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(val)))


@dataclass
class TacticalWeights:
    """Continuous tactical dials adjusted in real time by the Football Trainer."""

    defensive_line_offset: float = 0.0       # -12.0 (low block) .. +12.0 (high line)
    press_intensity: float = 0.72            # 0.30 .. 1.00
    press_height_x: float = 12.0             # -20.0 .. +30.0 (max x where DEF/MID press)
    pass_safety_margin: float = 0.22         # 0.12 .. 0.42 (cone clearance ratio required)
    long_pass_bias: float = 0.25             # 0.00 .. 1.00 (preference for AERIAL/switch)
    through_ball_aggression: float = 0.58    # 0.15 .. 1.00 (preference for THROUGH balls)
    max_shoot_distance: float = 22.5         # 16.0 .. 36.0 (meters from goal)
    cutback_preference: float = 0.50         # 0.15 .. 0.95 (extra pass inside box vs tight shot)
    dribble_caution_radius: float = 4.2      # 2.8 .. 8.0 (release ball when opp within radius)
    slide_tackle_aggression: float = 0.45    # 0.05 .. 0.90
    preferred_flank: float = 0.0             # -1.0 (left y<0) .. +1.0 (right y>0)
    gk_distribute_long_bias: float = 0.25    # 0.00 .. 1.00 (KICK vs THROW)
    tempo: float = 0.65                      # 0.20 (slow possession) .. 1.00 (fast break)
    box_protector_lock: bool = False         # Lock DEF to central penalty box protection
    exploit_sweeper_gk: bool = False         # Shoot from long range when opp GK sweeps out

    def clamp_all(self) -> "TacticalWeights":
        self.defensive_line_offset = round(_clamp(self.defensive_line_offset, -12.0, 12.0), 2)
        self.press_intensity = round(_clamp(self.press_intensity, 0.30, 1.00), 2)
        self.press_height_x = round(_clamp(self.press_height_x, -20.0, 30.0), 2)
        self.pass_safety_margin = round(_clamp(self.pass_safety_margin, 0.12, 0.42), 3)
        self.long_pass_bias = round(_clamp(self.long_pass_bias, 0.00, 1.00), 2)
        self.through_ball_aggression = round(_clamp(self.through_ball_aggression, 0.15, 1.00), 2)
        self.max_shoot_distance = round(_clamp(self.max_shoot_distance, 16.0, 36.0), 2)
        self.cutback_preference = round(_clamp(self.cutback_preference, 0.15, 0.95), 2)
        self.dribble_caution_radius = round(_clamp(self.dribble_caution_radius, 2.8, 8.0), 2)
        self.slide_tackle_aggression = round(_clamp(self.slide_tackle_aggression, 0.05, 0.90), 2)
        self.preferred_flank = round(_clamp(self.preferred_flank, -1.0, 1.0), 2)
        self.gk_distribute_long_bias = round(_clamp(self.gk_distribute_long_bias, 0.00, 1.00), 2)
        self.tempo = round(_clamp(self.tempo, 0.20, 1.00), 2)
        return self

    def copy(self) -> "TacticalWeights":
        return TacticalWeights(**asdict(self))


@dataclass
class SelfErrorLedger:
    """Tracks our own mistakes, positive breakthroughs, and applied fixes."""

    pass_interceptions: int = 0
    buildup_turnovers_danger: int = 0
    dribble_dispossessions: int = 0
    shots_wasted_low_xg: int = 0
    shots_blocked: int = 0
    press_bypassed: int = 0
    goals_conceded_counter: int = 0
    goals_conceded_central: int = 0
    goals_conceded_turnover: int = 0

    passes_completed: int = 0
    tackles_won: int = 0
    goals_scored: int = 0

    failed_pass_types: dict[str, int] = field(
        default_factory=lambda: {"GROUND": 0, "THROUGH": 0, "AERIAL": 0}
    )
    successful_pass_types: dict[str, int] = field(
        default_factory=lambda: {"GROUND": 0, "THROUGH": 0, "AERIAL": 0}
    )
    flank_turnovers: dict[str, int] = field(
        default_factory=lambda: {"left": 0, "center": 0, "right": 0}
    )
    flank_successes: dict[str, int] = field(
        default_factory=lambda: {"left": 0, "center": 0, "right": 0}
    )
    role_dispossessions: dict[int, int] = field(
        default_factory=lambda: {0: 0, 1: 0, 2: 0, 3: 0, 4: 0}
    )
    recent_lessons: list[str] = field(default_factory=list)

    def record_lesson(self, lesson: str) -> None:
        self.recent_lessons.append(lesson)
        if len(self.recent_lessons) > 25:
            self.recent_lessons = self.recent_lessons[-25:]


@dataclass
class OpponentPlayerProfile:
    """Per-opponent-player telemetry gathered by Eagle-Eye Scouting."""

    player_id: int
    samples: int = 0
    avg_x: float = 20.0
    avg_y: float = 0.0
    min_stamina: float = 1.0
    current_stamina: float = 1.0
    frozen_ticks: int = 0
    sprint_ticks: int = 0
    last_pos: Vec2 | None = None
    action_counts: dict[str, int] = field(default_factory=dict)

    @property
    def is_frozen(self) -> bool:
        return self.frozen_ticks >= 2

    @property
    def is_exhausted(self) -> bool:
        return self.current_stamina < 0.36


@dataclass
class OpponentScoutingReport:
    """Eagle-Eye snapshot of opponent strategy and exploitable weaknesses."""

    archetype: OpponentArchetype = OpponentArchetype.UNKNOWN
    opp_line_height_x: float = 30.0          # In normalized frame (+55 is opp goal line)
    opp_gk_depth_x: float = 52.0             # < 43.0 means sweeper-GK is far off line!
    opp_gk_offset_y: float = 0.0             # GK lateral offset from center (0.0)
    press_mob_count: int = 1                 # Opponents within 12.5m of ball
    left_flank_density: float = 2.0          # Opponents in y < -3
    right_flank_density: float = 2.0         # Opponents in y > +3
    weakest_flank: str = "balanced"          # "left" | "right" | "balanced"
    weakest_defender_id: int | None = None   # Lowest stamina / out-of-position defender
    frozen_opponent_ids: tuple[int, ...] = ()
    tired_opponent_ids: tuple[int, ...] = ()
    dominant_opp_actions: tuple[str, ...] = ()
    counter_strategy_note: str = "Standard balanced progression"


@dataclass
class TrainerDirective:
    """Output of `FootballTrainer.observe_and_learn(world)` consumed by role engines."""

    phase: Phase
    weights: TacticalWeights
    scouting: OpponentScoutingReport
    ledger: SelfErrorLedger
    new_events: tuple[str, ...] = ()
    coach_override_note: str | None = None


@dataclass
class _TickSnapshot:
    tick: int
    clock: float
    ball_pos: Vec2
    ball_owner_team: str | None
    ball_owner_id: int | None
    score_me: int
    score_opp: int
    commands: dict[int, Command]
    my_positions: dict[int, Vec2]
    def_mid_avg_x: float
    def_mid_wide_count: int


class OpponentScout:
    """Eagle-Eye Opponent Scouting Engine."""

    def __init__(self) -> None:
        self.profiles: dict[int, OpponentPlayerProfile] = {
            i: OpponentPlayerProfile(player_id=i) for i in range(5)
        }
        self.ema_line_height_x: float = 32.0
        self.ema_gk_depth_x: float = 52.0
        self.ema_gk_offset_y: float = 0.0
        self.ema_mob_count: float = 1.2
        self.total_ticks_observed: int = 0

    def update(self, world: World) -> OpponentScoutingReport:
        self.total_ticks_observed += 1
        alpha = 0.25 if self.total_ticks_observed > 4 else 0.60

        outfield_xs: list[float] = []
        mob_count = 0
        left_weight = 0.0
        right_weight = 0.0
        frozen_ids: list[int] = []
        tired_ids: list[int] = []
        action_totals: dict[str, int] = {}

        for opp in world.opp:
            prof = self.profiles.setdefault(
                opp.id, OpponentPlayerProfile(player_id=opp.id)
            )
            prof.samples += 1
            prof.current_stamina = opp.stamina
            prof.min_stamina = min(prof.min_stamina, opp.stamina)

            # Check if opponent player is frozen/stalling (e.g. LLM timeout in OPEN_PLAY)
            if (
                world.play_mode == "OPEN_PLAY"
                and opp.id != 0
                and prof.last_pos is not None
                and opp.pos.dist(prof.last_pos) < 0.12
                and opp.speed < 0.08
                and opp.pos.dist(world.ball.pos) < 32.0
            ):
                prof.frozen_ticks += 1
            else:
                prof.frozen_ticks = 0

            prof.last_pos = opp.pos
            prof.avg_x = (1.0 - alpha) * prof.avg_x + alpha * opp.pos.x
            prof.avg_y = (1.0 - alpha) * prof.avg_y + alpha * opp.pos.y
            if opp.is_sprinting:
                prof.sprint_ticks += 1
            if opp.last_action:
                prof.action_counts[opp.last_action] = (
                    prof.action_counts.get(opp.last_action, 0) + 1
                )

            for act, cnt in prof.action_counts.items():
                action_totals[act] = action_totals.get(act, 0) + cnt

            if prof.is_frozen:
                frozen_ids.append(opp.id)
            if prof.is_exhausted:
                tired_ids.append(opp.id)

            if opp.id == 0:
                self.ema_gk_depth_x = (1.0 - alpha) * self.ema_gk_depth_x + alpha * opp.pos.x
                self.ema_gk_offset_y = (1.0 - alpha) * self.ema_gk_offset_y + alpha * opp.pos.y
            else:
                outfield_xs.append(opp.pos.x)
                if opp.pos.dist(world.ball.pos) <= 12.5:
                    mob_count += 1
                # Weight defensive presence on left (y < 0) vs right (y > 0)
                stamina_weight = 0.5 + 0.5 * opp.stamina
                if opp.pos.y < -2.0:
                    left_weight += stamina_weight
                elif opp.pos.y > 2.0:
                    right_weight += stamina_weight
                else:
                    left_weight += 0.35 * stamina_weight
                    right_weight += 0.35 * stamina_weight

        if outfield_xs:
            outfield_xs_sorted = sorted(outfield_xs, reverse=True)
            # Average of the 2 deepest outfield players (closest to +55)
            deepest_two = outfield_xs_sorted[: min(2, len(outfield_xs_sorted))]
            curr_line_x = sum(deepest_two) / len(deepest_two)
            self.ema_line_height_x = (1.0 - alpha) * self.ema_line_height_x + alpha * curr_line_x

        self.ema_mob_count = (1.0 - alpha) * self.ema_mob_count + alpha * float(mob_count)

        # Find weakest / most tired opponent defender/midfielder (IDs 1, 2)
        def_candidates = [p for p in world.opp if p.id in (1, 2)] or [
            p for p in world.opp if p.id != 0
        ]
        weakest_def_id: int | None = None
        if def_candidates:
            # Prefer frozen defender, else lowest stamina defender
            def_candidates.sort(
                key=lambda p: (
                    0 if self.profiles[p.id].is_frozen else 1,
                    p.stamina,
                )
            )
            weakest_def_id = def_candidates[0].id

        # Determine weakest flank (fewer/more tired defenders)
        if left_weight + 0.45 < right_weight:
            weakest_flank = "left"
        elif right_weight + 0.45 < left_weight:
            weakest_flank = "right"
        else:
            weakest_flank = "balanced"

        # Classify Opponent Archetype
        opp_gk = world.opp_gk()
        curr_gk_x = opp_gk.pos.x if opp_gk is not None else self.ema_gk_depth_x
        outfield_deep_count = sum(1 for x in outfield_xs if x > 20.0)

        if len(frozen_ids) >= 2 or any(
            act in ("RoamRandomly", "Idle") and cnt >= 4 for act, cnt in action_totals.items()
        ):
            archetype = OpponentArchetype.FROZEN_LLM
            note = "Exploit frozen/random opponent agents via direct half-space carries & through balls"
        elif self.ema_mob_count >= 2.35 or mob_count >= 3:
            archetype = OpponentArchetype.MOB_PRESS
            note = "Counter mob-press via 1-touch triangles, AERIAL switches to weak flank & early release"
        elif curr_gk_x < 42.5 or self.ema_line_height_x < 18.0:
            archetype = OpponentArchetype.ULTRA_HIGH_LINE
            note = "Punish ultra-high line / sweeper GK with instant THROUGH balls & long-range shots"
        elif outfield_deep_count >= 3 and self.ema_line_height_x > 34.0:
            archetype = OpponentArchetype.LOW_BLOCK
            note = "Break low block via high defensive line compression, flank switches & top-corner power shots"
        else:
            archetype = OpponentArchetype.BALANCED
            note = f"Balanced opponent — overload {weakest_flank} flank & target defender #{weakest_def_id}"

        top_actions = tuple(
            k for k, _ in sorted(action_totals.items(), key=lambda kv: kv[1], reverse=True)[:3]
        )

        return OpponentScoutingReport(
            archetype=archetype,
            opp_line_height_x=round(self.ema_line_height_x, 2),
            opp_gk_depth_x=round(curr_gk_x, 2),
            opp_gk_offset_y=round(opp_gk.pos.y if opp_gk else self.ema_gk_offset_y, 2),
            press_mob_count=mob_count,
            left_flank_density=round(left_weight, 2),
            right_flank_density=round(right_weight, 2),
            weakest_flank=weakest_flank,
            weakest_defender_id=weakest_def_id,
            frozen_opponent_ids=tuple(frozen_ids),
            tired_opponent_ids=tuple(tired_ids),
            dominant_opp_actions=top_actions,
            counter_strategy_note=note,
        )


class FootballTrainer:
    """Central 'Learn From Every Match' Coach & Tactical Adaptation Engine."""

    def __init__(
        self,
        memory_path: str | None = None,
        tuning_path: str | None = None,
    ) -> None:
        self.memory_path = memory_path or os.environ.get(
            "AFC_TRAINER_MEMORY_PATH", DEFAULT_MEMORY_PATH
        )
        self.tuning_path = tuning_path or os.environ.get(
            "AFC_TUNING_PATH", DEFAULT_TUNING_PATH
        )
        self.base_weights = TacticalWeights()
        self.learned_weights = TacticalWeights()
        self.ledger = SelfErrorLedger()
        self.scout = OpponentScout()
        self.history: deque[_TickSnapshot] = deque(maxlen=12)
        self.last_possessing_team: str | None = None
        self.possession_change_tick: int = -99
        self.matches_played: int = 0
        self._match_started: bool = False

        self._load_tuning_file()
        self._load_persistent_memory()

    def reset_for_new_match(self, preserve_cross_match_learning: bool = True) -> None:
        """Reset transient tick buffers while optionally keeping cross-match wisdom."""
        self.history.clear()
        self.scout = OpponentScout()
        self.last_possessing_team = None
        self.possession_change_tick = -99
        if not preserve_cross_match_learning:
            self.learned_weights = self.base_weights.copy()
            self.ledger = SelfErrorLedger()

    # ------------------------------------------------------------------
    # Persistence (Cross-Match Memory + `tuning.json`)
    # ------------------------------------------------------------------
    def _load_tuning_file(self) -> None:
        try:
            p = Path(self.tuning_path)
            if p.is_file():
                data = json.loads(p.read_text(encoding="utf-8"))
                weights_dict = data.get("weights", data)
                if isinstance(weights_dict, dict):
                    for k, v in weights_dict.items():
                        if hasattr(self.base_weights, k):
                            setattr(self.base_weights, k, v)
                    self.base_weights.clamp_all()
                    self.learned_weights = self.base_weights.copy()
        except Exception:
            pass

    def _load_persistent_memory(self) -> None:
        try:
            p = Path(self.memory_path)
            if not p.is_file():
                return
            data = json.loads(p.read_text(encoding="utf-8"))
            self.matches_played = int(data.get("matches_played", 0) or 0)
            w_data = data.get("learned_weights")
            if isinstance(w_data, dict):
                for k, v in w_data.items():
                    if hasattr(self.learned_weights, k):
                        setattr(self.learned_weights, k, v)
                self.learned_weights.clamp_all()
            l_data = data.get("ledger")
            if isinstance(l_data, dict):
                for k, v in l_data.items():
                    if hasattr(self.ledger, k) and not isinstance(getattr(self.ledger, k), dict):
                        setattr(self.ledger, k, v)
        except Exception:
            pass

    def save_persistent_memory(self) -> None:
        """Persist learned weights and error ledger to disk for future matches."""
        try:
            p = Path(self.memory_path)
            p.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "matches_played": self.matches_played,
                "learned_weights": asdict(self.learned_weights),
                "ledger": {
                    "pass_interceptions": self.ledger.pass_interceptions,
                    "buildup_turnovers_danger": self.ledger.buildup_turnovers_danger,
                    "dribble_dispossessions": self.ledger.dribble_dispossessions,
                    "shots_wasted_low_xg": self.ledger.shots_wasted_low_xg,
                    "shots_blocked": self.ledger.shots_blocked,
                    "press_bypassed": self.ledger.press_bypassed,
                    "goals_conceded_counter": self.ledger.goals_conceded_counter,
                    "goals_conceded_central": self.ledger.goals_conceded_central,
                    "goals_conceded_turnover": self.ledger.goals_conceded_turnover,
                    "passes_completed": self.ledger.passes_completed,
                    "tackles_won": self.ledger.tackles_won,
                    "goals_scored": self.ledger.goals_scored,
                    "failed_pass_types": self.ledger.failed_pass_types,
                    "successful_pass_types": self.ledger.successful_pass_types,
                    "flank_turnovers": self.ledger.flank_turnovers,
                    "flank_successes": self.ledger.flank_successes,
                    "recent_lessons": self.ledger.recent_lessons[-15:],
                },
                "last_scouting": asdict(self.scout.last_report)
                if hasattr(self.scout, "last_report")
                else {},
            }
            p.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Core Tick Observation & Learning Loop
    # ------------------------------------------------------------------
    def observe_and_learn(self, world: World) -> TrainerDirective:
        """Analyze what happened since last tick, update weights, and issue directive."""
        events: list[str] = []

        # Detect start of a new match (tick reset)
        if self.history and world.tick < self.history[-1].tick and world.tick <= 5:
            self.matches_played += 1
            self.save_persistent_memory()
            self.reset_for_new_match(preserve_cross_match_learning=True)
            events.append(f"MATCH_NEW:loaded_cross_match_experience(matches={self.matches_played})")
        elif not self._match_started:
            self._match_started = True
            self.matches_played += 1

        # 1. Evaluate outcome of previous tick's commands (Self-Error & Reinforcement learning)
        if self.history:
            prev = self.history[-1]
            if world.tick >= prev.tick:
                self._evaluate_causal_outcomes(prev, world, events)

        # 2. Update Eagle-Eye Opponent Scouting
        scouting = self.scout.update(world)
        self.scout.last_report = scouting  # type: ignore[attr-defined]

        # 3. Determine current tactical phase (including 3-tick transition windows)
        curr_owner = world.ball.owner_team
        if curr_owner in ("me", "opp") and curr_owner != self.last_possessing_team:
            if self.last_possessing_team is not None:
                self.possession_change_tick = world.tick
            self.last_possessing_team = curr_owner

        phase = self._classify_phase(world)

        # 4. Synthesize effective TacticalWeights for this tick
        effective = self._synthesize_weights(world, scouting, phase)

        # 5. Apply Live Human Coach Override (`teamChat` / `coachInstruction`) if present
        coach_note = self._apply_coach_instruction(world.coach_instruction, effective)

        effective.clamp_all()

        # Persist periodically or on goals
        if any(e.startswith("GOAL_") for e in events) or (world.tick > 0 and world.tick % 50 == 0):
            self.save_persistent_memory()

        return TrainerDirective(
            phase=phase,
            weights=effective,
            scouting=scouting,
            ledger=self.ledger,
            new_events=tuple(events),
            coach_override_note=coach_note,
        )

    def record_issued_commands(self, world: World, commands: list[Command]) -> None:
        """Store this tick's state + issued commands so next tick can evaluate their success/error."""
        cmd_map = {c.playerId: c for c in commands}
        my_pos = {p.id: p.pos for p in world.me}
        def_mid = [p for p in world.me if p.id in (1, 2)]
        avg_x = sum(p.pos.x for p in def_mid) / len(def_mid) if def_mid else -20.0
        wide_cnt = sum(1 for p in def_mid if abs(p.pos.y) > 11.5)

        self.history.append(
            _TickSnapshot(
                tick=world.tick,
                clock=world.clock,
                ball_pos=world.ball.pos,
                ball_owner_team=world.ball.owner_team,
                ball_owner_id=world.ball.owner_id,
                score_me=world.score.me,
                score_opp=world.score.opp,
                commands=cmd_map,
                my_positions=my_pos,
                def_mid_avg_x=avg_x,
                def_mid_wide_count=wide_cnt,
            )
        )

    # ------------------------------------------------------------------
    # Causal Self-Error & Breakthrough Evaluator
    # ------------------------------------------------------------------
    def _flank_name(self, y: float) -> str:
        if y < -6.0:
            return "left"
        if y > 6.0:
            return "right"
        return "center"

    def _evaluate_causal_outcomes(
        self, prev: _TickSnapshot, world: World, events: list[str]
    ) -> None:
        w = self.learned_weights

        # --- Check Goal Scored / Conceded ---
        if world.score.me > prev.score_me:
            self.ledger.goals_scored += world.score.me - prev.score_me
            flank = self._flank_name(prev.ball_pos.y)
            self.ledger.flank_successes[flank] = self.ledger.flank_successes.get(flank, 0) + 2
            if flank == "left":
                w.preferred_flank -= 0.15
            elif flank == "right":
                w.preferred_flank += 0.15
            msg = f"GOAL_SCORED:reinforced_{flank}_flank"
            self.ledger.record_lesson(msg)
            events.append(msg)

        if world.score.opp > prev.score_opp:
            # Diagnose WHY we conceded using the recent tick ring buffer
            recent_snaps = list(self.history)[-5:]
            had_high_line = any(s.def_mid_avg_x > 2.0 for s in recent_snaps)
            had_pulled_wide = any(s.def_mid_wide_count >= 2 for s in recent_snaps)
            had_buildup_loss = any(
                s.ball_owner_team == "me" and s.ball_pos.x < -12.0 for s in recent_snaps
            )

            if had_buildup_loss:
                self.ledger.goals_conceded_turnover += 1
                w.gk_distribute_long_bias += 0.22
                w.long_pass_bias += 0.12
                w.pass_safety_margin += 0.03
                msg = "GOAL_CONCEDED_DIAGNOSIS:buildup_turnover->increase_long_distribution_and_safety"
            elif had_pulled_wide:
                self.ledger.goals_conceded_central += 1
                w.box_protector_lock = True
                w.defensive_line_offset -= 2.5
                msg = "GOAL_CONCEDED_DIAGNOSIS:central_box_exposed->enable_box_protector_lock"
            elif had_high_line:
                self.ledger.goals_conceded_counter += 1
                w.defensive_line_offset -= 3.5
                w.press_height_x -= 5.0
                msg = "GOAL_CONCEDED_DIAGNOSIS:caught_on_counter->drop_defensive_line"
            else:
                self.ledger.goals_conceded_central += 1
                w.defensive_line_offset -= 2.0
                w.press_intensity += 0.06
                msg = "GOAL_CONCEDED_DIAGNOSIS:shot_conceded->tighten_defense_and_press"

            self.ledger.record_lesson(msg)
            events.append(msg)
            w.clamp_all()
            return

        # --- Evaluate Our Ball-Carrier Command on `prev` Tick ---
        if prev.ball_owner_team == "me" and prev.ball_owner_id is not None:
            carrier_id = prev.ball_owner_id
            carrier_cmd = prev.commands.get(carrier_id)
            flank = self._flank_name(prev.ball_pos.y)

            if carrier_cmd is not None:
                ctype = carrier_cmd.commandType

                # Case 1: We issued PASS or GK_DISTRIBUTE
                if ctype in ("PASS", "GK_DISTRIBUTE"):
                    ptype = str(
                        carrier_cmd.parameters.get("type")
                        or carrier_cmd.parameters.get("method")
                        or "GROUND"
                    ).upper()
                    if world.ball.owner_team == "opp":
                        # ERROR: Our pass was intercepted!
                        self.ledger.pass_interceptions += 1
                        if ptype in self.ledger.failed_pass_types:
                            self.ledger.failed_pass_types[ptype] += 1
                        self.ledger.flank_turnovers[flank] = (
                            self.ledger.flank_turnovers.get(flank, 0) + 1
                        )
                        w.pass_safety_margin += 0.025
                        if ptype == "GROUND":
                            w.long_pass_bias += 0.07
                        # Shift away from congested turnover flank
                        if flank == "left":
                            w.preferred_flank += 0.12
                        elif flank == "right":
                            w.preferred_flank -= 0.12

                        if prev.ball_pos.x < -15.0:
                            self.ledger.buildup_turnovers_danger += 1
                            w.gk_distribute_long_bias += 0.16
                            w.dribble_caution_radius += 0.35
                            lesson = f"ERR_BUILDUP_TURNOVER_DANGER:p{carrier_id}_{ptype}->boost_long_clear_and_safety"
                        else:
                            lesson = f"ERR_PASS_INTERCEPTED:p{carrier_id}_{ptype}_on_{flank}->widen_safety_margin"
                        self.ledger.record_lesson(lesson)
                        events.append(lesson)

                    elif world.ball.owner_team == "me" and world.ball.owner_id != carrier_id:
                        # SUCCESS: Pass completed to a teammate!
                        self.ledger.passes_completed += 1
                        if ptype in self.ledger.successful_pass_types:
                            self.ledger.successful_pass_types[ptype] += 1
                        self.ledger.flank_successes[flank] = (
                            self.ledger.flank_successes.get(flank, 0) + 1
                        )

                # Case 2: We issued MOVE_TO while holding the ball (Dribbling)
                elif ctype == "MOVE_TO":
                    if world.ball.owner_team == "opp":
                        # ERROR: Dispossessed while dribbling!
                        self.ledger.dribble_dispossessions += 1
                        self.ledger.role_dispossessions[carrier_id] = (
                            self.ledger.role_dispossessions.get(carrier_id, 0) + 1
                        )
                        w.dribble_caution_radius += 0.45
                        if prev.ball_pos.x < -15.0:
                            self.ledger.buildup_turnovers_danger += 1
                            w.gk_distribute_long_bias += 0.12
                        lesson = f"ERR_DRIBBLE_DISPOSSESSED:p{carrier_id}->increase_dribble_caution_radius({w.dribble_caution_radius:.1f}m)"
                        self.ledger.record_lesson(lesson)
                        events.append(lesson)

                # Case 3: We issued SHOOT
                elif ctype == "SHOOT":
                    shooter_pos = prev.my_positions.get(carrier_id, prev.ball_pos)
                    shot_dist = shooter_pos.dist(Vec2(55.0, 0.0))
                    if world.ball.owner_team == "opp":
                        if world.ball.owner_id != 0 and world.ball.pos.dist(shooter_pos) < 9.0:
                            # Shot was blocked by an outfield defender!
                            self.ledger.shots_blocked += 1
                            w.cutback_preference += 0.08
                            lesson = f"ERR_SHOT_BLOCKED:p{carrier_id}->increase_cutback_preference"
                            self.ledger.record_lesson(lesson)
                            events.append(lesson)
                        elif shot_dist > 20.5 or abs(shooter_pos.y) > 12.0:
                            # Long or tight-angle shot easily saved/missed
                            self.ledger.shots_wasted_low_xg += 1
                            w.max_shoot_distance -= 0.75
                            w.cutback_preference += 0.06
                            lesson = f"ERR_SHOT_LOW_XG_WASTED:dist={shot_dist:.1f}m->tighten_max_shoot_dist({w.max_shoot_distance:.1f}m)"
                            self.ledger.record_lesson(lesson)
                            events.append(lesson)

        # --- Evaluate Our Defensive Commands on `prev` Tick ---
        elif prev.ball_owner_team == "opp":
            if world.ball.owner_team == "me":
                # SUCCESS: We won the ball back!
                self.ledger.tackles_won += 1
                events.append("SUCCESS_BALL_RECOVERED")
            elif world.ball.owner_team == "opp":
                # Check if we lunged in (`SLIDE_TACKLE` or `PRESS_BALL`) and got bypassed
                lunged = any(
                    c.commandType == "SLIDE_TACKLE" for c in prev.commands.values()
                )
                if lunged and (world.ball.pos.x < prev.ball_pos.x - 3.5):
                    self.ledger.press_bypassed += 1
                    w.slide_tackle_aggression -= 0.06
                    w.defensive_line_offset -= 1.0
                    lesson = "ERR_PRESS_BYPASSED:slide_tackle_missed->prefer_goal_side_shadow_containment"
                    self.ledger.record_lesson(lesson)
                    events.append(lesson)

        w.clamp_all()

    # ------------------------------------------------------------------
    # Phase Classifier & Weight Synthesizer
    # ------------------------------------------------------------------
    def _classify_phase(self, world: World) -> Phase:
        if world.play_mode != "OPEN_PLAY":
            return Phase.SET_PIECE

        ticks_since_flip = world.tick - self.possession_change_tick
        if world.ball.owner_team == "me":
            if 0 <= ticks_since_flip <= 3:
                return Phase.TRANSITION_ATTACK
            return Phase.IN_POSSESSION

        if world.ball.owner_team == "opp":
            if 0 <= ticks_since_flip <= 3:
                return Phase.TRANSITION_DEFENSE
            return Phase.OUT_OF_POSSESSION

        return Phase.LOOSE_BALL

    def _synthesize_weights(
        self,
        world: World,
        scouting: OpponentScoutingReport,
        phase: Phase,
    ) -> TacticalWeights:
        """Combine learned baseline + Eagle-Eye opponent counter-strategy + match clock/score."""
        w = self.learned_weights.copy()

        # 1. Eagle-Eye Opponent Archetype Counter-Strategy Adjustments
        if scouting.archetype == OpponentArchetype.MOB_PRESS:
            # Beat ball-chasers by releasing early, switching flanks aerially, and avoiding dribbles
            w.dribble_caution_radius = max(w.dribble_caution_radius, 5.8)
            w.long_pass_bias = max(w.long_pass_bias, 0.55)
            w.through_ball_aggression = max(w.through_ball_aggression, 0.72)
            w.tempo = max(w.tempo, 0.80)
        elif scouting.archetype == OpponentArchetype.ULTRA_HIGH_LINE:
            # Beat high line / sweeper GK with instant through balls and long shots
            w.through_ball_aggression = max(w.through_ball_aggression, 0.85)
            w.long_pass_bias = max(w.long_pass_bias, 0.50)
            w.tempo = max(w.tempo, 0.85)
        elif scouting.archetype == OpponentArchetype.LOW_BLOCK:
            # Compress pitch, patient triangles, and unlock top-corner power shots from 20-24m
            w.defensive_line_offset += 4.5
            w.press_height_x += 6.0
            w.max_shoot_distance = max(w.max_shoot_distance, 24.0)
            w.pass_safety_margin = max(0.16, w.pass_safety_margin - 0.03)
        elif scouting.archetype == OpponentArchetype.FROZEN_LLM:
            # Opponent has frozen/stalling players: aggressive carries & direct vertical attacks
            w.dribble_caution_radius = max(2.8, w.dribble_caution_radius - 1.0)
            w.through_ball_aggression = max(w.through_ball_aggression, 0.80)
            w.press_intensity = max(w.press_intensity, 0.88)

        # Sweeper-GK punish trigger (opponent GK > 12m off goal line)
        if scouting.opp_gk_depth_x < 43.0:
            w.exploit_sweeper_gk = True
            w.max_shoot_distance = max(w.max_shoot_distance, 34.0)
        else:
            w.exploit_sweeper_gk = False

        # Exploit opponent's weaker flank
        if scouting.weakest_flank == "left":
            w.preferred_flank = _clamp(w.preferred_flank - 0.35, -1.0, 1.0)
        elif scouting.weakest_flank == "right":
            w.preferred_flank = _clamp(w.preferred_flank + 0.35, -1.0, 1.0)

        # 2. Score & Clock Game-State Awareness (Official 5-minute = 300s match)
        diff = world.score.diff
        late_game = world.clock >= 210.0 or world.time_remaining <= 90.0
        final_minute = world.clock >= 250.0 or world.time_remaining <= 50.0

        if diff < 0:
            # Losing: increase urgency, press higher, commit numbers forward
            urgency = 1.5 if final_minute else (1.2 if late_game else 0.7)
            w.defensive_line_offset += 4.0 * urgency
            w.press_intensity += 0.14 * urgency
            w.press_height_x += 8.0 * urgency
            w.tempo += 0.18 * urgency
            w.through_ball_aggression += 0.15 * urgency
            if final_minute:
                w.max_shoot_distance = max(w.max_shoot_distance, 26.0)
        elif diff > 0:
            # Winning: protect the lead, compact rest-defense, high-percentage passes
            caution = 1.4 if final_minute else (1.0 if late_game else 0.5)
            w.defensive_line_offset -= 4.0 * caution
            w.press_height_x -= 6.0 * caution
            w.pass_safety_margin += 0.04 * caution
            w.tempo -= 0.18 * caution
            w.slide_tackle_aggression -= 0.10 * caution
            if diff >= 2 or final_minute:
                w.box_protector_lock = True

        # 3. Phase-Specific Micro-Modulation
        if phase == Phase.TRANSITION_ATTACK:
            w.through_ball_aggression = max(w.through_ball_aggression, 0.82)
            w.tempo = max(w.tempo, 0.90)
        elif phase == Phase.TRANSITION_DEFENSE:
            # Immediate 3-second Gegenpress if ball is in opponent half, else drop fast
            if world.ball.pos.x > 0.0:
                w.press_intensity = max(w.press_intensity, 0.88)
            else:
                w.defensive_line_offset -= 3.0

        # 4. Team Stamina Safeguard
        outfield = [p for p in world.me if p.id != 0]
        avg_stamina = sum(p.stamina for p in outfield) / max(1, len(outfield))
        if avg_stamina < 0.42:
            w.press_intensity = min(w.press_intensity, 0.52)
            w.press_height_x = min(w.press_height_x, 0.0)
            w.tempo = min(w.tempo, 0.50)

        return w.clamp_all()

    def _apply_coach_instruction(
        self, instruction: str | None, weights: TacticalWeights
    ) -> str | None:
        """Deterministically translate live human Coach chat instructions into tactical dials."""
        if not instruction or not instruction.strip():
            return None
        low = instruction.lower()
        applied: list[str] = []

        if any(k in low for k in ("press", "high line", "win the ball", "aggressive", "push up")):
            weights.press_intensity = max(weights.press_intensity, 0.90)
            weights.press_height_x = max(weights.press_height_x, 20.0)
            weights.defensive_line_offset += 5.0
            applied.append("high_press")

        if any(k in low for k in ("defend", "park", "hold position", "protect", "drop back", "compact")):
            weights.defensive_line_offset -= 6.0
            weights.press_height_x = min(weights.press_height_x, -5.0)
            weights.box_protector_lock = True
            applied.append("compact_defense")

        if any(k in low for k in ("possession", "slow", "keep the ball", "short pass", "patient")):
            weights.pass_safety_margin += 0.05
            weights.long_pass_bias = min(weights.long_pass_bias, 0.15)
            weights.tempo = min(weights.tempo, 0.40)
            applied.append("possession_play")

        if any(k in low for k in ("counter", "direct", "fast", "long ball", "behind")):
            weights.through_ball_aggression = max(weights.through_ball_aggression, 0.85)
            weights.long_pass_bias = max(weights.long_pass_bias, 0.60)
            weights.tempo = max(weights.tempo, 0.90)
            applied.append("counter_attack")

        if "left" in low:
            weights.preferred_flank = -0.85
            applied.append("focus_left_flank")
        elif "right" in low:
            weights.preferred_flank = 0.85
            applied.append("focus_right_flank")

        if any(k in low for k in ("shoot", "attack", "all out", "score")):
            weights.max_shoot_distance = max(weights.max_shoot_distance, 26.0)
            weights.defensive_line_offset += 4.0
            applied.append("attack_shoot_on_sight")

        return ",".join(applied) if applied else "acknowledged"

    def export_summary(self) -> dict[str, Any]:
        """Export structured snapshot for telemetry and `coach_review.py`."""
        scouting_rep = getattr(self.scout, "last_report", OpponentScoutingReport())
        return {
            "matches_played": self.matches_played,
            "learned_weights": asdict(self.learned_weights),
            "ledger": asdict(self.ledger),
            "scouting": asdict(scouting_rep),
        }
