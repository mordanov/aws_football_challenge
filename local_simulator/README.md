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

A campaign plays all C(n,2) pairs × 2 legs × n blocks and produces standings.
It requires **at least two distinct entrant IDs**. With only `football_agent_v1`
registered there are no pairs, so round-robin is not yet useful on its own.

Add a profile variant (a second entrant JSON pointing to a different profile file)
and then run:

```sh
PYTHONPATH=src python3 -m football_lab campaign init \
  --entrants football_agent_v1,football_agent_v1_tuned \
  --blocks 20 --seed-pool-start 1000 \
  --output local/campaign/manifest.json

PYTHONPATH=src python3 -m football_lab campaign run \
  --manifest local/campaign/manifest.json \
  --replays local/campaign/replays --discard-replays

PYTHONPATH=src python3 -m football_lab campaign report \
  --manifest local/campaign/manifest.json \
  --json local/campaign/report.json \
  --baseline football_agent_v1
```

To compare a specific profile against the default, use cross mode instead
(`--challengers` vs `--pool`); see `docs/m5-evolution.md`.

For systematic multi-seed self-play analysis use the **evolution** section below —
it runs the agent against itself across many seeds automatically.

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
