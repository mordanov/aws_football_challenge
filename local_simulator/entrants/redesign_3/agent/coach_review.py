"""Post-Match Football Trainer Analyzer & `tuning.json` Evolver.

Can be invoked programmatically (`analyze_match_memory(...)`, `analyze_decision_logs(...)`)
or via CLI after any match:
    python3 -m agent.coach_review --memory /tmp/afc_trainer_memory.json --write-tuning agent/tuning.json
"""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
from typing import Any

from .trainer import TacticalWeights


def analyze_match_data(data: dict[str, Any]) -> dict[str, Any]:
    """Synthesize a post-match coaching review and recommended `TacticalWeights` update."""
    ledger = data.get("ledger", {})
    scouting = data.get("scouting", data.get("last_scouting", {}))
    raw_weights = data.get("learned_weights", {})

    w = TacticalWeights()
    for k, v in raw_weights.items():
        if hasattr(w, k):
            setattr(w, k, v)

    our_errors: list[str] = []
    our_resolutions: list[str] = []
    opponent_insights: list[str] = []

    # 1. Diagnose Pass Turnovers
    interceptions = int(ledger.get("pass_interceptions", 0) or 0)
    completed = int(ledger.get("passes_completed", 0) or 0)
    buildup_losses = int(ledger.get("buildup_turnovers_danger", 0) or 0)
    if interceptions > 0:
        rate = interceptions / max(1, interceptions + completed)
        our_errors.append(
            f"Pass interceptions: {interceptions} (turnover rate {rate:.0%}, {buildup_losses} in defensive third)."
        )
        if rate > 0.25:
            w.pass_safety_margin = min(0.38, w.pass_safety_margin + 0.02)
            our_resolutions.append(
                f"Increased `pass_safety_margin` to {w.pass_safety_margin:.3f} to eliminate risky central passes."
            )
        if buildup_losses >= 2:
            w.gk_distribute_long_bias = min(0.85, max(w.gk_distribute_long_bias, 0.60))
            our_resolutions.append(
                f"Elevated `gk_distribute_long_bias` to {w.gk_distribute_long_bias:.2f} to bypass opponent high press."
            )

    # 2. Diagnose Dribble Dispossessions
    dispossessed = int(ledger.get("dribble_dispossessions", 0) or 0)
    if dispossessed >= 2:
        our_errors.append(f"Dribble dispossessions: {dispossessed} times caught holding the ball.")
        w.dribble_caution_radius = min(7.5, w.dribble_caution_radius + 0.35)
        our_resolutions.append(
            f"Raised `dribble_caution_radius` to {w.dribble_caution_radius:.2f}m for faster 1-touch ball release."
        )

    # 3. Diagnose Shooting Efficiency
    wasted_shots = int(ledger.get("shots_wasted_low_xg", 0) or 0)
    blocked_shots = int(ledger.get("shots_blocked", 0) or 0)
    goals_scored = int(ledger.get("goals_scored", 0) or 0)
    if wasted_shots >= 2 or blocked_shots >= 2:
        our_errors.append(
            f"Shot inefficiency: {wasted_shots} low-xG long/tight shots wasted, {blocked_shots} shots blocked (Goals: {goals_scored})."
        )
        w.max_shoot_distance = max(17.5, w.max_shoot_distance - 0.6)
        w.cutback_preference = min(0.90, w.cutback_preference + 0.06)
        our_resolutions.append(
            f"Tightened `max_shoot_distance` to {w.max_shoot_distance:.1f}m and boosted `cutback_preference` to {w.cutback_preference:.2f}."
        )

    # 4. Diagnose Defensive & Conceded Goal Patterns
    conceded_counter = int(ledger.get("goals_conceded_counter", 0) or 0)
    conceded_central = int(ledger.get("goals_conceded_central", 0) or 0)
    conceded_turnover = int(ledger.get("goals_conceded_turnover", 0) or 0)
    press_bypassed = int(ledger.get("press_bypassed", 0) or 0)

    if conceded_counter > 0:
        our_errors.append(f"Conceded {conceded_counter} goal(s) on opponent counter-attacks behind a high line.")
        w.defensive_line_offset = max(-10.0, w.defensive_line_offset - 2.0)
        our_resolutions.append(
            f"Dropped baseline `defensive_line_offset` to {w.defensive_line_offset:.1f}m for rest-defense protection."
        )
    if conceded_central > 0:
        our_errors.append(f"Conceded {conceded_central} goal(s) in the central penalty box / cutback zone.")
        w.box_protector_lock = True
        our_resolutions.append("Enabled `box_protector_lock = True` to keep DEF anchored in the central danger zone.")
    if press_bypassed >= 2:
        our_errors.append(f"Over-committed slide tackles bypassed {press_bypassed} times.")
        w.slide_tackle_aggression = max(0.20, w.slide_tackle_aggression - 0.05)
        our_resolutions.append(
            f"Reduced `slide_tackle_aggression` to {w.slide_tackle_aggression:.2f} in favor of goal-side jockeying."
        )

    # 5. Eagle-Eye Opponent Scouting Insights
    arch = scouting.get("archetype", "UNKNOWN")
    weak_flank = scouting.get("weakest_flank", "balanced")
    gk_depth = scouting.get("opp_gk_depth_x", 52.0)
    frozen_ids = scouting.get("frozen_opponent_ids", [])
    tired_ids = scouting.get("tired_opponent_ids", [])

    opponent_insights.append(f"Classified Opponent Archetype: {arch}")
    opponent_insights.append(f"Opponent Weakest Flank: {weak_flank} (GK avg depth x={gk_depth})")
    if frozen_ids:
        opponent_insights.append(f"Detected stalling/frozen opponent players: {frozen_ids} (LLM latency/timeout).")
    if tired_ids:
        opponent_insights.append(f"Detected exhausted opponent players (<36% stamina): {tired_ids}.")
    if scouting.get("counter_strategy_note"):
        opponent_insights.append(f"Counter-Strategy Applied: {scouting['counter_strategy_note']}")

    w.clamp_all()

    return {
        "matches_played": int(data.get("matches_played", 1) or 1),
        "our_errors": our_errors or ["No major tactical errors recorded in this sample."],
        "our_resolutions": our_resolutions or ["Maintain current balanced tactical weights."],
        "opponent_insights": opponent_insights,
        "recommended_weights": asdict(w),
    }


def parse_decision_log_lines(lines: list[str]) -> dict[str, Any]:
    """Extract latest trainer summary from structured `DECISION` / `TRAINER` log lines."""
    ledger: dict[str, Any] = {
        "pass_interceptions": 0,
        "buildup_turnovers_danger": 0,
        "dribble_dispossessions": 0,
        "shots_wasted_low_xg": 0,
        "shots_blocked": 0,
        "press_bypassed": 0,
        "goals_conceded_counter": 0,
        "goals_conceded_central": 0,
        "passes_completed": 0,
        "tackles_won": 0,
        "goals_scored": 0,
    }
    last_scouting: dict[str, Any] = {}
    last_weights: dict[str, Any] = {}

    for line in lines:
        line = line.strip()
        if not line:
            continue
        idx = line.find("{")
        if idx < 0:
            continue
        try:
            obj = json.loads(line[idx:])
        except Exception:
            continue
        if not isinstance(obj, dict):
            continue
        for ev in obj.get("trainer_events", []):
            if not isinstance(ev, str):
                continue
            if ev.startswith("ERR_PASS_INTERCEPTED"):
                ledger["pass_interceptions"] += 1
            elif ev.startswith("ERR_BUILDUP_TURNOVER_DANGER"):
                ledger["pass_interceptions"] += 1
                ledger["buildup_turnovers_danger"] += 1
            elif ev.startswith("ERR_DRIBBLE_DISPOSSESSED"):
                ledger["dribble_dispossessions"] += 1
            elif ev.startswith("ERR_SHOT_LOW_XG_WASTED"):
                ledger["shots_wasted_low_xg"] += 1
            elif ev.startswith("ERR_SHOT_BLOCKED"):
                ledger["shots_blocked"] += 1
            elif ev.startswith("ERR_PRESS_BYPASSED"):
                ledger["press_bypassed"] += 1
            elif "caught_on_counter" in ev:
                ledger["goals_conceded_counter"] += 1
            elif "central_box_exposed" in ev:
                ledger["goals_conceded_central"] += 1
            elif ev.startswith("GOAL_SCORED"):
                ledger["goals_scored"] += 1
            elif ev.startswith("SUCCESS_BALL_RECOVERED"):
                ledger["tackles_won"] += 1
        if isinstance(obj.get("scouting"), dict):
            last_scouting = obj["scouting"]
        if isinstance(obj.get("weights"), dict):
            last_weights = obj["weights"]

    return {
        "matches_played": 1,
        "ledger": ledger,
        "scouting": last_scouting,
        "learned_weights": last_weights,
    }


def render_markdown_report(review: dict[str, Any]) -> str:
    """Format the analysis dictionary as a readable Football Coach Markdown Report."""
    lines = [
        "# Football Trainer Post-Match Review",
        "",
        f"**Matches Analyzed:** {review.get('matches_played', 1)}",
        "",
        "## 1. Our Mistakes Detected",
    ]
    for item in review.get("our_errors", []):
        lines.append(f"- {item}")
    lines.append("")
    lines.append("## 2. Automatic Trainer Fixes Applied")
    for item in review.get("our_resolutions", []):
        lines.append(f"- {item}")
    lines.append("")
    lines.append("## 3. Eagle-Eye Opponent Scouting Dossier")
    for item in review.get("opponent_insights", []):
        lines.append(f"- {item}")
    lines.append("")
    lines.append("## 4. Evolved `tuning.json` Weights")
    lines.append("```json")
    lines.append(json.dumps({"weights": review.get("recommended_weights", {})}, indent=2))
    lines.append("```")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Post-Match Football Trainer Review & Tuning Evolver")
    parser.add_argument("--memory", type=str, default="/tmp/afc_trainer_memory.json", help="Path to trainer memory JSON")
    parser.add_argument("--logs", type=str, default=None, help="Optional path to DECISION log file")
    parser.add_argument("--write-tuning", type=str, default=None, help="Optional path to write updated tuning.json")
    args = parser.parse_args(argv)

    if args.logs and Path(args.logs).is_file():
        raw_lines = Path(args.logs).read_text(encoding="utf-8").splitlines()
        data = parse_decision_log_lines(raw_lines)
    elif Path(args.memory).is_file():
        data = json.loads(Path(args.memory).read_text(encoding="utf-8"))
    else:
        data = {}

    review = analyze_match_data(data)
    print(render_markdown_report(review))

    if args.write_tuning:
        out_path = Path(args.write_tuning)
        out_path.write_text(
            json.dumps(
                {
                    "version": "3.0.0",
                    "description": "Updated by Football Trainer coach_review",
                    "weights": review["recommended_weights"],
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
