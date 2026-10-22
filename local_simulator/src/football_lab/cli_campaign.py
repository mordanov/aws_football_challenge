"""CLI subcommand: python -m football_lab campaign init|run|report ..."""

from __future__ import annotations

import argparse
import datetime
import json
import sys
import time
import uuid
from pathlib import Path
from typing import Any, cast

from .campaign import (
    add_result,
    load_manifest,
    pending_fixtures,
    round_robin_fixtures,
    save_manifest,
)
from .campaign_report import bootstrap_ci, compute_standings
from .engine import MatchEngine
from .entrant_contracts import BudgetSpec, CampaignManifest, EntrantSpec, FixtureResult
from .match_contracts import EngineState
from .match_replay import JsonMatchReplayStore
from .team_runner import RunnerError, TeamRunner

_ENTRANTS_DIR = Path(__file__).parent.parent.parent / "entrants"

_DEV_BUDGET: BudgetSpec = {
    "startup_timeout_s": 15.0,
    "request_timeout_s": 10.0,
    "output_size_limit": 65_536,
}


def _load_entrant_spec(entrant_id: str) -> EntrantSpec:
    """Load EntrantSpec from entrants/<source_name>/entrant.json by id."""
    for d in _ENTRANTS_DIR.iterdir():
        if not d.is_dir():
            continue
        ej = d / "entrant.json"
        if ej.exists():
            manifest = json.loads(ej.read_text())
            if manifest["id"] == entrant_id:
                shim_path = str(d / "shim.py")
                return {
                    "id": manifest["id"],
                    "source_name": manifest["source_name"],
                    "topology": manifest["topology"],
                    "shim": shim_path,
                    "entrant_hash": manifest["entrant_hash"],
                    "rules_version": manifest["rules_version"],
                    "track": manifest["track"],
                }
    raise ValueError(f"entrant not found: {entrant_id!r}")


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
        return self._runner.decide(cast(EngineState, state))


def _cmd_init(args: argparse.Namespace) -> int:
    entrant_ids = [e.strip() for e in args.entrants.split(",")]
    specs = [_load_entrant_spec(eid) for eid in entrant_ids]
    fixtures = round_robin_fixtures(
        entrant_ids, seed_pool_start=args.seed_pool_start, n_blocks=args.blocks
    )

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
        "rules_version": "v1",
        "fixtures": fixtures,
        "results": [],
        "locked": False,
    }
    save_manifest(manifest, Path(args.output))
    print(f"campaign init: {len(fixtures)} fixtures → {args.output}")
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    path = Path(args.manifest)
    manifest = load_manifest(path)
    replays_dir = Path(args.replays)
    replays_dir.mkdir(parents=True, exist_ok=True)
    store = JsonMatchReplayStore()

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
            TeamRunner(spec0, team_id=0, budget=budget, work_dir=replays_dir) as r0,
            TeamRunner(spec1, team_id=1, budget=budget, work_dir=replays_dir) as r1,
        ):
            try:
                engine = MatchEngine(
                    seed=fixture["seed"],
                    team0_agent=_TeamRunnerEngineAgent(r0),
                    team1_agent=_TeamRunnerEngineAgent(r1),
                    store=store,
                )
                replay = engine.run(replay_path)
                score = list(replay["frames"][-1]["score"])
                duration_ms = (time.perf_counter() - t0) * 1000
                result: FixtureResult = {
                    "fixture_id": fid,
                    "status": "complete",
                    "score": score,
                    "failure_reason": None,
                    "replay_path": str(replay_path),
                    "duration_ms": duration_ms,
                    "team0_rejections": 0,
                    "team1_rejections": 0,
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
    results = [r for r in manifest["results"] if r["status"] == "complete"]
    if not results:
        print("no complete results")
        return 0

    standings = compute_standings(results)
    print(
        f"\n{'Entrant':<20} {'G':>3} {'W':>3} {'D':>3} {'L':>3} {'Pts':>4} {'GF':>4} {'GA':>4} {'GD':>4}"
    )
    print("-" * 60)
    for s in standings:
        print(
            f"{s['entrant_id']:<20} {s['games']:>3} {s['wins']:>3} {s['draws']:>3}"
            f" {s['losses']:>3} {s['points']:>4} {s['goals_for']:>4}"
            f" {s['goals_against']:>4} {s['goal_diff']:>+4}"
        )

    ci = bootstrap_ci(results, rng_seed=0)
    if ci:
        print("\nPairwise 95% CI (per-match point differential a − b):")
        for a, vs in sorted(ci.items()):
            for key, (lo, hi) in sorted(vs.items()):
                b = key[3:]  # strip "vs_"
                print(f"  {a} vs {b}: [{lo:+.2f}, {hi:+.2f}]")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="football_lab campaign")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser("init", help="Create a new campaign manifest")
    p_init.add_argument("--track", default="development")
    p_init.add_argument("--entrants", required=True, help="Comma-separated entrant IDs")
    p_init.add_argument("--blocks", type=int, default=5)
    p_init.add_argument("--seed-pool-start", type=int, default=0, dest="seed_pool_start")
    p_init.add_argument("--output", required=True)

    p_run = sub.add_parser("run", help="Run pending fixtures")
    p_run.add_argument("--manifest", required=True)
    p_run.add_argument("--replays", required=True)

    p_report = sub.add_parser("report", help="Print standings and bootstrap CI")
    p_report.add_argument("--manifest", required=True)

    args = parser.parse_args(argv)
    if args.cmd == "init":
        return _cmd_init(args)
    if args.cmd == "run":
        return _cmd_run(args)
    if args.cmd == "report":
        return _cmd_report(args)
    return 1
