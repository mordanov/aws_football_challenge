# Coach policy, strategy archetypes and off-tick LLM advisor — design

Date: 2026-10-22. Status: approved design, revision 3 (amended while planning).
Scope: `redesign_4/` and `local_simulator/`.

## 1. Goal

Answer three questions with evidence, then ship the winner in `redesign_4`:

1. Does a **coach policy** (mid-match preset selection) improve results? Formation
   is fixed during a match and changes only between matches.
2. Can a custom SDK (LangChain) add value **off the tick path**?
3. Which in-rules **strategy archetype** wins: aggressive press, park the bus,
   time-wasting, hard tackling, or balanced?

"Dirty" = rough play through legal commands only. No simulator/infrastructure
exploits, no interaction with other teams' endpoints.

### Success criteria

- S1. For every archetype × coach variant, under rules v2, the report gives the
  pooled goal difference per match vs the opponent pool with a 95% block-bootstrap
  CI, plus pairwise points CIs among archetypes (existing `bootstrap_ci`).
- S2. Ship decision rule: the shipped variant is the one with the highest pooled
  goal-difference lower bound vs the pool, **provided** that lower bound is > 0 and
  its ranking survives the foul-rate sensitivity runs (§8). Otherwise ship
  `balanced` (+ coach policy only if its pooled-GD delta CI vs `balanced/none`
  excludes zero). `winner_claim` on the archetype round robin is reported as
  supporting evidence.
- S3. No regression: all existing tests in both projects pass; the default profile
  reproduces pre-change decisions exactly (golden test); `agent.runtime` imports
  without LangChain (guard test); p99 `Runtime.handle` latency < 50 ms.
- S4. With an empty platform coach field and `coach_policy="rules"`, all five
  players choose the same preset for the same tick (test).

## 2. Findings that shape the design (verified 2026-10-22)

| # | Finding | Evidence | Consequence |
|---|---|---|---|
| F1 | Simulator never sends a coach instruction | `local_simulator/src/football_lab/obs_encoder.py:42` | per-team coach hook |
| F2 | Carried ball can never be won | `physics.py:261` pickup only when free; `TACKLE_RADIUS` unused | dispossession model |
| F3 | No foul/card/free-kick logic; `play_mode` never changes | `physics.py`, `engine.py` | foul model without restart machinery |
| F4 | Only `SLIDE_TACKLE` is valid in the sim; `TACKLE` is aliased to it | `actions.py:11-21`, `cmd_decoder.py:8` | one tackle type |
| F5 | Player `vx/vy` are never updated | `physics.py` (no player velocity writes) | "from behind" uses position |
| F6 | `redesign_4` never tackles and rejects `SLIDE_TACKLE` | `tactics.py:219-276`, `protocol.py:154-163` | add the command |
| F7 | Four numeric settings cannot express block/tempo/formation | `model.py:49-53` | bounded `style` section |
| F8 | No AWS practice bots locally | `local_simulator/entrants/` | labelled stand-ins |
| F9 | Trainer rebuilds profiles from defaults | `trainer.py:446-567` | carry base style through training |
| F10 | LangChain is at 1.x (core 1.6, aws 1.9) | PyPI, 2026-10-22 | pin `>=1,<2` |

## 3. Rules v2 (local simulator)

v1 remains the default and byte-identical. Campaigns pin `rules_version`; v1 and
v2 results are never mixed. Every constant is a **local assumption** documented in
`local_simulator/docs/rules-v2.md`.

- **Slide tackle** (one-shot): resolves once, the first step the tackler is within
  `TACKLE_RADIUS` (2.4) of the targeted carrier. Foul with `p_foul = 0.25 ×
  foul_scale` (×2 if from behind); otherwise win with `p_win = 0.55`.
- **Standing challenge**: a `PRESS_BALL` player within `TACKLE_RADIUS` of the
  carrier wins the ball with 0.01 per step; never a foul.
- **Win**: the tackler takes possession (prevents instant re-pickup by the carrier).
- **From behind**: the angle between (carrier→tackler) and the carrier's backward
  attack direction is < 60°.
- **Foul outside the box** (simplified free kick): the carrier keeps the ball; the
  fouler is inactive for 40 steps (one decision window).
- **Foul inside the defender's penalty area** (16.5 deep, |y| ≤ 20): penalty
  resolved instantly, goal with 0.75; ball restarts at centre.
- **Send-off**: the 3rd foul moves the player to the touchline; inactive for the
  rest of the match (still present in observations, so the team plays a man down).
- **RNG**: `random.Random(seed)` per match; deterministic.
- **Coach hook**: per-team `coach(state, team_id) -> str` written to
  `coachInstruction`; default none ⇒ `""`.
- **Events**: `FOUL`, `PENALTY` (team_id = awarded team), `SEND_OFF`, `TACKLE_SUCCESS`
  (also for standing-challenge wins). Fixture results record per-team counts.
- **Replays**: campaigns may run with `--discard-replays` (replays are ~3 MB each).
- **Shot aim**: v2 honours `aim_location` (TL/BL → y = −3.5, TR/BR → y = +3.5, CENTER → 0, world coordinates); v1 aims every shot at the goal centre, which a centred keeper always catches.

## 4. Opponent pool

`initial_idea_v1`, `redesign_1_v1`, `redesign_2_v1`, `redesign_3_v1`, frozen
`redesign_4_v1`, and three **stand-in** bots (`aws_like_balanced`,
`aws_like_attacking`, `aws_like_defensive`), which are `redesign_4_v2` profiles
labelled "stand-in, not the AWS bot".

## 5. Tactical profile `style` section (redesign_4)

Profile v2 adds `style` and `coach_policy`. A v1 profile (no such keys) loads with
the defaults, which are **identity values**: decisions are byte-identical to the
pre-change agent (golden test).

| Field | Range | Default (identity) | Effect |
|---|---|---|---|
| `block_depth` | 0–1 | 1.0 | out of possession, outfield move targets capped at attack-x `-50 + 96·depth`; a marker whose threat is beyond the cap moves to (cap, threat.y) instead of marking |
| `attackers_committed` | int 1–3 | 3 | in possession, only players `(4,3,2)[:n]` may move past halfway |
| `tackle_aggression` | 0–1 | 0.0 | presser slides within `1 + 2.5·aggr` m; from behind only if ≥ 0.7; no slides at all when 0 |
| `tempo` | 0–1 | 0.5 | pass score `+ (tempo-0.5)·1.2·gain`; hold score `max(0,0.5-tempo)·120` when no opponent within 8 m |
| `formation` | `1-1-2`, `2-1-1`, `1-2-1` | `1-1-2` | pinned anchors (outfield DEF-MID-FWD); **immutable in a match** |

Roles by formation (pid 0 is always GK):

| pid | 1-1-2 | 2-1-1 | 1-2-1 |
|---|---|---|---|
| 1 | DEF | DEF | DEF |
| 2 | MID | DEF (mirrored) | MID |
| 3 | FWD (link) | MID | MID (mirrored) |
| 4 | FWD | FWD | FWD |

`1-1-2` reproduces the pre-change anchors exactly. Archetype definitions live in
`agent/archetypes.py`.

`coach_policy`: `none` (default) or `rules`.

### Archetypes

| Archetype | depth | attackers | tackle | tempo | formation | settings deltas |
|---|---|---|---|---|---|---|
| `balanced` | 1.0 | 3 | 0.0 | 0.5 | 1-1-2 | — |
| `press_aggressive` | 1.0 | 3 | 0.6 | 0.8 | 1-1-2 | press_radius 30 |
| `park_the_bus` | 0.1 | 1 | 0.4 | 0.3 | 2-1-1 | press_radius 12 |
| `time_waster` | 0.5 | 1 | 0.2 | 0.0 | 1-2-1 | pass_margin 4.5, shot_range 20 |
| `hard_tackle` | 0.7 | 2 | 1.0 | 0.5 | 1-1-2 | press_radius 26 |

Each archetype runs with `coach_policy` `none` and `rules`. Time-wasting uses legal
holding/recycling only (no restart stalling).

## 6. Coach policy (redesign_4)

`agent/coach.py: choose_preset(s: Snapshot) -> str` — pure; it receives only the
snapshot, so it cannot touch formation.

- Platform-sent recognised preset wins (returned unchanged).
- Otherwise, with `diff` = own − opponent goals and `r` = remaining fraction:
  `diff<0 ∧ r≤0.35 → go_all_out_attack`; `diff<0 → press_high`;
  `diff>0 ∧ r≤0.40 → slow_the_tempo`; `diff=0 ∧ r≤0.20 → shoot_on_sight`; else `""`.
- It depends only on shared snapshot fields, so all five players agree (S4).
- `Runtime` applies it only when `profile.coach_policy == "rules"`.
- The simulator's scripted external coach uses the same table (variant
  `r4v2_balanced_extcoach`), modelling a human coach shouting.

## 7. Off-tick advisor (LangChain)

- `agent/advisor/` behind the extra `llm = ["langchain-core>=1.0,<2",
  "langchain-aws>=1.0,<2"]`; never imported by the runtime (guard test).
- `Advisor(llm, archetype_profiles)`:
  - `pick_archetype(matrix, opponent_id)` → `ArchetypeAdvice` + validated `Profile`
  - `review_match(report, current)` → `SettingsAdvice` + validated `Profile`
- Model output is parsed as JSON into Pydantic models, then through `parse_profile`.
  Any model error, invalid JSON, or out-of-bounds value raises `AdvisorError`, and
  the CLI writes no proposal.
- Output is a **proposal file** (`status: "proposal"`). Deployment remains an
  operator action (`AFC_PROFILE`), and trainer promotion is unchanged.
- `Trainer.train(base=...)` carries the base profile's `style`/`coach_policy`, so
  retraining doesn't revert a shipped archetype.
- Tests use `FakeListChatModel`; no network.

## 8. Experiment protocol (rules v2, 20 blocks × 2 legs = 40 matches per pairing)

1. Sanity: `r4v2_balanced_none` vs `redesign_4_v1` — the pairwise CI contains 0.
2. Campaign A (cross): 10 archetype variants + `r4v2_balanced_extcoach` vs the
   8-team pool.
3. Campaign B (round robin): the 10 archetype variants.
4. Sensitivity: the top 3 by pooled-GD lower bound, Campaign A again with
   `foul_scale` 0.5 and 2.0.
5. Formation follow-up: the leader re-run in all three formations vs the pool.
6. `redesign_4/docs/strategy_report.md`; ship per S2 as
   `redesign_4/profiles/recommended.json` (deployed via `AFC_PROFILE`).

Reports also give the pooled goal-difference delta CI against a baseline entrant (`--baseline`) and discipline counts.

## 9. Out of scope

Live AWS; any LLM on the tick path; in-match formation change; exploits; claiming
transfer of local results to the real platform.

## 10. Risks

- R1. v2 constants are guesses; mitigated by sensitivity runs and a caveat.
- R2. The real `SLIDE_TACKLE` parameter names are unknown (we send
  `target_player_id`). A server rejection only idles one player for one tick.
- R3. The real coach channel may not reach players; the policy is self-generated
  anyway.
