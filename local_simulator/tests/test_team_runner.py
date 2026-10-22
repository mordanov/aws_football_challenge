"""Tests for TeamRunner: shim process management and encode/decode pipeline."""
import json
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest

from football_lab.entrant_contracts import BudgetSpec, EntrantSpec
from football_lab.team_runner import RunnerError, TeamRunner


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_state(tick: int = 0) -> dict:
    players = []
    for pid in range(5):
        for tid in range(2):
            players.append({
                "team_id": tid, "player_id": pid,
                "x": float(-40 + pid * 10 * (1 - 2 * tid)),
                "y": 0.0, "vx": 0.0, "vy": 0.0, "stamina": 1.0,
            })
    return {
        "tick": tick, "play_mode": "OPEN_PLAY", "mode_team": None,
        "players": players,
        "ball": {"x": 0.0, "y": 0.0, "z": 0.0,
                  "vx": 0.0, "vy": 0.0, "vz": 0.0,
                  "owner_team": None, "owner_player": None, "in_flight": False},
        "score": [0, 0],
    }


def _stub_budget() -> BudgetSpec:
    return {"startup_timeout_s": 5.0, "request_timeout_s": 5.0, "output_size_limit": 65536}


def _tight_budget(**overrides) -> BudgetSpec:
    b = _stub_budget()
    b.update(overrides)  # type: ignore[arg-type]
    return b


def _write_shim(tmp_path: Path, name: str, body: str) -> Path:
    p = tmp_path / name
    p.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        + body
    )
    p.chmod(0o755)
    return p


def _stub_spec(shim_path: Path, team_id: int = 0) -> EntrantSpec:
    return {
        "id": "stub_v1",
        "source_name": "stub",
        "topology": "per_player",
        "shim": str(shim_path),
        "entrant_hash": "0" * 64,
        "rules_version": "v1",
        "track": "patched",
    }


STUB_SHIM_BODY = textwrap.dedent("""\
    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        msg = json.loads(raw)
        if msg.get("_command") == "stop":
            break
        if msg.get("_command") == "reset":
            print(json.dumps({"ok": True}), flush=True)
            continue
        obs = msg["obs"]
        tid = obs["teamId"]
        cmds = [{"commandType": "SET_STANCE", "playerId": i, "teamId": tid,
                  "parameters": {"stance": 0}} for i in range(5)]
        print(json.dumps(cmds), flush=True)
""")

SLOW_SHIM_BODY = textwrap.dedent("""\
    import time
    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        msg = json.loads(raw)
        if msg.get("_command") == "stop":
            break
        if msg.get("_command") == "reset":
            print(json.dumps({"ok": True}), flush=True)
            continue
        time.sleep(10)
        print(json.dumps([]), flush=True)
""")

BIG_SHIM_BODY = textwrap.dedent("""\
    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        msg = json.loads(raw)
        if msg.get("_command") == "stop":
            break
        if msg.get("_command") == "reset":
            print(json.dumps({"ok": True}), flush=True)
            continue
        print("x" * 200000, flush=True)
""")

CRASH_SHIM_BODY = textwrap.dedent("""\
    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        msg = json.loads(raw)
        if msg.get("_command") == "reset":
            print(json.dumps({"ok": True}), flush=True)
            continue
        sys.exit(1)
""")


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_start_spawns_process(tmp_path):
    shim = _write_shim(tmp_path, "stub_shim.py", STUB_SHIM_BODY)
    spec = _stub_spec(shim)
    runner = TeamRunner(spec, team_id=0, budget=_stub_budget(), work_dir=tmp_path)
    runner.start()
    assert runner.is_alive()
    runner.stop()


def test_decide_returns_five_commands(tmp_path):
    shim = _write_shim(tmp_path, "stub_shim.py", STUB_SHIM_BODY)
    with TeamRunner(_stub_spec(shim), team_id=0, budget=_stub_budget(), work_dir=tmp_path) as runner:
        cmds = runner.decide(_make_state())
    assert len(cmds) == 5
    assert all(c["team_id"] == 0 for c in cmds)


def test_decide_returns_engine_format(tmp_path):
    shim = _write_shim(tmp_path, "stub_shim.py", STUB_SHIM_BODY)
    with TeamRunner(_stub_spec(shim), team_id=1, budget=_stub_budget(), work_dir=tmp_path) as runner:
        cmds = runner.decide(_make_state())
    for c in cmds:
        assert "command_type" in c
        assert "player_id" in c


def test_stop_terminates(tmp_path):
    shim = _write_shim(tmp_path, "stub_shim.py", STUB_SHIM_BODY)
    runner = TeamRunner(_stub_spec(shim), team_id=0, budget=_stub_budget(), work_dir=tmp_path)
    runner.start()
    runner.stop()
    assert not runner.is_alive()


def test_request_timeout(tmp_path):
    shim = _write_shim(tmp_path, "slow_shim.py", SLOW_SHIM_BODY)
    with TeamRunner(_stub_spec(shim), team_id=0,
                    budget=_tight_budget(request_timeout_s=0.2), work_dir=tmp_path) as r:
        with pytest.raises(RunnerError, match="timeout"):
            r.decide(_make_state())


def test_output_size_limit(tmp_path):
    shim = _write_shim(tmp_path, "big_shim.py", BIG_SHIM_BODY)
    with TeamRunner(_stub_spec(shim), team_id=0,
                    budget=_tight_budget(output_size_limit=100), work_dir=tmp_path) as r:
        with pytest.raises(RunnerError, match="output"):
            r.decide(_make_state())


def test_process_crash(tmp_path):
    shim = _write_shim(tmp_path, "crash_shim.py", CRASH_SHIM_BODY)
    with TeamRunner(_stub_spec(shim), team_id=0, budget=_stub_budget(), work_dir=tmp_path) as r:
        with pytest.raises(RunnerError, match="crash|exit|EOF"):
            r.decide(_make_state())
