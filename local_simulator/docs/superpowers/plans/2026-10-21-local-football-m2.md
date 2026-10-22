# Local Football M2: Playable Engine Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement
> this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement a complete deterministic engine that runs a 120-second simulated match
between two isolated agent processes, saves authoritative v2 replay evidence, and renders it
in an extended browser viewer with time-seek, pause/play, decision stepping and a player
side-panel. No rankings, no entrant integration, no tuning.

**Architecture:** Engine contracts first (typed, independent of physics). TDD: failing tests
before each physics/action module. All agent calls go through the existing `ProcessAgent`
boundary unchanged; the engine never reads agent internals. Replay schema v2 is versioned
separately from the M0 wiring-check schema.

**Basis:** Approved `rules-v1.md` plus additional physics constants proposed below in Task 1.

---

## Authority and scope

- Approved specification sections 4, 6, 7, 8, 11 and engine acceptance in section 12.
- `rules-v1.md` approved 2026-10-21 (goal 4.5m, box 16.5m, 120s, DRIBBLE dropped,
  SET_STANCE any non-negative int, MARK TIGHT/LOOSE only, damping 0.82/decision-cycle).
- Execution authorized by "Continue with M2" on 2026-10-21.
- All five source reference directories remain unchanged throughout.
- All commands run from `local_simulator/` unless stated otherwise.
- The wiring-check schema (`kind: "wiring-check"`) and its stub agent are not modified.
- Match replays use `kind: "match-replay"` and `schema_version: 2`; the two schemas
  never overlap.

---

## Additional physics constants (proposed, not in rules-v1.md)

These fill gaps in approved `rules-v1.md`. They are proposals; any change redirects from here.

### Player movement

Speed formula (from `redesign_2/agent/tactics.py:arrival()`):

```
player_max_speed_per_decision_cycle = 4.0 + 2.0 * stamina   # units per 2s cycle
player_max_speed_per_step           = player_max_speed_per_decision_cycle / 40.0
                                    = 0.10 + 0.05 * stamina  # units per physics step
```

At full stamina (1.0): 0.15 units/step = 3.0 units/s.
At zero stamina (0.0): 0.10 units/step = 2.0 units/s.
No acceleration model: players snap to max_speed each step (acceleration not in any agent).

### Stamina

| Event | Drain per physics step | Drain per decision cycle |
|---|---|---|
| PRESS_BALL / SLIDE_TACKLE (sprint) | 0.005 | 0.20 |
| MOVE_TO / INTERCEPT (normal move) | 0.002 | 0.08 |
| PASS / SHOOT (burst, first step only) | 0.002 | — |
| SET_STANCE / MARK (low effort) | 0.000 | 0.00 |
| Recovery (any step not draining) | +0.002 | +0.08 |

Clamped to [0.0, 1.0]. At full sprint from 1.0 → 0.0: 200 steps = 10s; recovery 500 steps = 25s.

### Ball travel

**Launched ball (pass/shot):** constant speed; ball ownership is `free` in flight. Ball
transfers to PICKUP_RADIUS (2.1m) of any player; possession given to the first eligible player
who enters that radius (tie-broken by team then player_id).

| Launch type | Speed (units/step) |
|---|---|
| PASS (GROUND / THROUGH) | 1.5 |
| PASS AERIAL | 1.2 (longer arc, slower ground speed) |
| SHOOT | 2.5 |
| GK_DISTRIBUTE THROW | 1.5 |
| GK_DISTRIBUTE KICK | 2.0 |

**Free ball rolling (no owner, not freshly launched):** velocity decays each physics step:

```
damping_per_step = 0.82 ** (1.0 / 40.0)   # ≈ 0.9951
ball_vx *= damping_per_step
ball_vy *= damping_per_step
```

This makes the approved 0.82/decision-cycle constant apply correctly to free rolling balls.
Launched passes/shots use constant speed (not exponential decay) so they reliably reach
their target; the decay only applies once the ball becomes unowned and rolling.

### Simultaneous-command window

Decisions are collected at ticks 0, 40, 80, … (every 2 simulated seconds, 60 windows total).
All agents for both teams are queried at the same tick and returned commands all apply to the
same decision window. Physics runs for 40 steps between decision windows; one-shot commands
(PASS, SHOOT, GK_DISTRIBUTE, SLIDE_TACKLE) execute on step 0 of the window; continuous
commands (MOVE_TO, PRESS_BALL, MARK, INTERCEPT) repeat each step. SET_STANCE does nothing
each step but earns stamina recovery. An unrecognised or rejected command becomes SET_STANCE.

---

## File map

Create only these new files (existing M0 files unchanged):

```text
local_simulator/src/football_lab/
  match_contracts.py          typed engine state, events, action results, replay v2
  physics.py                  per-step state advance (movement, ball, possession, boundary)
  actions.py                  command validation and intent resolution
  engine.py                   match loop: observe → collect → step 40 → repeat
  match_replay.py             schema v2 save / load
  cli_match.py                `python -m football_lab match` subcommand
  viewer/match.js             new viewer module (separate from wiring-check app.js)
  viewer/match.html           standalone match replay viewer
tests/
  test_physics.py             movement, ball travel, possession, boundary, stamina
  test_actions.py             command validation, rejection, one-shot vs continuous
  test_engine.py              full match determinism, simultaneous obs, finite termination
```

---

## Task 1: fill rules-v1.md gaps

- [ ] Append the additional physics constants above (player speed, stamina drain, ball speeds,
  damping interpretation) to `local_simulator/docs/rules-v1.md` in a new
  "Additional constants (M2)" section, keeping the status line "APPROVED 2026-10-21".
- [ ] Commit the updated rules file alone on the M2 branch before writing any code.

---

## Task 2: define match_contracts.py

Create `src/football_lab/match_contracts.py` with all new TypedDicts, typed before any
physics implementation. No imports from `physics.py` or `actions.py`.

```python
# engine_state
class PlayerState(TypedDict):
    team_id: int           # 0 or 1
    player_id: int         # 0-4
    x: float               # ± 55
    y: float               # ± 35
    vx: float
    vy: float
    stamina: float         # [0.0, 1.0]

class BallState(TypedDict):
    x: float
    y: float
    z: float               # height (0 for ground passes)
    vx: float
    vy: float
    vz: float
    owner_team: int | None  # 0, 1, or None (free)
    owner_player: int | None

class EngineState(TypedDict):
    tick: int              # 0 .. 2400
    play_mode: str         # OPEN_PLAY | KICK_OFF | THROW_IN | CORNER | GOAL_KICK
    mode_team: int | None  # which team takes the set piece
    players: list[PlayerState]
    ball: BallState
    score: tuple[int, int] # (team_0_goals, team_1_goals)

# events
class MatchEvent(TypedDict):
    tick: int
    kind: str   # GOAL | KICK_OFF | THROW_IN | CORNER | GOAL_KICK | PASS_LAUNCHED
                # | PASS_COMPLETE | PASS_INTERCEPTED | PASS_OUT | SHOT_LAUNCHED
                # | SHOT_GOAL | SHOT_SAVE | SHOT_OUT | TACKLE_SUCCESS | MATCH_END
    team_id: int | None
    player_id: int | None
    x: float | None
    y: float | None

# action results
class ActionResult(TypedDict):
    tick: int
    team_id: int
    player_id: int
    attempted: Command         # from contracts.py
    accepted: bool
    rejection_reason: str | None

# replay schema v2
class FrameRecord(TypedDict):
    tick: int
    players: list[PlayerState]
    ball: BallState
    score: tuple[int, int]

class MatchReplay(TypedDict):
    schema_version: Literal[2]
    kind: Literal["match-replay"]
    status: Literal["complete", "partial", "failed"]
    rules_version: Literal["v1"]
    seed: int
    duration_ticks: int        # 2400 for a full 120s match
    action_results: list[ActionResult]
    events: list[MatchEvent]
    frames: list[FrameRecord]  # one per tick (2401 entries for complete match)
```

- [ ] Add mypy stubs and `__all__`.
- [ ] `ruff check` and `ruff format` pass.
- [ ] `mypy` passes.

---

## Task 3: physics tests (failing first)

Create `tests/test_physics.py` with the following failing test cases before implementing
`physics.py`:

```python
# T3.1  player moves toward target within one decision cycle
def test_move_to_reaches_nearby_target():
    # A player at (0,0) given MOVE_TO (5,0) should reach within 1 unit after 40 steps.

# T3.2  player does not leave pitch bounds (x stays in [-55, 55], y in [-35, 35])
def test_player_clamped_to_pitch():

# T3.3  launched pass travels toward target and enters PICKUP_RADIUS
def test_pass_arrives_at_target():
    # Ball launched toward (20, 0) from (-10, 0). After enough steps, ball within 2.1 of target.

# T3.4  free-ball velocity decays each step
def test_free_ball_decays():
    # Free ball with vx=1.0 after 40 steps has vx ≈ 1.0 * 0.82.

# T3.5  possession transfers when player enters pickup radius
def test_possession_pickup():
    # Ball at (0,0) free, player at (2.0, 0). After one step the player owns the ball.

# T3.6  goal detected when ball crosses goal line inside goal half-width
def test_goal_detected():
    # Ball at (54.5, 0) moving vx=1.5. After 1 step ball x > 55, |y| <= 4.5. GOAL event expected.

# T3.7  boundary restart: ball out on touchline
def test_throw_in_restart():
    # Ball at (0, 34.5) moving vy=1.5. After 1 step |y| > 35. THROW_IN state expected.

# T3.8  stamina drains while pressing
def test_stamina_drain_pressing():
    # Player with stamina=1.0 presses for 40 steps. Stamina ≈ 1.0 - 0.005*40 = 0.8.

# T3.9  stamina recovers when on SET_STANCE
def test_stamina_recovery():
    # Player with stamina=0.5 on SET_STANCE for 40 steps. Stamina ≈ 0.5 + 0.002*40 = 0.58.

# T3.10 same seed produces byte-identical state after 40 steps
def test_deterministic_step():
```

- [ ] Run `python -m unittest tests/test_physics.py`; all 10 tests **must fail** (NotImplementedError or ImportError from stub).

---

## Task 4: implement physics.py

Implement `src/football_lab/physics.py`:

```python
STEP_DT = 0.05      # simulated seconds per physics step
DECISION_DT = 2.0   # simulated seconds per decision window
STEPS_PER_DECISION = 40

GOAL_HALF_W = 4.5
PITCH_HALF_L = 55.0
PITCH_HALF_W = 35.0
PICKUP_RADIUS = 2.1
TACKLE_RADIUS = 2.4
PENALTY_BOX_X = 38.5
PENALTY_BOX_HALF_W = 16.5

FREE_BALL_DAMPING_PER_STEP = 0.82 ** (1.0 / STEPS_PER_DECISION)   # ≈ 0.9951

def step_players(state: EngineState, intents: list[Intent]) -> EngineState:
    """Move all players one step according to their current intent."""

def step_ball(state: EngineState) -> tuple[EngineState, list[MatchEvent]]:
    """Advance ball one step; detect goal, boundary, possession pickup."""

def step_stamina(state: EngineState, intents: list[Intent]) -> EngineState:
    """Apply stamina drain/recovery based on intent type."""

def step(state: EngineState, intents: list[Intent]) -> tuple[EngineState, list[MatchEvent]]:
    """Full single physics step: players → ball → stamina → events."""
```

The `Intent` is a plain dataclass: `team_id, player_id, command, is_one_shot, executed`.
One-shot intents set `executed=True` after their first step and become idle.

- [ ] All 10 tests in `test_physics.py` pass.
- [ ] `ruff`, `mypy` pass.

---

## Task 5: action validation tests (failing first)

Create `tests/test_actions.py`:

```python
# T5.1  MOVE_TO with valid coordinates accepted
def test_move_to_accepted():

# T5.2  MOVE_TO with target outside pitch rejected
def test_move_to_out_of_bounds_rejected():

# T5.3  PASS to own player_id rejected
def test_pass_to_self_rejected():

# T5.4  PASS with unknown type rejected
def test_pass_unknown_type_rejected():

# T5.5  SHOOT with unknown aim_location rejected
def test_shoot_bad_aim_rejected():

# T5.6  MARK with tightness=NORMAL rejected
def test_mark_normal_tightness_rejected():

# T5.7  SET_STANCE with negative stance rejected
def test_set_stance_negative_rejected():

# T5.8  unknown commandType → rejected, ActionResult has rejection_reason
def test_unknown_command_rejected():

# T5.9  rejected command produces ActionResult with accepted=False; intent is SET_STANCE
def test_rejection_produces_set_stance_intent():

# T5.10  PASS is one-shot (executed once only); MOVE_TO is continuous
def test_one_shot_vs_continuous():
```

- [ ] All 10 tests fail before implementation.

---

## Task 6: implement actions.py

```python
# One-shot commands: PASS, SHOOT, GK_DISTRIBUTE, SLIDE_TACKLE
ONE_SHOT_COMMANDS = frozenset({"PASS", "SHOOT", "GK_DISTRIBUTE", "SLIDE_TACKLE"})

def validate_command(cmd: Command, state: EngineState) -> str | None:
    """Return rejection reason string, or None if accepted."""

def resolve_command(cmd: Command, team_id: int, player_id: int,
                    state: EngineState, tick: int) -> tuple[Intent, ActionResult]:
    """Validate → produce Intent (with is_one_shot flag) + ActionResult."""
```

Rejection rules (on top of M0 wire.py):
- `target_x` / `target_y` outside `[-PITCH_HALF_L, PITCH_HALF_L]` / `[-PITCH_HALF_W, PITCH_HALF_W]` → "target_out_of_bounds"
- PASS `target_player_id == player_id` → "pass_to_self"
- PASS `type` not in `{GROUND, AERIAL, THROUGH}` → "unknown_pass_type"
- SHOOT `aim_location` not in `{TL, TR, BL, BR, CENTER}` → "bad_aim_location"
- MARK `tightness` not in `{TIGHT, LOOSE}` → "bad_tightness"
- SET_STANCE `stance < 0` → "negative_stance"
- Unknown commandType → "unknown_command"
- Fallback intent for any rejection: SET_STANCE with `stance=0`.

- [ ] All 10 tests in `test_actions.py` pass.
- [ ] `ruff`, `mypy` pass.

---

## Task 7: engine tests (failing first)

Create `tests/test_engine.py`:

```python
# T7.1  a complete match terminates in exactly 2400 ticks
def test_match_terminates():

# T7.2  same seed + same agents → identical FrameRecord sequence (determinism)
def test_deterministic_replay():

# T7.3  both teams receive identical game state (simultaneous observation)
def test_simultaneous_observation():
    # Record the `tick` in observations received by team 0 vs team 1 — must be equal.

# T7.4  mirrored initial state + mirrored commands → mirrored frame sequence
def test_mirrored_symmetry():

# T7.5  score increments on goal event
def test_goal_increments_score():

# T7.6  KICK_OFF state follows a goal
def test_kick_off_after_goal():

# T7.7  THROW_IN state follows ball leaving touchline
def test_throw_in_after_ball_out():

# T7.8  match replay v2 artifact saved and loadable
def test_replay_saved_and_loadable():

# T7.9  agent process crash produces ActionResult with accepted=False for all its players
def test_agent_crash_handled():

# T7.10 existing artifacts are never overwritten (OSError on duplicate path)
def test_no_overwrite():
```

- [ ] All 10 tests fail before implementation.

---

## Task 8: implement engine.py

```python
class MatchEngine:
    def __init__(self, seed: int, team0_agent: Agent, team1_agent: Agent,
                 store: ReplayStore) -> None: ...

    def run(self, output: Path) -> MatchReplay:
        """
        Run full match:
          for decision_window in range(60):
            obs = build_observations(state)
            cmds_0 = team0_agent.decide(obs[0])
            cmds_1 = team1_agent.decide(obs[1])
            intents, results = resolve_all_commands(cmds_0, cmds_1, state, tick)
            for _ in range(STEPS_PER_DECISION):
                state, events = step(state, intents)
                record frame + events
          store.save(replay, output)
          return replay
        """
```

- [ ] `build_observations(state)` produces observations consistent with M0 `Observation`
  TypedDict shape — same schema_version=1, same field names — so real agents from M3
  can be wired in without changes to the observation format.
- [ ] Restart logic: KICK_OFF, THROW_IN, CORNER, GOAL_KICK all handled inside the step loop.
  Ball placed at correct restart location; mode_team and play_mode set correctly.
- [ ] `cli_match.py` adds `python -m football_lab match --seed N --output FILE`.
- [ ] All 10 tests in `test_engine.py` pass.
- [ ] Full test suite (all test files) passes.
- [ ] `ruff`, `mypy` pass.

---

## Task 9: replay schema v2 and viewer

- [ ] Create `src/football_lab/match_replay.py`:
  - `JsonMatchReplayStore` — same atomic write pattern as M0 `JsonReplayStore`
    (tempfile + fsync + `os.link` no-overwrite).
  - `load_match_replay(path: Path) -> MatchReplay` — validates `schema_version==2`,
    `kind=="match-replay"`.
- [ ] Create `viewer/match.html` and `viewer/match.js`:
  - Loads a local `.json` file via file picker.
  - Validates `schema_version==2`, `kind=="match-replay"`.
  - Controls: **Play/Pause**, **Time seek** (slider maps to tick 0–2400),
    **Speed** (×0.5, ×1, ×2, ×4), **Prev/Next decision** (jump by 40 ticks).
  - Main SVG pitch: players (blue=team_0, orange=team_1), ball (white circle).
  - Aside panel: selected player (click to select) shows attempted command,
    ActionResult accepted/rejected, any events involving that player at this tick.
  - Status bar: `seed N / tick T / 0:SS.s / score A–B / [mode]`.
  - Renders text from replay JSON as text nodes only — no `innerHTML` with replay data.
  - Bundles all assets locally; no CDN.
- [ ] Add `test_viewer.py` Playwright tests:
  - Load a completed match replay artifact.
  - Assert 10 player circles visible, score visible.
  - Seek to tick 40 (first decision point), check tick display changes.
  - Load file with `schema_version: 1` (wiring-check); assert error message shown.
- [ ] All Playwright tests pass.

---

## Task 10: throughput measurement

- [ ] Add `src/football_lab/bench.py` (callable via `python -m football_lab bench`):
  - Runs 10 complete stub-agent matches headlessly.
  - Reports: mean ticks/s, mean wall-clock per match (ms), mean replay file size (KB),
    peak RSS memory (MB).
  - Results printed to stdout in structured lines: `bench.ticks_per_sec=<N>`, etc.
  - Must not write replay files to disk for the benchmark (use `/dev/null` path or
    in-memory store).
- [ ] Run bench and record results in `local_simulator/docs/m2-bench.md`
  (one-time measurement, not a CI check).

---

## Exit gate

All of the following before closing M2:

- [ ] `python -m unittest discover -s tests -v` — all tests pass.
- [ ] `python -m ruff check . && python -m ruff format --check .` — clean.
- [ ] `python -m mypy` — no errors.
- [ ] `python -m football_lab match --seed 42 --output local/match.json` runs to
  completion and prints the path.
- [ ] Browser opens `viewer/match.html`, loads `local/match.json`, plays back the match,
  seek slider works, player side-panel shows attempts.
- [ ] `python -m football_lab match --seed 42 --output local/match.json` a second time
  exits with an error (no overwrite).
- [ ] `python -m football_lab match --seed 42 --output local/match2.json` produces
  bit-identical replay frames to the first run (determinism).
- [ ] Five reference source directories have no modifications (checked by `git diff HEAD`).
- [ ] Throughput recorded in `local_simulator/docs/m2-bench.md`.
