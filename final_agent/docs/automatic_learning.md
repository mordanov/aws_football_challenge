# Automatic shared-memory learning

This implements the automatic loop from [the reconnaissance](recon-results.md):
automatic provisional observation delivery and match-wide immutable profile
selection, followed by bounded candidate generation, paired simulator evaluation
and conditional promotion. No deployment is implied by merging this code.

`redesign_4` is unchanged. Default player startup remains the packaged
`r4v7_route_stamina` profile with no registry/network access. No live player
reads AgentCore Memory or invokes an LLM.

## Provisional shared observations

```text
five existing CloudWatch log groups
  -> JSON decision/error subscription filters
  -> collector Lambda (reserved concurrency 2)
  -> DynamoDB outbox PENDING
  -> AgentCore Memory CreateEvent (deterministic token, extraction SKIP)
  -> outbox SENT
```

The collector validates canonical snapshots, command identity, input contract
and the trusted seat/log-group mapping. It stores only typed game data, profile
identity, telemetry drop/failure counters and source references/digests. It
excludes raw requests, reasons, unknown coach text, free-form exceptions and
unverified opponent IDs. Framework wrappers are not extra evidence.

The separate `provisional_team_observation_v1` schema always has
`evidence_status=provisional`, `final_result=null`, `opponent_id=null`.
Observed scores remain snapshot scores. A late clock, quiet log stream or new
UUID never marks a match complete. Tied clocks after 120 seconds remain legal.
Existing complete `ScoutingNote`/manifest requirements are not weakened.

Exact events use actor `team_<team_id>` and session
`observations_v1_<match UUID>_<team side>`. They deliberately skip semantic
extraction: provisional observations must not become supposedly proven lessons
in the existing opponent-scoped retrieval path. A future learner must validate
this schema rather than feeding it into the completed-match scout/trainer.

Observation IDs hash the canonical normalized content, not CloudWatch delivery
IDs. Replayed deliveries use the original outbox envelope and timestamp.
Uncertain Memory responses retain PENDING and retry the same idempotency token.
SENT outbox records have no TTL. Source-first retention does not establish that
the first-delivered source was chronologically first in the engine.

Different profile/snapshot variants are preserved, not voted away. Multiple
players, repeated ticks and frozen clocks are not independent tactical support:
`support_key` groups one match/side/game-clock observation. Consumers must also
reject mixed profile/code/input contracts before comparing behavior.
An issued command remains an attempt, never acknowledgement or goal causality.

Delivery is at-least-once, not an exactly-once platform recording guarantee.
Telemetry can already be lost before CloudWatch. Poison batches or exhausted
retries go to the retained SQS failure destination; CloudWatch alarms cover
worker errors, throttles, destination failures, learner errors and queued failures.
An existing monitored SNS topic can be supplied in the same account/Region.

### Provisioning

Use the existing environment's package manager to install `.[learning]`
(plus `.[dev,offline,deployment]` as needed). Packaging vendors the installed
boto3/botocore and dependencies, including the SDK model supporting
`extractionMode=SKIP`, rather than relying on Lambda's bundled SDK.

Copy `deploy/learning.example.json` to an ignored local config. Replace the
account/Region, actual existing Memory ID, existing private artifact bucket,
SNS topic, and the five actual runtime log groups **in seat order**
(GK, DEF, MID, FWD1, FWD2). Do not use the example identifiers as real resources.
Start with `collection_enabled=false` and `learning_enabled=false`.
An empty `alarm_topic_arn` creates an owned SNS topic instead; CloudWatch alarms
remain visible, but external notifications require a subscribed destination.

```sh
python deploy/learning.py plan --config local/learning.json
python deploy/learning.py package --output local/learning.zip
# Explicit AWS writes, after inspecting the plan and AWS identity:
python deploy/learning.py apply --config local/learning.json --approve
python deploy/learning.py bootstrap --config local/learning.json --approve
```

`plan` makes no AWS calls. Write commands require `--approve` and verify the
configured AWS account. Existing stacks must carry this team's ownership tags.
The stack creates a registry table, separate indexed outbox table, collector
and learner roles/Lambdas,
failure queue, alarms and an **unattached** player managed policy. It does not
change the five player runtimes or their roles, or create/change Memory itself.
The artifact bucket must already belong to the expected account in that Region.
Check subscription quotas on existing runtime log groups before enabling.

When ready, set `collection_enabled=true` and apply the config again. The five
subscriptions start delivering **new** matching events; there is no automatic
historical CloudWatch backfill. This collection-only activation needs no player
redeploy and is independent of optional profile selection.

Set `collection_enabled=false` and reapply to remove the subscriptions. This
stops new admission, not already queued Lambda work. Do not delete the outbox
to stop collection: that removes the durable deduplication boundary.
For emergency containment, set the collector's reserved concurrency to zero
while removing subscriptions; queued invocations can then fail/age into the
failure queue. Inspect and deliberately replay them before restoring admission.

For a failed Lambda invocation, the SQS destination message contains an
invocation record. Extract its `requestPayload` (the original `awslogs` envelope)
and invoke the collector with that envelope, **not** the SQS wrapper. Do not
delete the queue message until the replay succeeds. PENDING/SENT state permits
replay after partial batch progress without intentionally adding evidence.
The queue retains messages for 14 days; this is not unlimited failure retention.
Outbox envelopes retain source evidence beyond Memory's configured event expiry.

### Costs and operational limits

There is no model call, interpreter session or LLM evaluator invocation anywhere
in this loop. Lambda concurrency/retries, payload sizes and SDK timeouts are
bounded; DynamoDB uses on-demand billing. Each distinct attempted decision can
produce a Memory event and outbox writes. These limits are **not** a dollar cap.
PITR, retained tables/queue, S3 artifacts and accumulated outbox/pins incur
storage costs. Configure account budgets and monitoring before enabling; plan
deliberate archival without dropping deduplication or allowing old match rebinding.

## Match-pinned tactical profiles

`DynamoProfileRegistry` stores content-addressed canonical JSON directly in
DynamoDB (maximum 32 KB). There is no S3/profile-fetch dependency on the live
selection path. A profile document contains `code_generation` and a validated
`Profile`, with a SHA-256 digest over the whole document.

```text
TEAM/<team>/ACTIVE            digest + monotonically increasing generation
TEAM/<team>/PROFILE/<digest>  immutable profile document
TEAM/<team>/MATCH/<UUID>/<side> immutable winning digest + generation
```

These are conceptual names; actual primary keys use `#` separators.
`bootstrap` initializes the recommended artifact and generation 1; repeating it
is idempotent only while ACTIVE still selects that digest. It cannot overwrite
a different active version. There is no manual promotion/rollback CLI.

A first match invocation consistently reads the match pin. If absent, it reads
ACTIVE, validates content/compatibility, and transactionally checks the same
active generation/digest while conditionally creating the pin. Concurrent
losers re-read the winning pin. Uncertain transaction responses also re-read;
they never pick an independent local fallback. Three attempts bound contention.
Each process caches at most 64 match selections; cache eviction/restart re-reads
the durable pin. Each invocation uses local profile/settings variables, so
interleaved match UUIDs cannot overwrite the runtime's startup profile.

### Optional player activation

First provision/bootstrap the stack. In a controlled code-rollout window with
no matches admitted, set `afcLearning.enabled=true` in
`deploy/agentcore/cdk/cdk.json`, with the same `stackName` and `teamId`.
The default is false. The CDK stack imports the learning stack's exported
`RegistryTable` and `PlayerPolicyArn`, attaches the policy to the five existing
execution roles, and configures **all five** runtimes with:

```text
AFC_PROFILE_TABLE=<RegistryTable output>
AFC_TEAM_ID=<same stable team_id as the collector/registry>
```

The policy permits team-scoped reads plus transactional MATCH writes/ACTIVE
checks, not profile/ACTIVE writes. Runtime code never bootstraps the registry.
The deployed player SDK already depends on boto3 through bedrock-agentcore.
Keep the normal packaged profile for synthetic `scrimmage-drill-*` fitness
sessions. Outside those sessions, enabling selection requires a recognized UUID
and matching session team/seat; unknown/misrouted sessions fail explicitly.
Do not use operator `AFC_MATCH_ID` to override a contradictory live session.

Digest, code-generation and registry errors emit `profile_selection_error` and
fail that invocation instead of returning a success-shaped local tactical
fallback. Successful telemetry includes `profile_digest` and `code_generation`.
Selection adds first-match/cache-recovery network latency and an availability
dependency. SDK connect/read timeouts are 0.5 seconds with one attempt, not a
measured end-to-end p95 guarantee. Measure real first-request latency, warm
latency/errors and verify the same digest for all five seats before admitting
competition traffic. Until then leave learning disabled. This CDK binding
persists across subsequent player deployments; do not replace it with ad-hoc
console environment or role updates.

Never TTL/delete a pin while a match UUID can still be retried. Never replace a
profile document under the same digest. A future ACTIVE update affects new pins
only; rollback must likewise leave existing pins unchanged. Generation must
increase even on rollback. `code_generation` is a compatibility contract, not
proof all five runtime deployments updated atomically; change it when the
tactical interpretation changes and perform a controlled code rollout.

## Scheduled learning and automatic promotion

Enable `learning_enabled=true` **only after** all five players have compatible
selector code and the same initial profile pin has been verified. The
15-minute EventBridge schedule invokes a separate learner Lambda (reserved
concurrency 1, timeout 900 seconds). Setting the flag false and reapplying
disables new scheduled jobs and the handler; an already running job must be
allowed to finish or explicitly stopped before treating the switch as a fence.

The learner queries the team's recent outbox index (seven days, at most 3000
observations/20 match-side sessions). A truncated index window fails closed,
because interleaved matches can all have observations outside the window.
It excludes sessions with pending publication, recent activity within 120 seconds
or a future delivery timestamp. Quietness is an **analysis eligibility delay**, not
proof of completion. Interleaved UUIDs remain separate. It retrieves the exact
Memory events and verifies them against acknowledged outbox envelopes, source
timestamps and content digests. Missing/expired/contradictory Memory evidence
fails rather than silently reverting to semantic text or outbox-only learning.
Unexpected unindexed Memory events also fail closed, including events omitted
by index propagation delay. A later scheduled job may observe a complete index.

The initial deterministic remedy is intentionally narrow: at least five
distinct sampled clocks of central opponent possession without a goal-side
central outfielder, comprising at least 20% of sampled clocks in each of three
distinct matches, proposes reducing `press_radius` by two (floor 12).
Mixed profile/code/input contracts, conflicting same-clock snapshots and
runtime errors disqualify a match. Current ACTIVE profile identity must match;
legacy static logs may have no digest but must have the exact version.
This observed exposure is not an asserted tactical cause. It creates a
hypothesis to test; formation, style, coach policy, opponent overrides and all
other numeric fields remain unchanged.

One ATTEMPT reservation per active generation is permanent. An invocation owner
token recovers an uncertain reservation acknowledgement without stealing another
invocation's reservation. Additional evidence,
parallel invocations or a restart cannot rerun significance tests until ACTIVE
changes. A failed evaluator leaves the generation unchanged, records a failure
receipt and alerts; abrupt termination can leave a reserved attempt without a
result, requiring operator investigation rather than an automatic retry storm.

The immutable evaluation policy runs 144 matches: baseline and candidate against
three fixed profiles, both sides and twelve fixed seed blocks. It uses the current
`final_agent` tactics, not the reference entrant. Offline packaging includes the
repository's `football_lab` engine; the serving players remain standalone.
Acceptance requires positive paired goal-difference change, one-sided exact
sign-test p <= 0.01 over twelve aggregated seed blocks (ties counted as failures),
no opponent-pool mean regression and no increase in illegal actions/crashes for
either side. Missing fixtures, invalid results, identical policies, timeout or
inconclusive results cannot pass. Evaluation has a 600-second cooperative
budget checked between matches. This model's independence/symmetry assumptions
and simulator-to-platform transfer are limitations, not proven real efficacy.

The learner persists the exact evaluation receipt. Large raw evaluations are
stored in immutable content-addressed chunks, with their SHA-256 and ordered
keys in the terminal receipt; crash-heavy rejection reports are not discarded.
Rejection does not modify
ACTIVE. Acceptance transactionally updates ACTIVE only if its original digest
and generation still match, and creates the promotion receipt in the same
transaction. An operator/concurrent change makes the result `stale_baseline`;
uncertain responses recover from the immutable receipt. Existing match pins
remain unchanged. The runtime compatibility generation must be incremented when
its tactical semantics or evaluation policy changes.

There is no automatic real-result win-rate claim, official completion inference,
opponent identification, free-form LLM/code generation or automatic formation
change. The empty/insufficient/rejected paths are expected healthy outcomes.

## Activation status

The activation attempt on 2026-10-23 was blocked by the workshop participant
role's permissions. CloudFormation `final-agent-learning` reached
`ROLLBACK_COMPLETE`; no collector, learner, subscriptions or registry were
activated. Confirmed denied actions were `dynamodb:DescribeTable`,
`sns:CreateTopic` and `sqs:CreateQueue`. The failed table/queue resources have
no physical IDs in the stack's resource list. An artifact ZIP was uploaded
to the existing private CDK assets bucket; temporary log groups were rolled back.
The existing five runtime deployments and their policies/environment were not
changed, and `afcLearning.enabled` remains false.

An authorized administrator must provision the stack's resources or grant the
deployment identity the required lifecycle permissions for the scoped resources.
The three denials are confirmed blockers, not an exhaustive permissions list:
provisioning did not reach every resource. Retrying requires resolving the
failed CloudFormation stack (inspect any retained resources before explicit
deletion/recreation), then bootstrap, controlled player rollout, verification,
and finally learner activation. Do not enable players against an absent registry
or remove DynamoDB coordination/failure monitoring to bypass workshop restrictions.
