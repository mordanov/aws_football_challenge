# Local football lab

Offline simulator for running football matches between `football_agent` instances.
Use it to test tactic changes, run self-play matches, and evolve tactical profiles.

Requires Python 3.11+. Run all commands from `local_simulator/`.

## Setup

```sh
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e '.[dev]'
```

## Run a single match

Two `football_agent` instances play each other (seed 7, team 0 vs team 1):

```sh
PYTHONPATH=src python3 -m football_lab --seed 7 --output local/match.json
```

Seeds are integers 0–4294967295. The command writes a replay JSON to `local/match.json`.

## View a replay

```sh
python3 -m http.server 8000 --bind 127.0.0.1 --directory src/football_lab/viewer
```

Open http://127.0.0.1:8000/ and load `local/match.json` in the file picker.

## Run a campaign (round-robin)

Multiple seeds, results aggregated into standings:

```sh
PYTHONPATH=src python3 -m football_lab campaign init \
  --entrants football_agent_v1 \
  --blocks 20 --seed-pool-start 1000 \
  --output local/campaign/manifest.json

PYTHONPATH=src python3 -m football_lab campaign run \
  --manifest local/campaign/manifest.json \
  --replays local/campaign/replays

PYTHONPATH=src python3 -m football_lab campaign report \
  --manifest local/campaign/manifest.json \
  --json local/campaign/report.json
```

## Run a tactical profile evolution experiment

Bounded search over the four tactical parameters (`pass_margin`, `shot_range`,
`press_radius`, `support_width`) playing `football_agent_v1` against itself:

```sh
# 1. You need a stage-1 manifest first (run a campaign with --blocks 2 and save it):
PYTHONPATH=src python3 -m football_lab campaign init \
  --entrants football_agent_v1 --blocks 2 --seed-pool-start 0 \
  --output local/stage1/manifest.json
PYTHONPATH=src python3 -m football_lab campaign run \
  --manifest local/stage1/manifest.json

# 2. Init and run the evolution experiment:
PYTHONPATH=src python3 -m football_lab evolve init \
  --dir local/exp1 \
  --stage1-manifest local/stage1/manifest.json \
  --experiment-seed 42

PYTHONPATH=src python3 -m football_lab evolve run --dir local/exp1
PYTHONPATH=src python3 -m football_lab evolve report --dir local/exp1
```

See `docs/m5-evolution.md` for the full protocol.

## Tests

```sh
PYTHONPATH=src pytest tests/ --ignore=tests/test_viewer.py
```

(`test_viewer.py` requires playwright — skip it unless you install it separately.)

Linters:

```sh
python3 -m ruff check src/
python3 -m mypy src/football_lab/evolution/ --ignore-missing-imports
```

## Entrants

| ID | Source | Notes |
|----|--------|-------|
| `football_agent_v1` | `football_agent/` | The main agent; used for all self-play |

The entrant shim is at `entrants/football_agent/shim.py`. It wraps `football_agent`'s
`Runtime` and serves the simulator's stdin/stdout protocol.
