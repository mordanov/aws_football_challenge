import argparse
import subprocess
import sys
from pathlib import Path

from .process_agent import ProcessAgent
from .replay import JsonReplayStore
from .skeleton import TrivialStepper, run


def _wiring_check(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Local football wiring check; not a match")
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    agent = ProcessAgent((sys.executable, "-m", "football_lab.stub_agent"))
    try:
        run(args.seed, (agent, agent), TrivialStepper(), JsonReplayStore(), args.output)
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f"wiring check failed: {error}", file=sys.stderr)
        return 1
    print(args.output.resolve())
    return 0


def main() -> int:
    # Top-level dispatcher: `python -m football_lab [subcommand] ...`
    # Subcommand is optional for backward compatibility (defaults to wiring-check).
    if len(sys.argv) > 1 and sys.argv[1] == "match":
        from .cli_match import main as match_main
        return match_main(sys.argv[2:])
    if len(sys.argv) > 1 and sys.argv[1] == "bench":
        from .bench import main as bench_main
        bench_main()
        return 0
    if len(sys.argv) > 1 and sys.argv[1] == "campaign":
        from .cli_campaign import main as campaign_main
        return campaign_main(sys.argv[2:])
    return _wiring_check()


if __name__ == "__main__":
    raise SystemExit(main())
