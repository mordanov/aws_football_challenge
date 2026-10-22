# Local football lab

Current milestone: **M5 — tactical-profile evolution**. Bounded Gaussian
parameter search over redesign_4's four tactical settings, evaluated against
four frozen opponents, with selection gating, final-test lock, and paired
bootstrap promotion decision.

Requires Python 3.11+. Run from `local_simulator/`.

```sh
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e '.[dev]'
.venv/bin/python -m playwright install chromium
.venv/bin/python -m football_lab --seed 7 --output local/wiring.json
```

The command invokes two trusted trivial subprocesses and records unchanged
positions plus one hardcoded next frame. It prints the saved artifact path only
after successful publication. Existing artifacts are never overwritten.
Seeds must be integers from 0 through 4294967295.

To view, run the following command and keep that terminal open:

```sh
.venv/bin/python -m http.server 8000 --bind 127.0.0.1 \
  --directory src/football_lab/viewer
```

Open http://127.0.0.1:8000/ and choose `local/wiring.json` in the file picker.
The artifact stays in the browser; no upload or cloud service is used.
Stop the server with Ctrl-C when finished.

```sh
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m ruff check .
.venv/bin/python -m ruff format --check .
.venv/bin/python -m mypy
```

The development browser is required for the full acceptance suite. A browser
installation failure is not a successful or skipped acceptance check.

Run a profile evolution experiment:

```sh
python -m football_lab evolve init \
  --dir local/exp1 \
  --stage1-manifest local/stage1/manifest.json \
  --experiment-seed 42

python -m football_lab evolve run --dir local/exp1
python -m football_lab evolve report --dir local/exp1
```

See `docs/m5-evolution.md` for full protocol details.

Run a rules v2 cross campaign (profile variants + scripted coach):

```sh
python -m football_lab campaign init --rules v2 --blocks 20 --seed-pool-start 5000 \
  --challengers r4v2_balanced_none,r4v2_balanced_rules --pool initial_idea_v1,redesign_4_v1 \
  --output local/r4v2/a/manifest.json
python -m football_lab campaign run --manifest local/r4v2/a/manifest.json \
  --replays local/r4v2/a/replays --discard-replays
python -m football_lab campaign report --manifest local/r4v2/a/manifest.json \
  --json local/r4v2/a/report.json --baseline r4v2_balanced_none
```

See `docs/rules-v2.md` for the foul model and its assumptions.
