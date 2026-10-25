# CloudWatch Strategy Tuning — 2026-10-25

Branch: `tuning/cloudwatch-v2`  
Profile baseline: `r4v7_route_stamina`

---

## Matches analyzed (last 24h)

77 matches discovered. Detailed analysis of top 10 by recency.

| Match | Result | Score | Poss% | PASS | SHOOT | First goal |
|-------|--------|-------|-------|------|-------|------------|
| 1238d7c7 | L | 0:1 | 70% | 0 | 0 | 7.7s |
| 3ad59ba3 | L | 0:2 | 59% | 11 | 1 | 14.2s |
| 6d6e3c69 | L | 0:1 | 55% | 2 | 1 | 55.8s |
| ddc52122 | **W** | 2:1 | 59% | 16 | 2 | 86.1s (comeback) |
| b603459b | L | 2:4 | 67% | 32 | 2 | 10.5s |
| d4eb4df0 | L | 0:1 | 65% | 14 | 1 | — |
| 62aea512 | **W** | 1:0 | 66% | 15 | 5 | — |
| 8eec2b89 | **W** | 1:0 | 67% | 13 | 2 | — |
| 91c5248d | L | 0:1 | 50% | 12 | 0 | — |

Win rate: **3/9 = 33%**

---

## Step 5: Hypotheses ranked

### H1 (CONFIRMED PRIMARY): pass_margin=3.0 blocks all passes under tight marking

**Evidence:**
- We have 50–70% possession in every match — we control the ball
- Yet in most losses: 0–2 PASS events per match (full duration)
- Early period (0–25s) analysis: poss_ours=20–30 decisions, but 0–1 PASS events
- In match 1: 20 carrier decisions in first 25s, **0 passes**, all MOVE_TO

**Mechanism:**
`safe = min(lane, pressure) >= settings.pass_margin`  
When opponents man-mark all 4 outfield players within 2–3m, `pressure < 3.0` blocks every pass. With no safe passes and no shot available (outside 32m), carrier loops on MOVE_TO (carry). The defence never disengages — we carry, they track, we carry more. No shots, no goals.

In 5v5 with 4 active field players per side, tight 1v1 marking (2–3m) is easily sustained. The 3m threshold leaves zero passing options in that shape.

**Pass rate per carrier decision (early period):**
- Losses: 0%–14% (typically 3–5%)
- Win (match 4): 13% + 1 SHOOT early on

**Wins correlate with more early passing:** when passes succeed (loose marking or free-ball transitions), the team creates shots and converts.

### H2 (NOT CONFIRMED): Rush creating early defensive gaps

**Evidence:** deep_analysis shows `rush_bad_instances = 0` in all 5 detailed matches. Rush is correctly suppressed (ball in own half → rush off). Rush activates only 5–22 ticks in the first 25s (vs. 50+ suppressed), meaning opponents typically have possession in the early window.

**Verdict:** Rush is NOT causing early goals. No change needed.

### H3 (SECONDARY): Low shot generation

**Consequence, not root cause.** Shots are rare because the ball never reaches FWDs in range (~23m from goal). Root cause is the same: no passes reaching forwards.

---

## Step 6: Implementation

**Single targeted change: `pass_margin` 3.0 → 2.0 in `profiles/recommended.json`**

Reduces the minimum clearance and receiver-pressure threshold from 3m to 2m. A receiver 2m from the nearest opponent can still control a quick pass before pressure arrives — this is standard football mechanics.

Expected effects:
- Passes become available when opponents are 2–3m from receivers (currently 0 safe passes in that range)
- PASS events should increase significantly in losses where possession is high
- Ball reaches FWDs in attacking positions → more SHOOT opportunities
- Higher goal conversion, lower 0:1 loss rate

No change to rush_s (hypothesis H2 rejected by data).  
No change to shot_range (not blocking shots — the ball just doesn't reach shooters).

---

## Step 7: Validation

Core test suite (107 tests, 208 subtests): **all pass** before and after the change.

```
tests/test_rush_tactics.py
tests/test_style_tactics.py
tests/test_runtime.py
tests/test_coach_policy.py
tests/test_consolidation.py
→ 107 passed, 208 subtests passed
```

`test_consolidation.py:241` checks `trainer.train().settings.pass_margin == 3.0`. This tests the
learning system's default output, not the deployed profile — it still passes correctly.

No tests load `recommended.json` and assert specific parameter values.

---

## Step 8: Diff

```diff
--- a/final_agent/profiles/recommended.json
+++ b/final_agent/profiles/recommended.json
@@ -3,7 +3,7 @@
   "settings": {
-    "pass_margin": 3.0,
+    "pass_margin": 2.0,
```

**Status: change is local, not committed, not pushed, not deployed.**  
Review and deploy when ready.

---

## Limitations and risks

- **Increased interception risk**: passes to 2m-marked players can be intercepted more easily. If opponent positioning is tight AND fast-reacting, a bad pass may become a counter-attack.
- **Insufficient data for A/B validation**: no local simulator to measure exact win-rate delta before deployment. Prediction is based on the pass-blocking mechanism analysis.
- **Only 9 matches sampled**: small sample. Result could have confounding variables (opponent skill distribution).
- **Early goals (7.7s, 10.5s, 14.2s) cause unconfirmed**: rush is not the cause, but the exact counter-attack mechanism in those first seconds wasn't deep-dived. May be opponent-skill floor, not fixable via pass_margin.
