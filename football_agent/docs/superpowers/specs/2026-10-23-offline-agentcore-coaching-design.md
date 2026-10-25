# Offline AgentCore coaching — proposed design

**Status:** draft for review; implementation has not started.
**Date:** 2026-10-23.
**Scope:** `redesign_4` between-match tooling, not player tactics or player deployment.
**Research:** [AWS service assessment](../../agentcore_services_research.md).
**Plan:** [implementation tasks](../plans/2026-10-23-offline-agentcore-coaching.md).

The user requested all five recommended candidates: shared post-match Memory,
bounded Bedrock advice, prompt caching, sandboxed generated analysis, and
AgentCore evaluation of the advisor. The detailed design below is proposed,
not approved: the user was unavailable for the design-choice question.
Review this design before executing its plan. Planning authorizes documentation,
not resource provisioning, paid inference or changing the five player runtimes.

## Decision and alternatives

Use an operator-run offline workflow extending `agent.advisor`.
Keep shared evidence handling and approval rules in one place; implement five
small service boundaries that can be tested and used separately.

Independent disconnected commands would duplicate evidence/identity checks.
A fully autonomous cloud workflow would add scheduling, coordination and
promotion complexity before we know the advisor improves decisions. Neither
is needed for the first version. Existing `advisor pick` and `review` retain
their behavior and remain usable without the new workflow.

## Proposed flow

1. An operator selects a trainer report, trusted match manifest, current profile,
   and optional structured excerpts of both teams' coach advice.
2. Build a deterministic evidence packet. Telemetry-derived attempted actions,
   manifest results, report claims and hypotheses retain different labels.
3. Read same-opponent, unexpired scouting notes from AgentCore Memory. Retrieval
   is bounded and provenance-checked. Missing extracted memories are a recorded
   empty result, not a reason to guess opponent behavior or block indefinitely.
4. Optionally execute an operator-selected analysis script in a fresh,
   network-isolated Code Interpreter session with a sanitized JSON dataset.
   Generated scripts require a separate explicit execution choice.
5. Ask Bedrock for bounded advice and a candidate profile, using a stable prompt
   prefix eligible for caching. Capture actual usage, timing and request identity.
6. Validate schema, profile bounds, evidence references and proposal identity.
   Evaluate the advisor's actual input/output trace with AgentCore Evaluations.
7. Save an atomic local run artifact with advice, evaluation and service receipts.
   Optionally publish a validated scouting note in Memory via an explicit
   publish command. Generated advice stays distinguishable from observations.
8. Profile approval, tactical evaluation and player deployment remain separate
   existing operator actions. No step writes `profiles/recommended.json`.

## Authority and identity

The trainer database and explicit manifest remain authoritative for imported
identity/results. Retain raw capture artifacts outside Memory. An operator's
structured coach excerpt has `report_claim` status, not `observed` status.
An observed command proves an attempt, not execution, completion or goal causality.

A scouting note carries schema version, team/opponent/match/profile identity,
content digest, source references, creation/expiry times and individual claims.
Each claim has an ID, text, evidence IDs, and one of
`observed`, `report_claim`, `inferred`, `hypothesis`. Model-generated notes cannot
declare observations not present in the evidence packet.

Use a stable trusted team ID such as `aleksalotl`, an explicit opponent ID from
the manifest and a canonical match ID. Encode untrusted labels into a safe path
component; reject missing or mismatched IDs. Memory's actor is scoped to this
team/opponent, its session to the match, and semantic memory uses an actor-scoped
namespace. A namespace is retrieval organization, not authorization.

Every Memory read is untrusted input. Exact short-term scouting events provide
the provenance envelope; long-term extracted text is supporting context only.
Unverifiable extraction references are marked unusable, never silently promoted.
Filter expiry locally even when service deletion lags. Async extraction is not
a transaction or a promise of immediate read-after-write results.

## Model and cache boundary

Preserve the existing LangChain advisor interface. Add an offline-only Converse
provider exposing the `invoke(messages)` interface used by `Advisor`; this
permits explicit SDK schema/caching controls and usage receipts without changing
the player runtime. Reuse `parse_profile`, bounds and archetype definitions.
Add an offline `GroundedAdvisor` subclass with `review_evidence`, exposed through
a typed `EvidenceAdvisor` boundary. Extend advice only through a separate offline
schema; legacy `pick`/`review_match` advice stays valid.

The stable prefix contains role, football restrictions, evidence interpretation,
schema and rubric. Per-match evidence, opponent memories and generated results
follow the cache checkpoint. Support explicit `off`, `implicit` and `explicit`
cache modes with a declared model capability configuration. Do not silently
downgrade unsupported explicit caching or structured-output settings.
Record cache reads/writes when supplied; zero is a valid cache miss, not a failure.
Avoid padding prompts or issuing paid warmups just to demonstrate a hit.

Select model/inference profile, judge model, Region, token ceiling, timeouts and
call budget explicitly before AWS acceptance. Account/workshop permissions and
model feature availability are not established by the research.

## Interpreter boundary

Use a dedicated custom interpreter configured with `networkMode=SANDBOX`.
Its execution environment receives no deployment credentials, application secrets,
AWS execution role, persistent mount or shared home directory. Verify the actual
resource configuration before running a script; fail closed on a public resource.
The caller may assume an offline operator role; those credentials are never
uploaded to the interpreter.

A request consists of a bounded Python script and one allowlisted JSON dataset.
Use fixed remote filenames, a fresh session per run, a finite execution/session
timeout, bounded streamed output and `StopCodeInterpreterSession` in cleanup.
No package installation, terminal tools, network dependency, autonomous tool loop
or local execution fallback. Python standard-library analysis is the first scope.
A script's result is an untrusted computed claim, not authoritative evidence.

## Evaluation boundary

Evaluate advice, not imagined football outcomes. Emit a real generic GenAI
invocation span and actual inference child spans from the offline call, with
the documented scope/attributes, trace/session identity and exact sanitized
request/response content. Underlying botocore instrumentation alone is insufficient.
Keep the artifact locally; submit compatible `sessionSpans` to `Evaluate`.

The inline API path must pass an explicit compatibility smoke check. AWS's
CloudWatch tutorial is not proof that every inline span requires CloudWatch
provenance, nor is the API signature proof of successful account compatibility.
If inline acceptance fails, report the blocker; switching to CloudWatch/ADOT
requires an explicit design revision, not silent infrastructure changes.

Use local deterministic checks for JSON/profile/evidence-ID validity. Use one
versioned custom LLM judge rubric for report faithfulness, uncertainty handling,
both-team advice and actionable counter-tests. The judge gets actual evidence
and advice; its aggregate faithfulness score is 0..4 with an initial advisory
threshold of 3. Exact checks stay separate; the score is not win probability.
Acceptance fixtures include the report-3
zero-SHOOT contradiction, unsupported goal attribution and opposing press advice.
Judge text is untrusted; it never approves a profile or changes source data.

## Failure, persistence and permissions

Each enabled service is required for that invocation. Disabled is an explicit
configuration, not an automatic fallback. A permission error, malformed response,
timeout, truncated stream or absent expected evaluation yields a failed artifact,
nonzero CLI exit and sanitized diagnostic. Never write a successful proposal after
a required step fails. Preserve completed paid-call receipts for diagnosis.

An ambiguous write outcome remains `unknown`; retry only with the same supported
idempotency token. Never substitute empty scouting for an AWS failure. Separate
side-effecting publish from analysis to prevent retries re-running model calls.
Atomic artifact writes must preserve an existing output on filesystem failures.

Use separate offline invocation and provisioning roles. Scope Memory/interpreter/
evaluator/model access to declared resources where AWS supports it. No role or
policy changes to the five football players. Namespace and JSON validation are
not substitutes for IAM. Default live tests are skipped; paid tests require an
explicit flag, expected account and bounded operation manifest.

## Acceptance

- Existing player outputs/goldens, import isolation and `pick`/`review` tests pass.
- Each service works through its typed public boundary with replayable fakes.
- One fake end-to-end run exercises all five integrations and every required-step
  failure leaves no successful proposal.
- A separately approved, bounded AWS run proves Memory exact write/read and
  scoped retrieval, Bedrock advice/usage, cache usage reporting, isolated
  interpreter cleanup and advisor evaluation, with evidence artifacts.
- A cache hit and immediate long-term extraction are not required correctness
  properties. Cache savings and tactical improvement require later measurements.
- No changes to player formation, recommended profile, ARNs or deployed versions.

## Review decisions before execution

Approve the operator-run architecture and explicit publish/execution boundaries;
choose allowed account/model/Region/resource scope and a paid validation ceiling.
Code may subsequently be implemented with fakes before paid validation is
authorized, but this draft does not itself authorize implementation.
