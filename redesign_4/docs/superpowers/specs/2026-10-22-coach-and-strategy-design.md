# Coach policy, strategy archetypes and off-tick LLM advisor — design

Date: 2026-10-22. Status: draft for review. Scope: `redesign_4/` and `local_simulator/`.

## 1. Goal

Answer three questions with evidence, then ship the winner in `redesign_4`:

1. Does a **coach policy** (mid-match preset selection) improve goal difference?
   Formation is fixed during a match; it may change only between matches.
2. Can a custom SDK (LangChain) add value? It runs **off the tick path** only.
3. Which in-rules **strategy archetype** wins: aggressive press, park the bus,
   time-wasting, hard tackling, or balanced?

"Dirty" means rough play using legal commands only. Exploiting simulator or
infrastructure loopholes, or touching other teams' endpoints, is out of scope.

### Success criteria

- S1. Each archetype and coach variant has measured goal difference with a 95%
  bootstrap CI against every opponent in the pool (§4), under rules v2.
- S2. A winner is shipped only if `winner_claim` (`campaign_report.py:227`,
  Bonferroni 99.5%) returns `clear_winner`. Otherwise the report says
  "inconclusive", and we ship `balanced` plus the coach policy if, and only if,
  the coach policy's CI on goal-difference delta excludes zero.
- S3. No regression: every existing `redesign_4` test passes. The runtime never
  imports the advisor package (enforced by test). The p99 decision latency in
  `cli_match` stays under 50 ms per tick.
- S4. With the platform coach field empty, all five players choose the identical
  preset on every tick (enforced by test over recorded replays).

## 2. Findings that shape the design (verified 2026-10-22)

| # | Finding | Evidence | Consequence |
|---|---|---|---|
| F1 | The simulator never sends a coach instruction | `local_simulator/src/football_lab/obs_encoder.py:42` hardcodes `""` | Need a per-team coach hook |
| F2 | A carried ball can never be won; possession changes only when the ball is free | `physics.py:261` pickup only if `owner_team is None`; `TACKLE_RADIUS` (`physics.py:30`) unused | Pressing/tackling is meaningless today; need a dispossession model |
| F3 | No foul, card or free-kick logic | no matches for foul/card in `engine.py`, `physics.py`; `FREE_KICK` exists only as a play-mode name (`rules-v1.md:359`) | The hard-tackle archetype would look free; need a foul model |
| F4 | No AWS practice bots locally; the pool contains only our redesigns | `local_simulator/entrants/` | Add labelled stand-in bots |
| F5 | `redesign_4` settings have four numeric knobs; none express block depth or tempo | `redesign_4/agent/model.py:49-53`, `profile.py:12-17` | Add a bounded `style` section to the tactical profile |
| F6 | `redesign_4` already honours four presets | `protocol.py:128`, `tactics.py:57,59,71,108` | Coach policy reuses them; no new preset semantics |
| F7 | The real coach channel reportedly has ~6 presets; only 4 are known by name | `initial_idea/docs/01-task.md` (second-hand) | Unknown presets are ignored, as today |

## 3. Rules v2 (local simulator)

v1 stays runnable and unchanged. Campaigns pin `rules_version`, and results from
v1 and v2 are never mixed. Every v2 constant is documented in `docs/rules-v2.md`
and labelled as a **local assumption**, not platform truth.

- **Dispossession.** `TACKLE` or `SLIDE_TACKLE` against the carrier within
  `TACKLE_RADIUS` succeeds with probability `p_win` (TACKLE 0.35,
  SLIDE_TACKLE 0.55). `PRESS_BALL` within the radius succeeds with 0.10 per
  step. On success the ball becomes free at the carrier's position, and normal
  pickup applies.
- **Fouls.** Each tackle attempt fouls with probability `p_foul` (TACKLE 0.10,
  SLIDE_TACKLE 0.25). The probability is doubled when the tackle comes from
  behind, defined as the angle between the tackler's approach and the carrier's
  velocity being under 60°. A foul cancels the dispossession, sets `playMode`
  to `FREE_KICK` and gives the fouled team the ball. A foul inside the penalty
  area gives `PENALTY` (one shot, keeper only).
- **Cards.** Each player keeps a foul count. Three fouls send the player off for
  the rest of the match: that player is removed, and the team plays with four.
- **RNG.** All draws use a per-match `random.Random(seed)`, so v2 matches are
  reproducible.
- **Coach hook.** `TeamRunner` takes an optional `coach: Callable[[EngineState,
  team_id], str]`. Its value is written to `coachInstruction` in that team's
  observation. The default is `""`, which keeps current behaviour.

The constants are tuning targets for plausibility, not facts. The report must
include a sensitivity run: re-rank the archetypes with `p_foul` ×0.5 and ×2. A
ranking that flips under that range is reported as fragile.

## 4. Opponent pool

- Existing entrants: `initial_idea`, `redesign_1`, `redesign_2`, `redesign_3`, `redesign_4`.
- **Stand-in bots** `aws_like_balanced`, `aws_like_attacking`, `aws_like_defensive`.
  These are `redesign_4` with fixed `style` presets, labelled "stand-in, not the
  AWS bot" in every report.
- Archetypes also play each other in a round robin.

## 5. Tactical profile `style` section (redesign_4)

Profile schema v2 adds `style`, bounded and validated like `BOUNDS`. A v1
profile loads with the default style, which reproduces today's behaviour
exactly; this is regression-tested.

| Field | Range | Meaning |
|---|---|---|
| `block_depth` | 0.0–1.0 | 0 = deep block at own box, 1 = high line |
| `attackers_committed` | 1–3 | outfielders allowed beyond the halfway line in possession |
| `tackle_aggression` | 0.0–1.0 | willingness to tackle and slide (including from behind) versus contain |
| `tempo` | 0.0–1.0 | 0 = recycle and hold possession, 1 = forward pass and shoot early |
| `formation` | enum `1-1-2` (default, today's anchors, `tactics.py:198-215`), `2-1-1`, `1-2-1` (outfield lines DEF-MID-FWD; GK implicit) | pinned role anchors; **immutable during a match** |

### Archetypes (pinned profiles)

| Archetype | block_depth | attackers | tackle_aggr | tempo | other settings |
|---|---|---|---|---|---|
| `balanced` | 0.5 | 2 | 0.3 | 0.5 | defaults, formation `1-1-2` |
| `press_aggressive` | 0.9 | 3 | 0.6 | 0.8 | press_radius 30, formation `1-1-2` |
| `park_the_bus` | 0.1 | 1 | 0.4 | 0.2 | press_radius 12, formation `2-1-1` |
| `time_waster` | 0.4 | 1 | 0.2 | 0.0 | pass_margin 4.5, shot_range 20, formation `1-2-1` |
| `hard_tackle` | 0.6 | 2 | 1.0 | 0.5 | press_radius 26, formation `1-1-2` |

Formation is a confound: the matrix runs the winning archetype in all three
formations as a follow-up, so formation and style effects are not conflated.

`time_waster` uses only legal commands: square passes, keeper distribution and
holding the ball under low pressure. It does not stall restarts, because the
simulator auto-restarts and the real platform's restart rules are unknown.

## 6. Coach policy (redesign_4)

New module `agent/coach.py`, a pure function:

```python
def choose_preset(s: Snapshot, settings: Settings, style: Style,
                  scout: dict | None) -> str  # "" or a known preset
```

- **Precedence:** if the platform sent a recognised `coachInstruction`, it wins
  and the policy returns it unchanged. The policy runs only when the field is
  empty.
- **Rules** (tunable bounds live in the profile):
  - trailing with ≤ 25% of the match left → `go_all_out_attack`
  - leading with ≤ 30% left → `slow_the_tempo`
  - level, and the opponent scout's long-pass rate is low → `press_high`
  - otherwise `""`
- **Stability without shared state:** the preset depends only on the score and
  on match time quantised to 5 s buckets. Every player sees the same snapshot,
  so every player computes the same preset. No per-player history is used,
  which keeps decisions stateless per tick.
- **Formation is never touched.** `choose_preset` has no access to `formation`;
  this is enforced by its signature.

Two policy variants are measured per archetype: `none` and `rules`. In the
simulator the coach hook (§3) can also inject the same presets externally, to
model a human coach shouting. This variant is reported separately as
`external_coach`.

## 7. Off-tick advisor (LangChain)

- **Package:** `agent/advisor/`, installed only through the optional extra
  `llm = ["langchain-core>=0.3,<0.4", "langchain-aws>=0.2,<0.3"]`. The runtime
  never imports it, enforced by a test that imports `agent.runtime` with
  `langchain*` blocked.
- **Pre-match analyst:** takes the opponent scout and the latest strategy-matrix
  JSON. It outputs an archetype name plus a profile proposal, through structured
  output (Pydantic) followed by `parse_profile` validation.
- **Post-match coach:** takes `trainer.py` lessons and the telemetry summary,
  and outputs parameter proposals. These enter the **existing** proposal and
  approval flow; the advisor never approves anything itself.
- **Failure:** on timeout, invalid schema, out-of-bounds values or an
  unavailable model, the advisor logs the problem and returns no proposal. The
  deterministic trainer is unaffected.
- **Model:** Bedrock through `langchain-aws`, configurable. No live AWS calls
  happen in tests; a fake chat model is used. Live use is an operator action.
- **Value test:** the report compares the analyst's archetype pick against the
  matrix's empirical best for each stand-in opponent. Accuracy is reported, and
  no claim of improvement is made without it.

## 8. Experiment protocol

1. Sanity check: rules v2 with `redesign_4` against itself gives a mean goal
   difference CI that contains 0, which confirms no side bias.
2. Matrix: 5 archetypes × 2 coach variants against the pool (§4), plus the
   archetype round robin. N = 40 seeded matches per pairing, with legs swapped.
3. Sensitivity: rerun the top 3 with `p_foul` ×0.5 and ×2.
4. Report `redesign_4/docs/strategy_report.md` with goal-difference CIs, win,
   draw and loss rates, fouls and send-offs per match, end stamina,
   `winner_claim` output and a fragility note.
5. Ship according to S2: update the default profile and record the decision.
   The formation choice is part of the shipped profile.

## 9. Testing

- Simulator: deterministic dispossession and foul outcomes under a fixed seed;
  a send-off after 3 fouls; the coach hook appears in the observation; v1
  output stays byte-identical to the stored replay.
- `redesign_4`:
  - style bounds and v1-profile compatibility
  - each archetype changes the intended behaviour (for example, `park_the_bus`
    keeps ≥ 3 outfielders in the own half when out of possession)
  - `choose_preset` table tests and the precedence rule
  - S4 consistency across the 5 players
  - the advisor import guard
  - the advisor rejecting malformed LLM output (fake model)

## 10. Out of scope

Live AWS deployment; any LLM call on the tick path; changing the formation
during a match; simulator or infrastructure exploits; claims that local results
transfer to the real platform (the report states this caveat).

## 11. Risks

- **R1.** The rules v2 constants are guesses, so rankings may not transfer.
  Mitigations: the sensitivity run (§8.3) and the explicit caveat.
- **R2.** The real coach channel may not reach player agents at all. The policy
  is still useful, because it is self-generated.
- **R3.** LangChain version churn. Mitigated by pinned ranges in the optional
  extra, isolated from the runtime.
