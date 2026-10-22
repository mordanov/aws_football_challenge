# M5 — Tactical-profile evolution

Bounded Gaussian mutation search over redesign_4's four tactical settings
(`pass_margin`, `shot_range`, `press_radius`, `support_width`), evaluated against
four frozen opponents under rules-v1.

## What it does

- Mutates one incumbent profile into 12-member populations for up to 10 generations
- Each generation scored against `initial_idea_v1`, `redesign_1_v1`,
  `redesign_2_v1`, `redesign_3_v1`, and `redesign_4_v1` on a disjoint training pool
- Top challenger vs incumbent on a disjoint selection pool; only the best challenger
  is evaluated (ties favour the incumbent)
- If a finalist is found, a final-test lock is created and the finalist vs incumbent
  are evaluated on a 100-block final pool
- Paired bootstrap (95% aggregate CI, 99% per-opponent Bonferroni) decides promotion
- −0.10 non-regression margin per opponent

## Seed pools

| Pool | Start seed | Blocks | Notes |
|------|-----------|--------|-------|
| training | 100,000 | 8 | search loop |
| selection | 200,000 | 20 | finalist gating |
| final | 300,000 | 100 | promotion decision |

Pools are disjoint from each other and from the Stage 1 scored range (checked at
`init` time).

## Integrity

All five entrant directories (including redesign_4 itself) are SHA-256 hashed at
`init` time and re-verified before `run` and before opening the final pool.

## No model weights

This is a **bounded profile search**. No model weights are trained or modified.
The search space is the four numeric settings within their frozen validator bounds.

## CLI

```sh
# Initialise experiment directory
python -m football_lab evolve init \
    --dir <exp_dir> \
    --stage1-manifest <stage1/manifest.json> \
    --experiment-seed <int>

# Run full search → selection → (optional) final test
python -m football_lab evolve run --dir <exp_dir>

# Render report.md
python -m football_lab evolve report --dir <exp_dir>
```

### Outputs

| File | Created by | Content |
|------|-----------|---------|
| `manifest.json` | `init` | experiment config, pools, frozen hashes |
| `evaluations.jsonl` | `run` | evaluation cache (append-only) |
| `search.json` | `run` | training fitness, lineage |
| `selection.json` | `run` | finalist selection result |
| `final.lock.json` | `run` | final-test lock (absent when incumbent retained) |
| `final.json` | `run` | promotion decision |
| `report.md` | `report` | human-readable summary |
