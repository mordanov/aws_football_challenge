# Deployment Receipt — 2026-10-24

## Source

- Commit: `3ab82c5` (Merge pull request #21 from mordanov/cloudwatch-tuning)
- Branch: `main`
- Worktree: `.worktrees/final-agent-s3`

## Changes Deployed

Three tactical improvements from CloudWatch analysis (PR #21 / commit `da93b8a`):

1. **FWD midfield hold during GK possession** — FWDs (pid 3,4) target x≥0 when own GK has ball; previously retreated to own half.
2. **Longer shot range** — `shot_range` raised from 29→32m; profile `recommended.json` updated to match.
3. **1v1 penalty area shots** — lane threshold lowered from 1.8→1.2 when gap<15m.

## Runtimes (unchanged ARNs)

| Seat | Name    | ARN (suffix)                          | Pre v | Post v |
|------|---------|---------------------------------------|-------|--------|
| GK   | r4_gk   | `…/Redesign4_default_r4_gk-nxusigFggA`   | 11    | **12** |
| DEF  | r4_def  | `…/Redesign4_default_r4_def-t1Kh1c7eeZ`  | 11    | **12** |
| MID  | r4_mid  | `…/Redesign4_default_r4_mid-Nq5Hfb67re`  | 11    | **12** |
| FWD  | r4_fwd1 | `…/Redesign4_default_r4_fwd1-7vRt5lCgqV` | 11    | **12** |
| FWD  | r4_fwd2 | `…/Redesign4_default_r4_fwd2-syVGGx7Rzk` | 11    | **12** |

All five DEFAULT endpoints: `READY`, `liveVersion=12`.

## Profile Deployed

```json
{
  "settings": {
    "pass_margin": 3.0,
    "press_radius": 22.0,
    "shot_range": 32.0,
    "support_width": 15.0
  },
  "style": { "attackers_committed": 3, "formation": "1-1-2", "tempo": 0.5, ... },
  "version": "r4v7_route_stamina"
}
```

## Rollout Path

Compatible same-namespace rollout (CODE_GENERATION=`match_profiles_tactics_v3` unchanged).
No new team/registry namespace. Learning stack `final-agent-s3-tactics-v3` preserved.

## Learning State

- Stack: `final-agent-s3-tactics-v3`
- Team: `aleksalotl_tactics_v3`
- `AFC_LEARNING_ENABLED`: 1 (collection and learning active)
- `AFC_PROMOTION_NOT_BEFORE_MS`: **1792867671481** (2026-10-24T18:47:51Z UTC)
  - Updated from 1792856284078 (v11 guard, already expired)
  - Guard = v12 lastUpdatedAt (10:45:51Z) + maxLifetime (28800s) + safety (120s)

## Limitations

- Existing live sessions may retain v11 code until their session expires (maxLifetime=28800s).
- Promotion guard prevents any profile promotion until 2026-10-24T18:47:51Z UTC.
- Diagnostic probes not run (reserved UUID: `6dc3a574-299e-44e4-81aa-6eb36c5776ef`).
