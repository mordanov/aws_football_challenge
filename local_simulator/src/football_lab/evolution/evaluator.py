"""Real evaluator: plays profile-vs-opponent blocks through subprocess shims."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Sequence

from ..cli_campaign import _DEV_BUDGET, _load_entrant_spec, _TeamRunnerEngineAgent
from ..engine import MatchEngine
from ..match_replay import JsonMatchReplayStore
from ..team_runner import RunnerError, TeamRunner
from .contracts import BlockResult, EvaluationError, PoolSpec, Settings
from .mutation import profile_hash
from .pools import pool_seed

PROFILE_SHIM = "entrants/football_agent/profile_shim.py"
INCUMBENT_ID = "football_agent_v1"


def _points(own: int, other: int) -> int:
    return 3 if own > other else (1 if own == other else 0)


class MatchEvaluator:
    def __init__(self, work_dir: Path, evaluator_id: str, keep_replays: bool = False) -> None:
        self._work = Path(work_dir)
        self.evaluator_id = evaluator_id
        self._keep = keep_replays
        self._store = JsonMatchReplayStore()

    def _profile_file(self, settings: Settings) -> Path:
        path = self._work / "profiles" / f"{profile_hash(settings)}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(
            {"version": profile_hash(settings)[:12], "settings": settings, "opponents": None}
        ))
        return path

    def _play(self, seed: int, profile_team: int, settings: Settings,
              opp_spec, replay: Path) -> tuple[int, int]:
        own = {**_load_entrant_spec(INCUMBENT_ID), "shim": PROFILE_SHIM}
        env = {"AFC_PROFILE": str(self._profile_file(settings))}
        specs = (own, opp_spec) if profile_team == 0 else (opp_spec, own)
        envs: tuple = (env, None) if profile_team == 0 else (None, env)
        with (
            TeamRunner(specs[0], 0, _DEV_BUDGET, self._work, env=envs[0]) as r0,  # type: ignore[arg-type]
            TeamRunner(specs[1], 1, _DEV_BUDGET, self._work, env=envs[1]) as r1,  # type: ignore[arg-type]
        ):
            engine = MatchEngine(seed, _TeamRunnerEngineAgent(r0), _TeamRunnerEngineAgent(r1),
                                 self._store)
            frames = engine.run(replay)["frames"]
        g0, g1 = frames[-1]["score"]
        return (g0, g1) if profile_team == 0 else (g1, g0)

    def evaluate(self, settings: Settings, pool: PoolSpec,
                 opponents: Sequence[str]) -> list[BlockResult]:
        out: list[BlockResult] = []
        try:
            for oi, opp in enumerate(opponents):
                spec = _load_entrant_spec(opp)
                for block in range(pool["blocks"]):
                    pts, gd = 0, 0
                    for leg in range(2):
                        seed = pool_seed(pool, oi, block, leg)
                        if self._keep:
                            replay = (self._work / "replays" / pool["name"]
                                      / f"{profile_hash(settings)[:12]}__{opp}__b{block:04d}__l{leg}.json")
                            replay.parent.mkdir(parents=True, exist_ok=True)
                        else:
                            replay = self._work / f"scratch_{seed}.json"
                            replay.unlink(missing_ok=True)
                        own, other = self._play(seed, leg, settings, spec, replay)
                        pts += _points(own, other)
                        gd += own - other
                    out.append({"opponent": opp, "block": block, "points": float(pts),
                                "goal_diff": gd, "legs": 2})
        except (RunnerError, ValueError, OSError) as exc:
            raise EvaluationError(str(exc)) from exc
        return out
