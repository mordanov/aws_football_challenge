"""TeamRunner: manage one shim subprocess for one match lifetime."""
from __future__ import annotations

import collections
import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, Mapping

from football_lab.cmd_decoder import decode_command
from football_lab.entrant_contracts import BudgetSpec, EntrantSpec
from football_lab.match_contracts import EngineState
from football_lab.obs_encoder import encode_observation

_STDERR_RING = 2048  # bytes kept from shim stderr


class RunnerError(Exception):
    pass


class TeamRunner:
    """Manages one team's shim process for one match lifetime."""

    def __init__(
        self,
        spec: EntrantSpec,
        team_id: int,
        budget: BudgetSpec,
        work_dir: Path,
        seed: int = 0,
        env: Mapping[str, str] | None = None,
    ) -> None:
        self._spec = spec
        self._team_id = team_id
        self._budget = budget
        self._work_dir = work_dir
        self._seed = seed
        self._env = dict(env) if env else None
        self._proc: subprocess.Popen[bytes] | None = None
        self._stderr_buf: collections.deque[bytes] = collections.deque(maxlen=_STDERR_RING)
        self._stderr_thread: threading.Thread | None = None

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        shim = Path(self._spec["shim"])
        if not shim.is_absolute():
            # resolve relative to local_simulator root (parent of this file's package)
            shim = Path(__file__).parent.parent.parent / shim
        self._proc = subprocess.Popen(
            [sys.executable, str(shim)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(self._work_dir),
            env={**os.environ, **self._env} if self._env else None,
        )
        self._stderr_thread = threading.Thread(
            target=self._drain_stderr, daemon=True
        )
        self._stderr_thread.start()
        # send initial reset
        self._send({"_command": "reset", "_seq": 0})
        resp = self._read_line(self._budget["startup_timeout_s"])
        if resp is None or not json.loads(resp).get("ok"):
            self.stop()
            raise RunnerError("shim did not acknowledge reset on startup")

    def stop(self) -> None:
        if self._proc is None:
            return
        try:
            if self._proc.stdin and not self._proc.stdin.closed:
                self._send({"_command": "stop", "_seq": 0})
        except (OSError, BrokenPipeError):
            pass
        try:
            self._proc.wait(timeout=self._budget["startup_timeout_s"])
        except subprocess.TimeoutExpired:
            self._proc.kill()
            self._proc.wait()
        self._proc = None

    def is_alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    # ── Decision ──────────────────────────────────────────────────────────────

    def decide(self, state: EngineState, coach: str = "") -> list[dict[str, Any]]:
        if not self.is_alive():
            raise RunnerError("shim process is not running")
        payload = encode_observation(state, self._team_id, list(range(5)), self._seed, coach)
        seq = state["tick"]
        self._send({"obs": payload, "_seq": seq})
        line = self._read_line(self._budget["request_timeout_s"])
        if line is None:
            raise RunnerError("timeout waiting for shim response")
        raw = line.encode() if isinstance(line, str) else line
        if len(raw) > self._budget["output_size_limit"]:
            raise RunnerError(
                f"output size {len(raw)} exceeds limit {self._budget['output_size_limit']}"
            )
        try:
            wire_commands = json.loads(line)
        except json.JSONDecodeError as exc:
            raise RunnerError(f"shim returned invalid JSON: {exc}") from exc
        if not isinstance(wire_commands, list):
            raise RunnerError(f"shim returned {type(wire_commands).__name__}, expected list")
        return [decode_command(c, self._team_id) for c in wire_commands]

    # ── Context manager ───────────────────────────────────────────────────────

    def __enter__(self) -> "TeamRunner":
        self.start()
        return self

    def __exit__(self, *_: Any) -> None:
        self.stop()

    # ── Internal ──────────────────────────────────────────────────────────────

    def _send(self, msg: dict[str, Any]) -> None:
        assert self._proc is not None and self._proc.stdin
        line = (json.dumps(msg) + "\n").encode()
        self._proc.stdin.write(line)
        self._proc.stdin.flush()

    def _read_line(self, timeout: float) -> str | None:
        """Read one line from stdout with timeout. Returns None on timeout or EOF."""
        assert self._proc is not None and self._proc.stdout
        result: list[str] = []
        exc_holder: list[BaseException] = []

        def _reader() -> None:
            try:
                line = self._proc.stdout.readline()  # type: ignore[union-attr]
                if line:
                    result.append(line.decode(errors="replace").rstrip("\n"))
            except Exception as e:  # noqa: BLE001
                exc_holder.append(e)

        t = threading.Thread(target=_reader, daemon=True)
        t.start()
        t.join(timeout)

        if t.is_alive():
            # Timeout — process may still be running
            return None

        if exc_holder:
            raise RunnerError(f"crash reading shim stdout: {exc_holder[0]}")

        if not result:
            # EOF: process exited
            rc = self._proc.poll()
            raise RunnerError(f"shim exited unexpectedly (exit code {rc})")

        return result[0]

    def _drain_stderr(self) -> None:
        assert self._proc is not None and self._proc.stderr
        for chunk in iter(lambda: self._proc.stderr.read(256), b""):  # type: ignore[union-attr]
            self._stderr_buf.append(chunk)
