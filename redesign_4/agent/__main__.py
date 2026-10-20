from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from .model import Profile
from .profile import load_profile
from .runtime import Runtime
from .trainer import Trainer


def read_json(path: str):
    with Path(path).open() as stream:
        return json.load(stream)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Coordinated football agent and offline trainer")
    commands = parser.add_subparsers(dest="operation", required=True)
    live = commands.add_parser("decide", help="one sample-protocol invocation; JSONL on stderr")
    live.add_argument("--input", required=True)
    live.add_argument("--profile")
    live.add_argument("--match-id")
    live.add_argument("--opponent-id")
    live.add_argument("--stamina-scale", type=float, choices=(1, 100), default=1)
    for name in ("ingest", "learn", "report", "train", "export", "audit", "promote"):
        sub = commands.add_parser(name)
        sub.add_argument("--db", required=True)
        if name in ("ingest", "learn"):
            sub.add_argument("--manifest", required=True)
            sub.add_argument("--records", required=True)
            sub.add_argument(
                "--bind-context",
                action="store_true",
                help="explicitly label missing match/opponent IDs in a match-scoped export",
            )
        if name in ("learn", "train", "export"):
            sub.add_argument("--output", required=True)
        if name == "report":
            sub.add_argument("--match-id", required=True)
        if name in ("export", "audit", "promote"):
            sub.add_argument("--version", required=True)
        if name == "promote":
            sub.add_argument("--note", required=True)
    args = parser.parse_args(argv)
    try:
        if args.operation == "decide":
            profile = load_profile(args.profile) if args.profile else Profile()
            runtime = Runtime(
                profile,
                match_id=args.match_id,
                opponent_id=args.opponent_id,
                stamina_scale=args.stamina_scale,
                emit=lambda record: print(
                    json.dumps(record, allow_nan=False), file=sys.stderr, flush=True
                ),
            )
            try:
                print(runtime.handle(read_json(args.input)), flush=True)
            finally:
                drained = runtime.close()
            if not drained or runtime.log_status["failed"] or runtime.log_status["dropped"]:
                raise OSError(f"decision telemetry incomplete: {runtime.log_status}")
            return 0
        manifest: dict = read_json(args.manifest) if args.operation in ("ingest", "learn") else {}
        records: list[dict] = []
        if args.operation in ("ingest", "learn"):
            with Path(args.records).open() as stream:
                records = [json.loads(line) for line in stream if line.strip()]
            if args.bind_context:
                for record in records:
                    if not isinstance(record, dict):
                        raise TypeError("record must be an object")
                    for key in ("match_id", "opponent_id"):
                        if record.get(key) is None:
                            record[key] = manifest.get(key)
        trainer = Trainer(args.db)
        try:
            result = None
            if args.operation in ("ingest", "learn"):
                result = trainer.ingest(manifest, records)
            if args.operation in ("train", "learn"):
                profile = trainer.train()
                trainer.export(profile.version, args.output)
                result = {
                    "report": result,
                    "profile": profile.to_dict(),
                    "audit": trainer.audit(profile.version),
                }
            elif args.operation == "report":
                result = trainer.report(args.match_id)
            elif args.operation == "audit":
                result = trainer.audit(args.version)
            elif args.operation == "promote":
                result = trainer.promote(args.version, args.note)
            elif args.operation == "export":
                trainer.export(args.version, args.output)
                result = {"exported_version": args.version, "path": args.output}
            print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))
        finally:
            trainer.close()
    except (ValueError, TypeError, KeyError, OSError, sqlite3.Error) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
