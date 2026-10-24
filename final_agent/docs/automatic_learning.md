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

## S3 backend for workshop permissions

The alternative backend uses ordinary permitted S3/Lambda/AgentCore operations,
not DynamoDB, SNS, SQS or EventBridge. The existing DynamoDB backend remains
available but is not a dependency of this alternative.

```text
CloudWatch -> collector Lambda -> immutable S3 observation/day index
                              -> exact Memory event -> immutable S3 acknowledgement
                              -> S3 CAS work latch -> async learner Lambda
130 seconds without collection activity -> exact Memory verification
 -> bounded candidate -> fixed simulator gate -> S3 ACTIVE compare-and-swap
async Lambda failure -> separate owned S3 failure archive + CloudWatch alarms
```

S3 is the authoritative coordination/evidence archive. Memory is the exact
observational store, not a lock or mutable active-profile pointer. `CreateEvent`
idempotency does not provide compare-and-swap among different requests.
Conditional S3 creates preserve artifacts, match pins, attempts and evaluations;
ETag-conditional writes update ACTIVE and the work latch. Expected bucket owner
and account-scoped IAM conditions guard every storage boundary.

The registry prefix is `final-agent-learning/<team>/registry/`: `ACTIVE.json`,
`profiles/`, `generations/`, `evaluations/`, `attempts/`, `proposals/`, `results/`
and `matches/<UUID>/<side>.json`. The first successful conditional pin selects
one caller's validated ACTIVE snapshot. Unlike the DynamoDB transaction, S3
does not atomically check ACTIVE and create a separate pin: a racing promotion
can select the preceding generation for that new match. All five players still
use the same immutable winning selection. Existing pins never change.
Immutable generation ancestry binds promotion to the full evaluation and
recovers committed results after uncertain responses or a crash before result
materialization. A single-object ACTIVE CAS is the promotion commit point.

Observations and acknowledgements are immutable, separately scoped objects.
UTC day discovery references keep scans bounded to seven days rather than the
whole historical archive. At most 30,000 discovery references across eight UTC
partitions are ranked by each session's most recent S3 modification. The newest
whole sessions fitting 20 sessions/3,000 documents are selected; oversized,
capacity-excluded and expired sessions are excluded whole, never partially
sampled. Eight parallel readers and per-call immutable-document reuse bound
network overhead. The learner's stored status includes the evidence selection
plan, exclusion counts, publication recovery and analysis duration.
Selected sessions are checked in full. Pending
publication is recovered before analysis. Entire sessions crossing Memory's
retention boundary are excluded; partial expired sessions are not tactical
support. Missing/contradictory in-window events still fail explicitly.

The work latch coalesces log bursts under one owner with an expiring lease.
Dispatch remains pending until Lambda accepts it; failed dispatch can be retried.
Revision-conditional settlement prevents arrivals during evaluation from being
lost. A learner waits for collection quietness, not official match completion.
An invocation nearing its wait budget continues asynchronously with the same
owner token. Before reserving an evaluation, it checks that at least 660 seconds
remain for the 600-second evaluator and finalization. No reservation is consumed
merely because a delayed worker needs a fresh invocation.

The learner's reserved concurrency is one; deliberate Lambda recursion is
enabled only for this coalesced continuation. Continuous incoming games can keep
it active and incur billed waiting time. No new activity means the worker settles
and stops; there is no perpetual timer. Lambda async retries are bounded; expired
leases recover on subsequent activity, not an unconditional scheduled guarantee.
Errors/exhausted invocations are retained under
`aws/lambda/async/<function>/` in the separate owned `failure_bucket`. Replay a failed
collector's `requestPayload`, or re-signal pending learner work deliberately;
do not delete evidence/pins to retry an evaluation. CloudWatch alarms have no
SNS actions and require dashboard/metrics monitoring.

Start from `deploy/s3_learning.example.json`. Use a separate stack name
`final-agent-s3-learning`, `alarm_topic_arn=""`, an existing private `failure_bucket`,
and learning disabled. The archive must belong to the configured account and
have public access blocked and default encryption enabled. It is external to
CloudFormation and is never deleted with the stack. Lambda validates write access
to the whole archive bucket; a dedicated bucket avoids granting broad writes to
the assets bucket. Workshop bucket tagging is not permitted, so create/configure
the archive with permitted S3 operations, not a tagged CloudFormation resource:

```sh
python deploy/s3_learning.py plan --config local/s3-learning.json
python deploy/s3_learning.py apply --config local/s3-learning.json --approve
python deploy/s3_learning.py bootstrap --config local/s3-learning.json --approve
```

Both Lambda roles use explicitly permitted `AgentCore-*` names. Player CDK
configuration uses `afcLearning.backend="s3"`, `bucket`, `teamId`, `enabled`.
Players can read registry objects and conditionally create only match pins;
they cannot write ACTIVE, profiles or evaluation outcomes. Keep learning disabled
through the five-player rollout and baseline/digest verification, then enable
and reapply the S3 stack config. This changes no formation or tactical baseline.

The fixed diagnostic match UUID
`6dc3a574-299e-44e4-81aa-6eb36c5776ef` is reserved for live-shaped deployment
probes. Its records exercise collection and exact Memory delivery but are
explicitly excluded from candidate evidence. Fitness probes keep their existing
synthetic-session handling.

## Original DynamoDB activation attempt

The activation attempt on 2026-10-23 was blocked by the workshop participant
role's permissions. CloudFormation `final-agent-learning` reached
`ROLLBACK_COMPLETE`; no collector, learner, subscriptions or registry were
activated. Confirmed denied actions were `dynamodb:DescribeTable`,
`sns:CreateTopic` and `sqs:CreateQueue`. The failed table/queue resources have
no physical IDs in the stack's resource list. An artifact ZIP was uploaded
to the existing private CDK assets bucket; temporary log groups were rolled back.
The existing five runtime deployments and their policies/environment were not
changed, and `afcLearning.enabled` remains false.

For that original backend, an authorized administrator would have to provision the stack's resources or grant the
deployment identity the required lifecycle permissions for the scoped resources.
The three denials are confirmed blockers, not an exhaustive permissions list:
provisioning did not reach every resource. Retrying requires resolving the
failed CloudFormation stack (inspect any retained resources before explicit
deletion/recreation), then bootstrap, controlled player rollout, verification,
and finally learner activation. Do not enable players against an absent registry
or remove coordination/failure monitoring to bypass workshop restrictions.
The S3 alternative above replaces those components using normally permitted
services rather than attempting denied actions or changing participant policies.

## Verified S3 activation

The S3 alternative was activated on 2026-10-23 (UTC):

- CloudFormation `final-agent-s3-learning` completed, with five log subscriptions,
  collector/learner Lambdas, scoped `AgentCore-*` roles, account-bound S3 failure
  destinations and visible CloudWatch alarms.
- All five existing runtimes and DEFAULT endpoints are **version 9, READY**.
  Their ARNs are unchanged; deployment source is exclusively `final_agent`.
- ACTIVE generation 1 remains the exact `r4v7_route_stamina` profile, digest
  `934692a57fd8b542227d31b24b88292ffb186a3585d60116abd7f6fb3bb4d7b5`.
  All five seats emitted this same digest and shared the diagnostic match pin.
- Fifteen recorded live-shaped/fitness invocations returned correctly routed
  commands. Ten distinct diagnostic observations were verified byte-for-byte
  between S3 and Memory, with ten durable acknowledgements.
- Both collector and learner have learning enabled. Real post-activation log
  delivery automatically dispatched the learner, which settled its work latch
  and persisted `status=insufficient_evidence`, generation 1. No profile was
  forced through acceptance; diagnostic records cannot support a candidate.

### Safety pause and coordinated tactical integration

Automatic application was subsequently disabled in both Lambdas on
2026-10-23; CloudWatch collection and S3/Memory publication remain enabled.
The latest completed learner run verified 370 documents from two sessions in
14,867 ms, excluded no sessions, returned `insufficient_evidence`, and cleared
the work latch. ACTIVE remains generation 1 and the failure archive is empty.

Another development session switched the original shared worktree to
`rush-desperate-attack` and added commits `850f586` and `1485ea2` during this
implementation. Direct S3 inspection confirmed that all five live version-9
artifacts already contain the `1485ea2` tactical code. Their tactical SHA-256 is
`eaead9a76ca0c9d2c722f929daea691760204e0105f940b22408e92be7699f2c`.
That branch currently fails two legacy golden-decision checks.

The S3 changes were preserved in a separate worktree and then merged with the
intended rush/desperate tactics. Both serving and evaluation now use the same
tactical implementation. Compatibility is `match_profiles_rush_v2`; the fresh
registry/observation namespace and Memory actor use team `aleksalotl_rush_v2`.
The previous `aleksalotl` registry, evidence and immutable match pins are retained
unchanged. Automatic proposals now require an exact digest and generation;
missing legacy labels are not accepted. Old sessions can finish without their
evidence being eligible for the new learner.

The existing golden fixture is unchanged: its regression checks explicitly
disable rush/desperate modes to test legacy behavior. The merged dedicated
scenario tests cover enabled rush/desperate behavior. The complete integrated
suite passes without overwriting golden expectations.

Rollout order remains learning disabled -> fresh baseline bootstrap -> all five
players and shared diagnostic pin verified -> learner enabled. Enabling requires
serving artifacts and evaluator package to match, not merely a successful test run.

### Integrated rush-compatible deployment

The follow-up rollout completed with the intended rush tactics unchanged:

- Stack `final-agent-s3-rush-v2` owns the new team namespace
  `aleksalotl_rush_v2`. The previous stack's subscriptions and learner were
  disabled; its registry, evidence and match pins were retained.
- All five existing runtimes/DEFAULT endpoints are **version 10, READY**,
  with unchanged ARNs and the same tactical SHA-256 as commit `1485ea2`.
  Serving tactical environment overrides are absent, so players and evaluator
  use the same default rush/desperate policy. Changing these overrides requires
  another compatibility rollout, not a same-generation configuration tweak.
- New ACTIVE generation 1 is the unchanged `r4v7_route_stamina` profile with
  compatibility `match_profiles_rush_v2`, digest
  `ead892a960fb0ed4c17386f600d75edb2580139244df1a28824c1b33a9e2db69`.
  Fifteen live-shaped/fitness invocations routed correctly. All five seats used
  the same new pin; ten exact diagnostic documents were verified in Memory.
- S3 inspection verified the deployed learner archive's tactical, protocol,
  registry and evaluation code against the local source and serving artifacts.
  The packaged worker includes the simulator.
- Both new Lambdas have learning enabled after those checks. Diagnostic match
  evidence remains excluded from automatic proposals.

A separate integration exercise ran the full 144-match paired schedule with
the merged tactics. The proposed press-radius reduction 22 -> 20 was rejected
for opponent regression (mean paired goal-difference delta +0.02778, sign-test
p=0.9270); no crashes or illegal actions occurred. This was an offline plumbing
check, not eligible live evidence, a production promotion, or a consumed
generation attempt. The registry baseline was not changed to force acceptance.

The live automatic cycle subsequently verified the ten diagnostic records,
excluded them from candidate evidence, persisted `insufficient_evidence` in
1,097 ms and cleared its work latch. The failure archive was empty.

To preserve match-wide consistency during the namespace migration, the S3
configuration supports `promotion_not_before_ms` (nonnegative UTC epoch
milliseconds, default 0). Before this deadline an otherwise eligible candidate
produces `rollout_cooldown` without consuming a generation attempt. Evidence
collection/analysis continue and the work latch settles normally; later
activity after the deadline automatically resumes eligibility, with no
manual re-enabling or perpetual timer. Insufficient evidence still reports
`insufficient_evidence`.

For this deployment the deadline is **2026-10-24 07:48:37 UTC**
(`1792828117195`), computed from the latest five-player update plus the verified
28,800-second maximum session lifetime and 120-second margin. Older sessions
retain their prior profile while they drain. Both new Lambdas remain enabled;
the deadline is a promotion guard, not a disabled collector or a claim that
any real match has ended.

The failure archive is a separate private AES256-encrypted owned bucket,
retained outside the stack. The first S3 rollout exposed Lambda's destination
permission validation and denied bucket tagging; those were resolved by using
this dedicated existing archive, not by broadening participant permissions.
The failed intermediate S3 stack metadata was removed while preserving that
archive. The earlier inactive DynamoDB stack remains historical rollback metadata.

Fresh match sessions use version 9; already existing AgentCore sessions may
continue on their original runtime version. Keep the initial baseline unchanged
through a rollout and disqualify mixed-code evidence. Registry cache misses add
S3 network dependency/latency; small synthetic probes do not establish a
production p95 latency guarantee.

## Prepared tactics-v2 integration (not deployed)

The source revision integrating the tactics-v2 PR has compatibility generation
`match_profiles_tactics_v3`, player registry team `aleksalotl_tactics_v3` and
stack `final-agent-s3-tactics-v3`. These are distinct from the deployed
`match_profiles_rush_v2` / `aleksalotl_rush_v2` / `final-agent-s3-rush-v2`.
This code review fix does not deploy players, change active profiles or disable
the live learning stack.

Dynamic link/runner anchors now apply the defensive BALLSIDE shift before
the minimum separation correction and final movement guards. This preserves
both features when their default-on flags are combined. Legacy golden decisions
remain unchanged in `default_style_golden.json` with optional modes disabled;
the independent `tactics_v2_golden.json` exercises explicitly enabled defaults.
Generate them separately with `python tests/golden.py` and
`python tests/golden.py --tactics-v2`. Review expected behavioral differences
before regeneration. The BALLSIDE correction changes only 39 forward MOVE_TO
decisions in the 750-command tactics-v2 fixture.

Before deploying this revision:

1. Copy `deploy/s3_learning.example.json`, supply the actual account, bucket,
   existing Memory, failure archive and five log groups; keep learning disabled.
   The example and player CDK binding share the new team/stack names.
2. Provision and bootstrap the new namespace. Do not modify old ACTIVE objects
   or immutable match pins. Never reuse the rush-v2 namespace for these tactics.
3. Deploy all five players and the evaluator from the same source revision,
   with matching tactical flags. Verify endpoints, artifacts, current-generation
   digests and a shared match pin while learning remains disabled.
4. Set `promotion_not_before_ms` to the latest completed player update plus
   the actual maximum legacy-session lifetime and a safety margin. Enable the
   new collector/learner and retire the old subscriptions. The deadline guards
   promotion until legacy sessions expire; do not reuse the previous rollout's
   timestamp. Until then keep the new numeric baseline identical to the old one.

The registry and proposal guards reject previous-generation documents/evidence;
changing only the generation label is not a substitute for this coordinated
serving/evaluator deployment.
