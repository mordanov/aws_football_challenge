# Offline coaching — operator guide

## Provisioning attempt: 2026-10-23

After local readiness corrections (320 tests, Ruff and mypy passing), authorized
provisioning targeted account `559922262840`, region `us-east-1`, team `aleksalotl`.
AWS returned Memory ID `aleksalotl_offline_coach_memory-XQle3R3eHI`, but its
readiness polling exceeded the 120-second deadline. Provisioning stopped
immediately at that first failure. Interpreter and Evaluator creation were not
attempted, no model was invoked, no IAM roles were modified, and live players
were not redeployed. The Memory ID is persisted in the identity-bound session
receipt `.offline_coach_resources_aleksalotl.json` beside
`offline-coach-provision.json`. The timeout did not imply deletion.

After the operator confirmed Memory and its long-term strategy were active,
provisioning resumed from that same receipt. No duplicate Memory was created.
The create/readiness sequence completed, and a separate status query confirmed:

| Resource | ID | Verified status |
|---|---|---|
| Memory | `aleksalotl_offline_coach_memory-XQle3R3eHI` | ACTIVE |
| Interpreter | `aleksalotl_offline_coach_interpreter-ZPKXcZiqoK` | READY |
| Evaluator | `aleksalotl_offline_coach_evaluator-6sHxnCHdmc` | ACTIVE |

All IDs are recorded in the same identity-bound receipt. Resource readiness
does not establish successful model calls, cache hits, sandbox execution,
Memory round-trips or evaluation scoring. No paid inference or runtime analysis
was invoked, no IAM roles were modified, and player runtimes remain unchanged.

Offline coaching uses three AWS AgentCore services to produce a **proposal**
for a strategy profile update. A proposal is never approved or deployed
automatically. The operator reviews, decides, and deploys manually.

See [design spec](superpowers/specs/2026-10-23-offline-agentcore-coaching-design.md)
and [implementation plan](superpowers/plans/2026-10-23-offline-agentcore-coaching.md)
for full rationale.

---

## CloudWatch scouting and pre-match briefs

Run these commands from `final_agent/` with the `offline` extra installed.
This tooling does **not** require redeploying the five players. It uses the
existing Memory resource through the runtime configuration, not a new store.

### Prepare one historical match locally

Save exact-match standalone application records from our five CloudWatch groups
as a JSON array or JSONL. Do not supply `filter-log-events` wrappers, framework
copies, PDF statistics or records from multiple matches. Every decision snapshot
must already use the canonical coordinates logged by the runtime.

Provide a six-field manifest with a separately confirmed opponent identity:

```json
{
  "match_id": "confirmed-match-uuid",
  "opponent_id": "confirmed-stable-opponent-id",
  "team_id": 0,
  "played_at": "2026-10-23T18:00:00Z",
  "complete": true,
  "result": [1, 1]
}
```

This is a format example, **not** evidence of a played match. `team_id` here is
our numeric side (0/1); `--team` below is the durable team identity used to scope
Memory. Result is `[our_goals, opponent_goals]`. A final score must be verified
independently; the last sparse snapshot need not be the final result. A missing
telemetry opponent ID is explicitly bound by this manifest, never guessed from
positions or the score. Use the actual pinned profile from that match, not
necessarily today's `profiles/recommended.json`.

```sh
python -m agent.advisor.offline scout \
  --manifest local/match.json --records local/telemetry.json \
  --profile local/played-profile.json --team aleksalotl \
  --expires-at 2026-10-30T18:00:00Z \
  --output local/scout-evidence.json --note-output local/scout-note.json
```

Expiry must be timezone-aware, after the match and still in the future.
Input is bounded to16 MiB /20000 rows /256 distinct game clocks; larger or
malformed input fails rather than silently truncating. Multiple player records
at the same tick must agree. Repeated game clocks count once at their earliest
tick, even if later restart ticks advance. Errors are not samples.

The two versioned observations are deliberately narrow:

| Pattern | Positive observation | Denominator |
|---|---|---|
| `keeper_advanced` | Opposing keeper canonical x projected along our attack direction is <=30 | All distinct sampled game clocks |
| `all_players_attacking` | All five opponents are strictly in our half while opponent ownership is known | Distinct sampled clocks with known opponent possession |

Keeper threshold30 is a scouting heuristic in canonical units, not an official
keeper-zone rule. Snapshots do not prove intent, elapsed possession percentage,
opponent shots or successful execution. Source digests cover each normalized
snapshot and the validated manifest; keep the original export to inspect them.
The note ID derives from the packet digest for repeatable publication.

### Explicit publication to existing Memory

Review the packet/note and confirm the opponent before this AWS write:

```sh
python -m agent.advisor.offline publish \
  --config local/offline_runtime.json \
  --evidence local/scout-evidence.json --note local/scout-note.json
```

This reuses validated exact-event publication and deterministic team/opponent
sessions. IAM must restrict writers; hashes do not authenticate a source.
No semantic model extraction or paid model inference is requested by `scout`,
`publish` or `prematch`. Existing Memory retention can remove events earlier than
their note expiry; absence of retrieved notes is not negative tactical evidence.

### Before another match against the same confirmed opponent

```sh
python -m agent.advisor.offline prematch \
  --config local/offline_runtime.json --team aleksalotl \
  --opponent confirmed-stable-opponent-id --match-id next-confirmed-match-uuid \
  --kickoff 2026-10-24T18:00:00Z --profile profiles/recommended.json \
  --output local/prematch-brief.json --evidence-output local/prematch-evidence.json
```

The cutoff is exclusive. Future/same-match, wrong-identity and expired notes
cannot support the brief. Duplicate notes count once; conflicting notes for
one match require operator resolution, not selective merging. Sources keep
historical profile versions and dates; a changed profile is context, not proof
of improvement. Only typed scouting claims are aggregated; narrative notes and
semantic text are not converted into counts.

`review_required` needs a positive observation in at least two distinct matches,
at least three distinct-clock positives, and >=20% positive sampled observations
for a pattern. These are conservative **review heuristics**, not calibrated
confidence or proof of an effective counter-strategy. Otherwise the brief is
`insufficient_evidence` with no invented actionable evidence. Retrieval
exclusions and page/note limits are visible in the brief.

The brief suggests testing safe counterattacks/outlets while preserving shot and
cover guards. It does not modify any settings or change formation. If the brief
has eligible hypotheses, the existing optional advisor/evaluator can produce a
candidate:

```sh
python -m agent.advisor.offline run \
  --config local/offline_runtime.json --evidence local/prematch-evidence.json \
  --profile profiles/recommended.json --output local/prematch-proposal.json
```

This final command **does** invoke configured models and incurs inference cost.
The upcoming-match evidence is intentionally incomplete with no result; it
cannot be published as a completed-match scouting note. Review, offline checks,
profile approval and deployment to all five players remain separate operations.
Nothing automatically polls CloudWatch or changes a live match.

---

## Glossary

| Term | Meaning |
|------|---------|
| **Evidence packet** | Expiry-stamped bundle of uniquely identified scouting claims, match identity and source digests. A digest is not a signature or verification of the source. Never contains raw video or PII. |
| **Scouting note** | A labelled, structured claim about an opponent — tactic, formation, pressing pattern, or set-piece trigger — published to shared Memory. |
| **Analysis artifact** | Output of a sandboxed Python script run against an evidence packet; produced by `analyze`, consumed by the advisor as additional grounding. |
| **Advisor evaluation** | A faithfulness score (0–4) from an LLM-as-a-judge evaluator. A low score is a successful evaluation of poor advice, not a service failure. A score at or above `judge_min_score` produces a `"proposal"` artifact; below that produces `"rejected"`. **No score approves or deploys a profile.** |
| **Run artifact** | Immutable JSON output of `run_offline()`. Status is `"proposal"`, `"rejected"`, or `"failed"`. Never `"approved"` or `"deployed"`. |

---

## Services

Three AWS AgentCore resources are provisioned separately from the five player
runtimes. Use `deploy/offline_coach.py` (not `deploy/deploy.py`) to manage them.

| Service | Purpose | Resource |
|---------|---------|---------|
| **Memory** | Stores and retrieves scouting notes across sessions | AgentCore Memory (SEMANTIC strategy) |
| **CodeInterpreter** | Executes bounded Python analysis scripts | AgentCore CodeInterpreter (SANDBOX network, no execution role) |
| **Evaluator** | Faithfulness scoring for advisor output | AgentCore Evaluator (LLM-as-a-judge, rubric v1) |

Player runtimes (`r4_gk`, `r4_def`, `r4_mid`, `r4_fwd1`, `r4_fwd2`) are never
touched by these commands.

Resource management uses the `bedrock-agentcore-control` client with locally
SDK-validated create requests, underscore resource names and seven-day Memory
retention in the example. Receipts bind account, region and team and are saved
immediately after each create returns an ID, before readiness polling. Corrupt
or foreign receipts fail explicitly. Apply stops at its first failed operation;
SDK retries are disabled. The installed Evaluator creation API does not accept
an execution role, and this script does not create or modify IAM roles.
Plans print required invocation/provisioning policies; model invocation resource
ARNs must be configured explicitly before paid acceptance.

Scouting reads use bounded, paginated exact events from a deterministic
team/opponent session. Semantic extraction is explicitly excluded until its
provenance can be verified; asynchronous extraction availability is not assumed.
Writes require packet/note identity, digest and claim agreement and unexpired
evidence. IAM-trusted writers are still required: digests alone do not
authenticate a stored note.

Interpreter configuration is verified through the control-plane client before
runtime execution. SANDBOX networking, absence of an execution role, and absence
of filesystem mounts are required. SDK-shaped `stream` events are consumed with
output/deadline bounds; error, truncated or missing result output fails explicitly.

---

## Setup

```sh
# 1. Create the resource-management configuration (NOT the runtime OfflineConfig).
# Fill in your account, region, team_id, resource names and evaluator role settings.
cp deploy/offline_coach.example.json offline_coach.json

# 2. Preview intended changes (no AWS calls)
python deploy/offline_coach.py --config offline_coach.json plan

# 3. Provision (requires AWS credentials, explicit approval)
python deploy/offline_coach.py --config offline_coach.json apply --approve

# 4. Verify service IDs and status
python deploy/offline_coach.py --config offline_coach.json status
```

---

## Local preparation (no AWS)

```sh
# Build an evidence packet from local files
python -m agent.advisor.offline prepare \
  --manifest examples/match_manifest.json \
  --report examples/match_report.json \
  --coach-claims examples/coach_claims.json \
  --profile profiles/recommended.json \
  --team r4 \
  --expires-at 2026-11-01T00:00:00Z \
  --output local/evidence.json

# Create a separate runtime configuration matching OfflineConfig in
# agent/advisor/offline/contracts.py, using the resource IDs from provisioning.
# It needs expected_account, region, model_id, judge_model_id, memory_store_id,
# interpreter_id, evaluator_id, max_input_tokens, max_output_tokens,
# timeout_seconds, max_output_bytes, max_rounds and judge_min_score.
# The resource-management JSON above is NOT a runtime configuration.
python -m agent.advisor.offline doctor --config offline_runtime.json
```

---

## Explicit AWS setup and run

```sh
# Full workflow: retrieve → advise → evaluate → artifact
python -m agent.advisor.offline run \
  --config offline_runtime.json \
  --evidence local/evidence.json \
  --profile profiles/recommended.json \
  --output local/run.json

# Exit codes: 0=proposal, 2=failure, 3=rejected
```

---

## Deterministic counts and sandboxed execution

For the canonical shooting-count comparison, do **not** generate a parser with
Bedrock. Prepare a trusted script locally (no model call):

```sh
python -m agent.advisor.offline count-analysis \
  --evidence local/evidence.json --output local/counts-script.json
```

The parser recognizes `observed` claims beginning `SHOOT command issued` and
`report_claim` claims beginning `Report records SHOOT`. Accepted count forms are
`SHOOT command issued N times` and `Report records SHOOT=N`, optionally followed
by a parenthetical qualifier or semicolon-space note. N is an ASCII integer
from 0 to 1,000,000,000. Negative, fractional, malformed or duplicate count claims
raise fixed errors without echoing raw text. Other narrative claims are not
counts. Missing data returns `status=missing,count=null`, never zero. Results
retain claim ID, kind and source IDs; the difference is observed minus report,
or null if either is missing. No execution, goals or causality is inferred.

The script artifact contains trusted repository function source, the shared
file-loader/output wrapper, evidence digest, origin, preview counts and
`usage:null` (no model call). It does not hardcode the preview into computation.
Review the artifact before passing it to the existing `analyze` command with
explicit sandbox consent. No untrusted/model-generated code runs locally.

`run` also computes these counts deterministically before model advice,
regardless of optional `--analysis`. Bad counts block advice; valid counts enter
the grounded payload as `shooting_counts`, for model interpretation only.

## General generated-script review

```sh
# Generate analysis script (one model call, no execution)
python -m agent.advisor.offline generate-analysis \
  --config offline_runtime.json \
  --evidence local/evidence.json \
  --instruction "Identify top opponent pressing triggers" \
  --output local/analysis_script.json

# REVIEW the script yourself before executing it.

# Execute in sandbox (requires explicit consent flag)
python -m agent.advisor.offline analyze \
  --config offline_runtime.json \
  --evidence local/evidence.json \
  --script local/analysis_script.json \
  --execute-generated-code \
  --output local/analysis_result.json
```

The `--execute-generated-code` flag is required and documents explicit
operator consent. The script runs in a SANDBOX CodeInterpreter with no
execution role and no filesystem mounts.

Generation also writes `<output>.diagnostics.json`, including on failure.
This bounded sidecar retains usage receipts when the provider returned them,
fixed failure codes, response byte count/SHA-256, and format/stop labels.
It excludes prompts, code, raw replies and raw provider exception messages.
A provider failure without a response records unknown usage as null.
Its `provider_error` records an allowlisted AWS/transport code (otherwise
`UNKNOWN`), validated HTTP status and bounded request ID. It never records
raw exception messages, HTTP headers or endpoint URLs. These details identify
authentication, throttling or connectivity failures without guessing their cause.
Inspect the CLI exit code: a failed attempt does not approve an existing script
at the same output path. Use a fresh output path for each authorized retry.

The generator requests plain JSON; a complete outer `json` Markdown fence is
also accepted, matching the existing advisor's format handling. Extra prose,
extra object fields, empty code, malformed or truncated JSON remain failures.
The original acceptance failure discarded its reply, so its exact format is
unknown; fenced-JSON rejection is locally reproduced, not claimed as proven
for that historical AWS call.

To prevent copying inline evidence into a canned result, generation now sends
the packet's JSON Schema instead of actual claim values. The model now returns
exactly one function, not a full script:

```python
def analyze(evidence):
    # Compute a JSON-serializable dict from the packet.
    return {"claim_count": len(evidence["claims"])}
```

AST validation requires a synchronous function with this single argument and
no annotations, defaults, decorators, type parameters or top-level statements.
Invalid functions produce `INVALID_ANALYSIS_FUNCTION` with usage retained.
A deterministic wrapper reads UTF-8 `evidence.json`, invokes the function,
requires a dict, and prints JSON with `allow_nan=False`. The assembled script
is byte-bounded too. This replaces the brittle exact-prefix loader check.
Imports belong inside the function; generated code is never executed locally.
Review must still verify that results are computed
from `evidence`, missing counts are not treated as zero, and the script has no
unsafe effects. The AST contract is not a security sandbox and cannot prove
those properties.

The sandbox accepts exactly one JSON object across the complete stdout,
including pretty-printed JSON. It rejects surrounding logs, multiple objects,
non-object results and nonfinite JSON values. This replaces the earlier
final-line-only parser. Generated scripts are instructed to raise on malformed
or duplicate relevant count claims, return explicit missing/null when absent,
and never silently ignore parsing errors. Verify those properties in review;
prompt instructions alone do not guarantee them.

---

## Scoped publication

```sh
# Publish an approved scouting note to shared Memory
python -m agent.advisor.offline publish \
  --config offline_runtime.json \
  --note local/scouting_note.json \
  --evidence local/evidence.json
```

Publication is separate from the workflow and requires a deliberate call.
`run_offline()` never calls `publish()`.

---

## Independent reevaluation

```sh
# Re-score an existing run artifact without re-running inference
python -m agent.advisor.offline evaluate \
  --config offline_runtime.json \
  --run local/run.json \
  --output local/run_rescored.json
```

Use this as a recovery path if the evaluator was unavailable during `run`.
A low score produces a `"rejected"` artifact (exit 3), never a failure.
Reevaluation requires original grounded spans persisted in the run artifact;
legacy artifacts without spans must be regenerated from the original evidence.
The validated but unapproved `proposed_profile` is retained across failures and
rejections so reevaluation can restore a passing proposal without repeating
inference. Only passing proposals expose `candidate_profile`; neither field
constitutes approval or deployment. Legacy artifacts missing both profile
payloads require rerunning the source workflow.

---

## Costs

Bedrock and Evaluations clients use standard SDK retries capped at three total
attempts to tolerate transient 503/throttling responses. No `AWS_MAX_ATTEMPTS`
environment setting is needed. SDK retries remain disabled for resource/Memory
mutations and sandbox execution to avoid replaying side effects. A missing
response receipt does not prove zero cost.

Advisor replies may be raw JSON or JSON inside one complete outer Markdown
fence. Additional prose is rejected. Usage receipts are retained even when
response validation fails. The normal CLI does not persist raw model replies;
explicit operator diagnostic capture should remain local and bounded.

- `plan` and `doctor`: zero AWS calls, zero cost.
- `apply`: one create call per missing resource; no ongoing charge until invoked.
- `run`: one Memory retrieve, one Bedrock model call (advisor), one Evaluator job.
- `analyze`: one CodeInterpreter session; billed per session-second.
- `status`: three read calls; typically free-tier eligible.

`doctor` reports unknown pricing and account entitlements; no dollar estimate
is claimed or enforced.

---

## Cleanup

```sh
python deploy/offline_coach.py --config offline_coach.json cleanup --approve
```

Lists all owned resource IDs before deletion. Irreversible; destroys all
stored scouting notes. Player runtimes are NOT affected.

---

## Fault matrix

| Condition | Behavior |
|-----------|----------|
| Memory denied | `retrieve` raises `OfflineServiceError`; run artifact `status="failed"` |
| Successful Memory query with no notes | Empty context; no fabricated scouting history |
| Model malformed response | Advisor raises `OfflineServiceError(stage="advise")`; `status="failed"` |
| Model throttled | Same as above; operator retries |
| Cache miss (first call) | Normal; cache write proceeds |
| Cache mode disabled | `OfflineConfig.cache_mode="disabled"` skips cache; no failure |
| Interpreter timeout | `execute` raises `OfflineServiceError(stage="analyze")`; `status="failed"` |
| Interpreter cleanup failure | Cleanup outcome remains explicit/unknown; no confirmed termination claim |
| Evaluator unavailable | `evaluate` raises `OfflineServiceError(stage="evaluate")`; `status="failed"` |
| Faithfulness score below threshold | `status="rejected"` (not failed); exit 3 |
| Faithfulness score missing | `EvaluationReport.status="partial"`; workflow treats as `status="failed"` |
| Atomic write failure | `OSError`; caught by CLI, exit 2; no partial artifact written |
| Wrong account/region | `apply`/`cleanup` refuse before any resource change |

---

## State transitions (offline run stages)

```
prepare (local)
  ↓
doctor (local)
  ↓
run_offline():
  retrieve → advise → [analyze (optional)] → evaluate
  ↓
  RunArtifact{status: proposal | rejected | failed}
  ↓ (operator decision, manual step)
publish (note to Memory)
```

`publish()` is always a separate explicit step; `run_offline()` never calls it.
No stage writes to a player profile or triggers deployment.

## Local readiness and limits

The readiness tests validate AWS request shapes against the installed botocore
models rather than accepting arbitrary fake-client arguments. SDK serialization
and local tests do not prove account entitlement, service readiness or model
availability. AWS acceptance remains a separate explicit step.

`prepare` uses the same provenance-preserving builder as the SDK API.
`generate-analysis` writes a bounded, digest-bound script artifact without
executing it. `analyze` requires that digest and an explicit execution flag;
`run --analysis` accepts a result only for the same canonical evidence data.
Advisor input uses a conservative UTF-8-byte upper bound against the token cap,
not a tokenizer measurement. Output bytes and SDK read/connect timeouts are
bounded; model-call retries are disabled. Prompt-cache reuse/savings are not
guaranteed and require eligible model/prefix lengths and measured cache receipts.

---

## Relationship to live players

The offline coaching pipeline is **entirely separate** from the five deployed
players. It shares the same `agent/model.py` profile schema, but:

- It never calls `agent.runtime`, `agent.__main__` or `agent.trainer`.
- It never imports `langchain` or any GenAI instrumentation from the live path.
- No evaluation score approves or deploys a profile.
- The CloudFormation stack and player IAM roles are untouched.
