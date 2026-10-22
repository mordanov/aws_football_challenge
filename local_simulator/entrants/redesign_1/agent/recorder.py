"""Match recorder: keeps this runtime's snapshots and flushes them to the store
from a background thread (never on the tick path).

Keys:  matches/<match_id>/p<runtime>.jsonl   one snapshot per line (full rewrite)
       matches/<match_id>/meta-p<runtime>.json   playbook, score, nudges, fingerprint
"""
from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any, Optional

log = logging.getLogger("afc.recorder")

FLUSH_EVERY = 10          # ticks
IDLE_NEW_MATCH_S = 120.0  # wall-clock gap that means "new match"
IDLE_FLUSH_S = 6.0        # no tick for this long -> flush the tail (end of match)


class Recorder:
    def __init__(self, store, runtime: str = "0", clock=time.time, idle_flush: bool = True) -> None:
        self.store, self.runtime, self.clock = store, runtime, clock
        self.match_id: Optional[str] = None
        self.snaps: list[dict] = []
        self.meta: dict[str, Any] = {}
        self._last_t = -1.0
        self._last_wall = 0.0
        self._since_flush = 0
        self._lock = threading.Lock()
        self._pending: Optional[threading.Thread] = None
        self._queued: Optional[tuple] = None
        self._idle_flush = idle_flush
        self._timer: Optional[threading.Timer] = None

    def is_new_match(self, game_time: float) -> bool:
        if self.match_id is None:
            return True
        if game_time + 5.0 < self._last_t:
            return True
        return self.clock() - self._last_wall > IDLE_NEW_MATCH_S

    def start(self, team_id: int, game_time: float = 0.0) -> str:
        """Flush the old match (if any) and open a new one. Returns the new match id.

        The id is stamped with the *estimated kickoff* (now - gameTime), so runtimes
        that join mid-match still produce (almost) the same id as the others.
        """
        if self.match_id is not None:
            self.flush(sync=False)
        kickoff = self.clock() - max(0.0, float(game_time or 0.0))
        stamp = datetime.fromtimestamp(kickoff, timezone.utc).strftime("%Y%m%dT%H%M%S")
        with self._lock:
            self.match_id = f"t{team_id}-{stamp}"
            self.snaps, self.meta = [], {"team_id": team_id, "runtime": self.runtime, "started": stamp}
            self._last_t, self._since_flush = -1.0, 0
        return self.match_id

    def add(self, snap: dict, meta: Optional[dict] = None, urgent: bool = False) -> None:
        """urgent=True (e.g. the last 20 s of a match) flushes on every tick."""
        with self._lock:
            self.snaps.append(snap)
            if meta:
                self.meta.update(meta)
            self._last_t = snap.get("t", self._last_t)
            self._since_flush += 1
            due = urgent or self._since_flush >= FLUSH_EVERY
        self._last_wall = self.clock()
        if due:
            self.flush(sync=False)
        elif self._idle_flush:
            self._arm_idle_timer()

    def _arm_idle_timer(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
        self._timer = threading.Timer(IDLE_FLUSH_S, self._flush_if_dirty)
        self._timer.daemon = True
        self._timer.start()

    def _flush_if_dirty(self) -> None:
        if self._since_flush:
            self.flush(sync=False)

    def flush(self, sync: bool = True) -> None:
        with self._lock:
            if not self.match_id or not self.snaps:
                return
            mid, rt = self.match_id, self.runtime
            body = list(self.snaps)  # serialised in the worker, off the tick path
            meta = dict(self.meta, ticks=len(self.snaps), last_t=self._last_t, updated=self.clock())
            self._since_flush = 0

        job = (mid, rt, body, meta)
        if sync:
            self._write(job)
            return
        with self._lock:
            self._queued = job  # newest payload wins; the worker drains it
            if self._pending is not None:
                return
            self._pending = threading.Thread(target=self._drain, daemon=True)
            self._pending.start()

    def _drain(self) -> None:
        while True:
            with self._lock:
                job, self._queued = self._queued, None
                if job is None:
                    self._pending = None
                    return
            try:
                self._write(job)
            except Exception as e:  # noqa: BLE001
                log.warning("recorder flush failed: %r", e)

    def _write(self, job) -> None:
        mid, rt, snaps, meta = job
        body = "\n".join(json.dumps(s, separators=(",", ":")) for s in snaps)
        self.store.put_text(f"matches/{mid}/p{rt}.jsonl", body)
        self.store.put_json(f"matches/{mid}/meta-p{rt}.json", meta)

    def join(self, timeout: float = 5.0) -> None:
        t = self._pending
        if t is not None:
            t.join(timeout)
