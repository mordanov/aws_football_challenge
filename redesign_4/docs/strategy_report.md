# Strategy report: coach, custom SDKs, and "dirty" play (redesign_4)

Spec: `docs/superpowers/specs/2026-10-22-coach-and-strategy-design.md` (rev 4).
All numbers come from campaign reports in `local_simulator/local/r4v2/`. These files
are git-ignored; regenerate them with the commands in §2.

## 1. Question and answer

- **Shipped:** `r4v2_direct_carry_none`, i.e. the `direct_carry` archetype with
  formation `1-1-2` and `coach_policy: none`. It is saved as
  `profiles/recommended.json` and deployed with `AFC_PROFILE`.
- **Coach shouting does not help.** The rule-based coach policy never beat the same
  archetype without it. Its effect was at best zero and slightly negative for
  `direct_carry`. An external scripted coach "shouting" the same presets is exactly
  equivalent to the in-agent policy.
- **Custom SDKs (LangChain) are useful off the tick path only.** They work as an
  advisor that writes proposal files between matches. They are never in the live
  decision loop, for reasons of latency, determinism and failure isolation.
- **Best "dirty" style: none of them.** The winners are **attacking with the ball**:
  carrying/dribbling, then high pressing.
  - Hard tackling is neutral on goal difference and costs about 1 send-off per
    10 matches.
  - Parking the bus is no better than balanced.
  - Time-wasting loses.
- **Formation:** keep `1-1-2`. `1-2-1` looks slightly better, but the difference is
  not significant.

## 2. Setup

- **Simulator:** `local_simulator`, rules **v2**. Constants and rationale are in
  `local_simulator/docs/rules-v2.md`:
  - `p_foul` 0.25 ×2 from behind (within 60°), `p_win_slide` 0.55,
    `p_win_press_step` 0.01;
  - 40-step stun; sent off after 3 fouls;
  - penalty box 16.5 × 40 m, `p_penalty_goal` 0.75.
  - Only `SLIDE_TACKLE` can foul; `PRESS_BALL` never does. Plan text saying "only
    press fouls apply" was wrong.
- **v2 ball physics we added during this work** (§10, R4):
  - `SHOOT` honours `aim_location`: corners aim at y = ±3.5.
  - A player cannot collect an in-flight ball that is moving away from them, so a
    passer no longer recollects their own pass.
- **Opponent pool (8):**
  - the frozen v1 entrants `initial_idea_v1`, `redesign_1_v1` … `redesign_4_v1`;
  - three AWS-practice stand-ins, `r4v2_aws_like_{balanced,attacking,defensive}`.
- **Budget:** 20 seed blocks × 2 legs (sides swapped) = 40 matches per pairing.
  Seeds start at 5000. All intervals are 95% block-bootstrap unless marked 99.5%.

| Run | Fixtures | Failed | s/match | Goals/match |
|---|---|---|---|---|
| Sanity (`balanced_none` vs `redesign_4_v1`) | 40 | 0 | 0.69 | 0.00 |
| Campaign A (13 challengers × pool) | 4160 | 0 | 0.55 | 0.92 |
| Campaign B (12-variant round robin) | 2640 | 0 | 0.64 | 1.01 |
| Sensitivity, foul_scale 0.5 / 2.0 | 960 / 960 | 0 / 0 | 0.55 | 1.81 / 1.78 |
| Formation follow-up | 960 | 0 | 0.61 | 1.93 |

The sanity mirror ended 0-0 in all 40 matches, so its CI is [0, 0] and contains 0
as required.

Regenerate, from `local_simulator/`:
`PYTHONPATH=src ../.venv/bin/python -m football_lab campaign init|run|report`.

> **Caveat (spec §9):** these are local results under guessed v2 constants. They do
> not establish transfer to the AWS platform.

### Why earlier runs had no goals

Every match in the first sanity run was goalless, and so was the main checkout's
2000-match M-series benchmark. We found three causes:

1. `SHOOT` ignored its aim and always went to the goal centre, where a centred
   keeper caught everything.
2. A 1.5 m/step pass was still inside the 2.1 m pickup radius one step after
   release, so the passer took it back.
3. The redesign_4 and redesign_2 carriers had no dribble option (only shoot, a
   "safe" pass or hold), so under pressure they held the ball until tackled.

Causes 1–2 were fixed in rules v2 only; v1 is byte-identical and its golden hash is
unchanged. Cause 3 was fixed with the `style.carry` option. Its default is 0.0, so
the default profile still reproduces pre-change decisions.

## 3. Campaign A: each variant vs the 8-team pool

The baseline is `r4v2_balanced_none`. Fouls and send-offs are committed by the
variant. Penalties conceded are the opponents' penalties awarded in the variant's
matches. Each row covers 320 matches.

| Variant | Pooled GD/match [lo, hi] | Δ vs balanced/none [lo, hi] | Fouls | Pens conceded | Send-offs |
|---|---|---|---|---|---|
| `direct_carry_none` | **+1.087 [+0.906, +1.275]** | +1.253 [+1.062, +1.453] | 0 | 0 | 0 |
| `direct_carry_rules` | +1.038 [+0.866, +1.212] | +1.203 [+1.028, +1.387] | 0 | 0 | 0 |
| `press_aggressive_none` | +0.938 [+0.738, +1.137] | +1.103 [+0.916, +1.294] | 157 | 7 | 6 |
| `press_aggressive_rules` | +0.884 [+0.694, +1.072] | +1.050 [+0.866, +1.234] | 150 | 7 | 7 |
| `hard_tackle_rules` | +0.000 [−0.138, +0.131] | +0.166 [+0.047, +0.287] | 413 | 1 | 34 |
| `hard_tackle_none` | +0.000 [−0.153, +0.150] | +0.166 [+0.034, +0.300] | 414 | 1 | 34 |
| `park_the_bus_none` | −0.166 [−0.250, −0.084] | +0.000 [−0.109, +0.113] | 81 | 3 | 5 |
| `park_the_bus_rules` | −0.172 [−0.256, −0.091] | −0.006 [−0.113, +0.100] | 84 | 3 | 5 |
| `balanced_none` | −0.166 [−0.269, −0.069] | baseline | 0 | 0 | 0 |
| `balanced_extcoach` | −0.181 [−0.287, −0.081] | −0.016 [−0.041, +0.009] | 0 | 0 | 0 |
| `balanced_rules` | −0.181 [−0.287, −0.081] | −0.016 [−0.041, +0.009] | 0 | 0 | 0 |
| `time_waster_none` | −0.484 [−0.606, −0.372] | −0.319 [−0.425, −0.216] | 675 | 13 | 98 |
| `time_waster_rules` | −0.500 [−0.625, −0.384] | −0.334 [−0.447, −0.228] | 676 | 13 | 98 |

`direct_carry_none` record: W191 D102 L27, GF 471, GA 123.

Pool context:
- `r4v2_aws_like_attacking` was the strongest team in the campaign (+526 GD over
  520 matches). It both carries (1.0) and slide-tackles (411 fouls, 36 send-offs).
- `redesign_1_v1` (−22 GD) committed 959 fouls and 163 send-offs.

Interpretation of the "dirty" styles:
- **Hard tackling** wins the ball but gives fouls back, so its net goal difference
  is ≈ 0. It still edges balanced, because balanced cannot convert possession.
- **Parking the bus** concedes little (GA 108) but almost never scores (GF 55), so
  it is equivalent to balanced.
- **Time-wasting** is strictly worse. Holding and recycling hand the ball back, so
  the team defends most of the match: it won ~1170 tackles, the most of any
  variant, but also committed 675 fouls and had 98 send-offs. It scored 3–4 goals
  in 320 matches.

## 4. Does the coach help?

Paired deltas in Campaign A, `*_rules` minus `*_none` on the same seeds:

| Archetype | Δ pooled GD [lo, hi] |
|---|---|
| balanced | −0.016 [−0.041, +0.009] |
| direct_carry | −0.050 [−0.100, −0.003] |
| press_aggressive | −0.053 [−0.113, +0.003] |
| park_the_bus | −0.006 [−0.038, +0.028] |
| time_waster | −0.016 [−0.034, +0.003] |
| hard_tackle | 0.000 [−0.031, +0.034] |

- No archetype gains from the coach policy, and `direct_carry` loses slightly
  (CI excludes 0).
  - `go_all_out_attack` and `press_high` when trailing add little to teams that
    already attack.
  - `slow_the_tempo` when leading gives the ball back to the opponent.
- **External coach vs in-agent coach:** `balanced_extcoach` (a scripted coach
  shouting presets through the platform's coach field) minus `balanced_rules` is
  exactly 0 [0, 0], because both use the same table. Where the shout comes from is
  irrelevant; only the table matters.
- Formation cannot change mid-match by construction: `choose_preset` only sees the
  snapshot.

## 5. Campaign B: round robin of the 12 variants

| Variant | W-D-L | GD | Pts | Pooled GD/match [lo, hi] |
|---|---|---|---|---|
| `press_aggressive_none` | 281-108-51 | +383 | 951 | +0.870 [+0.739, +0.998] |
| `press_aggressive_rules` | 280-106-54 | +361 | 946 | +0.820 [+0.695, +0.943] |
| `direct_carry_rules` | 237-140-63 | +291 | 851 | +0.661 [+0.539, +0.786] |
| `direct_carry_none` | 229-143-68 | +280 | 830 | +0.636 [+0.523, +0.755] |
| `park_the_bus_rules` | 58-286-96 | −70 | 460 | −0.159 [−0.248, −0.073] |
| `hard_tackle_none` | 97-169-174 | −161 | 460 | −0.366 [−0.502, −0.223] |
| `hard_tackle_rules` | 97-169-174 | −163 | 460 | −0.370 [−0.498, −0.241] |
| `park_the_bus_none` | 55-288-97 | −58 | 453 | −0.132 [−0.211, −0.055] |
| `balanced_rules` | 22-304-114 | −153 | 370 | −0.348 [−0.439, −0.264] |
| `balanced_none` | 20-301-119 | −159 | 361 | −0.361 [−0.455, −0.273] |
| `time_waster_rules` | 1-256-183 | −278 | 259 | −0.632 [−0.727, −0.536] |
| `time_waster_none` | 0-256-184 | −273 | 256 | −0.620 [−0.714, −0.527] |

`winner_claim` is **inconclusive**. The relevant 99.5% points-per-match intervals:

| Pairing | Interval |
|---|---|
| `press_aggressive_none` vs `direct_carry_none` | [−0.75, +1.50] |
| `press_aggressive_none` vs `balanced_none` | [+1.50, +2.55] |
| `direct_carry_none` vs `balanced_none` | [+0.675, +2.25] |
| `direct_carry_none` vs `park_the_bus_none` | [−0.30, +1.50] |

Both attacking archetypes clearly beat balanced, hard tackling and time-wasting.
Head-to-head, pressing edges carrying but not significantly. Among peers pressing
pays more; against the varied pool, carrying is safer and foul-free.

## 6. Foul-rate sensitivity

These are the top 3 by Campaign A lower bound, re-run against the pool with
`foul_scale` 0.5 and 2.0.

| Variant | 0.5 | 1.0 (Campaign A) | 2.0 |
|---|---|---|---|
| `direct_carry_none` | **+1.025 [+0.838, +1.219]** | **+1.087 [+0.906, +1.275]** | **+1.116 [+0.934, +1.303]** |
| `direct_carry_rules` | +0.972 [+0.794, +1.153] | +1.038 [+0.866, +1.212] | +1.066 [+0.897, +1.238] |
| `press_aggressive_none` | +0.950 [+0.762, +1.137] | +0.938 [+0.738, +1.137] | +0.853 [+0.653, +1.053] |

- The ranking is identical at all three scales.
- `direct_carry` commits 0 fouls at every scale.
- `press_aggressive_none` committed:
  - 77 fouls and 3 send-offs at scale 0.5;
  - 157 fouls and 6 send-offs at 1.0;
  - 319 fouls and 51 send-offs at 2.0.

  Its edge shrinks as refereeing gets stricter, which is a real risk if AWS
  referees harder than our guess.

## 7. Formation follow-up

The leader, `direct_carry_none`, was re-run against the pool in all three
formations. The baseline is the native `1-1-2`.

| Formation | Pooled GD [lo, hi] | Δ vs 1-1-2 [lo, hi] | W-D-L |
|---|---|---|---|
| `1-2-1` | +1.225 [+1.041, +1.413] | +0.138 [−0.053, +0.322] | 210-86-24 |
| `2-1-1` | +1.106 [+0.906, +1.313] | +0.019 [−0.200, +0.234] | 193-93-34 |
| `1-1-2` | +1.087 [+0.906, +1.275] | baseline | 191-102-27 |

No delta excludes 0, so we keep `1-1-2`. `1-2-1` is the candidate to try next, as a
between-match change only. `2-1-1` conceded 9 penalties against 1 for the others.

## 8. Decision (rule S2)

1. Highest pooled-GD lower bound against the pool (Campaign A):
   `direct_carry_none`, lo = +0.906.
2. Is the lower bound > 0? Yes.
3. Does the ranking survive sensitivity? Yes: it is first by lower bound at
   `foul_scale` 0.5 (0.838) and 2.0 (0.934).
4. Ship it. The coach-policy fallback does not apply, and the coach CI does not
   favour `rules` in any case.
5. Supporting evidence: Campaign B `winner_claim` is inconclusive, and
   `press_aggressive` leads the round robin. That does not override S2, and
   `direct_carry` has zero discipline exposure.

**Shipped:** `profiles/recommended.json`, a copy of the
`r4v2_direct_carry_none` profile:
- style: carry 1.0, tackle_aggression 0.0, tempo 0.5, block_depth 1.0,
  3 attackers, `1-1-2`;
- base settings unchanged;
- `coach_policy: none`.

## 9. LangChain advisor

What it is:
- `agent/advisor/` is behind the `llm` extra (`langchain-core`, `langchain-aws`).
- It uses `ChatBedrockConverse` with `AFC_ADVISOR_MODEL`.
- Two operations:
  - `pick`: choose an archetype for an opponent from a campaign report;
  - `review`: propose bounded settings/style after a match report.
- Model output passes through Pydantic and then `parse_profile`, so out-of-bounds
  values are rejected.
- The output is a **proposal file** only (`status: "proposal"`). An operator still
  deploys it with `AFC_PROFILE`.

```sh
.venv/bin/python -m pip install -e '.[llm]'
AFC_ADVISOR_MODEL=<bedrock-model-id> .venv/bin/python -m agent.advisor pick \
  --matrix ../local_simulator/local/r4v2/campaign_a_report.json \
  --opponent r4v2_aws_like_attacking --output local/proposal.json
```

This was verified offline with `FakeListChatModel` against the real
`campaign_a_report.json`. It produced a valid `advice-direct_carry-none` proposal
identical in style to the shipped profile.

Why it stays off the tick path:
- **Latency:** an LLM round trip consumes most of the ~1 s per-tick budget.
- **Determinism:** the five players must agree on shared assignments and coach
  presets from the snapshot alone (S4). Sampled LLM output breaks that.
- **Failure isolation:** a model or network error must never cost a tick.

The runtime does not import LangChain; a guard test enforces this. The answer to
"can we bring our own SDK" is yes, and this is how: around the agent, not inside
its tick.

## 10. Risks (spec §10, updated)

- **R1. v2 constants are guesses.**
  - Rankings were stable across a 4× foul-rate range.
  - The pressing edge decays as refereeing gets stricter; carrying is unaffected.
- **R2. Unknown real `SLIDE_TACKLE` parameter names.** The shipped profile has
  `tackle_aggression` 0, so it never slides and this risk does not apply to it.
- **R3. The real coach channel may not reach players.** This is now moot: the
  shipped profile uses no coach policy, and the experiments show no benefit.
- **R4 (new). Simulator fidelity.**
  - Shot aim and the no-recapture rule were added by us. The dominance of carrying
    may partly reflect our physics, e.g. the dribble speed vs the
    tackle-probability balance.
  - The v1 reference entrants are frozen under v1 physics, which had no tackling,
    so they are weak opponents.
  - Validate on the real platform before relying on these margins.
- **R5 (new). Pressing vs carrying.**
  - Head-to-head, pressing is not significantly worse and leads the round robin.
  - If AWS opponents resemble our stand-ins and referee leniently,
    `press_aggressive` is a reasonable alternative to try.
  - Its profile is `local_simulator/entrants/redesign_4_v2/profiles/r4v2_press_aggressive_none.json`.
