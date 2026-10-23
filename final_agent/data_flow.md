# Data flow - learning football team

## Deployment source isolation

Merged `redesign_4` at the revision in `source.json` -> independent tracked copy
in `final_agent`. `deploy/deploy.py` resolves its own parent as the project root:
`final_agent/agent` + `final_agent/profiles/recommended.json` -> staged
`final_agent/deploy/app/player/{agent,profile.json}` -> existing `Redesign4`
AgentCore application and five `r4_*` runtime identities on an explicit rollout.
No imports, symlinks, credentials or deployment caches connect this copy back to
`redesign_4`. Copying and staging are local-only and do not change AWS versions.
Offline tooling is copied too, but local databases and AWS resource receipts are
operator-managed state, not source files.

## Live decisions

**Source:** public sample envelope, `prompt -> {gameState, teamId, myPlayers}`.
**Sink:** command-array JSON string and structured JSONL record.
**Transformations:** validate identity -> normalize state -> compute canonical
shared assignments -> role-specific choice -> validate command -> serialize ->
bounded best-effort telemetry enqueue and independent command return.
**Shape at each step:** envelope -> immutable `Snapshot`/player index ->
`Decision(command,reason,alternatives,assignments)` -> wire command with
`commandType,teamId,playerId,parameters,duration` and snapshot/context/warnings.
Recognized `coachInstruction` survives normalized snapshot serialization.
The coach preset comes from the platform, or from the in-agent rules coach when
`coach_policy` is `rules` and no platform preset is present. It is derived from
the snapshot alone, so all five players agree. The profile `style` (block depth,
attackers committed, tackle aggression, tempo, fixed formation, carry) shapes
anchors and option scores inside `decide`.
Keeper outlet candidates retain normal length, lane and receiver-pressure
filters. Rejected non-self candidates are separately ranked for an emergency
KICK only if the keeper has no normally safe outlet. Telemetry labels these
attempts contested; keeper holds are not candidates. This does not bypass
ownership resolution or alter outfield possession filters.
The three-match feedback revision (`r4v6_three_match_feedback`) keeps numeric
profile settings, formation and coach policy unchanged. It changes deterministic
option selection: clear forward carries, center aim against a stranded keeper,
forward-progress ranking of safe keeper outlets, goal-line keeper positioning
and less rigid midfield support/pressing. Telemetry reasons retain lane/clear-run
information. No external service or shared runtime state enters these decisions.
The candidate `r4v7_route_stamina` retains those numeric settings and adds bounded
escape-route selection, fresh near-tie press allocation and calm-carry sprint
reserves. Escape limits reuse the protocol's compact lateral limit and coordinate
scale even for sample inputs; the conservative inner envelope keeps the selected
route reachable in both contracts. Reasons label `escape blocked route`; wire and
snapshot schemas are unchanged. This source candidate is not the deployed v6
profile (which runs on AgentCore runtime version 7).
The actual pinned AgentCore adapter wraps the array string in one SSE data event.
On AgentCore each of the five runtimes runs the same staged code and profile;
its `AFC_PLAYER_ID` seat only fills a missing `myPlayers` (explicit routing wins).
The deployment sets `AFC_STAMINA_SCALE=100` for synthetic Fitness Check stamina
and enables `AFC_COMPETITION_ADAPTER=1`. The SDK request context supplies the
session ID: `<UUID>-team[01]-pos[0-4]` selects the compact live-match contract,
while fitness sessions retain sample geometry and percentage stamina. Unknown
non-fitness session formats retain the sample contract with an explicit warning.
Live fractional stamina remains unchanged; live player position/velocity x/y
is multiplied by 8 into canonical coordinates. A 3D live ball uses x/z (y is
height); 2D balls retain x/y. MOVE_TO output is divided by 8 and bounded to the
observed player pitch limits, x +/-6.4 and y +/-3.5. Scale 8 is a provisional
first-match calibration, not a verified exact field dimension.
The same x-limit is passed to `decide(max_move_x=...)` in canonical units:
51.2 for compact matches, 54 for sample inputs. Keeper movement projection intersects
that bounded command plane. The existing modeled goal plane/half-width additionally
classifies incoming urgency, not a deeper movement target or wider keeper range.
Incoming shot reactions can request short-distance sprint while preserving
stamina validation; outfield movement retains its normal distance guard.
Recorded canonical snapshot replay must supply
the original input-format limit explicitly rather than applying scale twice.
Telemetry includes `input_format`/`input_contract`, canonical snapshots and
wire-format commands/alternatives. A recognized match session supplies its UUID
as `match_id` when no operator binding is configured; opponent identity remains
unknown. Local CLI/simulator inputs retain the original contract.
**Source:** `agent/protocol.py`, `agent/coach.py`, `agent/tactics.py`,
`agent/runtime.py`, `agent/telemetry.py`, `agent/main.py`.

## Durable coaching

**Source:** exactly-one-match JSONL export plus explicit six-field manifest.
**Sink:** SQLite matches, observations, decisions, errors and coaching reports.
**Transformations:** optional explicit binding of missing match/opponent IDs ->
identity/profile validation -> sorted deduplicated observations -> bounded
elapsed-time transition inference (including explicit free-ball flight) ->
evidence/role/remedy lessons and opponent scouting. Log drop/failure counters
remain in raw exports; absence of records never proves absence of errors.
**Shape at each step:** manifest + record list -> canonical snapshot/decision
rows -> JSON report with outcomes, lessons, confidence and limitations.
**Source:** `agent/__main__.py`, `agent/trainer.py`.

## Next-match tactics

**Source:** stored matches, evidence and final-result availability.
**Sink:** SQLite profile/audit/proposals and separate approval events ->
atomically exported JSON -> five runtime boots.
**Transformations:** threshold general corrections -> recency-weight opponent
scouts -> bounded candidate -> content-derived version -> operator-selected
export/deployment -> startup profile/opponent selection.
**Shape at each step:** stored reports -> `Profile(version,settings,opponents)`
and audit -> JSON file -> immutable effective `Settings`.
**Source:** `agent/trainer.py`, `agent/profile.py`, `agent/main.py`.

Archetype choice runs alongside this. Local campaign evidence
(`docs/strategy_report.md`) selects an archetype profile, which is committed as
`profiles/recommended.json` and carried through retraining via `--base-profile`.
The optional advisor reads a campaign or match report and writes a validated
`status: "proposal"` JSON file. An operator decides whether to deploy it with
`AFC_PROFILE`; nothing deploys automatically.
**Source:** `agent/archetypes.py`, `agent/advisor/`, `profiles/recommended.json`.

With default startup there is no live database, LLM or network dependency in
decision logic; the advisor runs only off-tick. The optional match-profile
selector adds registry access on first selection/cache recovery, not tactical
per-tick Memory or model access.
Actual platform result/report delivery and trusted match-boundary collection
remain unverified integration responsibilities.

## Automatic learning foundations (opt-in)

Five existing CloudWatch runtime log groups -> matching standalone JSON
subscription events -> bounded Lambda decompression and canonical observation
validation -> DynamoDB PENDING outbox -> exact AgentCore Memory event
(`provisional_team_observation_v1`, semantic extraction skipped) -> SENT.
Hash identity covers normalized allowlisted content; persisted first source
envelope/timestamp makes replay stable even with a different delivery ID.
Unknown match identity and framework logs are reported ignored; invalid recognized
evidence fails. Side/seat, snapshot and command contracts are checked.
Free-form reasons, requests/exceptions and unknown opponent mappings do not
enter Memory. Repeated clocks share a support key; conflicting variants retain
different IDs. None is a confirmed result or a complete scouting note.

Failed/uncertain writes retain PENDING; Lambda async retries and a retained SQS
failure destination support recovery. The generated CloudFormation stack
includes alarms, an explicit subscription switch and an unattached least-privilege
player policy. Packaging vendors the installed AWS SDK into an independent
Lambda archive. Plan/package are local; apply/bootstrap are explicit account-checked
operations. No role attachment or player deployment is performed by this tooling.

Optional `AFC_PROFILE_TABLE`/`AFC_TEAM_ID` -> registry match pin, or consistent
ACTIVE/document reads plus transactional first-pin creation -> digest-validated
compatible profile -> bounded per-process cache -> request-local tactical settings.
All five players use one pin. Cached matches ignore later ACTIVE changes;
cache recovery re-reads the same durable binding. Failed selection logs and
raises, never independently chooses the packaged baseline. Default and synthetic
fitness serving retain the startup profile.
There is no candidate generator, comparative acceptance gate or automatic
ACTIVE writer in this implementation.
**Source:** `agent/profile_registry.py`, `agent/learning_observations.py`,
`agent/learning_collector.py`, `agent/main.py`, `deploy/learning.py`,
`docs/automatic_learning.md`.

## Offline coaching pipeline

### CloudWatch scouting and pre-match review

Saved standalone application JSON/JSONL + six-field confirmed match manifest +
the exact historical profile -> `scout` -> `EvidencePacket` and `ScoutingNote`.
Snapshots are already canonical: no second compact-coordinate conversion.
Match/team/profile conflicts, unknown coordinate formats, decreasing clocks,
invalid geometry and disagreeing snapshots at one tick fail explicitly.
Equal ticks and repeated game clocks count once, choosing the earliest tick.
Error rows are not observations. The manifest's opponent ID explicitly binds
missing opponent IDs; it cannot override a contradictory one.

Each observed claim contains a versioned typed count and supporting tick IDs.
Source digests cover the validated manifest and every sampled normalized snapshot.
Only keeper position and all-five-player attack patterns are extracted; our
issued commands cannot establish opponent shot attempts or shot accuracy.
Local preparation makes no AWS/model calls. Explicit existing `publish` sends
the source-bound note and packet to the team's exact opponent Memory session.

`prematch` -> bounded exact Memory events before the exclusive kickoff cutoff ->
integrity/identity/expiry checks -> one unambiguous note per historical match ->
brief JSON + an incomplete (upcoming-match) `EvidencePacket` of hypotheses.
Sources retain note/packet digests, ticks, played/expiry dates and historical
profile versions. A hypothesis needs positive samples in at least two matches,
at least three distinct-clock positives and >=20% of sampled observations.
These are review heuristics, not statistical significance or deployment gates.
Insufficient history writes an explicit `insufficient_evidence` brief.

The evidence file is accepted by existing `run --evidence`; Memory retrieval for
incomplete packets applies the same exclusive historical cutoff. That optional
next step invokes advisor/evaluator and still only produces a proposal. No
command here writes the recommended profile, changes formation, deploys a
runtime, or adds Memory calls to live decisions.
**Source:** `agent/advisor/offline/scouting.py`, `memory.py`, `__main__.py`.

**Source:** local evidence packet (`EvidencePacket`: team/opponent/match IDs, played_at,
coach claims, source digests, expiry).
**Sink:** `RunArtifact` JSON file (status: proposal/rejected/failed, advice, candidate_profile,
evaluation scores, stage receipts). No write to live player profile or AgentCore runtime.
**AWS boundaries:**
- Memory (retrieve): `agent/advisor/offline/memory.py` → `AgentCoreMemory.retrieve(packet)` → `Scouting note list`.
- Bedrock (advise): `agent/advisor/offline/bedrock.py` → `GroundedAdvisor.review_evidence(...)` → `OfflineAdvice + Profile`.
- CodeInterpreter (analyze, optional): `agent/advisor/offline/interpreter.py` → `AgentCoreInterpreter.execute(request)` → `AnalysisResult`.
- Evaluator (evaluate): `agent/advisor/offline/evaluation.py` → `AgentCoreEvaluator.evaluate(spans)` → `EvaluationReport`.
**Shape at each step:**
  evidence_packet → scouting_notes (Memory) → [analysis_result (CodeInterpreter)] → advice + candidate_profile (Bedrock)
  → GenAI spans → EvaluationReport{status, scores} → RunArtifact{status, advice, candidate_profile, evaluation}.
**Constraints:** `run_offline()` never calls Memory.publish or modifies any live player state.
publish() is a separate explicit CLI step. Evaluation score is evidence, not approval.
`candidate_profile` in the artifact becomes the live profile only after an explicit operator
`AFC_PROFILE` change and redeployment — separate steps not included in this pipeline.
**Source:** `agent/advisor/offline/`, `deploy/offline_coach.py`, `docs/offline_coaching.md`.

The `prepare` CLI calls the shared evidence builder; it does not bypass manifest
and report claim extraction. `generate-analysis` makes one bounded Converse call
and writes code, usage and the evidence digest; `analyze` separately requires
execution consent and an exact digest match. Optional analysis results supplied
to `run --analysis` must match the canonical evidence data digest.
Generation writes `<output>.diagnostics.json` atomically on success or provider/
validation failure. Usage is captured before validating the script; service
failure without a response leaves usage null. Only fixed reasons, byte count,
SHA-256, recognized stop/format labels and bounded usage metadata are retained,
not response text. A complete outer JSON Markdown fence may be removed before
the strict single-code-field object validation; prose and malformed JSON fail.
Invocation exceptions contribute only allowlisted error codes, integer HTTP
status (100..599) and bounded alphanumeric/hyphen request IDs to diagnostics.
Messages, headers, endpoints and unknown code strings are never persisted.
Script-generation prompts carry the EvidencePacket JSON Schema instead of
packet values. Generated code defines one synchronous `analyze(evidence)` function.
AST validation rejects executable top-level statements, defaults, annotations,
decorators, type parameters or other signatures with `INVALID_ANALYSIS_FUNCTION`;
usage is retained. A fixed wrapper loads `evidence.json`, calls the function,
requires a dict and prints finite JSON. The assembled script is byte-bounded.
This syntactic guard does not prove data dependence or safety: review remains
mandatory before sandbox execution.
For canonical SHOOT counts, `count-analysis` bypasses the model: validated packet
-> trusted parser -> atomic digest-bound script artifact with origin and null
model usage. The exported script uses the exact same function source and fixed
wrapper, not embedded match results. Optional sandbox execution remains explicit.
Local advisor preparation always computes `shooting_counts` using that parser,
even without `--analysis`; malformed/duplicate canonical claims fail before any
model call. The advisor's grounded context and evaluation spans include counts
with claim/source provenance. The model only interprets them.
Interpreter stdout is parsed in full as one JSON object (compact or multiline),
not merely its final line. Additional prose, multiple objects, arrays and
nonfinite values fail; existing stream/output bounds and cleanup still apply.
Advisor output schemas are serialized JSON strings for Converse. Input is
conservatively bounded by UTF-8 bytes against the configured token cap, output
by bytes, with SDK connect/read timeouts. Bedrock and evaluation clients use
standard SDK retries capped at three total attempts; requests may incur charges
even when a transport error prevents receipt delivery. Mutation/sandbox clients
retain disabled retries. No AWS_MAX_ATTEMPTS environment override is required.
Advisor JSON fence removal shares the generator's strict whole-response helper.
Usage is captured before stop/size/JSON/schema checks and copied into failed
run artifacts; raw advisor replies are not logged by the normal CLI.
Run artifacts persist the actual grounding text in recognized generic GenAI
agent spans (`scope.name=opentelemetry.instrumentation.football_advisor`).
`evaluate` reuses those spans and recomputes status against the current threshold.
The unapproved `proposed_profile` is persisted even when evaluation fails/rejects;
only passing proposals expose `candidate_profile`. Reevaluation records the current
configuration digest and can restore that candidate without another advisor call.
No model call, profile write or invented evidence is used during reevaluation.

Memory uses `memoryId`, timestamped conversational event payloads and a bounded,
paginated team/opponent session; unlinked semantic extraction is excluded.
Interpreter preflight uses `bedrock-agentcore-control` and runtime invocation
uses `bedrock-agentcore`. Top-level SANDBOX configuration is checked before
starting a session and runtime `stream` events are bounded and validated before
producing an analysis result. All CLI-injected AWS clients use explicit
connect/read timeouts; mutation/sandbox clients keep SDK retries disabled.

Resource create responses flow immediately to an atomic identity-bound local
receipt before readiness polling, so timeout preserves returned IDs. Management
calls use the control-plane client. IAM policies are printed as plans, not
attached to player roles. The first provisioning failure prevents subsequent
resource requests; recorded receipts support later explicit operator inspection.
