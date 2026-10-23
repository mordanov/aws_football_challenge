# Report 4: keeper reaction to fast incoming balls

Status: implemented and deployed to all five runtimes as version 6, with
unchanged ARNs. Keep these corrections separate from the proposed offline
AgentCore coaching integrations.

## Evidence and attribution

Aleksalotl lost 1-3 to Total Attack United. Match
`b5a0c118-b7d8-4de4-ac76-5e528604be19` used
`r4v4_match3_keeper_outlet` (runtime version 5).
CloudWatch contains 425 application records: 420 decisions and five initial
invalid-roster fallbacks. There are 88 distinct observed ticks, not a complete
replay. Actual decisions are 320 MOVE_TO, 29 MARK, 28 PASS, 26 INTERCEPT,
11 PRESS_BALL, 5 SHOOT and 1 GK_DISTRIBUTE. Twenty decisions warn of unresolved
possession. There are no application hold decisions in this capture.

The PDF attributes 88 shooting commands and two passes to Aleksalotl; its
opposing column resembles our telemetry again. Do not adopt that attribution.
The official result remains 1-3; 65% possession and shot statistics are report
claims, not independently validated team metrics.

Our coach advice recommends PASS-first play and fewer indiscriminate shots.
We actually attempted 28 passes and five shots, with 23 passing decisions at
frozen post-goal clocks. These repeats do not prove active passing loops or
successful build-up. Do not blindly suppress shooting or increase pass weights.

The opponent's advice recommends better precision and forward support. Treat
that as an adversarial scenario: accurate, fast incoming shots while our keeper
is slightly displaced. This is directly testable without guessing how the
opponent will change its implementation.

## Reproduced correction

At tick 4959 (23.18034 seconds, score 0-1), the free ball is at
(-4.0808096,-10.1263304), velocity (-120.334744,18.7536928).
It crosses the compact keeper target plane x=-51.2 in 0.391568 seconds at
y=-2.782991. The full-stamina keeper is at (-47.5479624,-1.77779696), about
3.78785 canonical units from that target. The general movement guard rejects
sprint because distance is below 5, despite a threatening shot. The next
captured score is 0-2. This is temporal evidence, not proof a sprint saves it.

At tick 7560 (47.14089 seconds, score 1-2), the free ball is at
(-10.6065608,6.8950744), velocity (-122.645096,-11.7009688).
It crosses x=-51.2 in 0.330983 seconds at y=3.022253, just outside the keeper's
existing +/-2.8 movement clamp. Its projected crossing of the existing modeled
goal line x=-55 is within the existing +/-3 goal mouth. It should still be
treated as urgent, keeping the target clamped rather than widening geometry.
The next captured score is 1-3, again without causal proof of a preventable goal.

Permit the keeper's nonzero-distance sprint for explicitly free incoming
goal-threatening trajectories. Preserve stamina >=0.35, normal outfield/carry
distance guards, existing movement bounds and the three-second prediction
horizon. Trajectories already past the target plane, stationary/unknown/carried
balls, clearly wide shots and depleted keepers must not gain this exception.
Use the existing modeled goal dimensions only for threat classification;
do not claim that their exact physical calibration is established.

## Acceptance and non-goals

1. Watch both captured trajectories fail mirrored regression tests on old code.
2. Preserve projected y targets, stamina gating, no-sprint behavior for clearly
   wide/moving-away/carried/unknown balls, and outfield short-distance guards.
3. Replay all 420 decisions once from canonical snapshots with max_move_x=51.2.
   Inspect every change; preserve all five shooting decisions and passing logic.
4. Recheck report 3's 278 captured decisions to retain its three shots and keeper
   distribution correction. Review golden differences rather than resetting them.
5. Run agent checks; rebuild simulator entrant and run its nonbrowser suite.
   Simulator sprint physics cannot establish competitive benefit.
6. Update context/docs and version the code-only profile, preserving weights,
   formation 1-1-2 and coach policy. Deploy all five existing ARNs together.
7. Verify READY versions, live/fitness commands, mirrored captured shot reactions,
   exact known Fitness Check session refresh and new-profile CloudWatch records.

The first concession has no captured incoming-shot frame. Unknown ownership,
initial roster readiness, accepted commands, precise save mechanics and exact
goal calibration remain unresolved. Do not hide those gaps, guess holder IDs,
add arbitrary shot range, or claim a guaranteed better match result.

## Local outcome

Profile `r4v5_match4_keeper_reaction`. Replay changes exactly two commands:
keeper sprint false -> true at ticks 4959 and 7560, with identical legal targets.
All other 418 decisions are identical, including all five shots and 28 passes.
Report 3 replay preserves its three shots and prior keeper-outlet fix.
The full agent suite has 120 passing tests; Ruff, formatting and mypy pass.
The simulator entrant was rebuilt (`ac797304c030` hash prefix); 205 nonbrowser
tests and seven subtests pass. No sprint/save benefit is claimed from that suite.

## Deployment outcome

All five runtimes and their DEFAULT endpoints are READY on version 6. Verified
65 direct AWS invocations against local expected commands: five seats, both
teams, sample/live input contracts, stamina extremes, both captured shots
mirrored with depleted/full keeper stamina, all five actual shots mirrored, and
the previous contested keeper-distribution correction mirrored. The five exact
known portal Fitness Check sessions were refreshed and exercised; unrelated
live sessions were untouched.

CloudWatch confirms all 65 new-profile decisions, with zero recorded telemetry
drops/failures. Verification UUID `c32a673f-1380-45d4-ad3d-0c68ceffe193`;
synthetic ticks 990008/990009 must be excluded from match analysis.
Maximum measured invocation round trip was 4.521 seconds, not a competitive
per-tick latency guarantee. Evidence is persisted as
`report4-deployment-verification.json` in the session files directory.
Implementation commit: `4799bb0`.
