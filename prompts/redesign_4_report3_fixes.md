# Report 3: preserve the winning shape and unblock keeper distribution

Apply this correction to `redesign_4`, then deploy all five agents together.
Keep formation 1-1-2, existing profile weights, conservative ownership resolution
and the separate live/fitness contracts. This document is an implementation
prompt, not a runtime player prompt.

## Evidence

`report3.pdf` records a 1-0 win over Fort Knox Athletic. CloudWatch match
`b728eb37-3829-48f2-b026-22040537fa76` ran `r4v3_match2_corrections`.
The available capture has 283 records: 278 decisions and five initial roster
errors, across 57 observed ticks. Actual commands are 199 MOVE_TO, 36 MARK,
15 PRESS_BALL, 13 INTERCEPT, 9 PASS, 3 SHOOT, 2 GK_DISTRIBUTE and 1 SET_STANCE.
Twenty decisions across four ticks warn of unresolved possession.

The PDF assigns our team zero shooting commands while reporting three shots.
Our command distribution instead resembles its opponent column. Use telemetry
for command attribution, retain the official score, and label possession,
latency and opponent shot statistics as report-attributed.

All three recorded shots (ticks 3587, 4851, 6352) use the stranded-keeper
extension. The score first becomes 1-0 at tick 4950, 36.380642 s, after the
second attempt. This ordering does not prove the command caused the goal or
that the update caused the win against a different opponent.

Telemetry artifact, when available:
`/Users/aleksandr/.copilot/session-state/3e735515-e140-4fa4-b0a8-fa0d79499798/files/report3-telemetry.json`.
If missing, retrieve this exact match from the five runtime log groups.

## Correction

Treat both coach recommendations as hypotheses. Aleksalotl's recommendation
to shoot more must be checked against the three actual shooting decisions,
not the misattributed zero. Fort Knox's recommendation to press more is a
red-team scenario: challenge our keeper and nearest receiver with coordinated
opposing pressure. Prefer another safe outlet if available, then the contested
kick only when all safe routes disappear. Test this scenario in both team
directions; raising our own press setting does not test the opponent's advice.

Reproduce tick 9786: our goalkeeper has known possession at 119.67193 s but
returns SET_STANCE because all outlets fail length/lane/pressure filters.
The role contract requires GK_DISTRIBUTE, including when no outlet is safe.

In `carrier_options`, retain ordinary safe-outlet scoring and THROW/KICK choice.
If none qualifies for the keeper, rank legal non-self teammates using the
existing outlet score and attempt KICK to the best candidate. Label the reason
as a contested emergency outlet; the command does not establish a safe or
completed pass. The keeper must not select the slow-tempo hold option over a
distribution. Preserve holding behavior for outfield players.

## Acceptance

1. Watch a mirrored captured-frame regression fail on the old SET_STANCE.
2. Cover safe-outlet preference, changed receiver geometry, slow tempo,
   all-close/all-distant teammates and uncertain ownership. No fabricated
   keeper possession or self-pass is allowed.
3. Replay all available match-3 decisions in canonical coordinates without
   applying compact scaling twice. Preserve the three shooting decisions and
   ordinary safe distributions; inspect every changed command.
4. Run agent checks, synchronize the simulator entrant/hash, and run relevant
   simulator integration tests. Update project-context docs and version the
   profile without changing its weights.
5. Deploy all five runtimes, verify READY DEFAULT versions and unchanged ARNs,
   exercise live and fitness inputs plus captured keeper congestion through
   AWS, and confirm the new profile/commands in CloudWatch. Refresh only known
   stale Fitness Check sessions, not unrelated match sessions.

Keep initial roster errors and ambiguous possession explicit. Their raw inputs
are absent from snapshots, so this win does not justify guessed readiness
handling, looser holder attribution, more shooting everywhere or wider goal
geometry. Compare future matches before attributing a higher win rate.

## Implementation status

Implemented in `351bcdb`, profile `r4v4_match3_keeper_outlet`. All 116 agent
tests and Ruff/mypy checks passed. The rebuilt simulator entrant passed 205
nonbrowser tests and seven subtests; viewer tests remain excluded because
Playwright is absent. The 278-decision replay changes only the keeper hold
at tick 9786, preserving all three shots and both ordinary distributions.
Bidirectional adversarial tests cover pressure on the nearest safe receiver
and on the preferred contested receiver.

All five existing runtime ARNs now serve version 5 on READY DEFAULT endpoints.
On 2026-10-23, 57 direct AWS invocations matched local commands, including
mirrored captured frames and refreshed known Fitness Check sessions. CloudWatch
confirmed all 57 under the new profile without telemetry drops/delivery errors.
The portal verdict and performance against a real coordinated press still
require a new Fitness Check and match; no win-rate improvement is established.
