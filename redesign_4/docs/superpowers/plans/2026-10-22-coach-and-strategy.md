# Coach Policy, Strategy Archetypes and Off-tick Advisor — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Measure coach policies and in-rules "dirty" strategy archetypes in the local simulator (with a new rules v2 foul model), add an off-tick LangChain advisor, and ship the winning profile in `redesign_4`.

**Architecture:** `local_simulator` gains an opt-in rules v2 contest hook (fouls, slide tackles, penalties, send-offs), a per-team coach hook, cross-campaigns and pooled goal-difference statistics; v1 stays byte-identical (golden hash). `redesign_4` gains a `style` profile section and an optional rules-based coach policy; defaults are identity values (golden decisions). The LLM advisor lives in `agent/advisor/` behind an optional extra and only writes proposal files.

**Tech Stack:** Python 3.12 (local_simulator, pytest), Python 3.13 (redesign_4, unittest, ruff), LangChain (`langchain-core`, `langchain-aws`) optional extra.

**Spec:** `redesign_4/docs/superpowers/specs/2026-10-22-coach-and-strategy-design.md` (revision 3 after Task 0).

## Commands

| What | Command (from repo root) |
|---|---|
| redesign_4 tests | `cd redesign_4 && .venv/bin/python -m unittest discover -s tests` |
| redesign_4 lint | `cd redesign_4 && .venv/bin/python -m ruff check agent tests` |
| redesign_4 install | `cd redesign_4 && uv pip install --python .venv/bin/python -e '.[llm]'` |
| simulator tests | `cd local_simulator && ../.venv/bin/python -m pytest -q tests --ignore=tests/test_viewer.py` |
| campaign CLI | `cd local_simulator && ../.venv/bin/python -m football_lab campaign <init\|run\|report> ...` |

Do **not** touch: `local_simulator/tests/test_evo_profile_shim.py` (foreign uncommitted edit), `local_simulator/docs/superpowers/plans/2026-10-22-local-football-m5.md`, `.claude/`. Stage files explicitly (never `git add -A`).

Every commit message ends with:

```
Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>
```

## File structure

**local_simulator** (`src/football_lab/`)

| File | Change | Responsibility |
|---|---|---|
| `match_contracts.py` | modify | `RulesVersion`, player discipline keys, `ContestHook`, replay `rules_params` |
| `entrant_contracts.py` | modify | `EntrantSpec.profile/coach`, `FixtureResult.discipline`, manifest `rules_version/rules_params/challengers` |
| `rules_v2.py` | create | `RulesV2Config`, `contest()` — fouls, slides, standing challenges, penalties, send-offs |
| `physics.py` | modify | `is_inactive`, skip inactive players, optional `contest` hook |
| `engine.py` | modify | `rules_version`, `rules_config`, `coaches` |
| `coaches.py` | create | `scripted_coach`, `COACHES` |
| `obs_encoder.py`, `team_runner.py` | modify | pass coach text into `coachInstruction` |
| `campaign.py` | modify | `_pair_fixtures`, `cross_fixtures`, v2 manifests |
| `campaign_report.py` | modify | `pooled_goal_diff_ci`, `goal_diff_delta_ci`, `discipline_counts`, `discipline_by_entrant` |
| `cli_campaign.py` | modify | variants, `--rules`, `--foul-scale`, `--challengers/--pool`, `--discard-replays`, `--json`, `--baseline` |
| `entrants/redesign_4_v2/` | create | build script, shim, copied agent, profiles, `variants.json` |
| `docs/rules-v2.md` | create | rules v2 assumptions |

**redesign_4** (`agent/`)

| File | Change | Responsibility |
|---|---|---|
| `model.py` | modify | `Style`, `DEFAULT_STYLE`, `Profile.style/coach_policy` |
| `profile.py` | modify | `STYLE_BOUNDS`, `FORMATIONS`, `COACH_POLICIES`, `checked_style`, v1/v2 parsing |
| `protocol.py` | modify | `SLIDE_TACKLE` schema |
| `tactics.py` | modify | `role`, `attack_cap`, `capped`, `tackle_ok`, style-aware `decide` |
| `coach.py` | create | `choose_preset` |
| `runtime.py` | modify | apply coach policy and style |
| `archetypes.py` | create | `ARCHETYPES`, `STAND_INS` |
| `trainer.py`, `__main__.py` | modify | `train(base=)`, `--base-profile` |
| `advisor/` | create | LangChain advisor + CLI (optional extra) |
| `tests/golden.py`, `tests/fixtures/default_style_golden.json` | create | golden decisions |

---

## Task 0: Amend the spec to revision 3

**Files:** Modify `redesign_4/docs/superpowers/specs/2026-10-22-coach-and-strategy-design.md`

- [ ] **Step 1: Apply the amendments**

1. Header line: `Status: approved design, revision 3 (amended while planning).`
2. §5 table, `tempo` row: replace `max(0,0.5-tempo)·80` with `max(0,0.5-tempo)·120` (hold must be able to beat the best pass, ≈59).
3. §5 table, `tackle_aggression` row: append `; no slides at all when 0`.
4. §5 table, `block_depth` row: append `; a marker whose threat is beyond the cap moves to (cap, threat.y) instead of marking`.
5. Append to §5, after the table:

```markdown
Roles by formation (pid 0 is always GK):

| pid | 1-1-2 | 2-1-1 | 1-2-1 |
|---|---|---|---|
| 1 | DEF | DEF | DEF |
| 2 | MID | DEF (mirrored) | MID |
| 3 | FWD (link) | MID | MID (mirrored) |
| 4 | FWD | FWD | FWD |

`1-1-2` reproduces the pre-change anchors exactly. Archetype definitions live in
`agent/archetypes.py`.
```

6. §3, append bullets:

```markdown
- **Events**: `FOUL`, `PENALTY` (team_id = awarded team), `SEND_OFF`, `TACKLE_SUCCESS`
  (also for standing-challenge wins). Fixture results record per-team counts.
- **Replays**: campaigns may run with `--discard-replays` (replays are ~3 MB each).
```

7. §8, append: `Reports also give the pooled goal-difference delta CI against a baseline entrant (`--baseline`) and discipline counts.`

- [ ] **Step 2: Commit**

```bash
git add redesign_4/docs/superpowers/specs/2026-10-22-coach-and-strategy-design.md
git commit -m "docs(redesign_4): spec revision 3 for coach/strategy plan" -m "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

---

# Part A — local_simulator

## Task A1: Pin v1 with a golden replay hash (before any simulator change)

**Files:** Create `local_simulator/tests/test_rules_v1_golden.py`

- [ ] **Step 1: Write the test with a sentinel hash**

```python
"""v1 must stay byte-identical while rules v2 is added."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from football_lab.engine import MatchEngine

GOLDEN_V1_SHA256 = "FILL_ME"


class _ChaserAgent:
    """Carrier shoots, keeper holds the goal line, everyone else presses."""

    def __init__(self, team_id: int) -> None:
        self.team_id = team_id

    def decide(self, obs: dict[str, Any]) -> list[dict[str, Any]]:
        ball = obs["ball"]
        own_goal_x = -50.0 if self.team_id == 0 else 50.0
        out = []
        for pid in range(5):
            if ball["owner_team"] == self.team_id and ball["owner_player"] == pid:
                cmd = ("SHOOT", {"aim_location": "CENTER", "power": 1.0})
            elif pid == 0:
                cmd = ("MOVE_TO", {"target_x": own_goal_x, "target_y": 0.0})
            else:
                cmd = ("PRESS_BALL", {"intensity": 1.0})
            out.append(
                {"team_id": self.team_id, "player_id": pid,
                 "command_type": cmd[0], "parameters": cmd[1]}
            )
        return out


class _NullStore:
    def save(self, replay: Any, path: Path) -> None:
        return None


def replay_digest(**engine_kwargs: Any) -> str:
    engine = MatchEngine(7, _ChaserAgent(0), _ChaserAgent(1), _NullStore(), **engine_kwargs)
    replay = engine.run(Path("unused.json"))
    return hashlib.sha256(json.dumps(replay, sort_keys=True).encode()).hexdigest()


def test_v1_replay_is_byte_identical() -> None:
    assert replay_digest() == GOLDEN_V1_SHA256


if __name__ == "__main__":
    print(replay_digest())
```

- [ ] **Step 2: Record the digest on the unchanged simulator**

Run: `cd local_simulator && PYTHONPATH=src ../.venv/bin/python tests/test_rules_v1_golden.py`
Expected: one 64-hex-char line. Replace `"FILL_ME"` with it.

- [ ] **Step 3: Verify it passes**

Run: `cd local_simulator && ../.venv/bin/python -m pytest -q tests/test_rules_v1_golden.py`
Expected: `1 passed`. Run it twice more; the digest must be stable.

- [ ] **Step 4: Commit**

```bash
git add local_simulator/tests/test_rules_v1_golden.py
git commit -m "test(sim): pin v1 replay with golden sha256" -m "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

---

## Task A2: Contracts and the rules_v2 skeleton

**Files:**
- Modify `local_simulator/src/football_lab/match_contracts.py`
- Modify `local_simulator/src/football_lab/entrant_contracts.py`
- Create `local_simulator/src/football_lab/rules_v2.py`
- Create `local_simulator/tests/test_rules_v2.py`

- [ ] **Step 1: Failing config test**

`local_simulator/tests/test_rules_v2.py`:

```python
from __future__ import annotations

import pytest

from football_lab.rules_v2 import RulesV2Config


def test_config_defaults_and_validation() -> None:
    cfg = RulesV2Config()
    assert (cfg.foul_scale, cfg.p_foul, cfg.p_win_slide, cfg.stun_steps) == (1.0, 0.25, 0.55, 40)
    assert cfg.send_off_fouls == 3 and cfg.p_penalty_goal == 0.75
    with pytest.raises(ValueError):
        RulesV2Config(foul_scale=-0.1)
```

Run: `cd local_simulator && ../.venv/bin/python -m pytest -q tests/test_rules_v2.py`
Expected: FAIL (`ModuleNotFoundError: football_lab.rules_v2`).

- [ ] **Step 2: Extend `match_contracts.py`**

Add `Callable` and `NotRequired` to the imports (`from collections.abc import Callable`, `from typing import ..., NotRequired`), then:

```python
RulesVersion = Literal["v1", "v2"]
```

Append to `EnginePlayerState`:

```python
    fouls: NotRequired[int]  # rules v2 only
    sent_off: NotRequired[bool]  # rules v2 only
    stunned_until: NotRequired[int]  # rules v2 only; inactive while tick <= this
```

Extend the `MatchEvent.kind` comment with `| FOUL | PENALTY | SEND_OFF`. In `MatchReplay` replace `rules_version: Literal["v1"]` with:

```python
    rules_version: RulesVersion
    rules_params: NotRequired[dict[str, Any]]  # rules v2 only
```

Append at module end:

```python
ContestHook = Callable[
    [list[EnginePlayerState], EngineBallState, list[int], list[Intent], int],
    list[MatchEvent],
]
"""Called once per physics step after players move: (players, ball, score, intents, tick).

Mutates its arguments in place and returns the events it produced.
"""
```

- [ ] **Step 3: Extend `entrant_contracts.py`**

Import `Any`, `Literal`, `NotRequired` from `typing`. Append to `EntrantSpec`:

```python
    profile: NotRequired[str]  # absolute profile path for a variant (AFC_PROFILE)
    coach: NotRequired[str | None]  # key into football_lab.coaches.COACHES
```

Append to `FixtureResult`:

```python
    discipline: NotRequired[dict[str, list[int]]]  # kind -> [team0, team1]
```

In `CampaignManifest` set `rules_version: Literal["v1", "v2"]` and append:

```python
    rules_params: NotRequired[dict[str, Any]]
    challengers: NotRequired[list[str]]  # cross campaigns only
```

- [ ] **Step 4: Create `rules_v2.py` skeleton**

```python
"""Rules v2: local foul / tackle model. Every constant is a local assumption.

See local_simulator/docs/rules-v2.md.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from football_lab.match_contracts import EngineBallState, EnginePlayerState, Intent, MatchEvent


class RandomSource(Protocol):
    def random(self) -> float: ...


@dataclass(frozen=True)
class RulesV2Config:
    foul_scale: float = 1.0
    p_foul: float = 0.25
    behind_multiplier: float = 2.0
    behind_angle_deg: float = 60.0
    p_win_slide: float = 0.55
    p_win_press_step: float = 0.01
    stun_steps: int = 40
    send_off_fouls: int = 3
    box_depth: float = 16.5
    box_half_width: float = 20.0
    p_penalty_goal: float = 0.75

    def __post_init__(self) -> None:
        if self.foul_scale < 0:
            raise ValueError("foul_scale must be >= 0")


def contest(
    players: list[EnginePlayerState],
    ball: EngineBallState,
    score: list[int],
    intents: list[Intent],
    tick: int,
    *,
    rng: RandomSource,
    cfg: RulesV2Config,
) -> list[MatchEvent]:
    raise NotImplementedError
```

- [ ] **Step 5: Run tests**

Run: `cd local_simulator && ../.venv/bin/python -m pytest -q tests --ignore=tests/test_viewer.py`
Expected: all pass (including the golden test).

- [ ] **Step 6: Commit**

```bash
git add local_simulator/src/football_lab/match_contracts.py local_simulator/src/football_lab/entrant_contracts.py local_simulator/src/football_lab/rules_v2.py local_simulator/tests/test_rules_v2.py
git commit -m "feat(sim): rules v2 contracts and config skeleton" -m "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

---

## Task A3: Physics — inactive players and the contest hook

**Files:** Modify `local_simulator/src/football_lab/physics.py`; create `local_simulator/tests/test_physics_v2.py`

- [ ] **Step 1: Failing tests**

```python
from __future__ import annotations

from typing import Any

from football_lab.match_contracts import EngineState, Intent
from football_lab.physics import is_inactive, step


def _p(team: int, pid: int, x: float, y: float = 0.0, **extra: Any) -> dict[str, Any]:
    return {"team_id": team, "player_id": pid, "x": x, "y": y,
            "vx": 0.0, "vy": 0.0, "stamina": 1.0, **extra}


def _state(players: list[dict[str, Any]], ball: dict[str, Any] | None = None,
           tick: int = 10) -> EngineState:
    b = {"x": 0.0, "y": 0.0, "z": 0.0, "vx": 0.0, "vy": 0.0, "vz": 0.0,
         "owner_team": None, "owner_player": None, "in_flight": False}
    b.update(ball or {})
    return {"tick": tick, "play_mode": "OPEN_PLAY", "mode_team": None,
            "players": players, "ball": b, "score": [0, 0]}  # type: ignore[typeddict-item]


def _move(team: int, pid: int, x: float, y: float) -> Intent:
    return {"team_id": team, "player_id": pid, "command_type": "MOVE_TO",
            "parameters": {"target_x": x, "target_y": y}, "is_one_shot": False,
            "executed": False}


def test_is_inactive() -> None:
    assert not is_inactive(_p(0, 1, 0.0), 5)  # type: ignore[arg-type]
    assert is_inactive(_p(0, 1, 0.0, stunned_until=5), 5)  # type: ignore[arg-type]
    assert not is_inactive(_p(0, 1, 0.0, stunned_until=5), 6)  # type: ignore[arg-type]
    assert is_inactive(_p(0, 1, 0.0, sent_off=True), 10**6)  # type: ignore[arg-type]


def test_stunned_player_does_not_move_until_stun_expires() -> None:
    s = _state([_p(0, 1, 0.0, stunned_until=12)], tick=10)
    s, _ = step(s, [_move(0, 1, 10.0, 0.0)])  # tick 11: inactive
    s, _ = step(s, [_move(0, 1, 10.0, 0.0)])  # tick 12: inactive
    assert s["players"][0]["x"] == 0.0
    s, _ = step(s, [_move(0, 1, 10.0, 0.0)])  # tick 13: moves
    assert s["players"][0]["x"] > 0.0


def test_sent_off_player_cannot_pick_up_ball() -> None:
    s = _state([_p(0, 1, 0.0, sent_off=True)], ball={"x": 0.5})
    s, _ = step(s, [])
    assert s["ball"]["owner_team"] is None


def test_contest_hook_sees_moved_players_and_ball_follows_new_owner() -> None:
    seen: dict[str, Any] = {}

    def hook(players, ball, score, intents, tick):  # type: ignore[no-untyped-def]
        seen["x"], seen["tick"] = players[0]["x"], tick
        ball["owner_team"], ball["owner_player"] = 1, 2
        return [{"tick": tick, "kind": "TACKLE_SUCCESS", "team_id": 1, "player_id": 2,
                 "x": None, "y": None}]

    players = [_p(0, 1, 0.0), _p(1, 2, 20.0, 5.0)]
    s = _state(players, ball={"x": 0.0, "owner_team": 0, "owner_player": 1}, tick=10)
    s, events = step(s, [_move(0, 1, 10.0, 0.0)], contest=hook)
    assert seen["tick"] == 11 and seen["x"] > 0.0
    assert [e["kind"] for e in events] == ["TACKLE_SUCCESS"]
    assert (s["ball"]["x"], s["ball"]["y"]) == (20.0, 5.0)
```

Run: `cd local_simulator && ../.venv/bin/python -m pytest -q tests/test_physics_v2.py`
Expected: FAIL (`ImportError: cannot import name 'is_inactive'`).

- [ ] **Step 2: Implement**

In `physics.py` add `ContestHook` to the `match_contracts` import, then add after `_intent_for`:

```python
def is_inactive(p: EnginePlayerState, tick: int) -> bool:
    """Rules v2: sent off, or stunned through `tick`. Always False under v1."""
    return bool(p.get("sent_off")) or p.get("stunned_until", -1) >= tick
```

Change the signature:

```python
def step(
    state: EngineState,
    intents: list[Intent],
    contest: ContestHook | None = None,
) -> tuple[EngineState, list[MatchEvent]]:
```

First line inside the `for p in players:` movement loop:

```python
        if is_inactive(p, tick):
            continue
```

Between section 1 and `# ── 2. Move ball`:

```python
    # ── 1b. Rules v2 contests (may change ball owner; ball then follows below) ─
    if contest is not None:
        events.extend(contest(players, ball, score, intents, tick))
```

In section 4 (pickup), first line inside `for p in players:`:

```python
            if is_inactive(p, tick):
                continue
```

- [ ] **Step 3: Run all simulator tests**

Run: `cd local_simulator && ../.venv/bin/python -m pytest -q tests --ignore=tests/test_viewer.py`
Expected: all pass, golden v1 unchanged.

- [ ] **Step 4: Commit**

```bash
git add local_simulator/src/football_lab/physics.py local_simulator/tests/test_physics_v2.py
git commit -m "feat(sim): inactive players and contest hook in physics step" -m "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

---

## Task A4: rules_v2 boundary tests (red)

**Files:** Modify `local_simulator/tests/test_rules_v2.py`

- [ ] **Step 1: Append the tests**

Setup: carrier = team 0 pid 4 (attacks +x). Tackler = team 1 pid 2, 1.5 m in front by default. `_Seq` is a scripted RNG that fails on an unexpected draw.

```python
from typing import Any

from football_lab.match_contracts import Intent
from football_lab.rules_v2 import contest

TICK = 100


class _Seq:
    def __init__(self, *values: float) -> None:
        self.values = list(values)

    def random(self) -> float:
        if not self.values:
            raise AssertionError("unexpected RNG draw")
        return self.values.pop(0)


def _pl(team: int, pid: int, x: float, y: float = 0.0, **extra: Any) -> dict[str, Any]:
    return {"team_id": team, "player_id": pid, "x": x, "y": y,
            "vx": 0.0, "vy": 0.0, "stamina": 1.0, **extra}


def _intent(team: int, pid: int, kind: str, **params: Any) -> Intent:
    return {"team_id": team, "player_id": pid, "command_type": kind,
            "parameters": params, "is_one_shot": kind == "SLIDE_TACKLE", "executed": False}


def _run(rng: _Seq, *, carrier_x: float = 10.0, tackler_x: float = 11.5,
         kind: str = "SLIDE_TACKLE", target: int = 4, cfg: RulesV2Config | None = None,
         **tackler_extra: Any):
    carrier = _pl(0, 4, carrier_x)
    tackler = _pl(1, 2, tackler_x, **tackler_extra)
    players = [carrier, tackler]
    ball = {"x": carrier_x, "y": 0.0, "z": 0.0, "vx": 0.0, "vy": 0.0, "vz": 0.0,
            "owner_team": 0, "owner_player": 4, "in_flight": False}
    score = [0, 0]
    params = {"target_player_id": target} if kind == "SLIDE_TACKLE" else {"intensity": 1.0}
    intent = _intent(1, 2, kind, **params)
    events = contest(players, ball, score, [intent], TICK,  # type: ignore[arg-type]
                     rng=rng, cfg=cfg or RulesV2Config())
    assert not rng.values, "RNG values left unused"
    return [e["kind"] for e in events], tackler, ball, score, intent


def test_slide_foul_outside_box_stuns_and_keeps_possession() -> None:
    kinds, tackler, ball, _, intent = _run(_Seq(0.1))
    assert kinds == ["FOUL"]
    assert tackler["fouls"] == 1 and tackler["stunned_until"] == TICK + 40
    assert (ball["owner_team"], ball["owner_player"]) == (0, 4)
    assert intent["executed"] is True


def test_slide_win_gives_tackler_the_ball() -> None:
    kinds, _, ball, _, _ = _run(_Seq(0.9, 0.3))
    assert kinds == ["TACKLE_SUCCESS"]
    assert (ball["owner_team"], ball["owner_player"]) == (1, 2)


def test_slide_miss_resolves_once() -> None:
    kinds, _, ball, _, intent = _run(_Seq(0.9, 0.9))
    assert kinds == [] and ball["owner_team"] == 0 and intent["executed"] is True


def test_out_of_range_draws_nothing() -> None:
    kinds, _, _, _, intent = _run(_Seq(), tackler_x=20.0)
    assert kinds == [] and intent["executed"] is False


def test_from_behind_doubles_foul_probability() -> None:
    assert _run(_Seq(0.4), tackler_x=8.5)[0] == ["FOUL"]       # behind: p = 0.5
    assert _run(_Seq(0.4, 0.9), tackler_x=11.5)[0] == []        # front: p = 0.25


def test_foul_scale_multiplies_probability() -> None:
    assert _run(_Seq(0.4), cfg=RulesV2Config(foul_scale=2.0))[0] == ["FOUL"]


def test_penalty_scored_and_missed_restart_at_centre() -> None:
    kinds, _, ball, score, _ = _run(_Seq(0.1, 0.5), carrier_x=45.0, tackler_x=46.5)
    assert kinds == ["FOUL", "PENALTY", "GOAL"] and score == [1, 0]
    assert (ball["x"], ball["y"], ball["owner_team"]) == (0.0, 0.0, None)
    kinds, _, ball, score, _ = _run(_Seq(0.1, 0.9), carrier_x=45.0, tackler_x=46.5)
    assert kinds == ["FOUL", "PENALTY"] and score == [0, 0]
    assert (ball["x"], ball["y"], ball["owner_team"]) == (0.0, 0.0, None)


def test_third_foul_sends_off_to_touchline() -> None:
    kinds, tackler, _, _, _ = _run(_Seq(0.1), fouls=2)
    assert kinds == ["FOUL", "SEND_OFF"]
    assert tackler["sent_off"] is True and tackler["y"] == 35.0
    assert "stunned_until" not in tackler


def test_press_challenge_rarely_wins_and_never_fouls() -> None:
    kinds, _, ball, _, _ = _run(_Seq(0.005), kind="PRESS_BALL")
    assert kinds == ["TACKLE_SUCCESS"] and ball["owner_team"] == 1
    assert _run(_Seq(0.5), kind="PRESS_BALL")[0] == []


def test_inactive_tackler_and_wrong_target_are_ignored() -> None:
    assert _run(_Seq(), stunned_until=TICK + 5)[0] == []
    kinds, _, _, _, intent = _run(_Seq(), target=3)
    assert kinds == [] and intent["executed"] is False
```

- [ ] **Step 2: Verify red**

Run: `cd local_simulator && ../.venv/bin/python -m pytest -q tests/test_rules_v2.py`
Expected: the config test passes; every new test FAILS with `NotImplementedError`.

(No commit — Task A5 makes them green.)

---

## Task A5: Implement `contest`

**Files:** Modify `local_simulator/src/football_lab/rules_v2.py`

- [ ] **Step 1: Replace the `contest` stub and add helpers**

Add imports: `import math` and `from football_lab.physics import PITCH_HALF_L, PITCH_HALF_W, TACKLE_RADIUS, is_inactive`.

```python
def _event(tick: int, kind: str, team: int | None, pid: int | None,
           x: float | None = None, y: float | None = None) -> MatchEvent:
    return {"tick": tick, "kind": kind, "team_id": team, "player_id": pid, "x": x, "y": y}


def _from_behind(tackler: EnginePlayerState, carrier: EnginePlayerState,
                 cfg: RulesV2Config) -> bool:
    dx, dy = tackler["x"] - carrier["x"], tackler["y"] - carrier["y"]
    norm = math.hypot(dx, dy)
    if norm == 0:
        return False
    backward = -1.0 if carrier["team_id"] == 0 else 1.0
    return dx * backward / norm > math.cos(math.radians(cfg.behind_angle_deg))


def _win(tackler: EnginePlayerState, ball: EngineBallState, tick: int) -> MatchEvent:
    ball["owner_team"], ball["owner_player"] = tackler["team_id"], tackler["player_id"]
    ball["in_flight"], ball["vx"], ball["vy"] = False, 0.0, 0.0
    return _event(tick, "TACKLE_SUCCESS", tackler["team_id"], tackler["player_id"],
                  tackler["x"], tackler["y"])


def _foul(tackler: EnginePlayerState, carrier: EnginePlayerState, ball: EngineBallState,
          score: list[int], tick: int, rng: RandomSource,
          cfg: RulesV2Config) -> list[MatchEvent]:
    events = [_event(tick, "FOUL", tackler["team_id"], tackler["player_id"],
                     tackler["x"], tackler["y"])]
    tackler["fouls"] = tackler.get("fouls", 0) + 1
    if tackler["fouls"] >= cfg.send_off_fouls:
        tackler["sent_off"] = True
        tackler["y"] = math.copysign(PITCH_HALF_W, tackler["y"] or 1.0)
        events.append(_event(tick, "SEND_OFF", tackler["team_id"], tackler["player_id"],
                             tackler["x"], tackler["y"]))
    else:
        tackler["stunned_until"] = tick + cfg.stun_steps
    attack = 1.0 if carrier["team_id"] == 0 else -1.0
    in_box = (attack * carrier["x"] >= PITCH_HALF_L - cfg.box_depth
              and abs(carrier["y"]) <= cfg.box_half_width)
    if in_box:
        team = carrier["team_id"]
        events.append(_event(tick, "PENALTY", team, carrier["player_id"],
                             carrier["x"], carrier["y"]))
        if rng.random() < cfg.p_penalty_goal:
            score[team] += 1
            events.append(_event(tick, "GOAL", team, None, attack * PITCH_HALF_L, 0.0))
        ball.update({"x": 0.0, "y": 0.0, "vx": 0.0, "vy": 0.0,
                     "owner_team": None, "owner_player": None, "in_flight": False})
    return events


def contest(
    players: list[EnginePlayerState],
    ball: EngineBallState,
    score: list[int],
    intents: list[Intent],
    tick: int,
    *,
    rng: RandomSource,
    cfg: RulesV2Config,
) -> list[MatchEvent]:
    """Resolve at most one ball contest against the carrier this step."""
    if ball["owner_team"] is None:
        return []
    carrier = next(
        (p for p in players
         if p["team_id"] == ball["owner_team"] and p["player_id"] == ball["owner_player"]),
        None,
    )
    if carrier is None:
        return []
    first: dict[tuple[int, int], Intent] = {}
    for intent in intents:
        first.setdefault((intent["team_id"], intent["player_id"]), intent)
    for p in players:
        if p["team_id"] == carrier["team_id"] or is_inactive(p, tick):
            continue
        if math.hypot(p["x"] - carrier["x"], p["y"] - carrier["y"]) > TACKLE_RADIUS:
            continue
        intent = first.get((p["team_id"], p["player_id"]))
        if intent is None:
            continue
        kind = intent["command_type"]
        if (kind == "SLIDE_TACKLE" and not intent["executed"]
                and int(intent["parameters"].get("target_player_id", -1))
                == carrier["player_id"]):
            intent["executed"] = True
            behind = cfg.behind_multiplier if _from_behind(p, carrier, cfg) else 1.0
            if rng.random() < min(1.0, cfg.p_foul * cfg.foul_scale * behind):
                return _foul(p, carrier, ball, score, tick, rng, cfg)
            if rng.random() < cfg.p_win_slide:
                return [_win(p, ball, tick)]
        elif kind == "PRESS_BALL" and rng.random() < cfg.p_win_press_step:
            return [_win(p, ball, tick)]
    return []
```

- [ ] **Step 2: Verify green**

Run: `cd local_simulator && ../.venv/bin/python -m pytest -q tests --ignore=tests/test_viewer.py`
Expected: all pass.

- [ ] **Step 3: Commit**

```bash
git add local_simulator/src/football_lab/rules_v2.py local_simulator/tests/test_rules_v2.py
git commit -m "feat(sim): rules v2 contest (slides, fouls, penalties, send-offs)" -m "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

---

## Task A6: Engine — rules version, rules config, coaches

**Files:**
- Modify `local_simulator/src/football_lab/match_contracts.py` (add `CoachFn`)
- Modify `local_simulator/src/football_lab/engine.py`
- Create `local_simulator/tests/test_engine_v2.py`
- Modify `local_simulator/tests/test_rules_v1_golden.py`

- [ ] **Step 1: Failing tests**

`tests/test_engine_v2.py`:

```python
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from football_lab.engine import MatchEngine
from football_lab.rules_v2 import RulesV2Config


class _NullStore:
    def save(self, replay: Any, path: Path) -> None:
        return None


class _Press:
    """Outfield presses the ball; the carrier keeps pressing (moves with the ball)."""

    hold = False

    def __init__(self, team_id: int) -> None:
        self.team_id = team_id
        self.observations: list[dict[str, Any]] = []

    def decide(self, obs: dict[str, Any]) -> list[dict[str, Any]]:
        self.observations.append(obs)
        ball = obs["ball"]
        out = []
        for pid in range(5):
            carrier = ball["owner_team"] == self.team_id and ball["owner_player"] == pid
            if carrier and self.hold:
                cmd = ("SET_STANCE", {"stance": 0})
            elif pid == 0:
                cmd = ("MOVE_TO", {"target_x": -50.0 if self.team_id == 0 else 50.0,
                                   "target_y": 0.0})
            else:
                cmd = ("PRESS_BALL", {"intensity": 1.0})
            out.append({"team_id": self.team_id, "player_id": pid,
                        "command_type": cmd[0], "parameters": cmd[1]})
        return out


class _PressHold(_Press):
    hold = True


def _run(**kw: Any) -> dict[str, Any]:
    engine = MatchEngine(11, _Press(0), _PressHold(1), _NullStore(), **kw)
    return engine.run(Path("unused.json"))  # type: ignore[return-value]


def _count(replay: dict[str, Any], kind: str) -> int:
    return sum(e["kind"] == kind for e in replay["events"])


def test_v1_has_no_tackles_v2_does() -> None:
    assert _count(_run(), "TACKLE_SUCCESS") == 0
    v2 = _run(rules_version="v2")
    assert v2["rules_version"] == "v2"
    assert _count(v2, "TACKLE_SUCCESS") > 0


def test_v2_records_params_and_is_deterministic() -> None:
    a = _run(rules_version="v2", rules_config=RulesV2Config(foul_scale=2.0))
    b = _run(rules_version="v2", rules_config=RulesV2Config(foul_scale=2.0))
    assert a["rules_params"]["foul_scale"] == 2.0
    assert a["events"] == b["events"] and a["frames"][-1] == b["frames"][-1]
    assert "rules_params" not in _run()


def test_rules_config_requires_v2() -> None:
    with pytest.raises(ValueError):
        MatchEngine(1, _Press(0), _Press(1), _NullStore(), rules_config=RulesV2Config())


def test_coach_reaches_only_the_coached_team() -> None:
    t0, t1 = _Press(0), _Press(1)
    MatchEngine(3, t0, t1, _NullStore(),
                coaches=(lambda state, team: "press_high", None)).run(Path("unused.json"))
    assert {o["coach"] for o in t0.observations} == {"press_high"}
    assert all("coach" not in o for o in t1.observations)
```

Append to `tests/test_rules_v1_golden.py`:

```python
def test_explicit_v1_matches_golden() -> None:
    assert replay_digest(rules_version="v1") == GOLDEN_V1_SHA256
```

Run: `cd local_simulator && ../.venv/bin/python -m pytest -q tests/test_engine_v2.py tests/test_rules_v1_golden.py`
Expected: FAIL (`TypeError: ... unexpected keyword argument 'rules_version'`).

- [ ] **Step 2: Add `CoachFn` to `match_contracts.py`** (after `ContestHook`)

```python
CoachFn = Callable[[EngineState, int], str]
"""Scripted coach: (state, team_id) -> preset text written to coachInstruction."""
```

- [ ] **Step 3: Implement in `engine.py`**

Imports:

```python
import random
from dataclasses import asdict
from functools import partial

from football_lab import rules_v2
from football_lab.match_contracts import CoachFn, ContestHook, RulesVersion
```

Replace `MatchEngine.__init__`:

```python
    def __init__(
        self,
        seed: int,
        team0_agent: EngineAgent,
        team1_agent: EngineAgent,
        store: EngineStore,
        *,
        rules_version: RulesVersion = "v1",
        rules_config: rules_v2.RulesV2Config | None = None,
        coaches: tuple[CoachFn | None, CoachFn | None] = (None, None),
    ) -> None:
        if rules_version not in ("v1", "v2"):
            raise ValueError(f"unknown rules_version: {rules_version!r}")
        if rules_config is not None and rules_version != "v2":
            raise ValueError("rules_config requires rules_version='v2'")
        self._seed = seed
        self._agents = (team0_agent, team1_agent)
        self._store = store
        self._rules_version = rules_version
        self._rules_config = (
            (rules_config or rules_v2.RulesV2Config()) if rules_version == "v2" else None
        )
        self._coaches = coaches
```

In `run`, before the window loop:

```python
        contest: ContestHook | None = None
        if self._rules_config is not None:
            contest = partial(
                rules_v2.contest, rng=random.Random(self._seed), cfg=self._rules_config
            )
```

Replace `obs = _build_observation(state, tid, self._seed)` with:

```python
                obs = _build_observation(state, tid, self._seed)
                coach = self._coaches[tid]
                if coach is not None:
                    obs["coach"] = coach(state, tid)
```

Replace `state, events = step(state, intents)` with `state, events = step(state, intents, contest)`.

Replace `"rules_version": "v1",` in the replay with `"rules_version": self._rules_version,` and after the replay literal add:

```python
        if self._rules_config is not None:
            replay["rules_params"] = asdict(self._rules_config)
```

- [ ] **Step 4: Verify**

Run: `cd local_simulator && ../.venv/bin/python -m pytest -q tests --ignore=tests/test_viewer.py`
Expected: all pass; golden digest unchanged.

- [ ] **Step 5: Commit**

```bash
git add local_simulator/src/football_lab/match_contracts.py local_simulator/src/football_lab/engine.py local_simulator/tests/test_engine_v2.py local_simulator/tests/test_rules_v1_golden.py
git commit -m "feat(sim): engine rules_version v2 and per-team coach hook" -m "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

---

## Task A7: Coach channel to entrants and the scripted coach

**Files:**
- Modify `local_simulator/src/football_lab/obs_encoder.py`, `team_runner.py`, `cli_campaign.py` (bridge only)
- Create `local_simulator/src/football_lab/coaches.py`, `local_simulator/tests/test_coaches.py`

- [ ] **Step 1: Failing tests**

```python
from __future__ import annotations

from typing import Any

import pytest

from football_lab.coaches import COACHES, scripted_coach
from football_lab.obs_encoder import encode_observation


def _state(tick: int, score: list[int]) -> Any:
    return {"tick": tick, "play_mode": "OPEN_PLAY", "mode_team": None, "players": [],
            "ball": {"x": 0.0, "y": 0.0, "z": 0.0, "vx": 0.0, "vy": 0.0, "vz": 0.0,
                     "owner_team": None, "owner_player": None, "in_flight": False},
            "score": score}


@pytest.mark.parametrize(
    ("tick", "score", "team", "expected"),
    [
        (0, [0, 1], 0, "press_high"),
        (1600, [0, 1], 0, "go_all_out_attack"),   # r = 0.333
        (1440, [1, 0], 0, "slow_the_tempo"),      # r = 0.40
        (1400, [1, 0], 0, ""),                    # r = 0.417
        (1920, [0, 0], 0, "shoot_on_sight"),      # r = 0.20
        (1900, [0, 0], 0, ""),
        (1600, [0, 1], 1, "slow_the_tempo"),      # team 1 is leading
    ],
)
def test_scripted_coach_table(tick: int, score: list[int], team: int, expected: str) -> None:
    assert scripted_coach(_state(tick, score), team) == expected


def test_registry_and_encoder() -> None:
    assert COACHES["scripted"] is scripted_coach
    state = _state(0, [0, 0])
    assert encode_observation(state, 0, [0], 1)["gameState"]["coachInstruction"] == ""
    obs = encode_observation(state, 0, [0], 1, coach="press_high")
    assert obs["gameState"]["coachInstruction"] == "press_high"
```

Run: `cd local_simulator && ../.venv/bin/python -m pytest -q tests/test_coaches.py`
Expected: FAIL (`ModuleNotFoundError: football_lab.coaches`).

- [ ] **Step 2: Implement**

`src/football_lab/coaches.py`:

```python
"""Scripted coaches: model a human coach shouting presets (spec §6)."""
from __future__ import annotations

from football_lab.engine import TOTAL_TICKS
from football_lab.match_contracts import CoachFn, EngineState


def scripted_coach(state: EngineState, team_id: int) -> str:
    diff = state["score"][team_id] - state["score"][1 - team_id]
    remaining = 1 - state["tick"] / TOTAL_TICKS
    if diff < 0 and remaining <= 0.35:
        return "go_all_out_attack"
    if diff < 0:
        return "press_high"
    if diff > 0 and remaining <= 0.40:
        return "slow_the_tempo"
    if diff == 0 and remaining <= 0.20:
        return "shoot_on_sight"
    return ""


COACHES: dict[str, CoachFn] = {"scripted": scripted_coach}
```

`obs_encoder.py`: signature becomes
`def encode_observation(state: EngineState, team_id: int, player_ids: list[int], seed: int, coach: str = "") -> dict[str, Any]:`
and `"coachInstruction": coach,`.

`team_runner.py`: `def decide(self, state: EngineState, coach: str = "") -> list[dict[str, Any]]:` and
`payload = encode_observation(state, self._team_id, list(range(5)), self._seed, coach)`.

`cli_campaign.py` bridge last line:
`return self._runner.decide(cast(EngineState, state), coach=str(obs.get("coach", "")))`.

- [ ] **Step 3: Verify**

Run: `cd local_simulator && ../.venv/bin/python -m pytest -q tests --ignore=tests/test_viewer.py`
Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add local_simulator/src/football_lab/coaches.py local_simulator/src/football_lab/obs_encoder.py local_simulator/src/football_lab/team_runner.py local_simulator/src/football_lab/cli_campaign.py local_simulator/tests/test_coaches.py
git commit -m "feat(sim): scripted coach and coachInstruction channel" -m "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

---

## Task A8: Cross campaigns and v2 manifests

**Files:** Modify `local_simulator/src/football_lab/campaign.py`; create `local_simulator/tests/test_campaign_cross.py`

- [ ] **Step 1: Failing tests**

```python
from __future__ import annotations

import json
from pathlib import Path

import pytest

from football_lab.campaign import cross_fixtures, load_manifest, round_robin_fixtures


def test_cross_fixtures_pair_every_challenger_with_every_pool_member() -> None:
    fx = cross_fixtures(["b", "a"], ["y", "x", "z"], seed_pool_start=100, n_blocks=2)
    assert len(fx) == 2 * 3 * 2 * 2
    assert fx[0] == {"fixture_id": "a__vs__x__b0000__l0", "team0_entrant": "a",
                     "team1_entrant": "x", "seed_block": 0, "leg": 0, "seed": 100}
    assert fx[1]["fixture_id"] == "x__vs__a__b0000__l1" and fx[1]["seed"] == 101
    assert len({f["fixture_id"] for f in fx}) == len(fx)


@pytest.mark.parametrize(
    ("challengers", "pool"),
    [(["a"], ["a", "b"]), (["a__x"], ["b"]), (["a"], ["b__y"]), (["a", "a"], ["b"])],
)
def test_cross_fixtures_reject_bad_ids(challengers: list[str], pool: list[str]) -> None:
    with pytest.raises(ValueError):
        cross_fixtures(challengers, pool, seed_pool_start=0, n_blocks=1)


def test_round_robin_order_unchanged() -> None:
    ids = [f["fixture_id"] for f in round_robin_fixtures(["b", "a"], 0, 1)]
    assert ids == ["a__vs__b__b0000__l0", "b__vs__a__b0000__l1"]


def test_load_manifest_accepts_v1_and_v2_only(tmp_path: Path) -> None:
    for version, ok in (("v1", True), ("v2", True), ("v3", False)):
        path = tmp_path / f"{version}.json"
        path.write_text(json.dumps({"campaign_id": "c", "rules_version": version}))
        if ok:
            assert load_manifest(path)["rules_version"] == version
        else:
            with pytest.raises(AssertionError):
                load_manifest(path)
```

Run: `cd local_simulator && ../.venv/bin/python -m pytest -q tests/test_campaign_cross.py`
Expected: FAIL (`ImportError: cannot import name 'cross_fixtures'`).

- [ ] **Step 2: Implement in `campaign.py`**

Replace `round_robin_fixtures` with:

```python
def _pair_fixtures(
    first: str, second: str, seed_pool_start: int, n_blocks: int
) -> list[FixtureSpec]:
    """n_blocks × 2 legs; `first` is home in leg 0, leg 1 swaps home/away."""
    fixtures: list[FixtureSpec] = []
    for block in range(n_blocks):
        for leg in range(2):
            home, away = (first, second) if leg == 0 else (second, first)
            fixtures.append(
                {
                    "fixture_id": f"{home}__vs__{away}__b{block:04d}__l{leg}",
                    "team0_entrant": home,
                    "team1_entrant": away,
                    "seed_block": block,
                    "leg": leg,
                    "seed": fixture_seed(seed_pool_start, block, leg),
                }
            )
    return fixtures


def round_robin_fixtures(
    entrant_ids: list[str],
    seed_pool_start: int,
    n_blocks: int,
) -> list[FixtureSpec]:
    """All C(n,2) ordered pairs × n_blocks blocks × 2 legs, sorted deterministically."""
    fixtures: list[FixtureSpec] = []
    for t0, t1 in itertools.combinations(sorted(entrant_ids), 2):
        fixtures.extend(_pair_fixtures(t0, t1, seed_pool_start, n_blocks))
    return fixtures


def cross_fixtures(
    challengers: list[str],
    pool: list[str],
    seed_pool_start: int,
    n_blocks: int,
) -> list[FixtureSpec]:
    """Every challenger vs every pool member (challenger home in leg 0); no intra-group games."""
    if len(set(challengers)) != len(challengers) or len(set(pool)) != len(pool):
        raise ValueError("duplicate entrant id")
    overlap = set(challengers) & set(pool)
    if overlap:
        raise ValueError(f"entrants in both groups: {sorted(overlap)}")
    bad = [e for e in (*challengers, *pool) if "__" in e]
    if bad:
        raise ValueError(f"entrant ids must not contain '__': {bad}")
    fixtures: list[FixtureSpec] = []
    for c in sorted(challengers):
        for p in sorted(pool):
            fixtures.extend(_pair_fixtures(c, p, seed_pool_start, n_blocks))
    return fixtures
```

In `load_manifest` replace the assertion with:

```python
    assert data.get("rules_version") in ("v1", "v2"), (
        f"unsupported rules_version: {data.get('rules_version')}"
    )
```

- [ ] **Step 3: Verify and commit**

Run: `cd local_simulator && ../.venv/bin/python -m pytest -q tests --ignore=tests/test_viewer.py`
Expected: all pass.

```bash
git add local_simulator/src/football_lab/campaign.py local_simulator/tests/test_campaign_cross.py
git commit -m "feat(sim): cross-campaign fixtures and v2 manifests" -m "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

---

## Task A9: Report statistics — pooled goal difference, paired delta, discipline

**Files:** Modify `local_simulator/src/football_lab/campaign_report.py`; create `local_simulator/tests/test_campaign_stats.py`

A **unit** is one (opponent, seed block): both legs of a pairing on the same seeds. Bootstrap resamples units, as `bootstrap_ci` does.

- [ ] **Step 1: Failing tests**

Dataset: challenger `a` vs `b` (blocks 0 and 1) and `c` (block 0). a's goal differences are (b,0): +1, 0; (b,1): 0, 0; (c,0): +1, +1 → mean 3/6 = 0.5. Entrant `x` draws 0–0 on the same units.

```python
from __future__ import annotations

from typing import Any

import pytest

from football_lab.campaign_report import (
    discipline_by_entrant,
    discipline_counts,
    goal_diff_delta_ci,
    pooled_goal_diff_ci,
)


def _r(fixture_id: str, score: list[int], **extra: Any) -> Any:
    return {"fixture_id": fixture_id, "status": "complete", "score": score,
            "failure_reason": None, "replay_path": None, "duration_ms": 1.0,
            "team0_rejections": 0, "team1_rejections": 0, **extra}


A = [
    _r("a__vs__b__b0000__l0", [1, 0]), _r("b__vs__a__b0000__l1", [0, 0]),
    _r("a__vs__b__b0001__l0", [0, 0]), _r("b__vs__a__b0001__l1", [0, 0]),
    _r("a__vs__c__b0000__l0", [2, 1]), _r("c__vs__a__b0000__l1", [0, 1]),
]
X_BOTH_B = [
    _r("x__vs__b__b0000__l0", [0, 0]), _r("b__vs__x__b0000__l1", [0, 0]),
    _r("x__vs__b__b0001__l0", [0, 0]), _r("b__vs__x__b0001__l1", [0, 0]),
]
X_C = [_r("x__vs__c__b0000__l0", [0, 0]), _r("c__vs__x__b0000__l1", [0, 0])]


def test_pooled_goal_diff() -> None:
    p = pooled_goal_diff_ci(A, "a", rng_seed=1, n_resamples=2000)
    assert (p["entrant_id"], p["matches"], p["units"]) == ("a", 6, 3)
    assert p["mean"] == pytest.approx(0.5)
    assert 0.0 <= p["lo"] <= 0.5 <= p["hi"] <= 1.0
    with pytest.raises(ValueError):
        pooled_goal_diff_ci(A, "nobody", rng_seed=1)


def test_goal_diff_delta_is_paired_over_shared_units() -> None:
    mean, lo, hi = goal_diff_delta_ci(A + X_BOTH_B + X_C, "a", "x", rng_seed=1,
                                      n_resamples=2000)
    assert mean == pytest.approx(0.5) and lo <= mean <= hi   # units 0.5, 0, 1
    mean, _, _ = goal_diff_delta_ci(A + X_BOTH_B, "a", "x", rng_seed=1, n_resamples=2000)
    assert mean == pytest.approx(0.25)                       # (c,0) not shared
    with pytest.raises(ValueError):
        goal_diff_delta_ci(A, "a", "x", rng_seed=1)


def test_discipline_counts_and_by_entrant() -> None:
    ev = [{"kind": k, "team_id": t} for k, t in
          [("FOUL", 1), ("FOUL", 1), ("SEND_OFF", 1), ("TACKLE_SUCCESS", 0),
           ("PENALTY", 0), ("GOAL", 0)]]
    d = discipline_counts(ev)  # type: ignore[arg-type]
    assert d == {"fouls": [0, 2], "send_offs": [0, 1], "tackles_won": [1, 0],
                 "penalties_awarded": [1, 0]}
    by = discipline_by_entrant([
        _r("a__vs__b__b0000__l0", [0, 0], discipline=d),
        _r("b__vs__a__b0000__l1", [0, 0], discipline=d),
        _r("a__vs__c__b0000__l0", [0, 0]),  # no discipline recorded: ignored
    ])
    assert by["a"] == {"matches": 2, "fouls": 2, "send_offs": 1, "tackles_won": 1,
                       "penalties_awarded": 1}
    assert by["b"]["fouls"] == 2 and "c" not in by
```

Run: `cd local_simulator && ../.venv/bin/python -m pytest -q tests/test_campaign_stats.py`
Expected: FAIL (`ImportError`).

- [ ] **Step 2: Implement (append to `campaign_report.py`)**

Add `from typing import Any` to imports if missing.

```python
class PooledGoalDiff(TypedDict):
    entrant_id: str
    matches: int
    units: int
    mean: float
    lo: float
    hi: float


def _units(results: list[FixtureResult], entrant_id: str) -> dict[tuple[str, int], list[int]]:
    """Goal differences from `entrant_id`'s view, grouped by (opponent, block)."""
    units: dict[tuple[str, int], list[int]] = defaultdict(list)
    for r in results:
        if r["status"] != "complete":
            continue
        t0, t1, block, _ = _parse_fixture_id(r["fixture_id"])
        g0, g1 = r["score"]
        if t0 == entrant_id:
            units[(t1, block)].append(g0 - g1)
        elif t1 == entrant_id:
            units[(t0, block)].append(g1 - g0)
    return units


def _percentiles(stats: list[float], confidence: float) -> tuple[float, float]:
    stats.sort()
    n = len(stats)
    alpha = (1 - confidence) / 2
    return stats[int(alpha * n)], stats[int((1 - alpha) * n) - 1]


def pooled_goal_diff_ci(
    results: list[FixtureResult],
    entrant_id: str,
    rng_seed: int,
    n_resamples: int = 10_000,
    confidence: float = 0.95,
) -> PooledGoalDiff:
    """Mean goal difference per match vs all opponents; block bootstrap over units."""
    units = list(_units(results, entrant_id).values())
    if not units:
        raise ValueError(f"no complete results for {entrant_id!r}")
    matches = sum(len(u) for u in units)
    rng = random.Random(rng_seed)
    stats = []
    for _ in range(n_resamples):
        sample = [units[rng.randrange(len(units))] for _ in units]
        stats.append(sum(sum(u) for u in sample) / sum(len(u) for u in sample))
    lo, hi = _percentiles(stats, confidence)
    return {"entrant_id": entrant_id, "matches": matches, "units": len(units),
            "mean": sum(sum(u) for u in units) / matches, "lo": lo, "hi": hi}


def goal_diff_delta_ci(
    results: list[FixtureResult],
    a: str,
    b: str,
    rng_seed: int,
    n_resamples: int = 10_000,
    confidence: float = 0.95,
) -> tuple[float, float, float]:
    """(mean, lo, hi) of a's minus b's per-match goal difference, paired on shared units."""
    ua, ub = _units(results, a), _units(results, b)
    shared = sorted(set(ua) & set(ub))
    if not shared:
        raise ValueError(f"{a!r} and {b!r} share no (opponent, block) units")
    deltas = [sum(ua[k]) / len(ua[k]) - sum(ub[k]) / len(ub[k]) for k in shared]
    rng = random.Random(rng_seed)
    stats = [
        sum(deltas[rng.randrange(len(deltas))] for _ in deltas) / len(deltas)
        for _ in range(n_resamples)
    ]
    lo, hi = _percentiles(stats, confidence)
    return sum(deltas) / len(deltas), lo, hi


_DISCIPLINE = {
    "FOUL": "fouls",
    "SEND_OFF": "send_offs",
    "TACKLE_SUCCESS": "tackles_won",
    "PENALTY": "penalties_awarded",
}


def discipline_counts(events: list[Any]) -> dict[str, list[int]]:
    """Per-team [team0, team1] counts of discipline events in one replay."""
    out = {name: [0, 0] for name in _DISCIPLINE.values()}
    for e in events:
        name = _DISCIPLINE.get(e["kind"])
        if name is not None and e["team_id"] in (0, 1):
            out[name][e["team_id"]] += 1
    return out


def discipline_by_entrant(results: list[FixtureResult]) -> dict[str, dict[str, int]]:
    """Totals per entrant over results that recorded discipline."""
    totals: dict[str, dict[str, int]] = {}
    for r in results:
        d = r.get("discipline")
        if r["status"] != "complete" or d is None:
            continue
        t0, t1, _, _ = _parse_fixture_id(r["fixture_id"])
        for side, eid in enumerate((t0, t1)):
            row = totals.setdefault(eid, {"matches": 0, **{n: 0 for n in _DISCIPLINE.values()}})
            row["matches"] += 1
            for name in _DISCIPLINE.values():
                row[name] += d.get(name, [0, 0])[side]
    return totals
```

- [ ] **Step 3: Verify and commit**

Run: `cd local_simulator && ../.venv/bin/python -m pytest -q tests --ignore=tests/test_viewer.py`
Expected: all pass.

```bash
git add local_simulator/src/football_lab/campaign_report.py local_simulator/tests/test_campaign_stats.py
git commit -m "feat(sim): pooled goal-difference CI, paired delta and discipline stats" -m "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

---

## Task A10: Campaign CLI — variants, rules v2, cross mode, JSON report

**Files:** Modify `local_simulator/src/football_lab/cli_campaign.py`; create `local_simulator/tests/test_campaign_cli_v2.py`

`variants.json` (optional, beside an `entrant.json`) declares profile variants of the same code:

```json
{"base_id": "redesign_4_v2",
 "variants": [{"id": "r4v2_balanced_none", "profile": "profiles/balanced_none.json", "coach": null}]}
```

- [ ] **Step 1: Failing tests**

```python
from __future__ import annotations

import json
from pathlib import Path

import pytest

from football_lab import cli_campaign
from football_lab.cli_campaign import _coach_fn, _load_entrant_spec, _runner_env, main
from football_lab.coaches import scripted_coach


@pytest.fixture
def fake_entrants(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    d = tmp_path / "entrants" / "foo"
    (d / "profiles").mkdir(parents=True)
    (d / "profiles" / "p.json").write_text("{}")
    (d / "entrant.json").write_text(json.dumps({
        "id": "foo_v1", "source_name": "foo", "topology": "per_player",
        "shim": "entrants/foo/shim.py", "entrant_hash": "0" * 64,
        "rules_version": "v1", "track": "patched"}))
    (d / "variants.json").write_text(json.dumps({"base_id": "foo_v1", "variants": [
        {"id": "foo_a", "profile": "profiles/p.json", "coach": "scripted"},
        {"id": "foo_bad", "profile": "profiles/p.json", "coach": "nope"}]}))
    monkeypatch.setattr(cli_campaign, "_ENTRANTS_DIR", tmp_path / "entrants")
    return d


def test_variant_resolution_env_and_coach(fake_entrants: Path) -> None:
    base = _load_entrant_spec("foo_v1")
    assert "profile" not in base and _runner_env(base) is None and _coach_fn(base) is None
    v = _load_entrant_spec("foo_a")
    assert v["id"] == "foo_a" and v["source_name"] == "foo"
    assert v["profile"] == str((fake_entrants / "profiles" / "p.json").resolve())
    assert _runner_env(v) == {"AFC_PROFILE": v["profile"]}
    assert _coach_fn(v) is scripted_coach
    with pytest.raises(ValueError):
        _coach_fn(_load_entrant_spec("foo_bad"))
    with pytest.raises(ValueError):
        _load_entrant_spec("missing")


@pytest.mark.parametrize("argv", [
    [],                                                    # no mode
    ["--entrants", "foo_v1", "--challengers", "foo_a"],   # both modes
    ["--challengers", "foo_a"],                            # pool missing
    ["--entrants", "foo_v1,foo_a", "--foul-scale", "2"],  # foul scale needs v2
    ["--challengers", "foo_a", "--pool", "foo_a"],         # overlap
])
def test_init_rejects_bad_arguments(fake_entrants: Path, tmp_path: Path,
                                     argv: list[str]) -> None:
    assert main(["init", "--output", str(tmp_path / "m.json"), *argv]) == 2


def test_init_cross_v2(fake_entrants: Path, tmp_path: Path) -> None:
    out = tmp_path / "m.json"
    assert main(["init", "--challengers", "foo_a", "--pool", "foo_v1", "--rules", "v2",
                 "--foul-scale", "0.5", "--blocks", "2", "--output", str(out)]) == 0
    m = json.loads(out.read_text())
    assert m["rules_version"] == "v2" and m["rules_params"]["foul_scale"] == 0.5
    assert m["challengers"] == ["foo_a"] and len(m["fixtures"]) == 4


def test_end_to_end_v2_campaign(tmp_path: Path) -> None:
    manifest, report = tmp_path / "m.json", tmp_path / "r.json"
    assert main(["init", "--entrants", "redesign_4_v1,initial_idea_v1", "--rules", "v2",
                 "--blocks", "1", "--seed-pool-start", "5000",
                 "--output", str(manifest)]) == 0
    assert main(["run", "--manifest", str(manifest), "--replays", str(tmp_path / "rp"),
                 "--discard-replays"]) == 0
    assert not list((tmp_path / "rp").glob("*.json"))
    assert main(["report", "--manifest", str(manifest), "--json", str(report),
                 "--baseline", "initial_idea_v1"]) == 0
    r = json.loads(report.read_text())
    assert r["rules_version"] == "v2" and r["complete"] == 2 and r["final"] is True
    assert {p["entrant_id"] for p in r["pooled_goal_diff"]} == {"redesign_4_v1",
                                                                "initial_idea_v1"}
    assert set(r["discipline"]) == {"redesign_4_v1", "initial_idea_v1"}
    # A 2-team round robin has no shared (opponent, block) units, so no paired delta.
    assert r["goal_diff_delta_vs_baseline"] == {"baseline": "initial_idea_v1", "deltas": {}}
    results = json.loads(manifest.read_text())["results"]
    assert all(res["replay_path"] is None and "discipline" in res for res in results)
```

Run: `cd local_simulator && ../.venv/bin/python -m pytest -q tests/test_campaign_cli_v2.py`
Expected: FAIL (`ImportError: cannot import name '_coach_fn'`).

- [ ] **Step 2: Implement**

Imports to add:

```python
from dataclasses import asdict

from .campaign import cross_fixtures
from .campaign_report import (
    discipline_by_entrant,
    discipline_counts,
    goal_diff_delta_ci,
    pooled_goal_diff_ci,
)
from .coaches import COACHES
from .match_contracts import CoachFn, MatchReplay
from .rules_v2 import RulesV2Config
```

Replace `_load_entrant_spec` and add helpers:

```python
def _base_spec(d: Path, manifest: dict[str, Any]) -> EntrantSpec:
    return {
        "id": manifest["id"],
        "source_name": manifest["source_name"],
        "topology": manifest["topology"],
        "shim": str(d / "shim.py"),
        "entrant_hash": manifest["entrant_hash"],
        "rules_version": manifest["rules_version"],
        "track": manifest["track"],
    }


def _load_entrant_spec(entrant_id: str) -> EntrantSpec:
    """Resolve an entrant id, or a profile variant id from a sibling variants.json."""
    for d in sorted(_ENTRANTS_DIR.iterdir()):
        ej = d / "entrant.json"
        if not d.is_dir() or not ej.exists():
            continue
        manifest = json.loads(ej.read_text())
        if manifest["id"] == entrant_id:
            return _base_spec(d, manifest)
        vj = d / "variants.json"
        if not vj.exists():
            continue
        for variant in json.loads(vj.read_text())["variants"]:
            if variant["id"] == entrant_id:
                spec = _base_spec(d, manifest)
                spec["id"] = variant["id"]
                spec["profile"] = str((d / variant["profile"]).resolve())
                spec["coach"] = variant.get("coach")
                return spec
    raise ValueError(f"entrant not found: {entrant_id!r}")


def _runner_env(spec: EntrantSpec) -> dict[str, str] | None:
    profile = spec.get("profile")
    return {"AFC_PROFILE": profile} if profile else None


def _coach_fn(spec: EntrantSpec) -> CoachFn | None:
    name = spec.get("coach")
    if name is None:
        return None
    if name not in COACHES:
        raise ValueError(f"unknown coach {name!r} for entrant {spec['id']!r}")
    return COACHES[name]


class _DiscardStore:
    def save(self, replay: MatchReplay, path: Path) -> None:
        return None


def _ids(text: str) -> list[str]:
    return [e.strip() for e in text.split(",") if e.strip()]
```

Replace `_cmd_init`:

```python
def _cmd_init(args: argparse.Namespace) -> int:
    cross = bool(args.challengers or args.pool)
    if bool(args.entrants) == cross or (cross and not (args.challengers and args.pool)):
        print("init: use either --entrants, or --challengers with --pool", file=sys.stderr)
        return 2
    if args.foul_scale is not None and args.rules != "v2":
        print("init: --foul-scale requires --rules v2", file=sys.stderr)
        return 2
    challengers: list[str] | None = None
    try:
        if cross:
            challengers, pool = _ids(args.challengers), _ids(args.pool)
            fixtures = cross_fixtures(challengers, pool, args.seed_pool_start, args.blocks)
            entrant_ids = challengers + pool
        else:
            entrant_ids = _ids(args.entrants)
            fixtures = round_robin_fixtures(
                entrant_ids, seed_pool_start=args.seed_pool_start, n_blocks=args.blocks
            )
        specs = [_load_entrant_spec(eid) for eid in entrant_ids]
        for spec in specs:
            _coach_fn(spec)
        rules_config = (
            RulesV2Config(foul_scale=1.0 if args.foul_scale is None else args.foul_scale)
            if args.rules == "v2"
            else None
        )
    except ValueError as exc:
        print(f"init: {exc}", file=sys.stderr)
        return 2

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
        "rules_version": args.rules,
        "fixtures": fixtures,
        "results": [],
        "locked": False,
    }
    if rules_config is not None:
        manifest["rules_params"] = asdict(rules_config)
    if challengers is not None:
        manifest["challengers"] = challengers
    save_manifest(manifest, Path(args.output))
    print(f"campaign init: {len(fixtures)} fixtures ({args.rules}) → {args.output}")
    return 0
```

In `_cmd_run`:

1. Replace `store = JsonMatchReplayStore()` with:

```python
    store = _DiscardStore() if args.discard_replays else JsonMatchReplayStore()
    rules_version = manifest["rules_version"]
    rules_config = (
        RulesV2Config(**manifest["rules_params"]) if rules_version == "v2" else None
    )
```

2. Pass `env=_runner_env(spec0)` / `env=_runner_env(spec1)` to the two `TeamRunner(...)` calls.
3. Add to the `MatchEngine(...)` call:

```python
                    rules_version=rules_version,
                    rules_config=rules_config,
                    coaches=(_coach_fn(spec0), _coach_fn(spec1)),
```

4. In the complete-result dict use `"replay_path": None if args.discard_replays else str(replay_path),` and add `"discipline": discipline_counts(replay["events"]),`.

In `_cmd_report`, replace everything from `# Pairwise 95% CI` to the end of the function with:

```python
    seed = manifest["seed_pool_start"]
    challengers = manifest.get("challengers")
    focus = challengers or entrant_ids

    ci = bootstrap_ci(results, rng_seed=seed)
    if ci:
        print("\nPairwise 95% CI (per-match point differential)")
        for a, vs in sorted(ci.items()):
            for key, (lo, hi) in sorted(vs.items()):
                print(f"  {a} vs {key[3:]}: [{lo:+.2f}, {hi:+.2f}]")

    pooled = []
    for eid in focus:
        try:
            pooled.append(pooled_goal_diff_ci(results, eid, rng_seed=seed))
        except ValueError:
            continue
    pooled.sort(key=lambda p: (-p["lo"], p["entrant_id"]))
    print("\nPooled goal difference per match (95% block-bootstrap CI)")
    for p in pooled:
        print(f"  {p['entrant_id']:<28} {p['mean']:+.3f} [{p['lo']:+.3f}, {p['hi']:+.3f}]"
              f"  n={p['matches']}")

    deltas: dict[str, dict[str, float]] = {}
    if args.baseline:
        print(f"\nGoal-difference delta vs {args.baseline} (paired, 95% CI)")
        for eid in focus:
            if eid == args.baseline:
                continue
            try:
                mean, lo, hi = goal_diff_delta_ci(results, eid, args.baseline, rng_seed=seed)
            except ValueError:
                continue
            deltas[eid] = {"mean": mean, "lo": lo, "hi": hi}
            print(f"  {eid:<28} {mean:+.3f} [{lo:+.3f}, {hi:+.3f}]")

    discipline = discipline_by_entrant(results)
    if discipline:
        print("\nDiscipline (totals)")
        print(f"  {'Entrant':<28} {'M':>4} {'Fouls':>6} {'Off':>4} {'TklWon':>7} {'PenFor':>7}")
        for eid, d in sorted(discipline.items()):
            print(f"  {eid:<28} {d['matches']:>4} {d['fouls']:>6} {d['send_offs']:>4}"
                  f" {d['tackles_won']:>7} {d['penalties_awarded']:>7}")

    claim = None
    n_pairs = len(entrant_ids) * (len(entrant_ids) - 1) // 2
    if challengers:
        print("\nWinner claim: n/a (cross campaign — use pooled goal difference)")
    else:
        print(f"\nWinner claim (99.5% CI, Bonferroni over {n_pairs} pairs)")
        if not is_final:
            print(f"  PROVISIONAL ({n_failed} failures) — no winner claim made")
        else:
            claim = winner_claim(all_results, entrant_ids, bootstrap_seed=seed)
            if claim["reason"] == "clear_winner":
                print(f"  Winner: {claim['winner_id']}")
                print(f"  Reasoning: positive lower bound vs all {len(entrant_ids) - 1} rivals")
            else:
                print(f"  {claim['reason'].upper()}")

    if args.json_path:
        out = {
            "campaign_id": manifest["campaign_id"],
            "rules_version": manifest["rules_version"],
            "rules_params": manifest.get("rules_params"),
            "complete": n_complete,
            "total": total,
            "final": is_final,
            "challengers": challengers,
            "standings": compute_standings(results),
            "pooled_goal_diff": pooled,
            "pairwise_points_ci": {
                a: {k[3:]: list(v) for k, v in vs.items()} for a, vs in ci.items()
            },
            "discipline": discipline,
            "winner_claim": claim,
            "goal_diff_delta_vs_baseline": (
                {"baseline": args.baseline, "deltas": deltas} if args.baseline else None
            ),
        }
        path = Path(args.json_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(out, indent=2, sort_keys=True) + "\n")
        print(f"\nJSON report → {path}")
    return 0
```

Parser changes in `main`:

```python
    p_init.add_argument("--entrants", help="Comma-separated entrant IDs (round robin)")
    p_init.add_argument("--challengers", help="Comma-separated challenger IDs (cross mode)")
    p_init.add_argument("--pool", help="Comma-separated opponent-pool IDs (cross mode)")
    p_init.add_argument("--rules", choices=["v1", "v2"], default="v1")
    p_init.add_argument("--foul-scale", type=float, dest="foul_scale")
```

(remove `required=True` from `--entrants`)

```python
    p_run.add_argument("--discard-replays", action="store_true", dest="discard_replays")
    p_report.add_argument("--json", dest="json_path")
    p_report.add_argument("--baseline")
```

- [ ] **Step 3: Verify**

Run: `cd local_simulator && ../.venv/bin/python -m pytest -q tests --ignore=tests/test_viewer.py`
Expected: all pass (the end-to-end test runs 2 real matches).

- [ ] **Step 4: Commit**

```bash
git add local_simulator/src/football_lab/cli_campaign.py local_simulator/tests/test_campaign_cli_v2.py
git commit -m "feat(sim): campaign CLI variants, rules v2, cross mode, JSON report" -m "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

---

## Task A11: Simulator docs

**Files:** Create `local_simulator/docs/rules-v2.md`; modify `local_simulator/README.md`

- [ ] **Step 1: Write `docs/rules-v2.md`**

Sections (prose + tables, no code changes):
1. Status: local assumptions, not the AWS rules; v1 stays default and byte-identical (golden hash test).
2. Constants table — every `RulesV2Config` field, default, meaning.
3. Resolution order per physics step: players move → `contest` → ball follows owner → one-shots → pickup → boundaries. At most one contest per step.
4. Slide tackle, standing challenge, "from behind" (60° cone), foul outside the box (stun 40 steps), penalty (instant, centre restart), send-off (3rd foul, touchline, inactive rest of match).
5. Known simplifications: no free-kick restart, no cards UI, slide movement continues after resolution (v1 physics), the carrier is never fouled twice in one step.
6. Coach hook: `COACHES`, `scripted_coach` table (same as spec §6), delivered through `coachInstruction`.
7. CLI: `--rules v2`, `--foul-scale`, `--challengers/--pool`, `--discard-replays`, `report --json --baseline`, and `variants.json` format.

- [ ] **Step 2: README** — after the evolution section, add:

````markdown
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
````

- [ ] **Step 3: Commit**

```bash
git add local_simulator/docs/rules-v2.md local_simulator/README.md
git commit -m "docs(sim): rules v2 foul model and campaign usage" -m "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

---

# Part B — redesign_4

## Task B1: Golden decisions fixture (before any tactics change)

**Files:** Create `redesign_4/tests/golden.py`, `redesign_4/tests/test_golden.py`, `redesign_4/tests/fixtures/default_style_golden.json` (generated)

- [ ] **Step 1: Write `tests/golden.py`**

```python
"""Golden decisions: the default profile must reproduce pre-change commands exactly.

Regenerate ONLY on the pre-change agent: `.venv/bin/python tests/golden.py`.
"""
from __future__ import annotations

import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.model import Profile  # noqa: E402
from agent.runtime import Runtime  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "default_style_golden.json"
SEED = 20261022
PRESETS = ("", "go_all_out_attack", "slow_the_tempo", "press_high", "shoot_on_sight")


def _xy(rng: random.Random, sx: float, sy: float) -> dict:
    return {"x": round(rng.uniform(-sx, sx), 3), "y": round(rng.uniform(-sy, sy), 3)}


def payloads(n: int = 150) -> list[dict]:
    rng = random.Random(SEED)
    out = []
    for _ in range(n):
        team = rng.randrange(2)
        players = [
            {"playerId": pid, "teamId": side, "position": _xy(rng, 54, 34),
             "velocity": _xy(rng, 1, 1), "stamina": round(rng.uniform(0.1, 1.0), 3)}
            for side in (0, 1)
            for pid in range(5)
        ]
        u = rng.random()
        owner = (team, rng.randrange(5)) if u < 0.4 else (
            (1 - team, rng.randrange(1, 5)) if u < 0.8 else None
        )
        position = players[owner[0] * 5 + owner[1]]["position"] if owner else _xy(rng, 50, 30)
        tick = rng.randrange(2400)
        state = {
            "tick": tick,
            "gameTime": round(tick * 0.05, 2),
            "matchDuration": 120.0,
            "playMode": "OPEN_PLAY",
            "score": {"home": rng.randrange(3), "away": rng.randrange(3)},
            "players": players,
            "ball": {
                "position": dict(position),
                "velocity": {"x": 0, "y": 0} if owner else _xy(rng, 1.5, 1.5),
                "isFree": owner is None,
                "possessionPlayerId": owner[1] if owner else None,
                "possessionTeamId": owner[0] if owner else None,
            },
            "coachInstruction": rng.choice(PRESETS),
        }
        out.append({"gameState": state, "teamId": team, "myPlayers": [0]})
    return out


def responses(profile: Profile | None = None) -> list[list[str]]:
    runtimes = [Runtime(profile, emit=lambda _record: None) for _ in range(5)]
    try:
        rows = []
        for p in payloads():
            row = []
            for pid in range(5):
                rows_payload = {**p, "myPlayers": [pid]}
                row.append(runtimes[pid].handle(rows_payload))
            rows.append(row)
        return rows
    finally:
        for runtime in runtimes:
            runtime.close()


if __name__ == "__main__":
    FIXTURE.parent.mkdir(exist_ok=True)
    FIXTURE.write_text(json.dumps({"seed": SEED, "responses": responses()}, indent=1) + "\n")
    print(f"wrote {FIXTURE}")
```

- [ ] **Step 2: Write `tests/test_golden.py`**

```python
import json
import unittest

from golden import FIXTURE, responses


class GoldenDecisionsTest(unittest.TestCase):
    def test_default_profile_reproduces_golden_decisions(self):
        expected = json.loads(FIXTURE.read_text())["responses"]
        self.assertEqual(responses(), expected)

    def test_golden_covers_varied_commands(self):
        rows = json.loads(FIXTURE.read_text())["responses"]
        kinds = {json.loads(r)[0]["commandType"] for row in rows for r in row}
        self.assertGreaterEqual(len(kinds), 5, kinds)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Generate the fixture on the unchanged agent and verify**

Run: `cd redesign_4 && .venv/bin/python tests/golden.py && .venv/bin/python -m unittest discover -s tests`
Expected: `wrote .../default_style_golden.json`; all tests pass (57 + 2). If `test_golden_covers_varied_commands` fails, change the probabilities in `payloads` (not the agent) and regenerate.

- [ ] **Step 4: Commit**

```bash
git add redesign_4/tests/golden.py redesign_4/tests/test_golden.py redesign_4/tests/fixtures/default_style_golden.json
git commit -m "test(redesign_4): golden default-profile decisions" -m "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

---

## Task B2: `Style` and profile v2

**Files:** Modify `redesign_4/agent/model.py`, `redesign_4/agent/profile.py`; create `redesign_4/tests/test_profile_v2.py`; modify `redesign_4/tests/test_golden.py`

- [ ] **Step 1: Failing tests**

`tests/test_profile_v2.py`:

```python
import copy
import unittest
from dataclasses import asdict

from agent.model import DEFAULT_SETTINGS, DEFAULT_STYLE, Profile, Style
from agent.profile import parse_profile

V1 = {"version": "baseline", "settings": asdict(DEFAULT_SETTINGS), "opponents": None}


class ProfileV2Test(unittest.TestCase):
    def test_v1_profile_loads_with_identity_style(self):
        profile = parse_profile(copy.deepcopy(V1))
        self.assertEqual(profile.style, DEFAULT_STYLE)
        self.assertEqual(profile.coach_policy, "none")
        self.assertEqual(
            DEFAULT_STYLE, Style(1.0, 3, 0.0, 0.5, "1-1-2")
        )

    def test_v2_round_trip(self):
        profile = Profile(
            version="park",
            style=Style(block_depth=0.1, attackers_committed=1, tackle_aggression=0.4,
                        tempo=0.3, formation="2-1-1"),
            coach_policy="rules",
        )
        self.assertEqual(parse_profile(profile.to_dict()), profile)

    def test_rejects_invalid_style_and_policy(self):
        good = Profile().to_dict()
        bad_styles = [
            {"block_depth": 1.5}, {"tempo": -0.1}, {"tackle_aggression": 2},
            {"attackers_committed": 0}, {"attackers_committed": 2.0},
            {"formation": "3-1"}, {"extra": 1},
        ]
        for change in bad_styles:
            data = copy.deepcopy(good)
            data["style"].update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                parse_profile(data)
        missing = copy.deepcopy(good)
        del missing["style"]["tempo"]
        for data in (missing, {**good, "coach_policy": "llm"}, {**good, "surprise": 1}):
            with self.subTest(data=data), self.assertRaises(ValueError):
                parse_profile(data)


if __name__ == "__main__":
    unittest.main()
```

Append to `GoldenDecisionsTest` in `tests/test_golden.py`:

```python
    def test_v1_shaped_profile_reproduces_golden_decisions(self):
        from dataclasses import asdict

        from agent.model import DEFAULT_SETTINGS
        from agent.profile import parse_profile

        v1 = parse_profile(
            {"version": "baseline", "settings": asdict(DEFAULT_SETTINGS), "opponents": None}
        )
        expected = json.loads(FIXTURE.read_text())["responses"]
        self.assertEqual(responses(v1), expected)
```

Run: `cd redesign_4 && .venv/bin/python -m unittest discover -s tests`
Expected: FAIL (`ImportError: cannot import name 'DEFAULT_STYLE'`).

- [ ] **Step 2: `model.py`** — after `DEFAULT_SETTINGS = Settings()`:

```python
@dataclass(frozen=True)
class Style:
    """Strategy archetype knobs. Defaults are identity values (pre-change behaviour)."""

    block_depth: float = 1.0
    attackers_committed: int = 3
    tackle_aggression: float = 0.0
    tempo: float = 0.5
    formation: str = "1-1-2"


DEFAULT_STYLE = Style()
```

Append to `Profile` (after `opponents`):

```python
    style: Style = DEFAULT_STYLE
    coach_policy: str = "none"
```

- [ ] **Step 3: `profile.py`**

Import `DEFAULT_STYLE, Style` from `.model`. Add after `BOUNDS`:

```python
STYLE_BOUNDS = {
    "block_depth": (0.0, 1.0),
    "tackle_aggression": (0.0, 1.0),
    "tempo": (0.0, 1.0),
}
FORMATIONS = ("1-1-2", "2-1-1", "1-2-1")
COACH_POLICIES = ("none", "rules")
_REQUIRED = {"version", "settings", "opponents"}
_OPTIONAL = {"style", "coach_policy"}


def checked_style(data: dict) -> Style:
    if not isinstance(data, dict) or set(data) != {f.name for f in fields(Style)}:
        raise ValueError("style must supply all known fields and no unknown fields")
    values = {key: number(data[key], key) for key in STYLE_BOUNDS}
    for key, (lo, hi) in STYLE_BOUNDS.items():
        if not lo <= values[key] <= hi:
            raise ValueError(f"{key} outside safe bounds {lo}..{hi}")
    committed = data["attackers_committed"]
    if type(committed) is not int or not 1 <= committed <= 3:
        raise ValueError("attackers_committed must be an integer 1..3")
    if data["formation"] not in FORMATIONS:
        raise ValueError(f"formation must be one of {FORMATIONS}")
    return Style(attackers_committed=committed, formation=data["formation"], **values)
```

In `parse_profile` replace the first check with:

```python
    if not isinstance(data, dict) or not _REQUIRED <= set(data) <= _REQUIRED | _OPTIONAL:
        raise ValueError("invalid profile fields")
```

and replace `return Profile(version, settings, opponents)` with:

```python
    style = checked_style(data["style"]) if "style" in data else DEFAULT_STYLE
    coach_policy = data.get("coach_policy", "none")
    if coach_policy not in COACH_POLICIES:
        raise ValueError(f"coach_policy must be one of {COACH_POLICIES}")
    return Profile(version, settings, opponents, style, coach_policy)
```

- [ ] **Step 4: Verify, lint, commit**

Run: `cd redesign_4 && .venv/bin/python -m unittest discover -s tests && .venv/bin/python -m ruff check agent tests`
Expected: all pass (golden unchanged), ruff clean.

```bash
git add redesign_4/agent/model.py redesign_4/agent/profile.py redesign_4/tests/test_profile_v2.py redesign_4/tests/test_golden.py
git commit -m "feat(redesign_4): profile v2 with style and coach_policy" -m "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

---

## Task B3: `SLIDE_TACKLE` command schema

**Files:** Modify `redesign_4/agent/protocol.py`, `redesign_4/tests/test_runtime.py`

- [ ] **Step 1: Failing test** — append to `tests/test_runtime.py` (before the `if __name__` guard, if any):

```python
class SlideTackleSchemaTest(unittest.TestCase):
    def command(self, **params):
        return {"commandType": "SLIDE_TACKLE", "teamId": 0, "playerId": 2,
                "parameters": params, "duration": 0}

    def test_slide_tackle_requires_only_a_valid_target(self):
        validate_command(self.command(target_player_id=3))
        for params in ({}, {"target_player_id": 5}, {"target_player_id": 3, "force": 1}):
            with self.subTest(params=params), self.assertRaises(ValueError):
                validate_command(self.command(**params))
```

Run: `cd redesign_4 && .venv/bin/python -m unittest discover -s tests -k SlideTackle`
Expected: FAIL (`unsupported command or invalid parameters: SLIDE_TACKLE`).

- [ ] **Step 2: Implement** — in `validate_command` add to `schemas`:

```python
        "SLIDE_TACKLE": {"target_player_id"},
```

- [ ] **Step 3: Verify and commit**

Run: `cd redesign_4 && .venv/bin/python -m unittest discover -s tests`
Expected: all pass.

```bash
git add redesign_4/agent/protocol.py redesign_4/tests/test_runtime.py
git commit -m "feat(redesign_4): accept SLIDE_TACKLE commands" -m "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

---

## Task B4: Style-aware tactics (roles, block, commitment, tackles, tempo)

**Files:** Modify `redesign_4/agent/tactics.py`; create `redesign_4/tests/test_style_tactics.py`

The code and expected values below were prototyped against the current agent: the 10 new tests pass and the golden test stays green.

- [ ] **Step 1: Failing tests** — `tests/test_style_tactics.py`:

```python
import unittest
from dataclasses import replace

from agent.model import DEFAULT_SETTINGS, DEFAULT_STYLE, Player, Snapshot, Style
from agent.tactics import assignments, attack_cap, decide, role, support, tackle_ok

OURS = [(-51, 0), (-28, 0), (-5, 0), (20, -15), (23, 15)]
THEIRS = [(51, 0), (30, 0), (8, 20), (-10, -20), (-12, 20)]


def snap(team=0, ours=None, theirs=None, ball=(-5, 0), owner=(0, 2)):
    """Positions are given in team-0 view; team 1 mirrors x so tests stay direction-free."""
    m = 1 if team == 0 else -1
    sides = {team: ours or OURS, 1 - team: theirs or THEIRS}
    players = tuple(
        Player(side, pid, m * x, y, stamina=0.9)
        for side in (0, 1)
        for pid, (x, y) in enumerate(sides[side])
    )
    owner = None if owner is None else (team if owner[0] == 0 else 1 - team, owner[1])
    return Snapshot(team, 10, 10.0, 120.0, players, (m * ball[0], ball[1]), (0.0, 0.0),
                    owner, owner is None, (0, 0))


def target(decision):
    return decision.command["parameters"]["target_x"], decision.command["parameters"]["target_y"]


class RoleAnchorTest(unittest.TestCase):
    def test_roles_by_formation(self):
        self.assertEqual([role(p) for p in (1, 2, 3, 4)],
                         [("DEF", False), ("MID", False), ("LINK", False), ("FWD", False)])
        self.assertEqual(role(2, "2-1-1"), ("DEF", True))
        self.assertEqual(role(3, "1-2-1"), ("MID", True))

    def test_formation_anchors(self):
        s = snap(ball=(0, 0), owner=(0, 4), ours=[*OURS[:4], (0, 0)])
        plan = assignments(s)
        self.assertEqual(plan["cover"], 1)
        cases = [(2, "1-1-2", (-9, -9.75)), (2, "2-1-1", (-18, -8)), (3, "1-2-1", (-9, 9.75)),
                 (3, "2-1-1", (-9, -9.75)), (3, "1-1-2", (5, -15))]
        for pid, formation, expected in cases:
            with self.subTest(pid=pid, formation=formation):
                got = support(s, s.player(pid), DEFAULT_SETTINGS, plan, formation)
                self.assertAlmostEqual(got[0], expected[0])
                self.assertAlmostEqual(got[1], expected[1])


class BlockAndCommitTest(unittest.TestCase):
    def test_attack_cap_values(self):
        defending = snap(owner=(1, 1), ball=(30, 0))
        self.assertIsNone(attack_cap(defending, 3, DEFAULT_STYLE))
        self.assertAlmostEqual(
            attack_cap(defending, 3, replace(DEFAULT_STYLE, block_depth=0.1)), -40.4
        )
        self.assertAlmostEqual(
            attack_cap(defending, 3, replace(DEFAULT_STYLE, block_depth=0.5)), -2.0
        )
        attacking = snap()
        one = replace(DEFAULT_STYLE, attackers_committed=1)
        self.assertEqual([attack_cap(attacking, p, one) for p in (1, 2, 3, 4)],
                         [0.0, 0.0, 0.0, None])
        self.assertEqual([attack_cap(attacking, p) for p in (1, 2, 3, 4)],
                         [0.0, None, None, None])

    def test_deep_block_keeps_outfield_behind_cap(self):
        style = Style(block_depth=0.1, attackers_committed=1, tackle_aggression=0.0,
                      tempo=0.5, formation="2-1-1")
        for team in (0, 1):
            s = snap(team=team, owner=(1, 1), ball=(30, 0))
            moves = 0
            for pid in (1, 2, 3, 4):
                cmd = decide(s, pid, DEFAULT_SETTINGS, style).command
                with self.subTest(team=team, pid=pid, cmd=cmd):
                    self.assertIn(cmd["commandType"], ("MOVE_TO", "PRESS_BALL"))
                    if cmd["commandType"] == "MOVE_TO":
                        moves += 1
                        self.assertLessEqual(s.direction * cmd["parameters"]["target_x"], -40.4)
            self.assertGreaterEqual(moves, 2)

    def test_attackers_committed_holds_link_at_halfway(self):
        s = snap(ours=[OURS[0], OURS[1], (20, 0), OURS[3], OURS[4]], ball=(20, 0))
        self.assertEqual(target(decide(s, 3))[0], 25)
        one = replace(DEFAULT_STYLE, attackers_committed=1)
        self.assertEqual(target(decide(s, 3, DEFAULT_SETTINGS, one))[0], 0)
        self.assertEqual(target(decide(s, 4, DEFAULT_SETTINGS, one))[0], 37)


class TackleTest(unittest.TestCase):
    def tackler(self, x, aggression, team=0):
        s = snap(team=team, ours=[*OURS[:3], (x, 0), OURS[4]],
                 theirs=[THEIRS[0], (10, 0), *THEIRS[2:]], ball=(10, 0), owner=(1, 1))
        return s, tackle_ok(s, s.player(3), replace(DEFAULT_STYLE, tackle_aggression=aggression))

    def test_tackle_ok_distance_and_from_behind(self):
        cases = [(8.5, 1.0, True), (13.0, 1.0, True), (14.0, 1.0, False),
                 (11.5, 0.6, False), (8.5, 0.6, True), (9.5, 0.0, False)]
        for team in (0, 1):
            for x, aggression, expected in cases:
                with self.subTest(team=team, x=x, aggression=aggression):
                    self.assertIs(self.tackler(x, aggression, team)[1], expected)

    def test_presser_slides_only_when_tackle_ok(self):
        s, _ = self.tackler(8.5, 1.0)
        self.assertEqual(assignments(s)["presser"], 3)
        self.assertEqual(decide(s, 3).command["commandType"], "PRESS_BALL")
        slide = decide(s, 3, DEFAULT_SETTINGS, replace(DEFAULT_STYLE, tackle_aggression=1.0))
        self.assertEqual(slide.command["commandType"], "SLIDE_TACKLE")
        self.assertEqual(slide.command["parameters"], {"target_player_id": 1})


class TempoTest(unittest.TestCase):
    def hold_score(self, decision):
        return next(a["score"] for a in decision.alternatives
                    if a["command"]["commandType"] == "SET_STANCE")

    def test_slow_tempo_holds_when_unpressed(self):
        s = snap()
        self.assertEqual(decide(s, 2).command["commandType"], "PASS")
        slow = decide(s, 2, DEFAULT_SETTINGS, replace(DEFAULT_STYLE, tempo=0.0))
        self.assertEqual(slow.command["commandType"], "SET_STANCE")
        self.assertEqual(self.hold_score(slow), 60.0)

    def test_no_hold_bonus_under_pressure(self):
        s = snap(theirs=[*THEIRS[:3], (-3, 2), THEIRS[4]])
        slow = decide(s, 2, DEFAULT_SETTINGS, replace(DEFAULT_STYLE, tempo=0.0))
        self.assertEqual(self.hold_score(slow), 0.0)

    def test_high_tempo_prefers_forward_pass(self):
        s = snap()
        fast = decide(s, 2, DEFAULT_SETTINGS, replace(DEFAULT_STYLE, tempo=1.0))
        forward = [a for a in fast.alternatives if a["command"]["commandType"] == "PASS"]
        self.assertGreater(forward[0]["score"], decide(s, 2).alternatives[0]["score"])


if __name__ == "__main__":
    unittest.main()
```

Run: `cd redesign_4 && .venv/bin/python -m unittest discover -s tests -p test_style_tactics.py`
Expected: FAIL (`ImportError: cannot import name 'attack_cap'`).

- [ ] **Step 2: Implement in `agent/tactics.py`** (each edit replaces exactly one occurrence)

1. Import line becomes:

```python
from .model import DEFAULT_SETTINGS, DEFAULT_STYLE, Decision, Player, Settings, Snapshot, Style
```

2. Insert immediately before `def posture`:

```python
# Outfield roles per formation: (role, mirrored). LINK is the withdrawn forward.
ROLES = {
    "1-1-2": {1: ("DEF", False), 2: ("MID", False), 3: ("LINK", False), 4: ("FWD", False)},
    "2-1-1": {1: ("DEF", False), 2: ("DEF", True), 3: ("MID", False), 4: ("FWD", False)},
    "1-2-1": {1: ("DEF", False), 2: ("MID", False), 3: ("MID", True), 4: ("FWD", False)},
}


def role(pid: int, formation: str = "1-1-2") -> tuple[str, bool]:
    return ROLES[formation][pid]


def attack_cap(s: Snapshot, pid: int, style: Style = DEFAULT_STYLE) -> float | None:
    """Furthest attack-direction x the style allows this outfield player to move to."""
    if pid == 0:
        return None
    if s.owner is not None and s.owner[0] == s.team:
        return None if pid in (4, 3, 2)[: style.attackers_committed] else 0.0
    return -50 + 96 * style.block_depth if style.block_depth < 1 else None


def capped(s: Snapshot, x: float, cap: float | None) -> float:
    return x if cap is None else s.direction * min(s.direction * x, cap)


def tackle_ok(s: Snapshot, p: Player, style: Style = DEFAULT_STYLE) -> bool:
    if style.tackle_aggression <= 0 or s.owner is None or s.owner[0] == s.team:
        return False
    carrier = next(o for o in s.opponents if o.id == s.owner[1])
    dx, dy = p.x - carrier.x, p.y - carrier.y
    gap = math.hypot(dx, dy)
    if gap > 1 + 2.5 * style.tackle_aggression:
        return False
    # Same test as the simulator: within 60 degrees of the carrier's backward direction.
    behind = gap > 0 and s.direction * dx / gap > 0.5
    return not behind or style.tackle_aggression >= 0.7
```

3. `carrier_options` signature:

```python
def carrier_options(
    s: Snapshot, p: Player, settings: Settings, style: Style = DEFAULT_STYLE
) -> list[tuple[float, dict, str]]:
```

4. After `score = 22 + min(pressure, 12) * 2 + 0.6 * gain + min(lane, 8)` insert (the guard keeps default floats bit-identical):

```python
        if style.tempo != 0.5:
            score += (style.tempo - 0.5) * 1.2 * gain
```

5. Replace the final `options.append((0.0, SET_STANCE ...))` block with:

```python
    calm = min(distance(point(p), point(o)) for o in s.opponents) > 8
    hold = max(0.0, 0.5 - style.tempo) * 120 if calm else 0.0
    options.append(
        (
            hold,
            command(s, p.id, "SET_STANCE", stance=0),
            "hold: slow tempo, no opponent within 8 m"
            if hold
            else "hold balanced: no safe supported possession action",
        )
    )
```

6. `support` signature gains `formation: str = "1-1-2"`:

```python
def support(
    s: Snapshot, p: Player, settings: Settings, plan: dict, formation: str = "1-1-2"
) -> tuple[float, float]:
```

and the `if p.id == 1: ... link = p.id == 3` block is replaced with:

```python
    kind, mirrored = role(p.id, formation)
    flip = -1 if mirrored else 1
    if kind == "DEF":
        return d * max(-42, min(-10, bp - 18)), flip * (-8 if s.ball[1] > 0 else 8)
    if kind == "MID":
        return d * max(-30, min(23, bp - 9 + shift)), flip * (
            -1 if s.ball[1] >= 0 else 1
        ) * settings.support_width * 0.65
    link = kind == "LINK"
```

7. `decide` signature:

```python
def decide(
    snapshot: Snapshot,
    player_id: int,
    settings: Settings = DEFAULT_SETTINGS,
    style: Style = DEFAULT_STYLE,
) -> Decision:
```

   - carrier branch: `choices = carrier_options(s, p, settings, style)`
   - presser branch, before the `PRESS_BALL` return:

```python
        if tackle_ok(s, p, style):
            return Decision(
                command(s, player_id, "SLIDE_TACKLE", target_player_id=s.owner[1]),
                f"slide tackle; aggression={style.tackle_aggression:.1f}",
                assignments=plan,
            )
```

   - replace the `if player_id in plan["marks"]:` line with:

```python
    cap = attack_cap(s, player_id, style)
    if player_id in plan["marks"]:
        threat = next(o for o in s.opponents if o.id == plan["marks"][player_id])
        if cap is not None and s.direction * threat.x > cap:
            return Decision(
                movement(s, p, capped(s, threat.x, cap), threat.y, urgent=True),
                "hold block line; runner beyond cap",
                assignments=plan,
            )
```

   - the final support move:

```python
    x, y = support(s, p, settings, plan, style.formation)
    x = capped(s, x, cap)
```

`assignments` is unchanged; a deep block still presses through the normal presser rule, which is limited by `press_radius`.

- [ ] **Step 3: Verify green, golden unchanged, lint**

Run: `cd redesign_4 && .venv/bin/python -m unittest discover -s tests && .venv/bin/python -m ruff check agent tests`
Expected: all pass, including `test_golden` (defaults are identity), and ruff is clean.

- [ ] **Step 4: Commit**

```bash
git add redesign_4/agent/tactics.py redesign_4/tests/test_style_tactics.py
git commit -m "feat(redesign_4): style-aware tactics (formation roles, block, tackles, tempo)" -m "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

---

## Task B5: Coach policy and Runtime wiring

> **Verification status for B5–B8:** this code was written against the verified B1–B4 code but was not executed while planning. Expect small fixes: LangChain import paths, the `ChatBedrockConverse` parameter names, and p99 timing on slow machines. Treat the tests as the contract; adjust the code, not the assertions, unless an assertion is clearly wrong.

**Files:** Create `redesign_4/agent/coach.py`, `redesign_4/tests/test_coach_policy.py`; modify `redesign_4/agent/runtime.py`

- [ ] **Step 1: Failing tests** — `tests/test_coach_policy.py`:

```python
import json
import time
import unittest
from dataclasses import replace

from golden import payloads
from test_runtime import payload
from test_style_tactics import snap

from agent.archetypes import ARCHETYPES
from agent.coach import choose_preset
from agent.model import Profile
from agent.runtime import Runtime


class ChoosePresetTest(unittest.TestCase):
    def test_table(self):
        base = snap()
        cases = [
            ((0, 1), 10.0, "", "press_high"),
            ((0, 1), 80.0, "", "go_all_out_attack"),  # r = 0.333
            ((1, 0), 80.0, "", "slow_the_tempo"),
            ((1, 0), 10.0, "", ""),
            ((0, 0), 100.0, "", "shoot_on_sight"),  # r = 0.167
            ((0, 0), 60.0, "", ""),
            ((0, 1), 10.0, "slow_the_tempo", "slow_the_tempo"),  # platform preset wins
        ]
        for score, clock, sent, expected in cases:
            with self.subTest(score=score, clock=clock, sent=sent):
                s = replace(base, score=score, clock=clock, coach=sent)
                self.assertEqual(choose_preset(s), expected)

    def test_score_is_read_from_own_side(self):
        s = replace(snap(team=1), score=(1, 0), clock=10.0)
        self.assertEqual(choose_preset(s), "press_high")


class RuntimeCoachTest(unittest.TestCase):
    def coach_seen(self, policy):
        losing = json.loads(payload()["prompt"])
        losing["gameState"]["score"] = {"home": 0, "away": 1}
        seen = []
        for pid in range(5):
            records = []
            runtime = Runtime(Profile(coach_policy=policy), emit=records.append)
            try:
                runtime.handle({"prompt": json.dumps({**losing, "myPlayers": [pid]})})
                self.assertTrue(runtime.flush_logs())
            finally:
                runtime.close()
            seen.append(records[-1]["snapshot"]["coach"])
        return seen

    def test_all_players_agree_on_the_rules_preset(self):
        self.assertEqual(self.coach_seen("rules"), ["press_high"] * 5)

    def test_no_policy_leaves_coach_empty(self):
        self.assertEqual(self.coach_seen("none"), [""] * 5)

    def test_p99_latency_under_50ms(self):
        profile = replace(ARCHETYPES["hard_tackle"], coach_policy="rules")
        runtime = Runtime(profile, emit=lambda _record: None)
        timings = []
        try:
            for p in payloads():
                for pid in range(5):
                    start = time.perf_counter()
                    runtime.handle({**p, "myPlayers": [pid]})
                    timings.append(time.perf_counter() - start)
        finally:
            runtime.close()
        timings.sort()
        self.assertLess(timings[int(0.99 * len(timings))], 0.050)


if __name__ == "__main__":
    unittest.main()
```

`ARCHETYPES` arrives in B6. Until then, replace that profile with `Profile(coach_policy="rules")`, and switch it to `hard_tackle` in B6 Step 3.

Run: `cd redesign_4 && .venv/bin/python -m unittest discover -s tests -p test_coach_policy.py`
Expected: FAIL (`ModuleNotFoundError: No module named 'agent.coach'`).

- [ ] **Step 2: `agent/coach.py`**

```python
"""Rules coach: picks a preset from shared snapshot fields only, so all players agree."""
from __future__ import annotations

from .model import Snapshot


def choose_preset(s: Snapshot) -> str:
    if s.coach:
        return s.coach
    diff = s.score[s.team] - s.score[1 - s.team]
    remaining = max(0.0, 1 - s.clock / s.duration) if s.duration > 0 else 0.0
    if diff < 0:
        return "go_all_out_attack" if remaining <= 0.35 else "press_high"
    if diff > 0 and remaining <= 0.40:
        return "slow_the_tempo"
    if diff == 0 and remaining <= 0.20:
        return "shoot_on_sight"
    return ""
```

This must match `local_simulator/src/football_lab/coaches.py::scripted_coach` (A7). If either table changes, change both.

- [ ] **Step 3: `agent/runtime.py`**

Imports: add `from dataclasses import replace` and `from .coach import choose_preset`. In `handle`, replace

```python
            decision = decide(snapshot, pid, self.settings)
```

with

```python
            if self.profile.coach_policy == "rules":
                snapshot = replace(snapshot, coach=choose_preset(snapshot))
            decision = decide(snapshot, pid, self.settings, self.profile.style)
```

The logged `snapshot` therefore records the preset that was applied.

- [ ] **Step 4: Verify and commit** (after B6 makes `ARCHETYPES` importable, or with the temporary profile)

Run: `cd redesign_4 && .venv/bin/python -m unittest discover -s tests && .venv/bin/python -m ruff check agent tests`
Expected: all pass; golden unchanged (`coach_policy` defaults to `none`).

```bash
git add redesign_4/agent/coach.py redesign_4/agent/runtime.py redesign_4/tests/test_coach_policy.py
git commit -m "feat(redesign_4): rules coach policy wired into Runtime" -m "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

---

## Task B6: Archetypes and AWS-like stand-ins

**Files:** Create `redesign_4/agent/archetypes.py`, `redesign_4/tests/test_archetypes.py`; modify `redesign_4/tests/test_coach_policy.py`

- [ ] **Step 1: Failing tests** — `tests/test_archetypes.py`:

```python
import unittest
from dataclasses import asdict

from golden import payloads

from agent.archetypes import ARCHETYPES, STAND_INS
from agent.model import DEFAULT_SETTINGS, DEFAULT_STYLE
from agent.profile import parse_profile
from agent.runtime import Runtime


class ArchetypeTest(unittest.TestCase):
    def test_names_and_spec_values(self):
        self.assertEqual(
            sorted(ARCHETYPES),
            ["balanced", "hard_tackle", "park_the_bus", "press_aggressive", "time_waster"],
        )
        self.assertEqual(
            sorted(STAND_INS), ["aws_like_attacking", "aws_like_balanced", "aws_like_defensive"]
        )
        balanced = ARCHETYPES["balanced"]
        self.assertEqual((balanced.settings, balanced.style), (DEFAULT_SETTINGS, DEFAULT_STYLE))
        park = ARCHETYPES["park_the_bus"]
        self.assertEqual(
            (park.style.block_depth, park.style.attackers_committed, park.style.formation),
            (0.1, 1, "2-1-1"),
        )
        self.assertEqual(park.settings.press_radius, 12.0)
        waster = ARCHETYPES["time_waster"]
        self.assertEqual((waster.settings.pass_margin, waster.settings.shot_range), (4.5, 20.0))

    def test_every_profile_is_valid_and_round_trips(self):
        for name, profile in {**ARCHETYPES, **STAND_INS}.items():
            with self.subTest(name=name):
                self.assertEqual(profile.version, f"archetype-{name}")
                self.assertEqual(profile.coach_policy, "none")
                self.assertEqual(parse_profile(asdict(profile)), profile)

    def test_every_profile_decides_without_errors(self):
        for name, profile in {**ARCHETYPES, **STAND_INS}.items():
            records = []
            runtime = Runtime(profile, emit=records.append)
            try:
                for p in payloads()[:40]:
                    for pid in range(5):
                        runtime.handle({**p, "myPlayers": [pid]})
                self.assertTrue(runtime.flush_logs())
            finally:
                runtime.close()
            with self.subTest(name=name):
                self.assertEqual([r for r in records if r["type"] == "error"], [])


if __name__ == "__main__":
    unittest.main()
```

Run: `cd redesign_4 && .venv/bin/python -m unittest discover -s tests -p test_archetypes.py`
Expected: FAIL (`ModuleNotFoundError: No module named 'agent.archetypes'`).

- [ ] **Step 2: `agent/archetypes.py`**

```python
"""Strategy archetypes (spec §5) and AWS-like opponent stand-ins (spec §4)."""
from __future__ import annotations

from dataclasses import replace

from .model import DEFAULT_SETTINGS, DEFAULT_STYLE, Profile, Style


def _p(name: str, style: Style, **settings: float) -> Profile:
    return Profile(
        version=f"archetype-{name}",
        settings=replace(DEFAULT_SETTINGS, **settings),
        style=style,
    )


ARCHETYPES: dict[str, Profile] = {
    "balanced": _p("balanced", DEFAULT_STYLE),
    "press_aggressive": _p(
        "press_aggressive", Style(1.0, 3, 0.6, 0.8, "1-1-2"), press_radius=30.0
    ),
    "park_the_bus": _p("park_the_bus", Style(0.1, 1, 0.4, 0.3, "2-1-1"), press_radius=12.0),
    "time_waster": _p(
        "time_waster", Style(0.5, 1, 0.2, 0.0, "1-2-1"), pass_margin=4.5, shot_range=20.0
    ),
    "hard_tackle": _p("hard_tackle", Style(0.7, 2, 1.0, 0.5, "1-1-2"), press_radius=26.0),
}

# Stand-ins for the unknown AWS practice bots; they only widen the opponent pool.
STAND_INS: dict[str, Profile] = {
    "aws_like_balanced": _p(
        "aws_like_balanced", DEFAULT_STYLE, press_radius=18.0, support_width=14.0
    ),
    "aws_like_attacking": _p(
        "aws_like_attacking", Style(1.0, 3, 0.3, 0.9, "1-1-2"), shot_range=33.0,
        press_radius=28.0,
    ),
    "aws_like_defensive": _p(
        "aws_like_defensive", Style(0.3, 1, 0.0, 0.3, "2-1-1"), press_radius=15.0
    ),
}
```

- [ ] **Step 3: Point the B5 latency test at `hard_tackle`** (if the temporary profile was used) and verify

Run: `cd redesign_4 && .venv/bin/python -m unittest discover -s tests && .venv/bin/python -m ruff check agent tests`
Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add redesign_4/agent/archetypes.py redesign_4/tests/test_archetypes.py redesign_4/tests/test_coach_policy.py
git commit -m "feat(redesign_4): strategy archetypes and AWS-like stand-ins" -m "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

---

## Task B7: Trainer keeps the base profile's style

**Files:** Modify `redesign_4/agent/trainer.py`, `redesign_4/agent/__main__.py`, `redesign_4/tests/test_learning.py`

- [ ] **Step 1: Failing test** — add to `LearningTests` in `tests/test_learning.py` (add `Style` to the `agent.model` import):

```python
    def test_train_keeps_base_style_and_coach_policy(self):
        self.trainer.ingest(manifest(), records())
        base = Profile(style=Style(0.1, 1, 0.4, 0.3, "2-1-1"), coach_policy="rules")
        plain = self.trainer.train()
        kept = self.trainer.train(base=base)
        self.assertEqual((plain.style, plain.coach_policy), (Profile().style, "none"))
        self.assertEqual((kept.style, kept.coach_policy), (base.style, base.coach_policy))
        self.assertEqual(kept.settings, plain.settings)
        self.assertNotEqual(kept.version, plain.version)
        self.trainer.export(kept.version, self.path / "kept.json")
        self.assertEqual(load_profile(self.path / "kept.json"), kept)

    def test_cli_base_profile(self):
        from contextlib import redirect_stdout
        from io import StringIO

        from agent.__main__ import main

        self.trainer.ingest(manifest(), records())
        self.trainer.close()
        base = Profile(style=Style(0.5, 1, 0.2, 0.0, "1-2-1"))
        save_profile(base, self.path / "base.json")
        out = self.path / "trained.json"
        with redirect_stdout(StringIO()):
            code = main(["train", "--db", str(self.path / "coach.db"), "--output", str(out),
                         "--base-profile", str(self.path / "base.json")])
        self.trainer = Trainer(self.path / "coach.db")
        self.assertEqual(code, 0)
        self.assertEqual(load_profile(out).style, base.style)
```

Run: `cd redesign_4 && .venv/bin/python -m unittest discover -s tests -k base`
Expected: FAIL (`TypeError: Trainer.train() got an unexpected keyword argument 'base'`).

- [ ] **Step 2: `trainer.py`**

Signature: `def train(self, base: Profile | None = None) -> Profile:` (import `Profile` from `.model` if not already imported). Replace

```python
        material = {"settings": asdict(settings), "opponents": scouts}
```

with

```python
        material = {"settings": asdict(settings), "opponents": scouts}
        if base is not None:
            # Learning tunes settings only; the shipped archetype and coach policy carry over.
            material["style"] = asdict(base.style)
            material["coach_policy"] = base.coach_policy
```

Without `base`, `material` (and therefore the version digest) is unchanged.

- [ ] **Step 3: `__main__.py`**

In the subcommand loop add:

```python
        if name in ("learn", "train"):
            sub.add_argument("--base-profile", dest="base_profile")
```

and replace `profile = trainer.train()` with:

```python
                base = load_profile(args.base_profile) if args.base_profile else None
                profile = trainer.train(base=base)
```

- [ ] **Step 4: Verify and commit**

Run: `cd redesign_4 && .venv/bin/python -m unittest discover -s tests && .venv/bin/python -m ruff check agent tests`
Expected: all pass.

```bash
git add redesign_4/agent/trainer.py redesign_4/agent/__main__.py redesign_4/tests/test_learning.py
git commit -m "feat(redesign_4): train --base-profile keeps style and coach policy" -m "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

---

## Task B8: Off-tick LangChain advisor

**Files:** Modify `redesign_4/pyproject.toml`; create `redesign_4/agent/advisor/__init__.py`, `redesign_4/agent/advisor/core.py`, `redesign_4/agent/advisor/__main__.py`, `redesign_4/tests/test_advisor.py`

- [ ] **Step 1: Extra and install**

In `pyproject.toml`, under `[project.optional-dependencies]` add:

```toml
llm = ["langchain-core>=1.0,<2", "langchain-aws>=1.0,<2"]
```

and extend the mypy override module list to:

```toml
module = ["bedrock_agentcore.*", "langchain_core.*", "langchain_aws.*", "boto3.*"]
```

Run: `cd redesign_4 && uv pip install --python .venv/bin/python -e '.[llm]'`
Expected: installs `langchain-core` 1.x and `langchain-aws` 1.x (pydantic v2 comes with them). If a 1.x release is unavailable, use the newest available major, adjust the bounds here and in spec §7, and note it in the commit message.

- [ ] **Step 2: Failing tests** — `tests/test_advisor.py`:

```python
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from agent.model import Profile

ROOT = Path(__file__).resolve().parents[1]
HAS_LANGCHAIN = importlib.util.find_spec("langchain_core") is not None


class RuntimeGuardTest(unittest.TestCase):
    def test_runtime_never_imports_langchain(self):
        code = (
            "import sys, agent.runtime, agent.__main__, agent.trainer; "
            "bad = sorted(m for m in sys.modules "
            "if m.startswith(('langchain', 'pydantic', 'agent.advisor'))); "
            "print(bad); sys.exit(1 if bad else 0)"
        )
        done = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True,
                              text=True, timeout=60)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)


@unittest.skipUnless(HAS_LANGCHAIN, "install the llm extra")
class AdvisorTest(unittest.TestCase):
    def advisor(self, *replies):
        from langchain_core.language_models.fake_chat_models import FakeListChatModel

        from agent.advisor import Advisor
        from agent.archetypes import ARCHETYPES

        return Advisor(FakeListChatModel(responses=list(replies)), ARCHETYPES)

    def test_pick_archetype_returns_validated_profile(self):
        from agent.archetypes import ARCHETYPES

        reply = '{"archetype": "park_the_bus", "coach_policy": "rules", "rationale": "deep"}'
        for text in (reply, f"```json\n{reply}\n```"):
            with self.subTest(text=text):
                advice, profile = self.advisor(text).pick_archetype({"rows": []}, "redesign_3_v1")
                self.assertEqual(advice.archetype, "park_the_bus")
                self.assertEqual(profile.style, ARCHETYPES["park_the_bus"].style)
                self.assertEqual(profile.coach_policy, "rules")
                self.assertEqual(profile.version, "advice-park_the_bus-rules")

    def test_bad_model_output_raises_advisor_error(self):
        from agent.advisor import AdvisorError

        bad = [
            "not json",
            '{"archetype": "nope", "coach_policy": "none", "rationale": "x"}',
            '{"archetype": "balanced", "coach_policy": "llm", "rationale": "x"}',
            '{"archetype": "balanced", "coach_policy": "none", "rationale": "x", "extra": 1}',
        ]
        for text in bad:
            with self.subTest(text=text), self.assertRaises(AdvisorError):
                self.advisor(text).pick_archetype({}, "x")

    def test_model_failure_raises_advisor_error(self):
        from agent.advisor import Advisor, AdvisorError

        class Down:
            def invoke(self, _messages):
                raise RuntimeError("throttled")

        with self.assertRaises(AdvisorError):
            Advisor(Down(), {}).pick_archetype({}, "x")

    def test_review_match_bounds_are_enforced(self):
        from dataclasses import asdict

        from agent.advisor import AdvisorError

        current = Profile(version="cur", opponents={"o": {"weight": 1.0, "samples": 3,
                                                           "settings": asdict(Profile().settings)}})
        settings = {**asdict(current.settings), "press_radius": 26.0}
        style = {**asdict(current.style), "tackle_aggression": 0.5}
        ok = json.dumps({"settings": settings, "style": style, "rationale": "press more"})
        advice, profile = self.advisor(ok).review_match({"score": [0, 1]}, current)
        self.assertEqual(profile.settings.press_radius, 26.0)
        self.assertEqual(profile.style.tackle_aggression, 0.5)
        self.assertEqual(profile.opponents, current.opponents)
        self.assertEqual(profile.version, "cur-review")
        too_far = json.dumps({"settings": {**settings, "press_radius": 99.0}, "style": style,
                              "rationale": "x"})
        with self.assertRaises(AdvisorError):
            self.advisor(too_far).review_match({}, current)

    def test_cli_writes_proposal_or_nothing(self):
        from contextlib import redirect_stderr, redirect_stdout
        from io import StringIO

        from langchain_core.language_models.fake_chat_models import FakeListChatModel

        from agent.advisor.__main__ import main

        with tempfile.TemporaryDirectory() as tmp:
            matrix, out = Path(tmp) / "m.json", Path(tmp) / "proposal.json"
            matrix.write_text("{}")
            reply = '{"archetype": "hard_tackle", "coach_policy": "none", "rationale": "x"}'
            argv = ["pick", "--matrix", str(matrix), "--opponent", "o", "--output", str(out)]
            with redirect_stdout(StringIO()):
                self.assertEqual(main(argv, llm=FakeListChatModel(responses=[reply])), 0)
            proposal = json.loads(out.read_text())
            self.assertEqual(proposal["status"], "proposal")
            self.assertEqual(proposal["profile"]["version"], "advice-hard_tackle-none")
            out.unlink()
            with redirect_stderr(StringIO()):
                self.assertEqual(main(argv, llm=FakeListChatModel(responses=["junk"])), 2)
            self.assertFalse(out.exists())


if __name__ == "__main__":
    unittest.main()
```

The guard must pass with **and** without the extra installed.

Run: `cd redesign_4 && .venv/bin/python -m unittest discover -s tests -p test_advisor.py`
Expected: FAIL (`ModuleNotFoundError: No module named 'agent.advisor'`).

- [ ] **Step 3: `agent/advisor/core.py`**

```python
"""Off-tick LangChain advisor. Produces proposals only; the runtime never imports this."""
from __future__ import annotations

import json
from dataclasses import replace
from typing import Any, Literal

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..model import Profile
from ..profile import parse_profile

SYSTEM = (
    "You advise a 5-a-side football team between matches. Reply with one JSON object "
    "and nothing else. Never invent fields. "
)
PICK = (
    "Pick the strategy archetype with the strongest evidence against the opponent. "
    'Schema: {{"archetype": one of {names}, "coach_policy": "none" | "rules", '
    '"rationale": string}}.'
)
REVIEW = (
    "Propose small adjustments to the current profile after this match report. "
    'Schema: {"settings": <all current settings keys>, "style": <all current style keys>, '
    '"rationale": string}. Stay within the bounds you are given.'
)


class AdvisorError(RuntimeError):
    """The model failed or returned something that is not a valid proposal."""


class ArchetypeAdvice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    archetype: str
    coach_policy: Literal["none", "rules"]
    rationale: str = Field(min_length=1, max_length=2000)


class SettingsAdvice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    settings: dict[str, float]
    style: dict[str, Any]
    rationale: str = Field(min_length=1, max_length=2000)


def _text(content: Any) -> str:
    if isinstance(content, list):
        content = "".join(b.get("text", "") for b in content if isinstance(b, dict))
    text = str(content).strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        text = text.rsplit("```", 1)[0]
    return text.strip()


class Advisor:
    def __init__(self, llm: Any, archetypes: dict[str, Profile]):
        self.llm = llm
        self.archetypes = archetypes

    def _ask(self, schema: type[BaseModel], task: str, payload: dict) -> Any:
        messages = [
            SystemMessage(SYSTEM + task),
            HumanMessage(json.dumps(payload, sort_keys=True, default=str)),
        ]
        try:
            reply = self.llm.invoke(messages)
        except Exception as exc:  # provider, network and throttling errors alike
            raise AdvisorError(f"model call failed: {exc}") from exc
        try:
            return schema.model_validate_json(_text(reply.content))
        except ValidationError as exc:
            raise AdvisorError(f"invalid model output: {exc}") from exc

    @staticmethod
    def _checked(data: dict) -> Profile:
        try:
            return parse_profile(data)
        except (ValueError, TypeError, KeyError) as exc:
            raise AdvisorError(f"proposal rejected: {exc}") from exc

    def pick_archetype(self, matrix: dict, opponent_id: str) -> tuple[ArchetypeAdvice, Profile]:
        task = PICK.format(names=json.dumps(sorted(self.archetypes)))
        advice = self._ask(ArchetypeAdvice, task, {"opponent_id": opponent_id, "matrix": matrix})
        if advice.archetype not in self.archetypes:
            raise AdvisorError(f"unknown archetype: {advice.archetype}")
        base = self.archetypes[advice.archetype]
        profile = replace(
            base,
            version=f"advice-{advice.archetype}-{advice.coach_policy}",
            coach_policy=advice.coach_policy,
        )
        return advice, self._checked(profile.to_dict())

    def review_match(self, report: dict, current: Profile) -> tuple[SettingsAdvice, Profile]:
        advice = self._ask(SettingsAdvice, REVIEW, {"report": report, "current": current.to_dict()})
        data = {
            **current.to_dict(),
            "version": f"{current.version}-review",
            "settings": advice.settings,
            "style": advice.style,
        }
        return advice, self._checked(data)
```

`agent/advisor/__init__.py`:

```python
"""Optional LangChain advisor (`pip install -e '.[llm]'`); never imported by the runtime."""
from .core import Advisor, AdvisorError, ArchetypeAdvice, SettingsAdvice

__all__ = ["Advisor", "AdvisorError", "ArchetypeAdvice", "SettingsAdvice"]
```

- [ ] **Step 4: `agent/advisor/__main__.py`**

```python
"""python -m agent.advisor pick|review — writes a proposal file; deployment stays manual."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from ..archetypes import ARCHETYPES
from ..profile import load_profile
from .core import Advisor, AdvisorError


def bedrock_llm() -> Any:
    from langchain_aws import ChatBedrockConverse

    model = os.environ.get("AFC_ADVISOR_MODEL")
    if not model:
        raise AdvisorError("set AFC_ADVISOR_MODEL to a Bedrock model id")
    return ChatBedrockConverse(model=model, temperature=0)


def main(argv: list[str] | None = None, llm: Any = None) -> int:
    parser = argparse.ArgumentParser(description="Off-tick strategy advisor (proposals only)")
    ops = parser.add_subparsers(dest="operation", required=True)
    pick = ops.add_parser("pick", help="choose an archetype from a campaign report JSON")
    pick.add_argument("--matrix", required=True)
    pick.add_argument("--opponent", required=True)
    review = ops.add_parser("review", help="propose settings/style after a match report")
    review.add_argument("--report", required=True)
    review.add_argument("--profile", required=True)
    for sub in (pick, review):
        sub.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    try:
        advisor = Advisor(llm if llm is not None else bedrock_llm(), ARCHETYPES)
        if args.operation == "pick":
            matrix = json.loads(Path(args.matrix).read_text())
            advice, profile = advisor.pick_archetype(matrix, args.opponent)
        else:
            report = json.loads(Path(args.report).read_text())
            advice, profile = advisor.review_match(report, load_profile(args.profile))
        proposal = {
            "status": "proposal",
            "advice": advice.model_dump(),
            "profile": profile.to_dict(),
        }
        Path(args.output).write_text(json.dumps(proposal, indent=2, sort_keys=True) + "\n")
    except (AdvisorError, OSError, ValueError, TypeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"proposal": args.output, "version": profile.version}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 5: Verify (with the extra, then without it for the guard)**

Run: `cd redesign_4 && .venv/bin/python -m unittest discover -s tests && .venv/bin/python -m ruff check agent tests`
Expected: all pass, with `AdvisorTest` running (not skipped).

Run: `cd redesign_4 && .venv/bin/python -c "import sys, agent.runtime; assert not [m for m in sys.modules if m.startswith('langchain')]"`
Expected: exit 0.

- [ ] **Step 6: Commit**

```bash
git add redesign_4/pyproject.toml redesign_4/agent/advisor redesign_4/tests/test_advisor.py
git commit -m "feat(redesign_4): off-tick LangChain advisor writing proposal files" -m "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

---

# Part C — Experiments and ship

Part C needs A1–A11 and B1–B8 merged. Campaign artefacts go to `local_simulator/local/r4v2/`, which is git-ignored. Only the report, the shipped profile and docs are committed.

## Task C1: `redesign_4_v2` entrant (34 profile variants)

**Files:** Create `local_simulator/entrants/redesign_4_v2/build.py`, `local_simulator/tests/test_redesign_4_v2_entrant.py`; generated (committed): `entrants/redesign_4_v2/{agent/,shim.py,profiles/,variants.json,entrant.json}`

Variant ids:
- `r4v2_<archetype>_<none|rules>`: native formation; 10 ids.
- `r4v2_<archetype>_<none|rules>_f<112|211|121>`: the two other formations; 20 ids.
- `r4v2_balanced_extcoach`: balanced, `coach_policy` none, simulator `scripted` coach.
- `r4v2_aws_like_<balanced|attacking|defensive>`: pool stand-ins.

Total: 34.

- [ ] **Step 1: Failing tests** — `tests/test_redesign_4_v2_entrant.py`:

```python
"""redesign_4_v2: the built entrant is in sync with redesign_4 and every variant loads."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from football_lab.cli_campaign import _load_entrant_spec, _runner_env
from football_lab.entrant_contracts import BudgetSpec
from football_lab.evolution.integrity import hash_tree
from football_lab.team_runner import TeamRunner

SIM = Path(__file__).resolve().parents[1]
ENTRANT = SIM / "entrants" / "redesign_4_v2"
SOURCE = SIM.parent / "redesign_4" / "agent"


def _filtered_hash(src: Path, dst: Path, *skip: str) -> str:
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", *skip))
    return hash_tree(dst)


def _state() -> dict:
    players = [
        {"team_id": tid, "player_id": pid, "x": float(-40 + pid * 10 * (1 - 2 * tid)),
         "y": 0.0, "vx": 0.0, "vy": 0.0, "stamina": 1.0}
        for pid in range(5) for tid in range(2)
    ]
    return {
        "tick": 0, "play_mode": "OPEN_PLAY", "mode_team": None, "players": players,
        "ball": {"x": 0.0, "y": 0.0, "z": 0.0, "vx": 0.0, "vy": 0.0, "vz": 0.0,
                 "owner_team": None, "owner_player": None, "in_flight": False},
        "score": [0, 1],
    }


def test_variants_cover_archetypes_formations_and_pool() -> None:
    variants = json.loads((ENTRANT / "variants.json").read_text())["variants"]
    ids = [v["id"] for v in variants]
    assert len(ids) == len(set(ids)) == 34
    for v in variants:
        profile = json.loads((ENTRANT / v["profile"]).read_text())
        assert profile["version"] == v["id"]
        assert profile["coach_policy"] == ("rules" if "_rules" in v["id"] else "none")
    coaches = {v["id"]: v["coach"] for v in variants}
    assert coaches["r4v2_balanced_extcoach"] == "scripted"
    assert sum(c is not None for c in coaches.values()) == 1
    park = json.loads((ENTRANT / "profiles" / "r4v2_park_the_bus_rules_f112.json").read_text())
    assert park["style"]["formation"] == "1-1-2" and park["style"]["block_depth"] == 0.1


def test_agent_copy_matches_redesign_4_and_hash_is_current(tmp_path: Path) -> None:
    assert _filtered_hash(SOURCE, tmp_path / "src", "advisor") == _filtered_hash(
        ENTRANT / "agent", tmp_path / "copy"
    )
    recorded = json.loads((ENTRANT / "entrant.json").read_text())["entrant_hash"]
    assert _filtered_hash(ENTRANT, tmp_path / "entrant", "entrant.json") == recorded


@pytest.mark.parametrize(
    "vid", ["r4v2_hard_tackle_rules", "r4v2_park_the_bus_none", "r4v2_time_waster_rules_f112"]
)
def test_variant_decides_one_tick(tmp_path: Path, vid: str) -> None:
    spec = _load_entrant_spec(vid)
    budget: BudgetSpec = {"startup_timeout_s": 15.0, "request_timeout_s": 10.0,
                          "output_size_limit": 65536}
    with TeamRunner(spec, team_id=0, budget=budget, work_dir=tmp_path,
                    env=_runner_env(spec)) as runner:
        cmds = runner.decide(_state(), coach="press_high")
    assert len(cmds) == 5
```

Run: `cd local_simulator && ../.venv/bin/python -m pytest -q tests/test_redesign_4_v2_entrant.py`
Expected: FAIL (missing `variants.json`).

- [ ] **Step 2: `entrants/redesign_4_v2/build.py`**

```python
#!/usr/bin/env python3
"""Rebuild the redesign_4_v2 entrant from ../../../redesign_4.

Run from local_simulator: ../.venv/bin/python entrants/redesign_4_v2/build.py
Rebuild (and commit the result) whenever redesign_4/agent changes.
"""
from __future__ import annotations

import json
import shutil
import sys
from dataclasses import replace
from pathlib import Path

HERE = Path(__file__).resolve().parent
SIM = HERE.parents[1]
SOURCE = SIM.parent / "redesign_4"
sys.path.insert(0, str(SOURCE))
sys.path.insert(0, str(SIM / "src"))

from agent.archetypes import ARCHETYPES, STAND_INS  # noqa: E402
from agent.model import Profile  # noqa: E402
from agent.profile import FORMATIONS, parse_profile  # noqa: E402
from football_lab.evolution.integrity import hash_tree  # noqa: E402

PREFIX = "r4v2"


def variants() -> list[tuple[str, Profile, str | None]]:
    out: list[tuple[str, Profile, str | None]] = []
    for name, base in ARCHETYPES.items():
        for policy in ("none", "rules"):
            for formation in FORMATIONS:
                suffix = "" if formation == base.style.formation else "_f" + formation.replace("-", "")
                vid = f"{PREFIX}_{name}_{policy}{suffix}"
                style = replace(base.style, formation=formation)
                out.append((vid, replace(base, version=vid, coach_policy=policy, style=style), None))
    vid = f"{PREFIX}_balanced_extcoach"
    out.append((vid, replace(ARCHETYPES["balanced"], version=vid), "scripted"))
    for name, profile in STAND_INS.items():
        vid = f"{PREFIX}_{name}"
        out.append((vid, replace(profile, version=vid), None))
    return out


def main() -> None:
    for generated in ("agent", "profiles"):
        shutil.rmtree(HERE / generated, ignore_errors=True)
    (HERE / "entrant.json").unlink(missing_ok=True)
    shutil.copytree(
        SOURCE / "agent", HERE / "agent",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "advisor"),
    )
    shutil.copy2(SIM / "entrants" / "redesign_4" / "profile_shim.py", HERE / "shim.py")
    (HERE / "profiles").mkdir()
    rows = []
    for vid, profile, coach in variants():
        data = parse_profile(profile.to_dict()).to_dict()
        (HERE / "profiles" / f"{vid}.json").write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
        rows.append({"id": vid, "profile": f"profiles/{vid}.json", "coach": coach})
    (HERE / "variants.json").write_text(
        json.dumps({"base_id": "redesign_4_v2", "variants": rows}, indent=1) + "\n"
    )
    entrant = {
        "id": "redesign_4_v2",
        "source_name": "redesign_4",
        "topology": "per_player",
        "shim": "entrants/redesign_4_v2/shim.py",
        "entrant_hash": hash_tree(HERE),  # everything except entrant.json (removed above)
        "rules_version": "v1",  # entrant protocol version; match rules come from the campaign
        "track": "patched",
        "patches": [{
            "id": "profile_env",
            "classification": "compatibility",
            "file": "shim.py",
            "description": "profile loaded from $AFC_PROFILE (set per variant by the campaign CLI)",
        }],
    }
    (HERE / "entrant.json").write_text(json.dumps(entrant, indent=2) + "\n")
    print(f"built {len(rows)} variants; entrant_hash={entrant['entrant_hash'][:12]}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Build and verify**

Run: `cd local_simulator && ../.venv/bin/python entrants/redesign_4_v2/build.py && ../.venv/bin/python -m pytest -q tests/test_redesign_4_v2_entrant.py`
Expected: `built 34 variants; ...`; 5 passed.

Run the full simulator suite: `cd local_simulator && ../.venv/bin/python -m pytest -q tests --ignore=tests/test_viewer.py`
Expected: all pass.

- [ ] **Step 4: Commit** (generated files included; no `__pycache__`)

```bash
git add local_simulator/entrants/redesign_4_v2/build.py local_simulator/entrants/redesign_4_v2/shim.py \
  local_simulator/entrants/redesign_4_v2/agent local_simulator/entrants/redesign_4_v2/profiles \
  local_simulator/entrants/redesign_4_v2/variants.json local_simulator/entrants/redesign_4_v2/entrant.json \
  local_simulator/tests/test_redesign_4_v2_entrant.py
git status --short local_simulator/entrants/redesign_4_v2 | grep -c pycache  # expect 0
git commit -m "feat(sim): redesign_4_v2 entrant with 34 strategy/coach variants" -m "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

---

## Task C2: Sanity campaign and time budget

Shell variables used by C2–C5 (run from `local_simulator`):

```bash
PY=../.venv/bin/python; OUT=local/r4v2; mkdir -p $OUT
POOL=initial_idea_v1,redesign_1_v1,redesign_2_v1,redesign_3_v1,redesign_4_v1,r4v2_aws_like_balanced,r4v2_aws_like_attacking,r4v2_aws_like_defensive
ARCH=r4v2_balanced_none,r4v2_balanced_rules,r4v2_press_aggressive_none,r4v2_press_aggressive_rules,r4v2_park_the_bus_none,r4v2_park_the_bus_rules,r4v2_time_waster_none,r4v2_time_waster_rules,r4v2_hard_tackle_none,r4v2_hard_tackle_rules
```

- [ ] **Step 1: Sanity run** (`r4v2_balanced_none` uses the same policy as `redesign_4_v1`, by the golden test)

```bash
$PY -m football_lab campaign init --rules v2 --blocks 20 --seed-pool-start 5000 \
  --entrants r4v2_balanced_none,redesign_4_v1 --output $OUT/sanity.json
$PY -m football_lab campaign run --manifest $OUT/sanity.json --replays $OUT/replays --discard-replays
$PY -m football_lab campaign report --manifest $OUT/sanity.json --json $OUT/sanity_report.json
```

Expected: 40 complete, 0 failed; the pairwise points CI contains 0.

If any fixture fails, or the CI excludes 0, **stop**. Investigate first (non-determinism, a seat or side bias, or a profile not being applied). Do not run the campaigns until it is resolved.

- [ ] **Step 2: Discipline sanity.** In `sanity_report.json`, `discipline` is present for both entrants. Fouls are small because `balanced` never slides; only press fouls apply.

- [ ] **Step 3: Choose the block count `B`**

```bash
$PY -c "import json,statistics as s; r=json.load(open('$OUT/sanity.json'))['results']; m=s.mean(x['duration_ms'] for x in r)/1000; print(f'{m:.1f}s/match; A at 20 blocks = {11*8*40*m/3600:.1f}h')"
```

Use `B=20`. If Campaign A at 20 blocks would take more than 4 h, use `B=10` for C3–C5 and say so in the report. Record `B` and the seconds per match in the report.

---

## Task C3: Campaign A (cross vs pool) and Campaign B (round robin)

- [ ] **Step 1: Campaign A** — 11 challengers × 8 pool entrants × 2·B matches

```bash
$PY -m football_lab campaign init --rules v2 --blocks $B --seed-pool-start 5000 \
  --challengers $ARCH,r4v2_balanced_extcoach --pool $POOL --output $OUT/campaign_a.json
$PY -m football_lab campaign run --manifest $OUT/campaign_a.json --replays $OUT/replays --discard-replays
$PY -m football_lab campaign report --manifest $OUT/campaign_a.json \
  --json $OUT/campaign_a_report.json --baseline r4v2_balanced_none
```

`run` is resumable: it skips completed fixtures and saves after each one. If interrupted, re-run the same command. Expected: 0 failed. Any failed fixture → fix the cause, then re-run (only pending fixtures run).

- [ ] **Step 2: Campaign B** — round robin of the 10 archetype variants (45 pairs × 2·B)

```bash
$PY -m football_lab campaign init --rules v2 --blocks $B --seed-pool-start 5000 \
  --entrants $ARCH --output $OUT/campaign_b.json
$PY -m football_lab campaign run --manifest $OUT/campaign_b.json --replays $OUT/replays --discard-replays
$PY -m football_lab campaign report --manifest $OUT/campaign_b.json --json $OUT/campaign_b_report.json
```

- [ ] **Step 3: Record the leaders**

From `campaign_a_report.json`, `pooled_goal_diff` is sorted by `lo` descending. Write down:
- the top 3 challengers (`TOP3`);
- each entry's `mean`, `lo`, `hi`;
- the baseline deltas;
- discipline per entrant.

---

## Task C4: Foul-rate sensitivity

- [ ] **Step 1: Re-run Campaign A for `TOP3` at two foul scales**

```bash
for FS in 0.5 2.0; do
  $PY -m football_lab campaign init --rules v2 --foul-scale $FS --blocks $B --seed-pool-start 5000 \
    --challengers $TOP3 --pool $POOL --output $OUT/sens_$FS.json
  $PY -m football_lab campaign run --manifest $OUT/sens_$FS.json --replays $OUT/replays --discard-replays
  $PY -m football_lab campaign report --manifest $OUT/sens_$FS.json --json $OUT/sens_${FS}_report.json
done
```

`TOP3` is comma-separated. No `--baseline` is needed here because the decision uses pooled lower bounds only.

- [ ] **Step 2: Apply S2 (the decision rule, verbatim)**

Let `L` be the top `TOP3` entry from Campaign A (highest pooled lower bound).
- **Ship `L`** if `L.lo > 0` at foul scale 1.0, **and** `L` is still first among `TOP3` by pooled lower bound at foul scales 0.5 and 2.0.
- **Otherwise ship balanced:**
  - `r4v2_balanced_rules` if its Campaign A delta vs `r4v2_balanced_none` has `lo > 0`;
  - else `r4v2_balanced_none`.

Record the decision and the numbers behind it. Campaign B's `winner_claim` is supporting evidence only; it never overrides S2.

---

## Task C5: Formation follow-up

- [ ] **Step 1: Run the shipped candidate `S` (from C4) in all three formations**

Formation variant ids add `_f112`, `_f211` or `_f121` to `S`, for the two formations that are not native. For `S = r4v2_park_the_bus_rules` those are `r4v2_park_the_bus_rules_f112` and `r4v2_park_the_bus_rules_f121`.

```bash
$PY -m football_lab campaign init --rules v2 --blocks $B --seed-pool-start 5000 \
  --challengers $S,${S}_fAAA,${S}_fBBB --pool $POOL --output $OUT/formation.json
$PY -m football_lab campaign run --manifest $OUT/formation.json --replays $OUT/replays --discard-replays
$PY -m football_lab campaign report --manifest $OUT/formation.json \
  --json $OUT/formation_report.json --baseline $S
```

Replace `AAA`/`BBB` with the two non-native formation codes.

- [ ] **Step 2: Decide the formation**

Keep the native formation unless a formation variant's delta vs `S` has `lo > 0`. In that case ship the variant with the highest delta `lo`. The formation is fixed for the whole match; it changes only between matches by redeploying the profile.

---

## Task C6: Strategy report, shipped profile, docs, final verification

**Files:** Create `redesign_4/docs/strategy_report.md`, `redesign_4/profiles/recommended.json`; modify `redesign_4/README.md`, `redesign_4/data_flow.md`, `redesign_4/state_machines.md`, `redesign_4/CONTEXT.md` (only if a new term appeared)

- [ ] **Step 1: `docs/strategy_report.md`** — sections, all numbers taken from the JSON reports:

1. **Question and answer summary** (≤ 10 lines): the shipped variant, whether the coach policy helps, whether LangChain adds value, and which "dirty" archetype is best and why.
2. **Setup:**
   - rules v2 constants (link `local_simulator/docs/rules-v2.md`);
   - pool composition;
   - `B`, seeds, seconds per match;
   - the caveat that local results don't establish transfer to the AWS platform (spec §9).
3. **Campaign A table:** variant | pooled GD mean [lo, hi] | delta vs balanced/none [lo, hi] | fouls | penalties conceded | send-offs.
4. **Coach question:** `*_rules` vs `*_none` per archetype (paired deltas from Campaign A), plus `r4v2_balanced_extcoach` (a scripted coach "shouting") vs `r4v2_balanced_rules` (the coach decides inside the agent).
5. **Campaign B:** points table, pairwise CIs and `winner_claim`.
6. **Sensitivity:** the `TOP3` ranking at foul scales 0.5, 1.0 and 2.0.
7. **Formation follow-up.**
8. **Decision** — the S2 rule applied step by step.
9. **LangChain advisor:**
   - what it does (proposal files only, off-tick);
   - an example `pick` invocation against `campaign_a_report.json`;
   - why it stays off the tick path (latency, determinism).
10. **Risks** — spec §10, updated with what was observed.

- [ ] **Step 2: Ship the profile**

```bash
mkdir -p redesign_4/profiles
cp local_simulator/entrants/redesign_4_v2/profiles/<SHIPPED_ID>.json redesign_4/profiles/recommended.json
cd redesign_4 && .venv/bin/python -c "from agent.profile import load_profile; print(load_profile('profiles/recommended.json'))"
```

Expected: prints the parsed `Profile`; it must not raise.

- [ ] **Step 3: End-to-end decision with the shipped profile**

```bash
cd redesign_4 && mkdir -p local
PYTHONPATH=tests .venv/bin/python -c "import json; from golden import payloads; json.dump(payloads(1)[0], open('local/one.json', 'w'))"
.venv/bin/python -m agent decide --input local/one.json --profile profiles/recommended.json 2>/dev/null
```

Expected: stdout is the protocol response for one player, a single valid command. The exit code is 0.

- [ ] **Step 4: Docs**
   - `README.md`:
     - a "Strategy profiles" section: `style` fields, archetypes, `coach_policy`, deploying `profiles/recommended.json` with `AFC_PROFILE`, and `train --base-profile`;
     - an "Advisor (optional)" section: the install extra, `AFC_ADVISOR_MODEL`, `python -m agent.advisor pick|review`, and that proposals are never auto-deployed.
   - `data_flow.md`:
     - under "Live decisions", add that the coach preset comes from the platform or the rules coach, and the profile style shapes `decide`;
     - under "Next-match tactics", add archetype choice, the advisor proposal and operator deployment.
   - `state_machines.md`:
     - under "Runtime decision", add the coach-policy step before `decide`;
     - add a "Strategy profile" lifecycle: archetype → campaign evidence → recommended → deployed; advisor proposal → operator review.
   - `CONTEXT.md`: add a term only if a new one appeared during execution (archetype, style, coach policy and advisor are already defined).

- [ ] **Step 5: Final verification (both suites, lint)**

```bash
cd redesign_4 && .venv/bin/python -m unittest discover -s tests && .venv/bin/python -m ruff check agent tests
cd ../local_simulator && ../.venv/bin/python -m pytest -q tests --ignore=tests/test_viewer.py
```

Expected: everything passes. `test_redesign_4_v2_entrant` confirms the entrant copy still matches `redesign_4/agent`. If it fails because the agent changed after C1, rebuild with `build.py` and commit.

- [ ] **Step 6: Commit**

```bash
git add redesign_4/docs/strategy_report.md redesign_4/profiles/recommended.json redesign_4/README.md \
  redesign_4/data_flow.md redesign_4/state_machines.md
git commit -m "docs(redesign_4): strategy report and recommended profile" -m "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

---

## Spec coverage

| Spec item | Tasks |
|---|---|
| S1 pooled GD CI per variant, pairwise points CIs | A9, A10, C3 |
| S2 ship rule incl. sensitivity | C4, C5, C6 |
| S3 no regression / golden / guard / p99 | A1, A6, B1, B2, B4, B5, B8, C6 step 5 |
| S4 all five players agree on preset | B5 |
| §3 rules v2 | A2–A6, A11 |
| §4 opponent pool + stand-ins | B6, C1, C2 |
| §5 style, archetypes, formations | B2, B4, B6, C1, C5 |
| §6 coach policy + scripted external coach | A7, B5, C1 (`extcoach`) |
| §7 off-tick advisor | B7, B8 |
| §8 experiment protocol | C2–C5 |

