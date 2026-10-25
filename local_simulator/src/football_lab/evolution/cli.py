"""evolve init|run|report — bounded profile search CLI."""
from __future__ import annotations

import argparse
import importlib
import json
import sys
from dataclasses import fields
from pathlib import Path

from .bounds import _load, load_bounds, validate_settings
from .contracts import Settings
from .evaluator import MatchEvaluator
from .fitness import EvaluationCache
from .integrity import hash_tree, verify_unchanged
from .mutation import profile_hash
from .pools import PoolOverlap, assert_disjoint, make_pool, stage1_range
from .promotion import decide_promotion
from .search import SearchConfig, run_search
from .selection import FinalTestLock, FinalTestLockError, select_finalist

_ENTRANTS_ROOT = Path(__file__).resolve().parents[3] / "entrants"

OPPONENTS: list[str] = []  # populated once football_agent entrant is registered


def _incumbent_settings() -> Settings:
    profile_mod = _load()
    assert profile_mod.__spec__ is not None
    pkg = profile_mod.__spec__.parent
    model_mod = importlib.import_module(f"{pkg}.model")
    ds = model_mod.DEFAULT_SETTINGS
    return {f.name: getattr(ds, f.name) for f in fields(ds)}


def _entrant_dir(entrant_id: str) -> Path:
    for d in _ENTRANTS_ROOT.iterdir():
        if d.is_dir():
            ej = d / "entrant.json"
            if ej.exists() and json.loads(ej.read_text())["id"] == entrant_id:
                return d
    raise ValueError(f"entrant not found: {entrant_id!r}")


def _frozen_hashes() -> dict[str, str]:
    return {eid: hash_tree(_entrant_dir(eid)) for eid in OPPONENTS}


def _load_manifest(d: Path) -> dict:
    return json.loads((d / "manifest.json").read_text())


def _save_json(path: Path, data: object) -> None:
    tmp = path.parent / f".{path.name}.tmp"
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(path)


def make_evaluator(work_dir: Path, evaluator_id: str, keep_replays: bool = False) -> MatchEvaluator:
    return MatchEvaluator(work_dir=work_dir, evaluator_id=evaluator_id, keep_replays=keep_replays)


# ── init ──────────────────────────────────────────────────────────────────────

def _cmd_init(args: argparse.Namespace) -> int:
    d = Path(args.dir)
    stage1_manifest = json.loads(Path(args.stage1_manifest).read_text())
    s1_range = stage1_range(stage1_manifest)
    bounds = load_bounds()
    incumbent = _incumbent_settings()
    validate_settings(incumbent)

    training = make_pool("training", 100_000, 8, len(OPPONENTS))
    selection = make_pool("selection", 200_000, 20, len(OPPONENTS))
    final = make_pool("final", 300_000, 100, len(OPPONENTS))
    try:
        assert_disjoint([training, selection, final], reserved=[s1_range])
    except PoolOverlap as e:
        print(f"pool overlap: {e}", file=sys.stderr)
        return 1

    frozen_hashes = _frozen_hashes()

    manifest = {
        "experiment_seed": args.experiment_seed,
        "rules_version": "v1",
        "incumbent": {"settings": incumbent, "hash": profile_hash(incumbent)},
        "bounds": {k: list(v) for k, v in bounds.items()},
        "opponents": OPPONENTS,
        "frozen_hashes": frozen_hashes,
        "pools": {
            "training": training,
            "selection": selection,
            "final": final,
        },
        "stage1_reserved": [s1_range.start, s1_range.stop],
        "bootstrap_seed": args.experiment_seed,
        "n_resamples": 10_000,
        "population": 12,
        "generations": 10,
    }
    d.mkdir(parents=True, exist_ok=True)
    _save_json(d / "manifest.json", manifest)
    print(f"init: experiment at {d}", flush=True)
    return 0


# ── run ───────────────────────────────────────────────────────────────────────

def _cmd_run(args: argparse.Namespace) -> int:
    d = Path(args.dir)
    m = _load_manifest(d)
    work = d / "work"
    work.mkdir(exist_ok=True)
    cache = EvaluationCache(d / "evaluations.jsonl")

    # Check integrity before starting
    opponent_dirs = {eid: _entrant_dir(eid) for eid in m["opponents"]}
    violations = verify_unchanged(opponent_dirs, m["frozen_hashes"])
    if violations:
        for v in violations:
            print(f"integrity error: {v}", file=sys.stderr)
        return 1

    evaluator_id = "rules-v1|" + "|".join(
        f"{k}={v}" for k, v in sorted(m["frozen_hashes"].items())
    )
    ev = make_evaluator(work, evaluator_id)
    bounds = {k: tuple(v) for k, v in m["bounds"].items()}
    incumbent = m["incumbent"]["settings"]
    training_pool = m["pools"]["training"]
    selection_pool = m["pools"]["selection"]
    final_pool = m["pools"]["final"]
    opponents = m["opponents"]

    lock_path = d / "final.lock.json"
    lock = FinalTestLock(lock_path)

    # Resume: if final.json already exists
    if (d / "final.json").exists():
        print("run: final.json already exists; nothing to do", flush=True)
        return 0

    # Run search (or load from cache)
    if not (d / "search.json").exists():
        cfg = SearchConfig(
            population=m["population"],
            generations=m["generations"],
            experiment_seed=m["experiment_seed"],
            pool=training_pool,
            opponents=opponents,
        )
        result = run_search(cfg, incumbent, ev, cache, bounds)
        _save_json(d / "search.json", result)
    else:
        result = json.loads((d / "search.json").read_text())

    # Select finalist
    if not (d / "selection.json").exists():
        settings_by_hash = {e["profile_hash"]: e["settings"] for e in result["lineage"]}
        settings_by_hash[profile_hash(incumbent)] = incumbent
        sel = select_finalist(
            result["training_fitness"],
            settings_by_hash,
            profile_hash(incumbent),
            ev, cache, selection_pool, opponents,
        )
        _save_json(d / "selection.json", sel)
    else:
        sel = json.loads((d / "selection.json").read_text())

    if sel["finalist"] is None:
        _save_json(d / "final.json", {
            "promoted": False, "finalist": None,
            "reasons": ["incumbent retained at selection"],
        })
        print("run: incumbent retained at selection; final pool never opened", flush=True)
        return 0

    finalist_hash = sel["finalist"]
    settings_by_hash_full = {e["profile_hash"]: e["settings"] for e in result["lineage"]}

    # Freeze lock if not yet
    if not lock_path.exists():
        lock.freeze(finalist_hash, profile_hash(incumbent))

    # Second integrity check before final
    if verify_unchanged(opponent_dirs, m["frozen_hashes"]):
        print("integrity error (pre-final): entrant changed", file=sys.stderr)
        return 1

    try:
        lock.begin_final(finalist_hash)
    except FinalTestLockError as e:
        print(f"lock error: {e}", file=sys.stderr)
        return 1

    # Evaluate finalist and incumbent on final pool
    finalist_blocks = ev.evaluate(settings_by_hash_full[finalist_hash], final_pool, opponents)
    incumbent_blocks = ev.evaluate(incumbent, final_pool, opponents)

    final_violations = verify_unchanged(opponent_dirs, m["frozen_hashes"])
    decision = decide_promotion(
        finalist_hash,
        finalist_blocks,
        incumbent_blocks,
        bootstrap_seed=m["bootstrap_seed"],
        n_resamples=m["n_resamples"],
        expected_blocks=100,
        extra_failures=final_violations,
    )
    _save_json(d / "final.json", decision)
    status = "PROMOTED" if decision["promoted"] else "REJECTED"
    print(f"run: {status}", flush=True)
    return 0


# ── report ────────────────────────────────────────────────────────────────────

def _cmd_report(args: argparse.Namespace) -> int:
    d = Path(args.dir)
    m = _load_manifest(d)
    lines = [
        "# Evolution Report",
        "",
        "## Bounds",
        *[f"- {k}: {v[0]}–{v[1]}" for k, v in m["bounds"].items()],
        "",
        "## Pools",
        *[f"- {name}: start={p['start']}, blocks={p['blocks']}, size={p['size']}"
          for name, p in m["pools"].items()],
        "",
        "## Frozen opponent hashes",
        *[f"- {k}: {v}" for k, v in m["frozen_hashes"].items()],
        "",
    ]

    if (d / "search.json").exists():
        sr = json.loads((d / "search.json").read_text())
        lines += [
            f"## Search: {sr['unique_evaluations']} unique evaluations, "
            f"{sr['cache_hits']} cache hits",
            f"Lineage entries: {len(sr['lineage'])}",
            "",
        ]

    if (d / "selection.json").exists():
        sel = json.loads((d / "selection.json").read_text())
        lines += [
            f"## Selection: finalist = {sel['finalist'] or 'none (incumbent retained)'}",
            "",
        ]

    if (d / "final.json").exists():
        fin = json.loads((d / "final.json").read_text())
        status = "PROMOTED" if fin["promoted"] else "REJECTED"
        lines += [
            f"## Final decision: {status}",
            *[f"- {r}" for r in fin.get("reasons", [])],
            "",
            "**bounded profile search; no model weights were trained or modified.**",
        ]

    report = "\n".join(lines) + "\n"
    (d / "report.md").write_text(report)
    print(report)
    return 0


# ── main ─────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="evolve")
    sub = parser.add_subparsers(dest="cmd")

    p_init = sub.add_parser("init")
    p_init.add_argument("--dir", required=True)
    p_init.add_argument("--stage1-manifest", required=True)
    p_init.add_argument("--experiment-seed", type=int, required=True)

    p_run = sub.add_parser("run")
    p_run.add_argument("--dir", required=True)

    p_rep = sub.add_parser("report")
    p_rep.add_argument("--dir", required=True)

    args = parser.parse_args(argv)
    if args.cmd == "init":
        return _cmd_init(args)
    if args.cmd == "run":
        return _cmd_run(args)
    if args.cmd == "report":
        return _cmd_report(args)
    parser.print_help()
    return 1
