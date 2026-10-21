# M2 Engine Throughput — 2026-10-21

10 complete stub-agent matches (2400 ticks each), headless in-memory store.
Platform: macOS, Python 3.12, Apple Silicon (M-series).

| Metric | Value |
|---|---|
| `bench.ticks_per_sec` | 64 548 |
| `bench.mean_wall_ms` | 37.2 ms/match |
| `bench.mean_replay_kb` | 2 672 KB/match |
| `bench.peak_rss_mb` | 64.0 MB |

One 120-second simulated match completes in ~37 ms wall-clock.
At 64 k ticks/s the engine can saturate a 120s match ~27× faster than real time.
Replay files are ~2.6 MB each (all 2401 frames, full player state).
