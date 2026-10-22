"""AgentCore entrypoint + the per-runtime 'brain'.

Hot path per invocation (no network I/O):
  parse -> (new match? swap playbook) -> scout nudges -> decide -> validate -> wire
  -> snapshot -> scout.observe -> recorder.add (background flush)
Learning happens between matches in trainer/; the runtime only *reads* the
active playbook and dossier index (background refresh) and *writes* recordings.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Any, Optional

from . import commands as C
from . import events as E
from . import tactics as T
from .playbook import Playbook
from .policy import decide
from .recorder import Recorder
from .scout import Scout, fp_distance
from .store import default_store
from .wire import parse_payload, parse_world, to_wire

log = logging.getLogger("afc.agent")

REFRESH_S = 60.0
DOSSIER_AT_S = 30.0
DOSSIER_MAX_DIST = 0.25
BUDGET_S = 0.40
EARLY_SWAP_S = 6.0   # a playbook loaded just after kickoff may still be applied
URGENT_LAST_S = 20.0  # flush the recording every tick near full time


class Brain:
    def __init__(self, store=None, runtime: Optional[str] = None, clock=time.time, background: bool = True) -> None:
        self.store = store if store is not None else default_store()
        self.clock = clock
        self.runtime = runtime or os.environ.get("MY_PLAYER_ID", "0")
        self.recorder = Recorder(self.store, self.runtime, clock)
        self.scout = Scout()
        self.base = Playbook()
        self.pending: Optional[Playbook] = None   # loaded in background, applied at kickoff
        self.dossiers: list[dict] = []
        self.dossier_done = False
        self.dossier_key: Optional[str] = None
        self.background = background
        self._last_refresh = -1e9
        self._refreshing = False
        if background:
            self._maybe_refresh()  # load active.json at container start, before the first tick

    # ------------------------------------------------------------ refresh
    def refresh(self) -> None:
        """Load active playbook + dossier index. Blocking; call from a thread."""
        try:
            act = self.store.get_json("active.json")
            if isinstance(act, dict) and isinstance(act.get("playbook"), dict):
                pb = Playbook.from_json(act["playbook"])
                same = (pb.name, pb.version) == (self.base.name, self.base.version)
                self.pending = None if same else pb
            idx = self.store.get_json("dossiers/index.json")
            if isinstance(idx, list):
                self.dossiers = [d for d in idx if isinstance(d, dict) and isinstance(d.get("fp"), list)]
        except Exception as e:  # noqa: BLE001
            log.warning("refresh failed: %r", e)
        finally:
            self._refreshing = False

    def _maybe_refresh(self) -> None:
        now = self.clock()
        if self._refreshing or now - self._last_refresh < REFRESH_S:
            return
        self._last_refresh, self._refreshing = now, True
        if self.background:
            threading.Thread(target=self.refresh, daemon=True).start()
        else:
            self.refresh()

    # ---------------------------------------------------------- match life
    def _new_match(self, team_id: int, game_time: float) -> None:
        if self.pending is not None:
            log.info("kickoff: switching playbook %s v%s -> %s v%s",
                     self.base.name, self.base.version, self.pending.name, self.pending.version)
            self.base, self.pending = self.pending, None
        self.scout.reset()
        self.dossier_done, self.dossier_key = False, None
        mid = self.recorder.start(team_id, game_time)
        log.info("new match %s playbook=%s v%s", mid, self.base.name, self.base.version)

    def _apply_dossier(self) -> None:
        self.dossier_done = True
        fp = self.scout.fingerprint()
        if not fp or not self.dossiers:
            return
        best = min(self.dossiers, key=lambda d: fp_distance(fp, d["fp"]))
        if fp_distance(fp, best["fp"]) < DOSSIER_MAX_DIST:
            self.dossier_key = best.get("key")
            self.scout.set_extra(best.get("nudges") or {})
            log.info("dossier match %s -> pre-nudges %s", self.dossier_key, best.get("nudges"))

    # ---------------------------------------------------------------- tick
    def handle(self, payload: Any, default_pid: Optional[int] = None) -> list[dict]:
        t0 = time.perf_counter()
        team_id, pids = 0, [default_pid if default_pid is not None else 0]
        try:
            gs, team_id, pids = parse_payload(payload, default_pid)
            w0 = parse_world(gs, team_id, pids[0])
        except Exception as e:  # noqa: BLE001
            log.warning("unparseable payload: %r", e)
            return [dict(C.set_stance(p, 0), teamId=team_id) for p in pids]

        pb = self.base
        try:
            self._maybe_refresh()
            if self.recorder.is_new_match(w0.game_time):
                self._new_match(team_id, w0.game_time)
            elif self.pending is not None and w0.game_time <= EARLY_SWAP_S:
                log.info("early swap to %s v%s", self.pending.name, self.pending.version)
                self.base, self.pending = self.pending, None
                self.recorder.meta.update(playbook=self.base.name, version=self.base.version)
            if not self.dossier_done and w0.game_time >= DOSSIER_AT_S:
                self._apply_dossier()
            pb = self.base.with_nudges(self.scout.nudges())
        except Exception as e:  # noqa: BLE001
            log.warning("match bookkeeping failed: %r", e)

        out, cmds, whys = [], {}, {}
        for pid in pids:
            try:
                w = w0 if pid == pids[0] else parse_world(gs, team_id, pid)
                cmd, why = self._decide_safe(w, pb)
                cmds[pid], whys[pid] = cmd, why
                out.append(to_wire(cmd, w))
            except Exception as e:  # noqa: BLE001
                log.warning("p%s: %r", pid, e)
                out.append(dict(C.set_stance(pid, 0), teamId=team_id))

        try:
            snap = E.snapshot(w0, cmds)
            evs = self.scout.observe(snap)
            self.recorder.add(snap, meta={
                "playbook": self.base.name, "version": self.base.version,
                "score": [w0.score_us, w0.score_them], "nudges": self.scout.active(),
                "dossier": self.dossier_key, "fp": self.scout.fingerprint(),
            }, urgent=w0.game_time >= w0.match_len - URGENT_LAST_S)
        except Exception as e:  # noqa: BLE001
            evs = []
            log.warning("record failed: %r", e)

        ms = (time.perf_counter() - t0) * 1000
        log.info("DECISION %s", json.dumps({
            "t": w0.game_time, "k": w0.tick, "pb": f"{self.base.name}:{self.base.version}",
            "why": whys, "ev": [e["type"] for e in evs], "nudges": self.scout.active(), "ms": round(ms, 2),
        }))
        return out

    def _decide_safe(self, w, pb: Playbook) -> tuple[dict, str]:
        try:
            d = decide(w, pb, self.base)
            if C.validate(d.cmd):
                return d.cmd, d.why
            log.warning("invalid command %s", d.cmd)
        except Exception as e:  # noqa: BLE001
            log.warning("policy error p%s: %r", w.me.id, e)
        try:
            x, y = T.anchor(w, w.me.id, self.base, "build")
            return C.move_to(w.me.id, x, y, False), "fallback anchor"
        except Exception:  # noqa: BLE001
            return C.set_stance(w.me.id, 0), "fallback stance"


_brain: Optional[Brain] = None


_brain_lock = threading.Lock()


def brain() -> Brain:
    global _brain
    with _brain_lock:
        if _brain is None:
            _brain = Brain(background=False)
    return _brain


def _env_pid() -> Optional[int]:
    v = os.environ.get("MY_PLAYER_ID")
    return int(v) if v not in (None, "") else None


def handle(payload: Any, default_pid: Optional[int] = None) -> list[dict]:
    return brain().handle(payload, default_pid if default_pid is not None else _env_pid())


try:  # AgentCore runtime (absent in tests / local runs)
    from bedrock_agentcore.runtime import BedrockAgentCoreApp

    app = BedrockAgentCoreApp()
    brain()  # warm up: starts loading the trained playbook before the first tick

    @app.entrypoint
    async def invoke(payload, context=None):
        yield json.dumps(handle(payload))
except ImportError:  # pragma: no cover
    app = None


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if app is None:
        raise SystemExit("bedrock_agentcore not installed")
    app.run()
