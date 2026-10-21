# Local football lab

Current milestone: wiring skeleton only. No football simulation, rankings,
real-agent integration or profile evolution exists yet.

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

Next gate: inventory all five reference agents' commands and approve complete
numerical local rules before implementing playable football. See
`docs/superpowers/plans/2026-10-21-local-football-roadmap.md`.
