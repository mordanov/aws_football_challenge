# Local Football Walking Skeleton Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prove one local command can invoke two isolated trivial agents, save their commands and a trivial frame, and display that saved artifact in a browser.

**Architecture:** Typed observation/reply/frame/replay contracts precede behavior. A subprocess adapter, trivial step, JSON recorder and CLI implement the wiring; a static browser page reads the artifact without invoking agents. No football physics, real-agent copies, rankings or evolution are implemented here.

**Tech Stack:** Python 3.11+, standard-library runtime, setuptools, unittest, Ruff, mypy, and Playwright/Chromium for the browser acceptance check.

---

## Authority, assumptions and scope

- Approved [specification](../specs/2026-10-21-local-football-design.md), section 3.
- Full [milestone roadmap](2026-10-21-local-football-roadmap.md).
- Execution is not authorized merely by saving this plan.
- Create an isolated execution workspace before implementation.
- All commands below run from `local_simulator/` unless stated otherwise.
- Preserve all five existing source directories and environments.
- Verified planning tools: `uv`, Python 3.12.3, Node 18.20.8 on the current Mac.
  Node is not required by the proposed runtime or viewer.
- Proposed package/runtime choices below are new, not already installed.
- Initial setup may download dependencies and Chromium. Actual runs use only
  local subprocesses/files and loopback HTTP.
- This artifact schema is explicitly `wiring-check`, not the playable engine's
  final replay format. An engine plan must version any later schema change.
- Skeleton seeds are integers in `[0, 2**32)`, exactly representable by both
  Python and the browser. They identify a wiring run, not football randomness.
- Subprocess capture here is only for controlled, tiny test agents. Before real
  entrants, M3 must replace it with bounded streaming transport; a post-capture
  length check is not a hard memory limit.

## File map

Create only these project files:

```text
local_simulator/
  .gitignore                         local environment, caches and run outputs
  pyproject.toml                     isolated package and development tooling
  README.md                          exact skeleton setup/run/view commands
  src/football_lab/
    __init__.py                      package marker
    contracts.py                     data shapes and public boundary protocols
    wire.py                          strict reply parsing
    stub_agent.py                    one-request trivial subprocess endpoint
    process_agent.py                 subprocess-to-agent protocol adapter
    skeleton.py                      hardcoded frames and wiring orchestration
    replay.py                        exclusively published JSON artifact
    __main__.py                      command parsing and visible failures
    viewer/index.html                local file picker, pitch and evidence panel
    viewer/app.js                    safe artifact validation and rendering
  tests/
    test_wire.py                     public parser rejection/acceptance cases
    test_process_agent.py            real subprocess success/failure cases
    test_skeleton.py                 end-to-end CLI/replay repeatability
    test_viewer.py                   real Chromium saved-artifact acceptance
```

The existing glossary and approved specification remain documentation. Bootstrap
state/data-flow context once these flows exist, without changing agent contexts.

## Task 1: define contracts and package setup

**Create:** `.gitignore`, `pyproject.toml`,
`src/football_lab/__init__.py`, `src/football_lab/contracts.py`.

- [ ] Create `.gitignore`:

```gitignore
.venv/
__pycache__/
*.egg-info/
.ruff_cache/
.mypy_cache/
local/
```

- [ ] Create `pyproject.toml`:

```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "local-football-lab"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = []

[project.optional-dependencies]
dev = ["ruff>=0.16,<0.17", "mypy>=2.3,<2.4", "playwright>=1.50,<2"]

[project.scripts]
football-lab = "football_lab.__main__:main"

[tool.setuptools.packages.find]
where = ["src"]

[tool.setuptools.package-data]
football_lab = ["viewer/*.html", "viewer/*.js"]

[tool.ruff]
target-version = "py311"
line-length = 100

[tool.ruff.lint]
select = ["E4", "E7", "E9", "F", "I"]

[tool.mypy]
python_version = "3.11"
files = ["src/football_lab"]
check_untyped_defs = true
no_implicit_optional = true
warn_unused_ignores = true
```

- [ ] Create an empty `src/football_lab/__init__.py`.
- [ ] Create `src/football_lab/contracts.py` with interfaces only:

```python
from collections.abc import Sequence
from pathlib import Path
from typing import Literal, Protocol, TypedDict


class Point(TypedDict):
    x: float
    y: float


class Player(TypedDict):
    team_id: int
    player_id: int
    position: Point


class Frame(TypedDict):
    tick: int
    players: list[Player]
    ball: Point


class Observation(TypedDict):
    schema_version: Literal[1]
    seed: int
    team_id: int
    frame: Frame


class Command(TypedDict):
    team_id: int
    player_id: int
    command_type: Literal["MOVE_TO"]
    target_x: float
    target_y: float


class Reply(TypedDict):
    schema_version: Literal[1]
    commands: list[Command]


class Replay(TypedDict):
    schema_version: Literal[1]
    kind: Literal["wiring-check"]
    status: Literal["complete"]
    seed: int
    observations: list[Observation]
    replies: list[Reply]
    frames: list[Frame]


class Agent(Protocol):
    def decide(self, observation: Observation) -> Reply: ...


class Stepper(Protocol):
    def advance(self, frame: Frame, replies: Sequence[Reply]) -> Frame: ...


class ReplayStore(Protocol):
    def save(self, replay: Replay, path: Path) -> None: ...
```

- [ ] Set up this project's environment, not another agent's:

```bash
uv venv --python 3.12 .venv
.venv/bin/python -m pip --version
```

If the second command reports missing pip, run
`uv pip install --python .venv/bin/python -e '.[dev]'`.
Otherwise run `.venv/bin/python -m pip install -e '.[dev]'`.
Do not add packages unrelated to the manifest.

- [ ] Verify contracts before implementation:

```bash
.venv/bin/python -m mypy src/football_lab/contracts.py
```

Expected: success, one source file checked. Do not invoke the console entry point
before Task 5 defines it.

- [ ] Commit only this task's files with message
  `feat: define local simulator skeleton contracts` and the required
  `Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>` trailer.

## Task 2: implement the agent boundary under failing public tests

**Create:** `tests/test_wire.py`, `tests/test_process_agent.py`,
`src/football_lab/wire.py`, `src/football_lab/stub_agent.py`,
`src/football_lab/process_agent.py`.

- [ ] Create `tests/test_wire.py`:

```python
import unittest

from football_lab.wire import parse_reply


def reply(team=0):
    return {
        "schema_version": 1,
        "commands": [
            {
                "team_id": team,
                "player_id": player,
                "command_type": "MOVE_TO",
                "target_x": 0.0,
                "target_y": 0.0,
            }
            for player in range(5)
        ],
    }


class WireTests(unittest.TestCase):
    def test_rejects_wrong_owner_and_duplicate_player(self):
        with self.assertRaises(ValueError):
            parse_reply(reply(1), 0)
        value = reply()
        value["commands"][1]["player_id"] = 0
        with self.assertRaises(ValueError):
            parse_reply(value, 0)

    def test_rejects_boolean_ids_nonfinite_and_unknown_commands(self):
        for field, bad in (
            ("player_id", True),
            ("target_x", float("nan")),
            ("target_x", 10 ** 400),
            ("target_y", "0"),
            ("command_type", "IDLE"),
        ):
            with self.subTest(field=field):
                value = reply()
                value["commands"][0][field] = bad
                with self.assertRaises(ValueError):
                    parse_reply(value, 0)
        value = reply()
        value["schema_version"] = True
        with self.assertRaises(ValueError):
            parse_reply(value, 0)

    def test_accepts_complete_five_player_reply(self):
        parsed = parse_reply(reply(), 0)
        self.assertEqual([c["player_id"] for c in parsed["commands"]], list(range(5)))
```

- [ ] Create `tests/test_process_agent.py`:

```python
import subprocess
import sys
import unittest

from football_lab.process_agent import ProcessAgent


class ProcessAgentTests(unittest.TestCase):
    def observation(self):
        return {
            "schema_version": 1,
            "seed": 7,
            "team_id": 1,
            "frame": {"tick": 0, "players": [], "ball": {"x": 0.0, "y": 0.0}},
        }

    def test_real_stub_process_routes_all_five_commands(self):
        agent = ProcessAgent((sys.executable, "-m", "football_lab.stub_agent"))
        reply = agent.decide(self.observation())
        self.assertEqual([c["team_id"] for c in reply["commands"]], [1] * 5)

    def test_rejects_malformed_reply(self):
        agent = ProcessAgent((sys.executable, "-c", "print('not-json')"))
        with self.assertRaises(ValueError):
            agent.decide(self.observation())

    def test_surfaces_nonzero_exit(self):
        agent = ProcessAgent((sys.executable, "-c", "raise SystemExit(3)"))
        with self.assertRaisesRegex(RuntimeError, "exited 3"):
            agent.decide(self.observation())

    def test_watchdog_stops_nonresponsive_stub(self):
        agent = ProcessAgent(
            (sys.executable, "-c", "import time; time.sleep(5)"), timeout=0.1
        )
        with self.assertRaises(subprocess.TimeoutExpired):
            agent.decide(self.observation())
```

- [ ] Run boundary tests before implementation:

```bash
.venv/bin/python -m unittest discover -s tests -p 'test_wire.py' -v
.venv/bin/python -m unittest discover -s tests -p 'test_process_agent.py' -v
```

Expected: import failures for the not-yet-created public boundary modules. Confirm
these are the cause, not a missing installed `football_lab` package.

- [ ] Create `src/football_lab/wire.py`:

```python
import math

from .contracts import Command, Reply


def record(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(k, str) for k in value):
        raise ValueError("expected an object with string keys")
    return {k: v for k, v in value.items() if isinstance(k, str)}


def integer(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError("expected a native integer")
    return value


def number(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("expected a native number")
    try:
        result = float(value)
    except OverflowError as error:
        raise ValueError("number exceeds finite range") from error
    if not math.isfinite(result):
        raise ValueError("expected a finite number")
    return result


def parse_reply(value: object, team: int) -> Reply:
    root = record(value)
    if set(root) != {"schema_version", "commands"}:
        raise ValueError("unexpected reply fields")
    if integer(root["schema_version"]) != 1:
        raise ValueError("unsupported reply version")
    raw = root["commands"]
    if not isinstance(raw, list) or len(raw) != 5:
        raise ValueError("expected five commands")
    commands: list[Command] = []
    seen: set[int] = set()
    for item in raw:
        command = record(item)
        if set(command) != {
            "team_id", "player_id", "command_type", "target_x", "target_y"
        }:
            raise ValueError("unexpected command fields")
        player = integer(command["player_id"])
        if integer(command["team_id"]) != team or player not in range(5):
            raise ValueError("wrong command owner")
        if player in seen:
            raise ValueError("duplicate player command")
        if command["command_type"] != "MOVE_TO":
            raise ValueError("unsupported skeleton command")
        seen.add(player)
        commands.append({
            "team_id": team,
            "player_id": player,
            "command_type": "MOVE_TO",
            "target_x": number(command["target_x"]),
            "target_y": number(command["target_y"]),
        })
    return {"schema_version": 1, "commands": commands}
```

- [ ] Create `src/football_lab/stub_agent.py`:

```python
import json
import sys

from .contracts import Command, Reply
from .wire import integer, record


def main() -> None:
    request = record(json.load(sys.stdin))
    if integer(request.get("schema_version")) != 1:
        raise ValueError("unsupported observation version")
    team = integer(request.get("team_id"))
    if team not in (0, 1):
        raise ValueError("unsupported team")
    commands: list[Command] = [
        {
            "team_id": team,
            "player_id": player,
            "command_type": "MOVE_TO",
            "target_x": 0.0,
            "target_y": 0.0,
        }
        for player in range(5)
    ]
    reply: Reply = {"schema_version": 1, "commands": commands}
    print(json.dumps(reply, allow_nan=False, sort_keys=True))


if __name__ == "__main__":
    main()
```

This stub deliberately ignores tactical observation content. It is not a
validator or substitute for a real controller.

- [ ] Create `src/football_lab/process_agent.py`:

```python
import json
import subprocess

from .contracts import Observation, Reply
from .wire import parse_reply


class ProcessAgent:
    def __init__(self, argv: tuple[str, ...], timeout: float = 5.0) -> None:
        self.argv = argv
        self.timeout = timeout

    def decide(self, observation: Observation) -> Reply:
        result = subprocess.run(
            self.argv,
            input=json.dumps(observation, allow_nan=False) + "\n",
            text=True,
            capture_output=True,
            timeout=self.timeout,
            check=False,
        )
        if result.returncode:
            raise RuntimeError(
                f"agent exited {result.returncode}: {result.stderr[:200]}"
            )
        if len(result.stdout.encode("utf-8")) > 65536:
            raise ValueError("skeleton reply exceeds 65536 bytes")
        value: object = json.loads(result.stdout)
        return parse_reply(value, observation["team_id"])
```

- [ ] Repeat both boundary-test commands. Expected: seven tests pass.
- [ ] Run `.venv/bin/python -m mypy`; fix actual type errors without `Any` casts.
- [ ] Format this task's files with Ruff, run Ruff check and commit with message
  `feat: wire isolated trivial agent processes` and the required co-author trailer.

## Task 3: write the failing CLI-to-artifact acceptance tests

**Create:** `tests/test_skeleton.py`.

- [ ] Create:

```python
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class SkeletonTests(unittest.TestCase):
    def invoke(self, output, seed="7"):
        return subprocess.run(
            [
                sys.executable, "-m", "football_lab",
                "--seed", seed, "--output", str(output),
            ],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )

    def test_real_cli_produces_repeatable_semantic_replay(self):
        with tempfile.TemporaryDirectory() as directory:
            first = Path(directory) / "first.json"
            second = Path(directory) / "second.json"
            result = self.invoke(first)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(self.invoke(second).returncode, 0)
            self.assertEqual(first.read_bytes(), second.read_bytes())
            replay = json.loads(first.read_text())
            self.assertEqual(replay["kind"], "wiring-check")
            self.assertEqual(replay["status"], "complete")
            self.assertEqual(replay["seed"], 7)
            self.assertEqual(len(replay["observations"]), 2)
            self.assertEqual(replay["observations"][0]["frame"],
                             replay["observations"][1]["frame"])
            self.assertEqual(len(replay["frames"][-1]["players"]), 10)
            self.assertEqual(replay["frames"][-1]["tick"], 1)
            self.assertEqual(
                [[c["team_id"] for c in r["commands"]] for r in replay["replies"]],
                [[0] * 5, [1] * 5],
            )

    def test_existing_artifact_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "replay.json"
            output.write_text("existing evidence")
            result = self.invoke(output)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("failed", result.stderr)
            self.assertEqual(output.read_text(), "existing evidence")
            self.assertEqual(list(Path(directory).iterdir()), [output])

    def test_invalid_seed_reports_failure_without_artifact(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "replay.json"
            for seed in ("-1", str(2 ** 32)):
                with self.subTest(seed=seed):
                    result = self.invoke(output, seed)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("nonnegative", result.stderr)
                    self.assertFalse(output.exists())
```

- [ ] Run:

```bash
.venv/bin/python -m unittest discover -s tests -p 'test_skeleton.py' -v
```

Expected: failures because `football_lab.__main__` does not exist, not because
of missing dependencies.

- [ ] Keep the failing tests in the execution workspace while Tasks 4-5 satisfy
  them. Do not claim this intermediate revision is a working release.

## Task 4: implement the trivial step and replay publication

**Create:** `src/football_lab/skeleton.py`, `src/football_lab/replay.py`.

- [ ] Create `src/football_lab/skeleton.py`:

```python
from collections.abc import Sequence
from pathlib import Path

from .contracts import Agent, Frame, Observation, Player, Replay, ReplayStore, Reply, Stepper


def initial_frame() -> Frame:
    players: list[Player] = []
    for team in (0, 1):
        for player in range(5):
            players.append({
                "team_id": team,
                "player_id": player,
                "position": {
                    "x": float((-30 + player * 5) * (1 if team == 0 else -1)),
                    "y": float(-20 + player * 10),
                },
            })
    return {"tick": 0, "players": players, "ball": {"x": 0.0, "y": 0.0}}


class TrivialStepper:
    def advance(self, frame: Frame, replies: Sequence[Reply]) -> Frame:
        return {"tick": 1, "players": frame["players"], "ball": frame["ball"]}


def run(
    seed: int,
    agents: tuple[Agent, Agent],
    stepper: Stepper,
    store: ReplayStore,
    output: Path,
) -> Replay:
    if not 0 <= seed < 2 ** 32:
        raise ValueError("seed must be a nonnegative 32-bit integer")
    frame = initial_frame()
    observations: list[Observation] = [
        {"schema_version": 1, "seed": seed, "team_id": team, "frame": frame}
        for team in (0, 1)
    ]
    replies = [agent.decide(obs) for agent, obs in zip(agents, observations)]
    next_frame = stepper.advance(frame, replies)
    replay: Replay = {
        "schema_version": 1,
        "kind": "wiring-check",
        "status": "complete",
        "seed": seed,
        "observations": observations,
        "replies": replies,
        "frames": [frame, next_frame],
    }
    store.save(replay, output)
    return replay
```

The hardcoded next frame is intentional. Do not add movement, scoring or
randomized tactics to make this milestone look more complete.

- [ ] Create `src/football_lab/replay.py`:

```python
import json
import os
import tempfile
from pathlib import Path

from .contracts import Replay


class JsonReplayStore:
    def save(self, replay: Replay, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, delete=False
        ) as handle:
            temporary = Path(handle.name)
            try:
                json.dump(replay, handle, allow_nan=False, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
                os.link(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)
```

Publication uses a same-filesystem hard link to a fully written temporary file.
Unlike replacing a path, this fails if the destination exists. The `finally`
removes only this exact temporary file. This is an explicitly macOS-targeted
skeleton mechanism; do not imply universal filesystem portability.

- [ ] Run `.venv/bin/python -m mypy`. Expected: type-compatible concrete ports.
- [ ] Rerun Task 3 tests. Expected: still red only for the absent CLI.
- [ ] Continue to Task 5 without declaring the skeleton complete.

## Task 5: add the CLI and pass the end-to-end boundary

**Create:** `src/football_lab/__main__.py`.

- [ ] Create:

```python
import argparse
import subprocess
import sys
from pathlib import Path

from .process_agent import ProcessAgent
from .replay import JsonReplayStore
from .skeleton import TrivialStepper, run


def main() -> int:
    parser = argparse.ArgumentParser(description="Local football wiring check; not a match")
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    agent = ProcessAgent((sys.executable, "-m", "football_lab.stub_agent"))
    try:
        run(args.seed, (agent, agent), TrivialStepper(), JsonReplayStore(), args.output)
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f"wiring check failed: {error}", file=sys.stderr)
        return 1
    print(args.output.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] Run:

```bash
.venv/bin/python -m unittest discover -s tests -p 'test_skeleton.py' -v
.venv/bin/python -m unittest discover -s tests -p 'test_wire.py' -v
.venv/bin/python -m unittest discover -s tests -p 'test_process_agent.py' -v
```

Expected: three CLI tests and seven boundary tests pass. No football result exists.

- [ ] Run Ruff format, Ruff check and mypy for the changed files.
- [ ] Commit Tasks 3-5 together with message
  `feat: record reproducible CLI wiring-check artifacts` and the required trailer.

## Task 6: define and test the viewer boundary before rendering

**Create:** `tests/test_viewer.py`.

The public viewer interface is a local static page with `#artifact` file input,
`#status`, `#metadata`, `#players` SVG group and `#attempts` text output.
It accepts complete version-1 `wiring-check` artifacts only, with no HTTP upload.

- [ ] Create:

```python
import functools
import http.server
import importlib.resources
import json
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

from playwright.sync_api import sync_playwright


class ViewerTests(unittest.TestCase):
    def test_saved_cli_artifact_renders_and_bad_version_is_visible(self):
        viewer = importlib.resources.files("football_lab").joinpath("viewer")
        handler = functools.partial(
            http.server.SimpleHTTPRequestHandler, directory=str(viewer)
        )
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                artifact = Path(directory) / "replay.json"
                subprocess.run(
                    [sys.executable, "-m", "football_lab", "--seed", "7",
                     "--output", str(artifact)],
                    check=True, capture_output=True, text=True, timeout=20,
                )
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch()
                    try:
                        page = browser.new_page()
                        page.goto(f"http://127.0.0.1:{server.server_port}/")
                        page.locator("#artifact").set_input_files(str(artifact))
                        page.wait_for_function(
                            "document.querySelector('#status').textContent"
                            " === 'Wiring check complete'"
                        )
                        self.assertEqual(page.locator("#players circle").count(), 10)
                        self.assertIn("seed 7", page.locator("#metadata").inner_text())
                        self.assertIn('"team_id": 1', page.locator("#attempts").inner_text())
                        invalid = Path(directory) / "invalid.json"
                        payload = json.loads(artifact.read_text())
                        payload["schema_version"] = 2
                        invalid.write_text(json.dumps(payload))
                        page.locator("#artifact").set_input_files(str(invalid))
                        page.wait_for_function(
                            "document.querySelector('#status').textContent"
                            ".startsWith('Replay error:')"
                        )
                        self.assertEqual(page.locator("#players circle").count(), 0)
                    finally:
                        browser.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
```

- [ ] Install the declared browser dependency's local runtime:

```bash
.venv/bin/python -m playwright install chromium
.venv/bin/python -m unittest discover -s tests -p 'test_viewer.py' -v
```

Expected after successful browser installation: failure because the page/required
viewer elements do not exist. A browser-install failure is setup failure, not
the intended red test.

## Task 7: implement the static viewer

**Create:** `src/football_lab/viewer/index.html`,
`src/football_lab/viewer/app.js`.

- [ ] Create `index.html`:

```html
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Football lab wiring check</title>
  <script src="app.js" defer></script>
  <style>
    body { font: 16px system-ui; max-width: 1100px; margin: 24px auto; padding: 16px; }
    main { display: flex; gap: 24px; flex-wrap: wrap; }
    svg { width: min(100%, 660px); background: #14532d; }
    pre { white-space: pre-wrap; max-height: 420px; overflow: auto; }
    aside { flex: 1; min-width: 260px; }
  </style>
</head>
<body>
  <h1>Football lab wiring check</h1>
  <p>This is a saved wiring demonstration, not a playable match or benchmark.</p>
  <label>Load local replay <input id="artifact" type="file" accept=".json"></label>
  <p id="status" role="status">Choose a replay file</p>
  <p id="metadata"></p>
  <main>
    <svg viewBox="0 0 660 420" aria-label="Saved wiring-check frame" role="img">
      <rect x="1" y="1" width="658" height="418" fill="none" stroke="white"></rect>
      <path d="M330 0V420" stroke="white"></path>
      <circle cx="330" cy="210" r="54" fill="none" stroke="white"></circle>
      <g id="players"></g>
    </svg>
    <aside><h2>Recorded attempts</h2><pre id="attempts"></pre></aside>
  </main>
</body>
</html>
```

- [ ] Create `app.js`:

```javascript
"use strict";

const artifact = document.querySelector("#artifact");
const status = document.querySelector("#status");
const metadata = document.querySelector("#metadata");
const players = document.querySelector("#players");
const attempts = document.querySelector("#attempts");

artifact.addEventListener("change", async () => {
  players.replaceChildren();
  metadata.textContent = "";
  attempts.textContent = "";
  try {
    const selected = artifact.files[0];
    if (!selected) {
      status.textContent = "Choose a replay file";
      return;
    }
    const replay = JSON.parse(await selected.text());
    if (
      replay === null ||
      replay.schema_version !== 1 ||
      replay.kind !== "wiring-check" ||
      replay.status !== "complete" ||
      !Number.isSafeInteger(replay.seed) ||
      replay.seed < 0 ||
      replay.seed >= 2 ** 32 ||
      !Array.isArray(replay.frames) ||
      replay.frames.length !== 2 ||
      !Array.isArray(replay.replies) ||
      replay.replies.length !== 2
    ) {
      throw new Error("Unsupported or incomplete wiring artifact");
    }
    const frame = replay.frames[1];
    if (
      frame === null ||
      frame.tick !== 1 ||
      !Array.isArray(frame.players) ||
      frame.players.length !== 10
    ) {
      throw new Error("Invalid saved frame");
    }
    const seen = new Set();
    const circles = [];
    for (const player of frame.players) {
      if (
        player === null ||
        ![0, 1].includes(player.team_id) ||
        !Number.isInteger(player.player_id) ||
        player.player_id < 0 ||
        player.player_id > 4 ||
        !player.position ||
        !Number.isFinite(player.position.x) ||
        !Number.isFinite(player.position.y) ||
        Math.abs(player.position.x) > 55 ||
        Math.abs(player.position.y) > 35
      ) {
        throw new Error("Invalid saved player");
      }
      const identity = `${player.team_id}:${player.player_id}`;
      if (seen.has(identity)) throw new Error("Duplicate saved player");
      seen.add(identity);
      const circle = document.createElementNS("http://www.w3.org/2000/svg", "circle");
      circle.setAttribute("cx", String((player.position.x + 55) * 6));
      circle.setAttribute("cy", String((35 - player.position.y) * 6));
      circle.setAttribute("r", "9");
      circle.setAttribute("fill", player.team_id === 0 ? "#60a5fa" : "#fb923c");
      circles.push(circle);
    }
    players.replaceChildren(...circles);
    metadata.textContent = `seed ${replay.seed} / tick ${frame.tick} / wiring-check v1`;
    attempts.textContent = JSON.stringify(replay.replies, null, 2);
    status.textContent = "Wiring check complete";
  } catch (error) {
    players.replaceChildren();
    status.textContent = `Replay error: ${error.message}`;
  }
});
```

This is the approved layout's thin slice. Timeline controls, score, player
selection and action-outcome overlays belong to the playable replay milestone.

- [ ] Run:

```bash
.venv/bin/python -m unittest discover -s tests -p 'test_viewer.py' -v
```

Expected: real CLI creates the artifact; Chromium renders ten saved players and
both teams' commands; unsupported schema shows an error and clears players.

- [ ] Commit with message `feat: display saved wiring artifacts in local viewer`
  and the required co-author trailer.

## Task 8: document, verify and hand off to the rules gate

**Create:** `README.md`, `state_machines.md`, `data_flow.md`.
**Review:** existing `CONTEXT.md` for terminology drift, not architecture details.

- [ ] Create `README.md`:

````markdown
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
````

- [ ] Establish context documents using bootstrap-project-context once code exists.
  Use the following concrete state and data facts as the authoritative input:

```markdown
# Skeleton state transitions

Invocation: parsed -> observations built -> both replies collected -> trivial
frame created -> artifact published -> successful exit.
Any expected boundary failure -> visible stderr diagnostic -> nonzero exit.
Failure before publication must not create a complete artifact.

Viewer: no file -> selected file -> parsed/validated -> saved frame displayed.
An invalid file -> visible error with old player render cleared.

No goals, match lifecycle, learned profile or promotion exists yet.
```

```markdown
# Skeleton data flow

CLI seed/output -> shared hardcoded frame -> two observation envelopes ->
two isolated one-request stub subprocesses -> validated five-command replies ->
trivial step -> complete version-1 wiring artifact -> local browser file picker ->
saved-frame SVG plus recorded-attempt text.

The engine never receives browser input. The viewer never executes agents.
No reference implementation, AWS credential or remote endpoint participates.
```

- [ ] Run the full focused checks:

```bash
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m ruff format .
.venv/bin/python -m ruff check .
.venv/bin/python -m ruff format --check .
.venv/bin/python -m mypy
git diff --check
git diff f4a1a46 -- ../initial_idea ../redesign_1 ../redesign_2 ../redesign_3 ../redesign_4
```

Expected: 11 tests pass including real Chromium; lint/format/type checks pass;
reference diff is empty against the specification commit, including committed
changes. If the execution baseline already contains user-authored reference
changes after that commit, stop and reconcile the baseline without reverting them.

- [ ] Independently demonstrate the installed `football-lab` entry point using a
  new temporary output path, then remove only that exact demonstration artifact.
- [ ] Review the code diff against the skeleton scope. Remove accidental football
  logic, real-agent edits, cloud dependencies or benchmarking claims.
- [ ] Commit the final docs/check fixes with message
  `docs: document verified local football wiring skeleton` and the required trailer.
- [ ] Stop at M1's rules/inventory design gate. Do not infer authorization for
  playable physics, the five-agent tournament or evolution from this skeleton plan.

## Plan coverage and self-review

- Contract definitions precede implementation; public tests precede behavior.
- The CLI tests cover process routing, same-snapshot observations, deterministic
  artifact bytes, failed publication and invalid input without success artifacts.
- The browser test reads a real CLI artifact and checks visible schema failure.
- Every new runtime module has complete proposed content above; no reference
  agent is copied or changed.
- Prototype-only limitations are named: hardcoded frames, trusted short-lived
  stubs, provisional artifact schema and one displayed saved frame.
- Later spec requirements map to M1-M5 in the roadmap, not to pretend-complete
  skeleton tasks.
