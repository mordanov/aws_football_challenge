# Match boundary reconnaissance and automatic learning decision

Date: 2026-10-23.
Input reviewed: repository-root `05-match-end-detection-spec.md` (draft).
Scope: evidence review and architecture decision, not an activated automation
service or runtime rollout. `redesign_4` remains unchanged.

## Decision

Use AgentCore Memory automatically as an asynchronous, source-linked team
evidence/lesson store. Do not use it as a distributed lock, an authoritative
match-result source or the per-tick tactical coordinator.

Separate automatic evidence capture from automatic promotion:

1. Capture and checkpoint observations while they arrive, without declaring a
   final result or inventing an opponent identity.
2. Accumulate source-linked candidate lessons; repeat processing must be
   idempotent, and contradictions must be retained.
3. Evaluate candidate settings against the current pinned baseline.
4. Promote only validated settings for newly pinned matches through an atomic
   deployment-profile registry. Existing matches retain their pinned profile.

This is a change from the existing operator-only approval policy and is not yet
implemented or enabled by this document. Initial player integration requires
one coordinated code rollout; later supported profile-only promotions need not
update five AgentCore runtime versions.

## Step 0 results

### Match identity: already available, with a known boundary

The runtime receives SDK request context `session_id` in
`agent/main.py:create_app`. `agent/runtime.py:Runtime.handle` recognizes:

```text
<UUID>-team[01]-pos[0-4]
```

It emits that UUID as `match_id`, separately from the game-state payload. Both
captured matches below already contain this identity in decision and error
records. No raw-payload logging or additional practice match is needed merely
to recover their UUIDs. This does not prove that a `matchId` field exists inside
the raw payload or guarantee that every future session follows this format.

Unknown session formats must remain explicitly unbound. Do not infer identity
from inactivity, score resets or tick resets.

### Clocks and ticks: not global identity or a terminal contract

Recomputed from the two saved standalone application captures:

| UUID | Decisions | Distinct ticks | First tick / clock | Last tick / clock / home-away score |
|---|---:|---:|---|---|
| `a5ddac07-007f-4d78-8c97-cb7f9b1fcc42` | 386 | 81 | 3176 / 1.6999986 | 11179 / 119.49196 / 2:1 |
| `2d4dd176-ca81-4dd6-88de-e3e99b9a3e97` | 650 | 134 | 3201 / 1.9799984 | 16507 / 239.91443 / 1:1 |

Both carry `duration=120`. Neither begins at tick zero in the captured decisions.
There are respectively 18 and 12 adjacent equal-clock tick pairs; no decreasing
clock was observed within either capture. These samples do not establish reset
behavior for every match or a platform-wide sequence counter.

The tied match includes:

```text
tick10405  clock117.852234  score1:1
tick10504  clock119.8319    score1:1
tick10605  clock121.85156   score1:1
tick10702  clock123.79124   score1:1
...
tick16507  clock239.91443   score1:1
```

The user-confirmed rule is 120-second regulation, followed when tied by sudden
death until the first goal or240 seconds. Therefore `t >= duration -4` cannot
mean final. Even a clock/score combination consistent with the rules is a
terminal candidate, not acknowledgement of the platform's final result.

### Delivery gaps and overlapping observation windows

Using `_timestamp` from the saved CloudWatch application records:

| UUID prefix | First delivery UTC | Last delivery UTC | Largest combined gap | Largest single-runtime gap |
|---|---|---|---:|---:|
| `a5ddac07` | 20:56:45.754 | 20:59:25.793 | 2.029s | 4.313s |
| `2d4dd176` | 20:57:14.173 | 21:01:40.290 | 2.015s | 4.233s |

The windows overlap. This establishes interleaved delivery for two match IDs,
not necessarily simultaneous engine execution. A new UUID cannot prove that
the previous UUID completed. A single team-wide "last match" state is inadequate.

There is no positive between-match gap to calibrate from this pair: using these
captures to select a safe30/60-second deployment window would be unfounded.
The small observed within-match gaps do not bound future logging outages,
delivery delay, lost telemetry or platform stalls.

### Opponent and official result

All386 and650 decision records have `opponent_id=null`. A UUID identifies the
match, not the opposing team. Team-wide lessons about our own behavior can be
stored without opponent identity, with the missing identity explicit.
Opponent-specific scouting must remain quarantined/unbound until a trusted
mapping is available; do not use a shared "unknown opponent" scouting bucket.

The last observed score must remain `last_observed_score`, never silently
`final_result`. The complete-match `ScoutingNote` and existing trainer approval
contracts cannot be bypassed by setting `complete=true` after a quiet period.
A new provisional-observation contract is needed for automatic team evidence.

### Portal API and practice control

No authenticated portal browser session or documented football results/status
API is available in this workspace. Repository research
`redesign_2/PROTOCOL_RESEARCH.md`, section9, also records this gap.
Consequently a portal completion/idle signal is **unverified**, not nonexistent.
No private endpoint, browser token, callback or match scheduling capability was
invented or exercised in this reconnaissance.

## Corrections to the supplied draft

| Draft assumption | Decision |
|---|---|
| `final = t >= match_len -4` | Replace with `terminal_candidate` interpreted using regulation and sudden-death rules; never treat as authoritative completion |
| New UUID proves previous match completed | Reject; maintain independent per-UUID observations |
| Quiet30s without final means ABORTED | Label `stale_unconfirmed`; outage and abort are not distinguishable from these logs |
| Quiet60s is a safe update window | Useful operational signal, not a safety proof |
| Atomically update five runtime endpoints | Not established; no cross-runtime transaction is provided by the documented per-runtime version/endpoint API |
| LLM faithfulness permits promotion | Reject; faithfulness is not tactical efficacy |
| Missing opponent blocks all learning | Only opponent-specific scouting/result-based claims are blocked; typed provisional team observations can still be captured |

## Safe automatic profile activation

The current serving code loads one `Runtime(Profile)` at process startup.
Changing Memory or a profile object remotely does not change those players.
The following registry/selector is a proposed implementation, not existing code:

```text
AgentCore Memory       provisional observations, lessons, trusted completed scouting
immutable profile      supported settings/style, content digest, validation receipt
registry ACTIVE        profile digest eligible for new matches
registry MATCH/<UUID>  immutable pin of profile digest + compatible code generation
```

Use a strongly consistent coordination store such as DynamoDB for registry state,
not semantic retrieval. Scope match pins by team and validated match UUID.
All five players resolve the same pin even if only some were invoked initially.

The first invocation for a new match reads ACTIVE and executes a transaction:
condition ACTIVE generation unchanged + conditional creation of MATCH/<UUID>.
A concurrent loser re-reads the winning pin. An uncertain write response must
be resolved by reading the registry, not by assuming the transaction failed.
Use bounded retries; never default independently to whichever local profile is
available. Fetch and validate the immutable content against its digest and
supported code generation. Cache the resolved profile for that match.

This registry access is at first-match resolution/cache recovery, not a Memory
read on every tick. It adds first-invocation latency and an availability dependency
that must be measured before production use. A changed active digest affects
only subsequently created pins. Existing pins must outlive all accepted late
requests and retries; deleting one must not let an old UUID silently rebind.

Automatic promotion updates ACTIVE conditionally against the evaluated baseline
generation. Concurrent candidates trained against stale baselines cannot
overwrite a newer promotion. Keep immutable previous versions and promotion/
rollback receipts. Rollback changes ACTIVE for future pins; it does not promise
to change a running match without violating that match's immutable policy.

Code updates remain a separate rollout problem. Match-pinned profiles only solve
supported settings changes when all five runtimes already run compatible selector
code. Do not use this mechanism to distribute arbitrary generated Python.
Formation remains fixed within a match; initially exclude formation and coach
policy from automatic changes, and allow only explicitly validated numeric fields.

## Automatic learning eligibility

Provisional observations must identify their source match, record/time window,
team side, input contract, code/profile version and raw export digest; confidence
labels must distinguish observed selection from inferred outcome. Do not infer
failed passes/goals from sparse commands or count duplicate players/restart ticks
as independent events.

Memory writes should use deterministic chunk IDs and retry-safe publication;
bounded checkpoint overlap handles delayed logs. Idle-based snapshots remain
revisable, not final. Durable deduplication/checkpoints, delivery-lag policy and
explicit dead-letter/alert handling are required before scheduling a collector.
Do not upload raw incoming payloads, credentials or arbitrary framework logs.

A candidate may be proposed automatically from accumulated evidence, but may
become ACTIVE only after all of:

- Typed allowlisted bounded settings, fixed formation/coach policy and compatible
  code; reference baseline digest recorded and unchanged.
- Existing unit/lint/type and legal-command guards pass, including the original
  failure cases and known captured scenarios.
- Representative paired simulator evaluation with both sides, predeclared seed
  set/opponent pool and at least dozens of matches; no cherry-picked reruns.
  Minimum sample size alone is not acceptance: require a prespecified statistical
  improvement criterion and reject inconclusive/regressing candidates.
- Appropriate latency/error checks; internal `latency_ms` cannot establish
  AgentCore end-to-end p95 or platform timeouts. Distinguish these metrics.
- Immutable candidate/evaluation artifact plus atomic promotion receipt.

Simulator evidence is not guaranteed improvement against the real platform.
Automatic efficacy claims require validated actual outcomes; those remain
unavailable from logs alone. Set cost/work limits and a kill switch for scheduled
model/evaluation jobs; failed required stages cannot produce a promoted profile.

## Required acceptance cases before activation

1. Tied clock119.83 followed by121.85 and239.91 never becomes confirmed complete.
2. A delivery outage/restart pause never creates a final result or an opponent.
3. Interleaved UUIDs are tracked independently; late arrivals amend provisional evidence.
4. Repeated delivery, ticks and clocks do not duplicate memory/support counts.
5. Five concurrent first invocations resolve one match pin, including during promotion.
6. Pin/cache recovery and ambiguous transaction responses preserve that same version.
7. Bad/missing digest or unsupported profile cannot become ACTIVE or quietly fall back.
8. Stale-baseline promotion is rejected; failed/inconclusive evaluation is not promoted.
9. Rollback affects new pins and leaves existing pins intact; code updates are not
   misrepresented as atomic settings promotion.

## Sources and evidence location

Captured operator evidence is retained outside Git in session
`3e735515-e140-4fa4-b0a8-fa0d79499798/files/match-<UUID>/telemetry.json` and
`cloudwatch-events.json`. Counts/windows above were recalculated directly from
those files; they are not newly fetched CloudWatch data or raw-payload inspection.

- `agent/main.py:create_app` and `agent/runtime.py:Runtime.handle`: session binding,
  emitted identity, profile startup behavior and internal latency measurement.
- `agent/advisor/offline/memory.py`: exact validated events, not coordination locks.
- `agent/advisor/offline/scouting.py`: completed-match manifest/scouting boundary.
- [AWS AgentCore versions/endpoints](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/agent-runtime-versioning.html):
  immutable per-runtime versions; DEFAULT follows updates; named endpoint behavior.
- [AWS DynamoDB transactions](https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/transaction-apis.html):
  atomic conditional operations across distinct items in one account/Region.

No scheduled collector, profile registry, IAM change or new runtime deployment was
created in this reconnaissance. Existing live players remain at the last verified
`r4v7_route_stamina`, runtime version8. The scouting implementation PR #15 was
still open when this report was written.
