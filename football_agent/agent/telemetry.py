"""Bounded, best-effort logging; never a prerequisite for delivering a command."""

from __future__ import annotations

import time
from collections.abc import Callable
from queue import Empty, Full, Queue
from threading import Condition, Event, Thread


class DecisionLog:
    def __init__(self, emit: Callable[[dict], None]):
        self._emit = emit
        self._queue: Queue[dict] = Queue(maxsize=128)
        self._condition = Condition()
        self._closed = Event()
        self._pending = 0
        self._dropped = 0
        self._failed = 0
        self._last_error: str | None = None
        self._worker = Thread(target=self._run, name="football-decision-log", daemon=True)
        self._worker.start()

    @property
    def status(self) -> dict:
        with self._condition:
            return {
                "pending": self._pending,
                "dropped": self._dropped,
                "failed": self._failed,
                "last_error": self._last_error,
            }

    def submit(self, record: dict) -> None:
        with self._condition:
            if self._closed.is_set():
                self._dropped += 1
                return
            record = {
                **record,
                "telemetry": {
                    "dropped": self._dropped,
                    "failed": self._failed,
                    "last_error": self._last_error,
                },
            }
            try:
                self._queue.put_nowait(record)
            except Full:
                self._dropped += 1
            else:
                self._pending += 1

    def _run(self) -> None:
        while not self._closed.is_set() or not self._queue.empty():
            try:
                record = self._queue.get(timeout=0.05)
            except Empty:
                continue
            try:
                self._emit(record)
            except Exception as exc:
                # Isolate the external sink only; every failure remains observable.
                with self._condition:
                    self._failed += 1
                    self._last_error = type(exc).__name__
            finally:
                with self._condition:
                    self._pending -= 1
                    self._condition.notify_all()
                self._queue.task_done()

    def flush(self, timeout: float = 1.0) -> bool:
        with self._condition:
            return self._condition.wait_for(lambda: self._pending == 0, timeout=timeout)

    def close(self, timeout: float = 1.0) -> bool:
        start = time.monotonic()
        with self._condition:
            self._closed.set()
        drained = self.flush(timeout)
        self._worker.join(max(0, timeout - (time.monotonic() - start)))
        return drained and not self._worker.is_alive()
