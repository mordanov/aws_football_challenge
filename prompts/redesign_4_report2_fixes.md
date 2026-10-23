# Redesign_4: correct stamina handling and investigate goal coverage

Status: the stamina guard, reachable-plane keeper projection and bounded
stranded-keeper shooting are now implemented. See the second-match section of
`redesign_4/README.md` for evidence and deployment status. The remaining
investigations below are evidence gates, not confirmed bugs to implement blindly.

Implement evidence-backed corrections to the deterministic `redesign_4` agent.
Start from commit `e5da20b` or its descendant in the `r4-coach-strategies`
worktree. Keep formation 1-1-2 fixed during a match. Preserve the live/fitness
input separation introduced in that commit.

This is an implementation prompt, not a runtime player prompt. The deployed
players choose commands in Python; changing this document alone changes no
match behavior. Deployment requires separate authorization after verification.

## 1. Establish the evidence baseline

Read `redesign_4/{CONTEXT.md,state_machines.md,data_flow.md,README.md}` and the
relevant code before editing. Inspect the supplied `report2.pdf` at the main
repository root; it is outside the worktree and is not a tracked fixture.

The CloudWatch match ID is `64c91b0b-c1b1-4e93-ae1c-5e79bb06f6a9`.
The captured records are available locally at:

```text
/Users/aleksandr/.copilot/session-state/3e735515-e140-4fa4-b0a8-fa0d79499798/files/report2-telemetry.json
```

If that artifact is unavailable, retrieve this exact match from the five
`/aws/bedrock-agentcore/runtimes/Redesign4*` log groups in us-east-1.
Use existing local AWS credentials without displaying or committing them.
Preserve source telemetry as a session artifact, not as a credential-bearing
repository dump.

### Verified observations

- Official result: Aleksalotl lost 1-2 to Total Attack United.
- PDF statistics assign Aleksalotl 66% possession, 5 shots and 1 on target.
  These are **report-attributed**, not independently confirmed team metrics.
- The complete available capture has 399 records: 394 decisions and five
  tick-0 errors, one per seat, reporting an incomplete/invalid ten-player roster.
  All decision records selected `compact_match`, coordinate scale 8, stamina
  scale 1 and ball lateral axis z. The previous adapter was active.
- There are 81 distinct observed ticks. The observed score first changes to
  0-1 at 10.700054 s, 1-1 at 25.000381 s and 1-2 at 32.980564 s.
  These are first observations of each score, not exact event timestamps.
- Fifteen decisions across three ticks warn `unresolved_possession`.
  The other 16 owner-less observed ticks have a free ball; distinguish them.
- Logged decision computation median/max is 0.461/4.108 ms. The PDF's
  approximately 813 ms is an external latency measure, not Python computation.
  Transport success in the report does not mean every command was tactical:
  the five startup errors returned a legal fallback.

| Command | Our captured decisions | PDF Aleksalotl column | PDF opponent column |
|---|---:|---:|---:|
| MOVE_TO | 302 | 138 | 301 |
| PASS | 25 | 0 | 29 |
| SHOOT | 4 | 64 | 4 |
| MARK | 30 | 0 | 30 |
| PRESS_BALL | 16 | 120 | 13 |
| INTERCEPT | 16 | 72 | 16 |
| GK_DISTRIBUTE | 1 | 1 | 1 |
| CLEAR | 0 | 10 | 11 |

Our distribution resembles the PDF's opponent column, but it is not an exact
match. Each PDF column totals 405 commands; our capture has 394 decisions plus
five fallbacks. Establish attribution before using report possession, finishing,
latency, awards or scorer names to tune our team. The PDF names a goalkeeper
as both opposing scorer while naming a forward in its overview; neither claim
establishes the actual scorer. In particular, "add passing because we passed
zero times" is contradicted by our telemetry.

**Completion criterion:** reproduce these counts and document any discrepancy
before treating an observation as a regression.

## 2. Fix the confirmed stamina bypass first

Inspect `carry_option`, `movement` and `carrier_options` in
`redesign_4/agent/tactics.py`. Normal movement uses a stamina guard; the carry
candidate builds MOVE_TO directly with `sprint=True`.

Captured regression: tick 7328, seat 3, clock 42.280777 s, stamina 0.11782325.
The selected carry command still requests sprint, targeting wire
`(6.25, -0.268)`. This bypass is demonstrable independently of the final score.
Seat 3 reaches zero stamina during the match.

Route carrying through the existing movement policy, or share its eligibility
rule without changing unrelated scoring. Preserve a legal non-sprint carry
when it remains the best option. Cover low stamina, the existing threshold
boundary and healthy carrying on both teams. Preserve expected rounding and
compact wire conversion.

**Completion criterion:** a focused behavioral test fails on the old bypass
and passes after correction; healthy carrying and fitness transport retain
their intended behavior. Explain any intentional golden-output change.

## 3. Reproduce goalkeeper coverage before changing geometry

Inspect the goalkeeper branch in `decide`, `shot_option` and
`protocol.match_command`. Canonical keeper depth -52 is converted to -6.5 and
clipped to -6.4; lateral coverage is clamped to canonical +/-2.8, or wire
+/-0.35. Shooting separately assumes a goal line at canonical +/-55 and
posts near +/-3. Coordinate factor 8 and live dimensions remain provisional.

Use tick 3626 as a concrete probe:

- Ball canonical position `(-36.546852, 9.2039416)` and velocity
  `(-110.08664, -26.030848)`.
- Our goalkeeper canonical position `(-50.227144, 3.62165424)`.
- Command: MOVE_TO wire `(-6.4, 0.35)`, sprint false.
- The next observed score is 0-1 at tick 3727, with the ball at canonical
  `(-55.716736, 4.5183928)`.

This establishes a clipped target before a concession, not that a wider target
would save the shot. At the second concession the capture lacks the incoming
shot frame: tick 6126 is still opponent possession, then tick 6229 has 1-2.

Verify authoritative goal dimensions, reachable keeper depth, movement speed,
save mechanics and invocation cadence where available. Compare projected
crossing at the keeper's reachable plane with the current projection at -54.
Only change geometry with corroborating evidence; otherwise add diagnostics
and explicitly leave this hypothesis unresolved. Keep one coherent unit
contract for shot geometry, goal coverage and wire bounds. Avoid tuning
unrelated constants to make a single recorded frame look better.

**Completion criterion:** any coverage change has a failing, reachable-shot
regression plus mirrored-team and non-threatening-ball cases. State which
dimensions are verified and which remain calibrated. A saved synthetic shot
is not proof that either recorded goal would have been prevented.

## 4. Evaluate finishing and possession without command-count targets

The two early captured shots were taken with depleted stamina:
seat 4 at tick 5010, 24.06036 s, stamina 0.31222895; seat 3 at tick 7229,
40.30073 s, stamina 0.21967068. The first precedes our equalizer, so low stamina
alone is not a reason to suppress every shot. Two later shots had stamina
above 0.96. All four selected BR with power 0.9; inspect lane and keeper
geometry before inferring a directional bug.

Investigate stamina-aware shot quality, safe forward outlets and genuine
empty-goal opportunities only after verifying the relevant mechanics.
Maintain a shot when it is clearly preferable to carrying or passing.
Treat these as hypotheses, not mandatory speculative features.

Twelve of 25 captured passes are from seat 4 to seat 3. Twelve passes occur at
the repeated post-goal clocks around 10.7, 25.0 and 32.981 seconds. Segment
restart/frozen-clock periods before diagnosing a passing loop or estimating
possession duration. Tick advancement alone does not establish active play.
Use recorded candidates and progressing game time to identify real stagnation.

Investigate the tick-0 roster errors separately as readiness behavior. Preserve
strict validation of malformed active-play frames. If an explicit platform
readiness signal exists, test it and implement its documented legal response;
otherwise retain visible errors rather than guessing a partial team.

**Completion criterion:** each additional behavior change has its own
reproduction and behavioral regression. Record unsupported hypotheses as
unresolved instead of implementing them as if confirmed.

## 5. Verify and hand off

Run the existing targeted tests, formatter, linter and type checker using the
project's installed tooling. Preserve coverage of live fractional stamina,
fitness percentage stamina including 1%, 3D ball x/z, both team directions,
GK distribution, PASS target IDs and safe unresolved ownership. Synchronize
the simulator entrant and its hash if agent source changes, then run its
relevant integration tests.

Replay corrected decisions against the captured canonical snapshots without
applying the compact scale twice. Preserve full multi-player frames for useful
regression fixtures, while removing AWS/account metadata. Snapshots omit raw
ball height, unresolved holder IDs and readiness details; they cannot recreate
those inputs exactly. A counterfactual decision replay is not a resimulated
match result.

Update the three project-context documents for changed concepts, states or
data movement. Keep profile/formation tuning separate from confirmed bug fixes.
Report confirmed fixes, evidence, unresolved hypotheses and any replay
limitations. Keep code local until deployment is authorized; a new real match
is required to measure competitive improvement.
