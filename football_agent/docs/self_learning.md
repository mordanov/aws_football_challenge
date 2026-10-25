# How the football agents learn

## What "self-learning" means here

The agents use **bounded, deterministic profile adaptation**, not neural-network
training or autonomous code generation.

Five players run the same tactical implementation. A separate background service
collects observations, proposes one allowlisted parameter change, tests it in a
simulator, and conditionally makes it available to **new matches only**.

The live players do not call an LLM or AgentCore Memory to choose their next
action. They do not rewrite their code, change formation during a match, or
independently adopt recommendations.

```text
Player decisions and errors
          |
          v
CloudWatch log subscriptions
          |
          v
Collector Lambda
  |                  |
  v                  v
S3 evidence      AgentCore Memory
and acknowledgements (exact observation events)
          |
          v
Coalesced learner wake-up
          |
          v
Verify evidence against Memory
          |
          v
Propose a bounded profile candidate
          |
          v
Paired simulator evaluation
          |
     accepted?
     /       \
   no         yes
   |           |
Keep baseline  Conditional ACTIVE update in S3
                           |
                           v
                 New match selects profile
                           |
                           v
                 All five players share
                 one immutable match pin
```

This guide describes the deployed **S3-based** implementation. The alternative
DynamoDB/scheduled implementation is not the active workshop deployment.
See [Automatic shared-memory learning](automatic_learning.md) for provisioning
details and historical deployment receipts.

## 1. Capture observations without blocking tactical decisions

Each player emits structured decision/error telemetry to its existing CloudWatch
log group. Subscription filters forward new matching records to the collector.
There is no automatic historical backfill.

The collector checks the trusted seat/log-group mapping, match identity, command,
canonical snapshot, profile identity and input contract. It preserves typed game
data rather than raw prompts or arbitrary text.

Observations include:

- Match UUID, team side and player seat.
- Snapshot clock, ball/player state and observed score.
- Issued command, or a sanitized runtime error.
- Profile version, profile digest and tactical compatibility generation.
- Source references and telemetry failure/drop counters.

An issued command is an **attempt**, not proof that the game server executed it.
Observed scores are not an authoritative final-result feed. Opponent identity
remains unknown unless established through a separate trusted process.

## 2. Store durable evidence and exact Memory events

The collector creates content-addressed observations in S3 and publishes their
exact documents to AgentCore Memory. Durable acknowledgements record successful
publication. Retries reuse deterministic identities to avoid intentionally
creating a new observation for the same content.

The two services have different responsibilities:

| Service | Responsibility |
|---|---|
| S3 | Evidence archive, publication acknowledgements, work coordination, profile registry, match pins, attempts and evaluation receipts |
| AgentCore Memory | Exact, independently retrievable observation events used to verify the evidence |

These Memory events use extraction mode `SKIP`. They are not semantic summaries,
proven lessons, or model-generated coaching instructions. The learner retrieves
exact events and compares them with acknowledged source documents.

Missing, contradictory or expired selected Memory evidence does not silently
fall back to S3-only learning.

## 3. Start analysis automatically, not necessarily at match end

When learning is enabled, successful collection signals an S3 work latch and
asynchronously invokes the learner. Repeated deliveries are coalesced rather
than launching a separate evaluation for every decision.

The worker waits for approximately **130 seconds of collection quietness**.
Exact Memory verification also excludes sessions with source activity within
the preceding **120 seconds**.

**Quietness is an analysis delay, not match-end detection.** Neither a late
clock, an idle stream nor a new match UUID proves that a match ended. Regulation
is 120 seconds; a tied game can continue into sudden death up to 240 seconds.

There is no EventBridge schedule or permanent polling loop in this deployment.
The worker settles and stops when its pending work is processed. A continuation
can renew the same work owner when more execution time is needed; an expired
lease can recover on later activity.

## 4. Select and validate evidence

The learner discovers recent S3 observations within a seven-day window. Discovery
is bounded to 30,000 references across eight UTC date partitions; selection is
bounded to **20 match-side sessions and 3,000 documents**.

It selects whole sessions. Oversized, expired or capacity-excluded sessions are
not partially sampled into an apparently complete tactical example.

Before proposing a change, the learner requires compatible evidence:

- Exact current profile version, profile digest and code generation.
- A consistent input contract.
- Valid, acknowledged documents matching exact Memory events.
- No disqualifying runtime errors in the match-side group.
- No conflicting snapshots at the same game clock.

Multiple players observing the same clock do not count as independent tactical
support. Deployment probes using the reserved diagnostic match UUID are
explicitly excluded from candidate evidence.

### Important current exclusions

The current candidate policy rejects a whole match-side group if it contains
any non-decision observation, including a startup error at `tick=0`.

Different snapshots at the same frozen clock can also disqualify a match.
Changing only the snapshot tick is tolerated; changing player/ball state at that
clock is not. Post-goal restart frames can therefore prevent a match from
supporting a proposal.

These are current implementation limits, not evidence that every excluded match
was tactically unusable. Relaxing them requires a separate, carefully validated
change; the learner does not automatically ignore errors.

## 5. Generate the only currently supported candidate

The automatic learner currently tests one hypothesis:

> Repeated central defensive exposure may improve if routine pressing uses a
> slightly smaller radius.

A snapshot counts as exposed when:

1. The opponent has resolved possession.
2. The ball is more than 20 canonical units into our half and within 16 units
   of the centre line laterally.
3. No non-goalkeeper teammate is goal-side of the ball within a central
   14-unit lateral band.

Each supporting match needs at least **five distinct exposed game clocks**,
representing at least **20% of its sampled frames**. At least **three distinct
matches** must support the pattern.

The candidate reduces `press_radius` by **2**, with a floor of **12**.
All other numeric fields, formation, tactical style, coach policy and opponent
overrides remain unchanged.

For example:

```json
{
  "baseline": { "press_radius": 22.0 },
  "candidate": { "press_radius": 20.0 }
}
```

This is a testable hypothesis, not a causal conclusion. A defeat, conceded goal
or repeated blocked carry does not directly generate a remedy. The policy
cannot currently fix goalkeeper positioning, passing, carry selection or shot
range, and it cannot invent a new strategy.

## 6. Evaluate before applying

An eligible candidate is evaluated against the baseline using the same tactical
implementation as the serving players.

The fixed evaluation schedule contains **144 simulated matches**:

```text
12 seed blocks
  x 3 opponents
  x 2 playing sides
  x 2 profiles (baseline and candidate)
  = 144 matches
```

The opponent pool is the current baseline, `press_aggressive`, and
`park_the_bus`. Baseline and candidate use the same seed/opponent/side pairing.

Acceptance requires all of the following:

- Positive mean paired goal-difference improvement.
- A one-sided exact sign-test p-value no greater than **0.01**, using 12
  aggregated seed blocks; ties count as failures.
- No mean regression against any opponent in the pool.
- No increase in illegal actions or agent crashes for either team.
- Complete, valid evaluation evidence within the **600-second** cooperative
  evaluation budget.

The worker checks that at least 660 seconds remain before reserving an attempt.
A short remaining invocation budget causes deferral, not an evaluation attempt.

There is **one durable attempt per active generation**. Additional observations
or retries cannot repeatedly evaluate the same generation until it passes.
Failures and inconclusive results leave the baseline unchanged.

Simulator acceptance is not proof of improvement on the real platform.
The statistical test has independence assumptions, and fixed seeds/repeated
candidate selection do not provide a general sequential-testing guarantee.

## 7. Promote atomically and preserve running matches

The learner records the evaluation evidence. If accepted, it conditionally
updates the S3 `ACTIVE` pointer only if the baseline identity and generation
still match. A concurrent change prevents stale promotion.

Existing match pins are never rewritten.

On a new match's first profile selection, players resolve or create a durable
pin containing the selected profile digest and generation. Concurrent players
re-read the winning pin, so all five seats use the same profile.

S3 cannot atomically update `ACTIVE` and create a separate match-pin object.
A new match racing with promotion can select the preceding profile generation;
the winning pin still keeps all five players consistent.

Registry corruption, unavailable data or incompatible profile content fails
the invocation explicitly rather than silently choosing a different local
profile for one player.

## 8. Keep code rollouts separate from profile learning

Profile adaptation changes allowlisted configuration, not deployed code.
A tactical code change requires a coordinated deployment and a new compatibility
generation/namespace when its interpretation changes.

The v11 rollout uses:

| Item | Value |
|---|---|
| Code generation | `match_profiles_tactics_v3` |
| Team namespace | `aleksalotl_tactics_v3` |
| Learning stack | `final-agent-s3-tactics-v3` |
| Initial profile | `r4v7_route_stamina`, generation 1 |

Old evidence and match pins are retained, not relabelled as current-generation
training data.

The rollout also sets `promotion_not_before_ms`. Until that deadline, collection
and analysis continue, but an otherwise eligible candidate returns
`rollout_cooldown` without reserving an attempt. This allows legacy runtime
sessions to drain before profile promotion.

For the 2026-10-24 v11 rollout, that deadline is
**2026-10-24 15:38:04.078 UTC**. It is specific to that rollout and must not be
reused for a later deployment.

## 9. Observe the learning loop

Relevant objects live under:

```text
final-agent-learning/<team>/
  observations/<match>/<side>/<observation>.json
  observations/_days/<date>/...
  acknowledgements/...
  work.json
  last-learning.json
  registry/
    ACTIVE.json
    profiles/...
    matches/<match>/<side>.json
    generations/...
    attempts/...
    evaluations/...
```

`last-learning.json` reports analysis status, evidence selection, publication
recovery and generation. `work.json` records dispatch/lease state.

Typical outcomes include:

| Outcome | Meaning |
|---|---|
| `insufficient_evidence` | The current evidence does not produce an eligible allowlisted candidate |
| `rollout_cooldown` | A candidate exists, but the legacy-session promotion guard has not expired |
| `already_attempted` | This active generation already has its durable evaluation attempt |
| Evaluation rejection | The candidate failed the gate; ACTIVE remains unchanged |
| Successful promotion | A verified candidate conditionally becomes ACTIVE for new pins |

Collection and learning have separate enable switches. Disabling future
collection/dispatch is not proof that previously running work has stopped.
Inspect running work and its latch before treating a switch as a fence.

CloudWatch alarms and the separate S3 async-failure archive provide operational
signals. There is no external SNS notification channel in this workshop variant.
Logs, Memory events, S3 storage, waiting workers and simulations incur costs;
bounded concurrency and evaluation budgets are not a monetary spending cap.

## What this system does not guarantee

- It does not learn after every match or automatically react to every defeat.
- It does not know official match completion or opponent identity from telemetry.
- It does not reconstruct unobserved opponent commands or prove goal causality.
- It does not train model weights or use an LLM in the automatic learning loop.
- It does not modify an ongoing match's formation or pinned profile.
- It does not repair arbitrary tactical code defects.
- It does not guarantee that a simulator-approved candidate wins real matches.

The intended safety rule is: **collect broadly, verify exact evidence, propose
narrowly, evaluate once, and apply only to new match pins.**

## Implementation map

| Component | Source |
|---|---|
| Observation validation | `agent/learning_observations.py`, `agent/learning_collector.py` |
| S3 outbox and exact Memory source | `agent/s3_learning.py`, `agent/learning_source.py` |
| Work latch and Lambda handlers | `agent/s3_trigger.py`, `agent/s3_worker.py` |
| Candidate generation | `agent/learning_policy.py` |
| Learning orchestration | `agent/learning_worker.py` |
| Paired evaluation | `agent/learning_evaluation.py` |
| Conditional registry and match pins | `agent/s3_registry.py`, `agent/profile_registry.py` |
| Live profile selection | `agent/runtime.py`, `agent/main.py` |
| S3 deployment | `deploy/s3_learning.py` |
