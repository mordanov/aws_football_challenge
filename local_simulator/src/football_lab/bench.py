"""Throughput benchmark: python -m football_lab bench

Runs 10 complete stub-agent matches headlessly (in-memory store).
Reports: ticks/s, wall-clock/match (ms), replay size (KB), peak RSS (MB).
"""
from __future__ import annotations

import resource
import time
from pathlib import Path

from football_lab.engine import MatchEngine, StubAgent
from football_lab.match_contracts import MatchReplay


class _NullStore:
    def save(self, replay: MatchReplay, path: Path) -> None:
        pass  # discard; measure engine time only


def main() -> None:
    N = 10
    times: list[float] = []
    sizes: list[int] = []

    for i in range(N):
        store = _NullStore()
        t0 = time.perf_counter()
        replay = MatchEngine(
            seed=i,
            team0_agent=StubAgent(0),
            team1_agent=StubAgent(1),
            store=store,
        ).run(Path(f"bench_{i}.json"))
        elapsed = time.perf_counter() - t0
        times.append(elapsed)
        import json
        sizes.append(len(json.dumps(replay).encode()))

    total_ticks = N * 2400
    total_time = sum(times)
    ticks_per_sec = total_ticks / total_time
    mean_ms = (total_time / N) * 1000
    mean_kb = sum(sizes) / N / 1024
    rss_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024 * 1024)

    print(f"bench.ticks_per_sec={ticks_per_sec:.0f}")
    print(f"bench.mean_wall_ms={mean_ms:.1f}")
    print(f"bench.mean_replay_kb={mean_kb:.1f}")
    print(f"bench.peak_rss_mb={rss_mb:.1f}")
