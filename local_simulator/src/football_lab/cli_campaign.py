"""CLI subcommand: python -m football_lab campaign init|run|report ..."""

from __future__ import annotations

import argparse
import datetime
import json
import sys
import time
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any, cast

from .campaign import (
    add_result,
    cross_fixtures,
    load_manifest,
    pending_fixtures,
    round_robin_fixtures,
    save_manifest,
)
from .campaign_report import (
    bootstrap_ci,
    compute_standings,
    discipline_by_entrant,
    discipline_counts,
    goal_diff_delta_ci,
    head_to_head_matrix,
    operational_summary,
    pooled_goal_diff_ci,
    winner_claim,
)
from .coaches import COACHES
from .engine import MatchEngine
from .entrant_contracts import BudgetSpec, CampaignManifest, EntrantSpec, FixtureResult
from .match_contracts import CoachFn, EngineState, MatchReplay
from .match_replay import JsonMatchReplayStore
from .rules_v2 import RulesV2Config
from .team_runner import RunnerError, TeamRunner

_ENTRANTS_DIR = Path(__file__).parent.parent.parent / "entrants"

_DEV_BUDGET: BudgetSpec = {
    "startup_timeout_s": 15.0,
    "request_timeout_s": 10.0,
    "output_size_limit": 65_536,
}


def _base_spec(d: Path, manifest: dict[str, Any]) -> EntrantSpec:
    return {
        "id": manifest["id"],
        "source_name": manifest["source_name"],
        "topology": manifest["topology"],
        "shim": str(d / "shim.py"),
        "entrant_hash": manifest["entrant_hash"],
        "rules_version": manifest["rules_version"],
        "track": manifest["track"],
    }


def _load_entrant_spec(entrant_id: str) -> EntrantSpec:
    """Resolve an entrant id, or a profile variant id from a sibling variants.json."""
    for d in sorted(_ENTRANTS_DIR.iterdir()):
        ej = d / "entrant.json"
        if not d.is_dir() or not ej.exists():
            continue
        manifest = json.loads(ej.read_text())
        if manifest["id"] == entrant_id:
            return _base_spec(d, manifest)
        vj = d / "variants.json"
        if not vj.exists():
            continue
        for variant in json.loads(vj.read_text())["variants"]:
            if variant["id"] == entrant_id:
                spec = _base_spec(d, manifest)
                spec["id"] = variant["id"]
                spec["profile"] = str((d / variant["profile"]).resolve())
                spec["coach"] = variant.get("coach")
                return spec
    raise ValueError(f"entrant not found: {entrant_id!r}")


def _runner_env(spec: EntrantSpec) -> dict[str, str] | None:
    profile = spec.get("profile")
    return {"AFC_PROFILE": profile} if profile else None


def _coach_fn(spec: EntrantSpec) -> CoachFn | None:
    name = spec.get("coach")
    if name is None:
        return None
    if name not in COACHES:
        raise ValueError(f"unknown coach {name!r} for entrant {spec['id']!r}")
    return COACHES[name]


class _DiscardStore:
    def save(self, replay: MatchReplay, path: Path) -> None:
        return None


def _ids(text: str) -> list[str]:
    return [e.strip() for e in text.split(",") if e.strip()]


class _TeamRunnerEngineAgent:
    """Bridges MatchEngine's EngineAgent protocol to TeamRunner."""

    def __init__(self, runner: TeamRunner) -> None:
        self._runner = runner

    def decide(self, obs: dict[str, Any]) -> list[dict[str, Any]]:
        # Reconstruct minimal EngineState from M0-format observation
        state = {
            "tick": obs["tick"],
            "play_mode": obs["play_mode"],
            "score": obs["score"],
            "players": obs["players"],
            "ball": obs["ball"],
            "mode_team": obs.get("mode_team"),
        }
        return self._runner.decide(cast(EngineState, state), coach=str(obs.get("coach", "")))


def _cmd_init(args: argparse.Namespace) -> int:
    cross = bool(args.challengers or args.pool)
    if bool(args.entrants) == cross or (cross and not (args.challengers and args.pool)):
        print("init: use either --entrants, or --challengers with --pool", file=sys.stderr)
        return 2
    if args.foul_scale is not None and args.rules != "v2":
        print("init: --foul-scale requires --rules v2", file=sys.stderr)
        return 2
    challengers: list[str] | None = None
    try:
        if cross:
            challengers, pool = _ids(args.challengers), _ids(args.pool)
            fixtures = cross_fixtures(challengers, pool, args.seed_pool_start, args.blocks)
            entrant_ids = challengers + pool
        else:
            entrant_ids = _ids(args.entrants)
            fixtures = round_robin_fixtures(
                entrant_ids, seed_pool_start=args.seed_pool_start, n_blocks=args.blocks
            )
        specs = [_load_entrant_spec(eid) for eid in entrant_ids]
        for spec in specs:
            _coach_fn(spec)
        rules_config = (
            RulesV2Config(foul_scale=1.0 if args.foul_scale is None else args.foul_scale)
            if args.rules == "v2"
            else None
        )
    except ValueError as exc:
        print(f"init: {exc}", file=sys.stderr)
        return 2

    campaign_id = (
        datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S")
        + "-"
        + uuid.uuid4().hex[:8]
    )
    manifest: CampaignManifest = {
        "campaign_id": campaign_id,
        "track": args.track,
        "entrants": specs,
        "seed_pool_start": args.seed_pool_start,
        "budget": _DEV_BUDGET,
        "rules_version": args.rules,
        "fixtures": fixtures,
        "results": [],
        "locked": False,
    }
    if rules_config is not None:
        manifest["rules_params"] = asdict(rules_config)
    if challengers is not None:
        manifest["challengers"] = challengers
    save_manifest(manifest, Path(args.output))
    print(f"campaign init: {len(fixtures)} fixtures ({args.rules}) → {args.output}")
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    path = Path(args.manifest)
    manifest = load_manifest(path)
    replays_dir = Path(args.replays)
    replays_dir.mkdir(parents=True, exist_ok=True)
    store = _DiscardStore() if args.discard_replays else JsonMatchReplayStore()
    rules_version = manifest["rules_version"]
    rules_config = (
        RulesV2Config(**manifest["rules_params"]) if rules_version == "v2" else None
    )

    # Build entrant spec lookup
    spec_by_id = {s["id"]: s for s in manifest["entrants"]}

    pending = pending_fixtures(manifest)
    print(f"campaign run: {len(pending)} pending fixtures")
    budget: BudgetSpec = manifest["budget"]

    for fixture in pending:
        fid = fixture["fixture_id"]
        print(f"  running {fid} ...", end=" ", flush=True)
        t0 = time.perf_counter()

        spec0 = spec_by_id[fixture["team0_entrant"]]
        spec1 = spec_by_id[fixture["team1_entrant"]]
        replay_path = replays_dir / f"{fid}.json"

        with (
            TeamRunner(
                spec0, team_id=0, budget=budget, work_dir=replays_dir, env=_runner_env(spec0)
            ) as r0,
            TeamRunner(
                spec1, team_id=1, budget=budget, work_dir=replays_dir, env=_runner_env(spec1)
            ) as r1,
        ):
            try:
                engine = MatchEngine(
                    seed=fixture["seed"],
                    team0_agent=_TeamRunnerEngineAgent(r0),
                    team1_agent=_TeamRunnerEngineAgent(r1),
                    store=store,
                    rules_version=rules_version,
                    rules_config=rules_config,
                    coaches=(_coach_fn(spec0), _coach_fn(spec1)),
                )
                replay = engine.run(replay_path)
                score = list(replay["frames"][-1]["score"])
                duration_ms = (time.perf_counter() - t0) * 1000
                result: FixtureResult = {
                    "fixture_id": fid,
                    "status": "complete",
                    "score": score,
                    "failure_reason": None,
                    "replay_path": None if args.discard_replays else str(replay_path),
                    "duration_ms": duration_ms,
                    "team0_rejections": 0,
                    "team1_rejections": 0,
                    "discipline": discipline_counts(replay["events"]),
                }
                print(f"complete {score[0]}-{score[1]} ({duration_ms:.0f}ms)")
            except (RunnerError, Exception) as exc:
                duration_ms = (time.perf_counter() - t0) * 1000
                result = {
                    "fixture_id": fid,
                    "status": "failed",
                    "score": [0, 0],
                    "failure_reason": str(exc),
                    "replay_path": None,
                    "duration_ms": duration_ms,
                    "team0_rejections": 0,
                    "team1_rejections": 0,
                }
                print(f"FAILED: {exc}", file=sys.stderr)

        add_result(manifest, result)
        save_manifest(manifest, path)

    completed = sum(1 for r in manifest["results"] if r["status"] == "complete")
    failed = sum(1 for r in manifest["results"] if r["status"] == "failed")
    print(f"campaign run done: {completed} complete, {failed} failed")
    return 0


def _cmd_report(args: argparse.Namespace) -> int:
    manifest = load_manifest(Path(args.manifest))
    all_results = manifest["results"]
    results = [r for r in all_results if r["status"] == "complete"]

    total = len(manifest["fixtures"])
    n_complete = len(results)
    n_failed = sum(1 for r in all_results if r["status"] == "failed")
    is_final = n_complete == total and n_failed == 0
    status = "[FINAL]" if is_final else f"[PROVISIONAL – {n_failed} failed]"
    print(f"\nCampaign: {n_complete}/{total} complete {status}")

    entrant_ids = sorted({
        eid
        for f in manifest["fixtures"]
        for eid in (f["team0_entrant"], f["team1_entrant"])
    })

    # Standings
    print("\nStandings")
    print(f"{'Entrant':<22} {'G':>3} {'W':>3} {'D':>3} {'L':>3} {'Pts':>4} {'GF':>4} {'GA':>4} {'GD':>4}")
    print("-" * 62)
    for s in compute_standings(results):
        print(
            f"{s['entrant_id']:<22} {s['games']:>3} {s['wins']:>3} {s['draws']:>3}"
            f" {s['losses']:>3} {s['points']:>4} {s['goals_for']:>4}"
            f" {s['goals_against']:>4} {s['goal_diff']:>+4}"
        )

    # Head-to-head
    h2h = head_to_head_matrix(results)
    col_w = max(12, max((len(e) for e in entrant_ids), default=12) + 2)
    print("\nHead-to-head (W-D-L)")
    print(f"{'':22}" + "".join(f"{e:>{col_w}}" for e in entrant_ids))
    for a in entrant_ids:
        row = f"{a:<22}"
        for b in entrant_ids:
            if a == b:
                cell = "–"
            else:
                rec = h2h.get(a, {}).get(b)
                cell = f"{rec['wins']}-{rec['draws']}-{rec['losses']}" if rec else "?"
            row += f"{cell:>{col_w}}"
        print(row)

    # Operational summary
    op, rejections = operational_summary(manifest)
    print("\nOperational summary")
    print(f"  Total: {op['total']}  Complete: {op['complete']}  Failed: {op['failed']}")
    print(f"  {'Entrant':<22} {'Rejections':>12}")
    print(f"  {'-' * 36}")
    for e in entrant_ids:
        print(f"  {e:<22} {rejections.get(e, 0):>12}")

    seed = manifest["seed_pool_start"]
    baseline: str | None = getattr(args, "baseline", None)
    json_path: str | None = getattr(args, "json_path", None)
    challengers = manifest.get("challengers")
    focus = challengers or entrant_ids

    ci = bootstrap_ci(results, rng_seed=seed)
    if ci:
        print("\nPairwise 95% CI (per-match point differential)")
        for a, vs in sorted(ci.items()):
            for key, (lo, hi) in sorted(vs.items()):
                print(f"  {a} vs {key[3:]}: [{lo:+.2f}, {hi:+.2f}]")

    pooled = []
    for eid in focus:
        try:
            pooled.append(pooled_goal_diff_ci(results, eid, rng_seed=seed))
        except ValueError:
            continue
    pooled.sort(key=lambda p: (-p["lo"], p["entrant_id"]))
    print("\nPooled goal difference per match (95% block-bootstrap CI)")
    for p in pooled:
        print(f"  {p['entrant_id']:<28} {p['mean']:+.3f} [{p['lo']:+.3f}, {p['hi']:+.3f}]"
              f"  n={p['matches']}")

    deltas: dict[str, dict[str, float]] = {}
    if baseline:
        print(f"\nGoal-difference delta vs {baseline} (paired, 95% CI)")
        for eid in focus:
            if eid == baseline:
                continue
            try:
                mean, lo, hi = goal_diff_delta_ci(results, eid, baseline, rng_seed=seed)
            except ValueError:
                continue
            deltas[eid] = {"mean": mean, "lo": lo, "hi": hi}
            print(f"  {eid:<28} {mean:+.3f} [{lo:+.3f}, {hi:+.3f}]")

    discipline = discipline_by_entrant(results)
    if discipline:
        print("\nDiscipline (totals)")
        print(f"  {'Entrant':<28} {'M':>4} {'Fouls':>6} {'Off':>4} {'TklWon':>7} {'PenFor':>7}")
        for eid, d in sorted(discipline.items()):
            print(f"  {eid:<28} {d['matches']:>4} {d['fouls']:>6} {d['send_offs']:>4}"
                  f" {d['tackles_won']:>7} {d['penalties_awarded']:>7}")

    claim = None
    n_pairs = len(entrant_ids) * (len(entrant_ids) - 1) // 2
    if challengers:
        print("\nWinner claim: n/a (cross campaign — use pooled goal difference)")
    else:
        print(f"\nWinner claim (99.5% CI, Bonferroni over {n_pairs} pairs)")
        if not is_final:
            print(f"  PROVISIONAL ({n_failed} failures) — no winner claim made")
        else:
            claim = winner_claim(all_results, entrant_ids, bootstrap_seed=seed)
            if claim["reason"] == "clear_winner":
                print(f"  Winner: {claim['winner_id']}")
                print(f"  Reasoning: positive lower bound vs all {len(entrant_ids) - 1} rivals")
            else:
                print(f"  {claim['reason'].upper()}")

    if json_path:
        out = {
            "campaign_id": manifest["campaign_id"],
            "rules_version": manifest["rules_version"],
            "rules_params": manifest.get("rules_params"),
            "complete": n_complete,
            "total": total,
            "final": is_final,
            "challengers": challengers,
            "standings": compute_standings(results),
            "pooled_goal_diff": pooled,
            "pairwise_points_ci": {
                a: {k[3:]: list(v) for k, v in vs.items()} for a, vs in ci.items()
            },
            "discipline": discipline,
            "winner_claim": claim,
            "goal_diff_delta_vs_baseline": (
                {"baseline": baseline, "deltas": deltas} if baseline else None
            ),
        }
        path = Path(json_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(out, indent=2, sort_keys=True) + "\n")
        print(f"\nJSON report → {path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="football_lab campaign")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser("init", help="Create a new campaign manifest")
    p_init.add_argument("--track", default="development")
    p_init.add_argument("--entrants", help="Comma-separated entrant IDs (round robin)")
    p_init.add_argument("--challengers", help="Comma-separated challenger IDs (cross mode)")
    p_init.add_argument("--pool", help="Comma-separated opponent-pool IDs (cross mode)")
    p_init.add_argument("--rules", choices=["v1", "v2"], default="v1")
    p_init.add_argument("--foul-scale", type=float, dest="foul_scale")
    p_init.add_argument("--blocks", type=int, default=5)
    p_init.add_argument("--seed-pool-start", type=int, default=0, dest="seed_pool_start")
    p_init.add_argument("--output", required=True)

    p_run = sub.add_parser("run", help="Run pending fixtures")
    p_run.add_argument("--manifest", required=True)
    p_run.add_argument("--replays", required=True)
    p_run.add_argument("--discard-replays", action="store_true", dest="discard_replays")

    p_report = sub.add_parser("report", help="Print standings and bootstrap CI")
    p_report.add_argument("--manifest", required=True)
    p_report.add_argument("--json", dest="json_path")
    p_report.add_argument("--baseline")

    args = parser.parse_args(argv)
    if args.cmd == "init":
        return _cmd_init(args)
    if args.cmd == "run":
        return _cmd_run(args)
    if args.cmd == "report":
        return _cmd_report(args)
    return 1
