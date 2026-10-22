# AWS football agent: audit and consolidation

## Location update

After the original consolidation, the user requested that this version live
entirely in `redesign_4/`. Its code, tests, examples, package configuration,
documentation and local environment were moved there; `redesign_2/` was never
modified. The supplied prompt remains unchanged. Run application and quality-check
commands from `redesign_4/`.

Below, references to the "root implementation" describe the original audit and
now mean this project's root (`redesign_4/`), not the repository top level.
Reference-source citations such as `redesign_2/agent/runtime.py` remain relative
to the repository root. Earlier command results are historical evidence from
before relocation; relocation verification is recorded separately in section G.

## Evidence and scope

Audit date: 2026-10-20. The four implementations are reference material, not
deployed systems. The original Git worktree had only an untracked `redesign_4/`
containing the supplied task. No reference source or user input is to be edited.
Content hashes of all 85 reference/input files were saved outside the repository
before implementation; generated Python caches are excluded.

The complete original requirements corpus was read: `initial_idea/docs/README.md`,
`01-task.md`, `02-our-strategy.md`, `03-patterns-from-repos.md`, and
`04-crazy-ideas.md`, plus the linked `initial_idea/STRATEGY.md`.
Public articles mentioned there are second-hand evidence, not an authoritative
contract with the eventual tournament infrastructure.

### Requirements authority and assumptions

The following assumptions are discharged before implementation (gate passed for
the documented scope):

| Assumption | Classification | Evidence / conservative decision |
|---|---|---|
| Consolidation originally belonged at root; references remain immutable | Explicit user direction, location subsequently revised | Supplied task phases 1, 5 and 6; follow-up requests moving the completed project to `redesign_4/` |
| No live AWS deployment or invocation is permitted | Explicit user direction | Supplied task section 5.2; local fakes and SDK models only |
| Rules-first operation is intentional, not a missing LLM integration | Explicit design direction | `initial_idea/docs/02-our-strategy.md:9-11,40-44`; optional advisor may stay outside the tick path |
| Individual players must coordinate without shared runtime state | Explicit constraint | `initial_idea/docs/01-task.md:37-39`; identical snapshot and pinned configuration are necessary |
| Exact competition wire contract is unresolved | Accepted limitation | `initial_idea/docs/01-task.md:30-34`; sample-compatible code is not proof of target compatibility |
| Offline training is desirable but not part of the original minimum game interface | Inferred improvement | `initial_idea/docs/03-patterns-from-repos.md` P10/P11 and `04-crazy-ideas.md` E2; preserve useful existing implementation without claiming learning improves wins |
| Dependency and test tooling must be established locally | Code-verifiable | Python 3.12.3, unittest, pytest, Ruff, mypy, boto3 and botocore present; AgentCore SDK initially absent |

No original document specifies required IAM resources, an authoritative command
schema, a mandatory LLM model, an objective training promotion threshold, ranking
tiebreakers, or a production deployment topology. These must not be invented.
The apparent ~2-second tick, ~120-second match and 300-600 team invocation figures
are approximate public descriptions, not exact timing acceptance fixtures.

### Original implementation inventory

`initial_idea/agent/main.py:24-61` exposes `handle`; its optional AgentCore
registration is at lines 64-77. Execution is adapter -> mutable `World` ->
policy/tactics -> optional Strands tie-break -> command serialization -> logging.
`MY_PLAYER_ID`, pitch dimensions/origin and `LLM_*` are environment configured.
There is no dependency declaration. No S3, database, queue, or resource lifecycle
API is used. Bedrock/Strands and AgentCore are optional imports.

The original ten unittest cases passed locally, but the fixtures explicitly
describe a guessed schema (`initial_idea/tests/test_agent.py:1-5`). Tests directly
overwrite parsed possession in several cases, so they do not validate that
integration boundary. `commands.is_valid` checks only the envelope.
`adapter._owner` guesses possession by distance; attack direction uses keeper
position; `tactics._opp_line_ema` makes posture dependent on warm runtime history.
`llm_layer.choose` initializes outside its timeout and queues uncancelled work
against one shared mutable agent. These are defects to correct, not requirements
to preserve. Original documentation's assertions of zero timeout risk and an LLM
that cannot time out are not supported by the code or any AWS execution.

### Local evidence recorded before consolidation

| Command / check | Result | Meaning |
|---|---|---|
| `PYTHONDONTWRITEBYTECODE=1 AWS_EC2_METADATA_DISABLED=true python3 -m unittest discover -s initial_idea/tests -v` | Passed: 10 tests | Original mock-based policy behavior only |
| `botocore.session.Session().get_service_model("bedrock-runtime").operation_model("Converse")` | Passed | Local SDK shape inspection; no API invocation |

The initially installed boto3 was 1.35.9 and botocore 1.35.93. The local Converse
model includes `modelId`, `messages`, `system`, `inferenceConfig`; inference
configuration includes `maxTokens`, `temperature`, `topP`, `stopSequences`.
This verifies SDK input shape availability, not credentials, model access,
latency, response quality or service availability.

### Public sample contract cross-check

Read-only `gh api` requests retrieved the public AWS sample at commit
[`465fc5fc1608e59409b53e562986dafbdf2ec3cf`](https://github.com/aws-samples/sample-ai-possibilities/tree/465fc5fc1608e59409b53e562986dafbdf2ec3cf/agentic-football-sample-agents).
The balanced-team README, `lib/state.py` and `lib/parsing.py` corroborate:

- Separate player indices 0-4; home/away teams; approximately centered
  110 x 70 coordinates; home initially attacks positive x.
- `MOVE_TO` uses `target_x`, `target_y`, `sprint`; `PASS` uses
  `target_player_id`, `type`; shot labels are TL/TR/BL/BR/CENTER.
- `PRESS_BALL` takes intensity, `MARK` tightness, `INTERCEPT` aggressive,
  keeper distribution method THROW/KICK, and `SET_STANCE` numeric stance.
- The recognized command set does not contain DRIBBLE, CLEAR, TACKLE or IDLE.
  It includes SLIDE_TACKLE, FOLLOW_PLAYER, CLEAR_OVERRIDE and RESET.
- Both legacy playerId/teamId and newer agentId/teamCode payloads exist.
  Team codes are case-insensitive and `isFree` is meaningful.
- The sample itself documents ambiguous repeated agent IDs across teams.
  Its first-match possession resolution is not a reliable pattern to adopt.

These sources narrow the initial schema uncertainty; they are not the game
server's schema, do not settle halftime or aim orientation, and do not establish
the intended environment's acceptance of any response. No deploy scripts from
the public sample were executed.

## A. Executive summary

The root implementation is a hardened **redesign_2**: canonical snapshot parsing,
deterministic five-role planning, validated commands, startup-pinned profiles and
a separate transactional SQLite trainer. It retains the winner's architecture
rather than merging three competing runtime/learning systems.

The consolidation adds bounded nonblocking application telemetry, safe malformed
tick diagnostics, strict outbound numeric types, goal-side deep cover, recognized
coach presets, the original 120-second default with explicit override, elapsed-time
pass-flight inference, and a stronger supporting-evidence gate. The optional
AgentCore dependency is declared and pinned; its real local ASGI/SSE path is tested.
Root tests import the root package, not a reference implementation.

**Not competition-ready by assertion:** no real AWS invocation, deployment,
match, win-rate measurement or end-to-end deadline verification was performed.
Authoritative owner-team identity, actual tournament contract/physics, deployment
authorization and cold/warm transport timing remain mandatory acceptance gates.
Ambiguous ownership deliberately produces conservative shape with an explicit
warning instead of guessed action authority.

## B. Requirements assessment

Legend: **S** = satisfies the locally inspectable/implemented aspect;
**P** = partial, defective or incompletely verified; **V** = violates the local
invariant; **U** = cannot assess without infrastructure/matches. None of S means
the intended AWS environment has accepted the behavior. Source line references
are to the unchanged original/reference files; final references use component
and test names so they remain useful after formatting.

| ID / requirement and origin | R1 | R2 | R3 | Final implementation and evidence / remaining boundary |
|---|---|---|---|---|
| R01 autonomous live state -> actions, `01-task.md:3-6` | S | S | S | `Runtime.handle`, `decide`; CLI and actual SDK local transport tests. Real endpoint invocation U |
| R02 five independently controlled roles, task lines 13-16 | P | S | P | One controlled index per invocation; all five roles in `test_both_teams_and_all_roles_emit_legal_bounded_actions`. Five deployment registrations U |
| R03 consistent wins / goal difference, task lines 24-27 | U | U | U | No simulator or competitive result invented; future held-out/practice campaign mandatory |
| R04 ~1-second end-to-end deadline, task lines 16,26 | P | P | P | No model/network/database in core; blocked-sink test confirms application logging isolation. SDK logs, parsing size, scheduling, cold start and network U |
| R05 no crashes / invalid commands, task lines 27,30-31 | P | P | P | Strict selected schema, sanitized identifiable fallback; regression and malformed-command tests. Invalid identity fails explicitly; server acceptance U |
| R06 stateless per tick, task line 37 | P | S | V | No live learning/scout state; same snapshot/settings -> same command. `test_shared_plan_is_order_independent_and_keeps_cover` |
| R07 no peer messaging; one presser/interceptor and distinct markers, task line 39; strategy lines 13-22 | P | S | V | Canonical shared assignments, stable ties and keeper race gates; all-player assignment tests. Same pinned profile/context across deployments required |
| R08 deterministic keeper, shots and off-ball shape, strategy lines 24-25 | S | S | S | `tactics.decide`; keeper projection/race and possession tests; no mandatory LLM |
| R09 range/lane shot discipline and recycle, strategy lines 27-29 | S | S | P | Winner's angle/lane/range scoring preserved; clear/blocked shot tests. Constants and aim orientation U |
| R10 counter-pass preference, strategy lines 31-32 | S | P | P | Winner's high-line + forward gain + receiver-pressure through pass retained. Exact original +30 and no-goal-side-defender rule are replaced heuristics, not claimed identical behavior |
| R11 late lead/deficit and coach posture, strategy lines 34-38 | P | P | P | 120-second fallback and explicit duration; protect/chase and four exact presets tested, recorded and restored. Real coach channel U |
| R12 optional bounded LLM tie-break, strategy lines 40-44 | S | S | S | Intentionally absent from tick path; optional proposal, not mandatory Bedrock usage |
| R13 safe degradation, strategy lines 46-47 | P | P | P | Validated balanced stance only for known identity; errors/warnings visible. Fewer layers needed without a model; no invented fallback identity |
| R14 resolve protocol unknowns, task lines 30-34 | P | P | P | Pinned public sample cross-check; old/new IDs, free/unknown separation and sample coordinates supported. Exact live protocol and tournament variant U |
| R15 structured diagnosis and measured tuning, strategy lines 57-62; patterns P10/P11 | P | S | P | Runtime -> JSONL -> transactional trainer -> report/profile -> reload CLI round trip tested; telemetry loss explicitly counted |
| R16 durable/offline evidence and cautious learning, patterns P11; ideas E2 | P | S | P | SQLite dedup/rollback/finalization, explicit approvals, bounded proposals; three supporting ticks per each of three matches tested; improvement U |
| R17 action outcome uncertainty, inferred reliability requirement | P | S | P | Pass-flight within elapsed window, unknown/score/gap boundaries; no inferred scorer or confirmed pass claims. Engine acknowledgment absent |
| R18 configuration/dependency clarity, supplied phase 5/6 | P | P | P | Root package, optional pinned SDK, startup stamina/profile validation, no fabricated AWS resources; local package/CLI/SDK checks |
| R19 fair play / no undocumented interference, ideas introduction and G | S | S | S | No opponent probing, destructive/reset commands, deployment operations or live requests; optional restart scripts deferred |
| R20 root consolidation, preserved references, audit and future checklist, supplied definition of done | N/A | N/A | N/A | Root `agent`, tests, examples, package and docs; source hashing and final Git review recorded in section G |

Files named `task`, `strategy`, `patterns`, and `ideas` above mean the corresponding
`initial_idea/docs/01-task.md`, `02-our-strategy.md`,
`03-patterns-from-repos.md`, and `04-crazy-ideas.md`.

The rest of the original corpus is deliberately not turned into invented hard
requirements. P1-P3/P6-P8/P10/P15 contribute shared logic, explicit schemas,
deterministic choices, logs and geometry. P4/P5/P14 (per-role models, model history,
Memory/Gateway) are unnecessary optional mechanisms. P9 does not require issuing
every command merely for diversity. P12's live-coach sender/queue and P13's named
personalities are optional; bounded settings and recognized input presets are the
actual supported subset. Speculative A1-A3/B1-B3/C1-C3/D1-D3/E1-E4/F1-F3 are
assessed in section E: predictive geometry, score-aware risk and offline evidence
are retained; live mutable scouting, hidden-LLM diagnoses, unverified restart
scripts, self-play, prompt evolution, keeper-up, rule loopholes and automated
canary/deploy scheduling are deferred or rejected. None is falsely reported as
implemented or required for basic acceptance.

## C. Independent audits

Each audit read its own complete source/tests/configuration/documentation and all
five original requirements documents before comparing scores. Local reproduction
evidence is distinguished below from deployment-dependent consequences.
**No Critical defect was established.** High findings are acceptance blockers in
the affected scope; lower-severity findings are not allowed to cancel them out
through weighted scores.

### redesign_1

**Inventory/flow.** `app.py` -> optional AgentCore `invoke` ->
`Brain.handle` -> wire/world parsing -> playbook/policy/tactics -> command
validation/serialization. Warm scouting/recorder bookkeeping runs beside the
decision flow. Background S3 refresh loads active playbooks/dossiers; recording
rewrites estimated-kickoff/player JSONL objects. `trainer.run` merges observations,
extracts lessons/dossiers, optionally calls Bedrock Converse and updates the
champion/challenger state. `MemoryStore`/`DirStore` permit local use.
`requirements.txt` lists unpinned boto3 and bedrock-agentcore. Important operational
inputs include `AFC_BUCKET`, `MY_PLAYER_ID`, `ANALYST_MODEL_ID`, and `AIM_FRAME`.

**Strengths.** Pure world/policy/command boundaries; whole-frame coordinate
rotation; stable-ID assignment; bounded named tactical knobs; shared event
vocabulary; a fakeable storage interface; optional model analysis outside the
tick deadline; useful runbook and lesson-history concepts. These are real
engineering contributions even though the persistence and promotion mechanisms
need correction.

| Finding | Severity / certainty | Location | Evidence, consequence and recommended resolution |
|---|---|---|---|
| R1-1 divergent active playbooks | High; reproduced role conflict, lifecycle conditional | `agent/main.py:57-64,84-88,121-124`; `tactics.py:93-125` | Same snapshot with DEF using press_height=.6 and MID using defaults yields both SLIDE_TACKLE and PRESS_BALL. Independent polling/early-match swaps do not synchronize base parameters. Pin one immutable match configuration; reject mixed-version training |
| R1-2 restart overwrites history | High; reproduced | `agent/recorder.py:43-57,86-124` | Restart at same estimated kickoff/runtime key replaced stored tick 1 with tick 50. Single queued newest payload can also discard another match's final flush. Use immutable chunks/process IDs and acknowledged deduplicating ingest |
| R1-3 failed writes still acknowledge training | High; reproduced partial-write loss; concurrency risk static | `agent/store.py:77-87`; `trainer/league.py:124-140`; `trainer/run.py:59-65,91-102` | Failing lesson/report puts returned a report and marked the match processed; retry found nothing. Check writes, commit completion last, reconcile partial work and enforce one writer or conditional updates |
| R1-4 pass flight loses attribution | Medium; reproduced | `agent/events.py:63-94`; `trainer/ingest.py:78-103` | PASS -> free -> opponent produced no event; unchanged holder can look successful. Track bounded pending attempts, preserve unknowns and evaluate the selected shot lane |
| R1-5 permissive validator | Medium; reproduced | `agent/commands.py:82-111`; `main.py:161-174` | Invalid stance/player, NaN movement and negative duration passed. Validate complete numeric/type/identity/parameter contracts and final fallback output |
| R1-6 fallback misroutes/contaminates players | Medium; reproduced | `agent/wire.py:31-40`; `main.py:108-140` | Malformed away request fell back to home GK; missing first requested player degraded healthy later players. Parse routing separately and never invent identities |
| R1-7 rollback/reset instructions ineffective | Medium; reproduced wrapper mismatch, reset static | `docs/04-runbook.md:48-54`; `main.py:57-64`; `trainer/league.py:27-38` | History `values` copied to active lacks required `playbook` wrapper; refresh ignored it. Provide one tested state-consistent rollback operation |
| R1-8 prior/live nudge double counting | Medium; reproduced | `main.py:94-104`; `scout.py:92-123`; `trainer/dossier.py:87-91` | Same off-line-keeper evidence doubled shoot-range delta 6 -> 12. Deduplicate tendency identities, expire priors and let current evidence supersede them |
| R1-9 confounded trial attribution | Medium; source-confirmed | `trainer/league.py:79,89-96,121-122`; `trainer/run.py:39-47`; `trainer/dossier.py:81-105` | Trial-start list index survives truncation to 30 results; comparisons can use unrelated/empty baseline. Replay-derived final nudges need not equal played nudges. Use immutable trial windows, logged actual settings and comparable opponent strata |

**Tests and maintenance.** All 39 tests passed with network calls blocked and
credentials disabled; local memory-store pipeline tests are valuable but miss
restart/partial-write/mixed-version invariants. S3 GetObject/PutObject/
ListObjectsV2 and Converse request shapes validated using installed botocore
models, without constructing clients or sending requests. The architecture is
broader than the original task and carries multiple lifecycle/state stores to
coordinate. S3 durability/IAM, daemon survival, match grouping, model access,
AgentCore transport and actual aim/timing semantics remain unverified. Trainer
listing would require scoped ListBucket as well as object permissions; no
deployable IAM policy is inferred from a fake store.

### redesign_2

**Inventory/flow.** `agent.main` optionally serves AgentCore; `Runtime.handle`
normalizes a single-player envelope, computes pure shared tactics, validates,
synchronously logs, and returns an array string. `agent.__main__` exposes decide,
ingest/learn/report/train/export/audit/promote. Frozen dataclasses and bounded
profile parsing separate live decisions from SQLite evidence, atomic JSON exports
and manual promotion. Three test files and seven Markdown documents accompany
the code; no dependency manifest was supplied.

**Strengths.** Canonical roster ordering and ties; explicit free/known/unknown
possession; no warm-state live dependence; pinned effective settings; meaningful
snapshot/action deduplication; transactional rejection of conflicts; partial to
complete finalization; inferred versus confirmed outcome language; recency and
shrinkage; durable proposal/approval history; honest distinction between export,
approval and deployment. These reduce both maintenance cost and false confidence.

| Finding | Severity / certainty | Location | Evidence, consequence and recommended resolution |
|---|---|---|---|
| R2-1 logging vetoes/deadlines decisions | High; reproduced | `agent/runtime.py:13-14,29-55` | BrokenPipeError escaped; a 1.05-second sink made a valid invocation take 1.055 seconds. Isolate application telemetry behind bounded nonblocking delivery and explicit loss/error counters |
| R2-2 fallback log contains malformed tick | High; reproduced | `agent/runtime.py:39-46`; `protocol.py:120` | NaN tick first failed decoding, then strict log serialization, so no stance returned. Independently validate telemetry primitives and keep logging off the response path |
| R2-3 ambiguous owner blocks attacking control | High conditional integration blocker; local behavior reproduced | `agent/protocol.py:101-111`; `tactics.py:60-64,175-184` | Removing owner team yields five MOVE_TO actions, no carrier/press. Retain safe unknown state, expose diagnostics, require authoritative ownership before acceptance; proximity guessing is not a fix |
| R2-4 cover ahead of deep ball | Medium; reproduced | `agent/tactics.py:155-161` | Ball at own-frame -50 sent cover to -46, ahead of the attack. Use goal-relative depth within the pitch and test both teams |
| R2-5 tick ordinal assumption | Medium conditional integration risk; predicate reproduced | `agent/trainer.py:63-65` | Tick delta 120 with game-time delta 2 was rejected as nonadjacent. Use monotonic ticks plus bounded elapsed seconds and verify actual clock units later |
| R2-6 coercive outbound validator | Medium; reproduced | `agent/protocol.py:11-20,125-173`; `trainer.py:284-289` | String power and boolean duration survived validation unchanged. Separate permissive inbound normalization from strict outbound JSON types |
| R2-7 weak evidence gate versus README | Medium; reproduced | `agent/trainer.py:337-340`; `README.md:259-262` | One risky event plus unrelated observations in each of three matches changed the global margin, contrary to three supporting observations per match. Count distinct supporting ticks, not total snapshots |

**Tests and maintenance.** All 42 tests passed, including subprocess CLI and
filesystem/SQLite integration. Existing tests directly exercise conflict
rollback, deduplication, recency and approval, rather than just trivial return
values. Gaps included sink faults, malformed fallback fields, deep cover,
calendar-dependent training fixtures and the actual SDK transport. These are
addressed in the root tests; no test of the reference itself demonstrated AWS
readiness. Original AgentCore SDK was absent locally. Snapshot scale, halftime,
restarts, ownership, 300-second fallback and controller-facing framing remained
assumptions. No S3/Bedrock inference API, secret, resource mutation or IAM policy
is present in this design.

### redesign_3

**Inventory/flow.** Eager package/main import constructs a singleton trainer,
which reads tuning/local memory. `handle` parses input, observes/learns and may
checkpoint synchronously, computes shared assignments, then calls large
role-specific decision trees and records commands. `coach_review` reads local
memory/logs and optionally writes tuning. The optional AgentCore decorator exists
but no `app.run()` startup or dependency manifest is supplied.

**Strengths.** Rich immutable player/ball/vector model; current command vocabulary;
explicit role geometry; keeper goal-line projection; pass corridors/cutbacks;
defensive screen ideas; inspectable bounded weights; separate explicit offline
tuning output. Its tactical explanations offer ideas to test, not validated
superiority over the smaller winner.

| Finding | Severity / certainty | Location | Evidence, consequence and recommended resolution |
|---|---|---|---|
| R3-1 endpoint-local learning changes shared plans | High; reproduced | `agent/main.py:33,56-73`; `trainer.py:349-381,461-534` | Different controlled-player histories yielded pass margins .265 vs .24 on the same snapshot; duplicate tick observations manufactured frozen-player evidence. Use pure common snapshot/settings or fully isolated/deduplicated match state |
| R3-2 role overrides defeat chase ownership | High; reproduced | `agent/tactics.py:375-454`; `roles.py:125-133,426-432,617-746` | Shared owner 2 but IDs 2,3,4 all emitted INTERCEPT near a loose ball. Make every role respect one shared assignment, including keeper eligibility |
| R3-3 emergency fallback throws or misroutes | High; reproduced | `agent/main.py:96-120`; `adapter.py:151-198` | Supported away/agentId_3 identity fell back to home GK on state error; NaN controlled ID escaped with ValueError. Reuse validated minimal routing and protect fallback |
| R3-4 synchronous writes/logging on deadline path | High; source-confirmed, actual timeout unmeasured | `agent/trainer.py:401-503`; `main.py:75-95` | Goals/tick-50 checkpoint directly writes JSON; timing stops before logging/serialization. Remove persistence from decisions and stop claiming a hard SLA from average arithmetic timing |
| R3-5 lossy/absent persistence | Medium; reproduced dictionary loss, missing remote hooks source-confirmed | `agent/trainer.py:401-455`; `docs/04-questions-and-concerns.md:29-40` | Saved failed_pass_types.GROUND=7 restored as 0; no claimed S3/Memory hooks. Version and round-trip persisted data, or document only actual local capability |
| R3-6 emitted logs cannot feed advertised review | Medium; reproduced | `agent/main.py:77-95`; `coach_review.py:127-192`; `trainer.py:645-652` | Real DECISION line parsed to empty scouting/weights and zero completed passes. Align one telemetry contract and test actual emitter -> parser -> review; do not tune from biased denominators |
| R3-7 one-step outcome inference loses flight | Medium; reproduced | `agent/trainer.py:474-478,514-534,603-711` | PASS -> free -> opponent left interceptions at zero; generic recovery may count as tackle success. Retain bounded pending action evidence and label causal uncertainty |
| R3-8 free attacking corner has no taker | Medium conditional engine impact; output reproduced | `agent/roles.py:824-908` | All players go to support slots, none to corner ball. Reserve a taker only after verifying authoritative restart semantics; original did not require speculative set pieces |
| R3-9 time/dial mismatch | Medium; source-confirmed | `agent/adapter.py:29,450-457`; `trainer.py:715-821`; `tactics.py:70-77` | Default 300 seconds/tick÷60 conflicts with raw-tick transition windows; tempo/press_height_x are tuned but unused. Define units, wire only useful knobs and test their effects |
| R3-10 deployment/docs claims exceed code | Medium; source-confirmed | `agent/main.py:122-135`; `README.md:43-57`; `docs/04-questions-and-concerns.md:9-26` | No runnable SDK startup/dependencies; documented invoke_agentcore, role/output switches and remote hooks absent. Document actual entrypoints/settings and verify optional packaging |

**Tests and maintenance.** All 16 tests passed with socket calls blocked and
checkpoint files redirected outside references. They are fabricated snapshot
unit tests, not a simulator. Some test names overstate assertions (no penalty in
the corner/penalty case, no simultaneous forward target-distance invariant).
The log parser test uses fields that the emitter does not produce. Large role
trees coupled to a mutable trainer impose the highest maintenance burden.
Nearest-duplicate possession, restart rules, goal labels, velocity units,
offside/foul assumptions and warm-file persistence remain unverified. The
installed old botocore lacked an AgentCore service model; that failed inspection
was an environment limitation, not a live service failure.

Lower-priority documentation corrections across versions include replacing
blanket sub-millisecond/zero-timeout claims with scoped measurements, removing
unused tuning guarantees, preserving original versus later timing assumptions,
and describing local pipeline tests as local rather than AWS end-to-end.

## D. Comparative evaluation and winner selection

**Winner: `redesign_2/`.** Selection was made after three separate, independent
audits and before creating the root implementation. Scores are qualitative
engineering judgments, not measurements of playing strength. The supplied
weights are unchanged. No implementation is accepted as-is.

| Dimension | Weight | redesign_1 | redesign_2 | redesign_3 |
|---|---:|---:|---:|---:|
| Functional correctness / coverage | 25% | 7.0 | 6.5 | 6.0 |
| Reliability / failure handling | 20% | 4.0 | 6.0 | 3.0 |
| Architecture / maintainability | 20% | 6.0 | 8.0 | 5.0 |
| AWS correctness / security | 15% | 5.0 | 4.5 | 2.0 |
| Testing / verifiability | 10% | 6.0 | 7.0 | 5.0 |
| Documentation / operations | 10% | 7.0 | 8.5 | 4.0 |
| **Weighted score / 10** | **100%** | **5.80** | **6.65** | **4.30** |

The second redesign wins on pure snapshot planning, explicit unknown possession,
immutable startup profiles, transactional and deduplicated SQLite evidence, and
the distinction between proposing, approving and deploying a profile. It has a
complete locally executable live/offline loop without S3 or a model on the tick
path. Its 42 tests include real filesystem and subprocess boundaries.

The first redesign has broader autonomous learning and useful optional SDK
integration, but independently refreshed coordination parameters, overwriteable
match recording and unchecked multi-object trainer writes undermine its core
claims. Fixing those would require replacing more architectural machinery than
hardening the second redesign. The third has rich role heuristics but mutable
per-endpoint learning and role overrides break shared-team invariants; its
persisted and emitted evidence does not round-trip faithfully.

The winner still requires logging isolation, robust fallback telemetry, strict
outbound numeric types, goal-side cover, honest timing assumptions and reproducible
deployment packaging. Unknown possession remains a **conditional acceptance
blocker**, not a reason to invent an owner. The safe implementation may hold
shape until the integration supplies authoritative ownership.

> **Superseded (deployment, Oct 2026):** the official server format and sample
> (`lib/state.py`, `possessionAgentId` only) confirm no owner-team field is
> sent, so "authoritative ownership" will never arrive and R2-3 would leave the
> carrier passive in every real match. `decode` now resolves a team-less holder
> to the same-index player within 3 m of the ball and at least 1 m nearer than
> its counterpart; contested or distant cases stay unknown with the existing
> warning. Real AgentCore deployment and invocation were then performed: see
> README "AgentCore deployment".

## E. Consolidation decision record and implementation plan

The design is a hardened second redesign, not a fourth independent architecture.
The alternatives considered were repairing the first redesign's distributed
learning/storage lifecycle, or rebuilding the third's mutable role/trainer
coupling. Neither is justified for the original stateless, latency-first task.

| Origin / idea | Decision | Rationale / cost |
|---|---|---|
| R2 pure snapshot + canonical roster + shared assignments | Adopt | Directly supports identical-snapshot coordination; preserve interfaces and module boundaries |
| R2 startup-pinned bounded profiles and explicit opponent identity | Adopt | No mid-match version races; all five deployments must still pin the same artifact/context |
| R2 SQLite evidence, atomic profile export, approval distinct from deployment | Adopt | Durable local learning without unverified AWS resource dependencies |
| R2 unknown possession instead of guessing | Adopt | Prevents unauthorized carrier actions; add explicit diagnostic and retain future integration gate |
| R1 score/clock/coach posture | Adapt | Restore recognized coach presets as deterministic inputs; preserve winner's score-aware behavior and reject arbitrary tactical text interpretation |
| R1 named bounded knobs, rules-first / offline model separation | Adopt existing equivalent | Winner already implements bounded settings and offline learning; no duplicate knob framework |
| R1 background persistence concept | Adapt | Bounded nonblocking decision telemetry, not a new S3 recorder or unbounded retry queue |
| R1 background S3 refresh / automatic champion promotion | Reject | Unnecessary resources and coordination/transaction risks; explicit export and operator approval are safer |
| R1 optional Bedrock post-match analyst | Defer | Deterministic lessons suffice; model cost and actual improvement unproven |
| R3 defensive screen geometry and invariant-focused role planning | Adapt | Correct winner's deep-ball cover toward the goal; enforce shared assignment rather than extra chase overrides |
| R1/R3 pass-flight learning problem | Adapt | All three inspect only the next observation; extend the winner's conservative inference across explicit free snapshots within a bounded elapsed window instead of copying lossy one-step attribution |
| R3 live mutable trainer and frozen-LLM classification | Reject | Violates statelessness and infers hidden causes from weak observations |
| R3 restart scripts / sweeper exceptions / extra tuning dials | Defer | Actual play modes, foul economics and physics are unresolved; do not add speculative actions |
| All versions' default 300-second match | Adapt | Original task reports ~120 seconds; use that conservative default and permit explicit payload duration |

Execution and verification plan (root only):

1. Copy `redesign_2/agent` and its tests as the implementation baseline. Retain
   public `Runtime.handle`, `decode`, `decide`, `Trainer` and profile contracts.
2. Add regression tests at those boundaries for logging failures/backpressure,
   malformed ticks, command types, deep cover, coach posture, duration, ownership
   diagnostics, physics-style ticks and independent evidence thresholds.
   Run the tests against the copied baseline before changing behavior.
3. Implement narrow fixes in runtime/protocol/tactics/trainer. Keep ambiguous
   ownership fail-closed, logging bounded and observable, and training offline.
4. Add a declared Python package and optional pinned AgentCore dependency;
   exercise the actual local SDK entrypoint/transport with network disabled.
5. Align root README/context documents with actual behavior. Run the entire root
   unittest suite, formatter/linter, type checker, packaging/CLI/SDK checks and
   reference hash comparison. Complete this report with actual results and
   infrastructure-dependent acceptance work.

No automatic commit, deployment, model invocation or infrastructure provisioning
is part of this execution.

## F. Final implementation

| Surface | Consolidated change |
|---|---|
| `agent/model.py`, `protocol.py` | Preserve winner's frozen canonical snapshots and input/output boundary; add recognized coach field/round trip, 120-second fallback, strict native numeric command fields and integer-zero duration |
| `agent/tactics.py` | Preserve role policy, keeper projection, lanes and assignment interfaces; cover may retreat to own-frame -54 rather than -46; four coach presets affect bounded policy without adding chase owners |
| `agent/runtime.py`, `telemetry.py` | Serialize validated response before best-effort enqueue; sanitized error tick, explicit unresolved-possession warning; one bounded writer with loss/failure counters and bounded lifecycle methods |
| `agent/trainer.py` | Preserve SQLite schema, transactional conflict rejection, reports, export and approvals; infer across explicit free-ball flight within 4.1 elapsed seconds; count distinct supporting ticks for global changes |
| `agent/main.py` | Testable SDK application factory; normal executable server entrypoint; no AWS deployment or service client |
| `agent/__main__.py` | Same live/offline command family; local CLI returns command before bounded telemetry drain and surfaces incomplete delivery |
| `pyproject.toml` | Python >=3.11 package; standard-library core; optional `bedrock-agentcore==1.24.2`; declared Ruff/mypy/httpx development tools; only root `agent` packaged |
| `tests/` | Reference baseline imports redirected to root; stronger qualified training fixtures and current-date recency fixtures; runtime/fallback/coach/cover/type/flight/evidence/telemetry regressions; actual local SDK ASGI test |
| README, examples and context docs | Executable synthetic decide/learn/approve/export/reload examples, exact input/output/env contracts, lifecycle/limitations, synchronized terminology/state/data flow |

The telemetry worker is a deliberate new boundary, justified by a reproduced
deadline/failure dependency. Its callback-only exception handler records the
failure count and exception class, then continues; it does not silently return
successful delivery. A focused final review found that catching only expected
I/O/serialization exception families killed the worker on a transient formatter
KeyError. That finding was reproduced with a failing public-runtime test, then
corrected; the next record now arrives carrying the failure diagnostic.

Pass-flight inference remains offline and observational. It does not reproduce
the first/third redesigns' overconfident “causal” labels, live mutable weights or
distributed storage lifecycle. No new resource store, model service, automatic
promotion, simulator or deployment system was added.

Context maintenance gate passed: **updated** `CONTEXT.md` (telemetry/supporting
observation/coach semantics), `state_machines.md` (independent delivery, pass
inference and evidence gate), and `data_flow.md` (queue, coach and SDK framing).
No context file is exempt. Root formatting was applied using the declared
formatter; reference formatting was untouched.

## G. Verification results

### Checks executed

| Check / actual command or runner | Status and observed result |
|---|---|
| Original: `PYTHONDONTWRITEBYTECODE=1 AWS_EC2_METADATA_DISABLED=true python3 -m unittest discover -s initial_idea/tests -v` | **Passed**, 10 tests |
| R1: independent `unittest.main(module=None)` with argv `discover -s tests -t . -v`, from `redesign_1` | **Passed**, 39 tests; sanitized environment, disabled credential files/metadata, sockets/DNS blocked |
| R2: `python3 -m unittest discover -s tests -v`, from `redesign_2` | **Passed**, 42 tests; sanitized environment and temporary files outside reference tree |
| R3: `unittest.defaultTestLoader.discover("tests")` and `TextTestRunner(verbosity=2)`, from `redesign_3` | **Passed**, 16 tests; sockets blocked, existing temporary-directory fixture redirected to session storage, runtime checkpoint redirected and removed |
| Root red phase: `python3 -m unittest discover -s tests -p test_consolidation.py -v` | **Failed as intended** against copied baseline: 11 tests, 14 failing subcases and 3 errors, reproducing reported defects |
| Review red phase: `PYTHONPATH=tests .venv/bin/python -m unittest test_consolidation.BoundaryRegressions.test_sink_failure_does_not_disable_later_records -v` | **Failed as intended**, first sink KeyError killed writer and failure counter remained zero; repaired afterward |
| `.venv/bin/python -m ruff check agent tests` | **Passed**, all checks; root imports/formatting normalized with Ruff |
| `.venv/bin/python -m ruff format --check --quiet agent tests` | **Passed** |
| `.venv/bin/python -m mypy --cache-dir <session>/files/mypy-cache` | **Passed**, no issues in 10 source files; initial 21 baseline/integration typing issues corrected |
| Root full suite using the socket-blocking runner below | **Passed**, **57 tests**, 2.052 seconds on Python 3.13.12; includes actual installed SDK transport |
| `uv build --wheel --out-dir <session>/files/package --default-index https://pypi.org/simple` | **Passed**, built `afc_coordinated_agent-0.1.0-py3-none-any.whl` |
| Installed `afc-agent` CLI with `examples/invocation.json` and `examples/match.json` | **Passed**, synthetic decide -> learn -> audit -> approve -> export -> reload; exported JSON equals selected profile; temporary files removed |
| Wheel ZIP/metadata inspection | **Passed**, exactly 10 root Python modules, declared Python minimum and SDK pin; no reference packages included |
| SHA-256 and file-set comparison against initial snapshot | **Passed**, all **85 reference/input files unchanged**, including the pre-existing untracked `redesign_4` prompt |
| `git diff --check`; `git diff --exit-code HEAD -- initial_idea redesign_1 redesign_2 redesign_3` | **Passed**; final status contains only intended root additions, two `.gitignore` patterns and the original untracked prompt |
| SDK request-model inspection | **Passed** for R1's S3/Converse shape arguments using local botocore; no calls. Initial AgentCore model check **blocked** by old installed SDK catalog; optional SDK later installed and actual app boundary tested locally |
| AWS deployment, actual service invocation, practice matches, cold-start/E2E timing | **Not run**, explicitly prohibited/unavailable |
| Real match wins / goal difference / calibrated tactical benchmarks | **Not run**, no faithful simulator or real opponents |

`<session>` denotes the external Copilot session-artifact directory, not a
repository path or required application setting. The original local Python was
3.12.3; `uv venv .venv` selected 3.13.12. The initial 53-test root suite also passed
under Python 3.12.3 before adding SDK/extra regressions. Python 3.11 is the declared
minimum (the code uses `itertools.pairwise` and modern type syntax), but a separate
3.11 matrix execution was not performed.

The normal package index did not supply bedrock-agentcore; its lookup failed
explicitly. The public PyPI index succeeded. After creating the dependency
manifest, `uv pip install --python .venv/bin/python --index-url
https://pypi.org/simple -e '.[deployment,dev]'` installed AgentCore 1.24.2,
boto3/botocore 1.43.115, Ruff 0.16.10 and mypy 2.3.1 in the project venv.
No global packages or references were changed. The SDK was exercised through
httpx ASGITransport, not a live server or AWS endpoint. Earlier SDK test runs
reported dependency deprecation warnings (class-based Pydantic config and
`datetime.utcnow`); these are not fixed in vendored dependencies or hidden as
proof of future compatibility.

Final network-blocked runner, after lint/type checks:

```python
import socket
import unittest
from unittest.mock import patch

def blocked(*args, **kwargs):
    raise AssertionError("external network forbidden in local verification")

with (
    patch.object(socket.socket, "connect", blocked),
    patch.object(socket.socket, "connect_ex", blocked),
    patch.object(socket, "create_connection", blocked),
    patch.object(socket, "getaddrinfo", blocked),
):
    result = unittest.TextTestRunner(verbosity=1).run(
        unittest.defaultTestLoader.discover("tests")
    )
raise SystemExit(not result.wasSuccessful())
```

This ran with `PYTHONDONTWRITEBYTECODE=1`, `AWS_EC2_METADATA_DISABLED=true`,
`AWS_SHARED_CREDENTIALS_FILE=/dev/null`, and `AWS_CONFIG_FILE=/dev/null`.
The socket patches apply to the test process, not an OS sandbox; CLI child
processes execute the standard-library offline commands and inherit disabled
credential paths. No application path under test constructs a live AWS client.

### Evidence interpretation

- Reference audits include **code inspection and controlled reproductions**.
- Root tactics/protocol tests are **unit tests on synthetic snapshots**.
- SQLite/filesystem/subprocess and AgentCore ASGI tests are **local integration
  tests**. The actual installed SDK is involved, but the game/server is not.
- There are **zero real-AWS integration tests and zero intended-environment
  end-to-end tests**.
- The 2.052-second suite time is not a per-tick latency measurement. Earlier
  reference microbenchmarks used different sinks/bookkeeping and must not be
  compared as a speed ranking or used to claim the deadline.

### Relocation verification

After moving the project to `redesign_4/`, its virtual environment launchers and
editable installation were regenerated for that location. From `redesign_4/`:
`python -B -m unittest discover -s tests -q` passed all **57 tests** in 2.181
seconds, using `.venv/bin/python`; `.venv/bin/ruff check agent tests`,
`.venv/bin/ruff format --check --quiet agent tests`, `.venv/bin/mypy` (10 source
files), and `.venv/bin/afc-agent --help` all passed.

All 85 original reference/input hashes still match, including the supplied
prompt. The repository-root `.gitignore` is restored to its original content;
project-specific ignore rules are now in `redesign_4/.gitignore`. Original
reference trees and pre-existing root cache files remain in place. Git status
shows only the untracked `redesign_4/` directory. Local documentation links and
the absence of the relocated project files at repository root were verified.

## H. Remaining risks and follow-up

**Mandatory before acceptance:** establish actual tournament payload/framing,
authoritative ownership, tick/time/stamina units, match duration and attack
direction; register five correctly authorized endpoints with identical pinned
configuration; capture command acceptance and cold/warm end-to-end latency;
exercise real practice matches with preserved logs/profile versions.

**Conditional High blocker:** repeated player IDs without an owner-team
discriminator cannot identify the carrier. The final controller reports this
and holds shape rather than inventing ownership. This remains unresolved pending
the actual infrastructure contract. It is not counted as a fixed integration
problem merely because the diagnostic test passes.

**Other limitations:** best-effort telemetry may drop or lose its tail; framework
logs can still block independently of the application queue; large payload parsing
and process scheduling have no hard time bound; geometric constants and keeper
arrival estimates are uncalibrated; set pieces, red-card rosters, offside, foul
economics, aim orientation and maintained-command reset semantics are unresolved.
Cover is limited to the movement boundary (-54 in attack frame), so no strict
goal-side claim is made for a ball at/beyond that boundary. Offline inference
cannot establish actual pass completion, scorer, tackle success or causality.

Profile approvals are auditable intent, not automated deployment or guaranteed
improvement. Consistent profile version strings are not cryptographic proof that
all five runtime artifacts/contexts match; deployment must compare artifacts.
Autoplay requires trusted match-boundary collection; fixed environment context is
unsafe if reused across matches. Existing reference databases are not migrated
automatically; import scoped raw records into a new root database and preserve
the original evidence. Atomic file replacement does not guarantee persistence
across machine loss without an appropriate durable filesystem/backup.

**Optional later work:** evaluate additional sample-supported tactics or verified
restart scripts; controlled opponent-stratified experiments; durable external log
collection and dashboards; signed profile manifests; optional offline Bedrock
analysis only after proving value. Do not expand services merely to imitate
every AWS pattern in the original reading list.

## I. Future AWS verification checklist

No resource names, account IDs, ARNs, secrets or successful live outcomes are
invented here. Each item needs captured evidence before being marked verified.

### Mandatory acceptance

| Check | Prerequisite / safe procedure | Expected outcome and evidence to retain |
|---|---|---|
| Identify tournament variant and authoritative contract | Obtain organizer schema/version and sanitized home/away invocation captures; deployment currently unresolved | Captured fields match `protocol.decode`; document every mapping, not just a public README link |
| Resolve possession | Obtain owner team or authoritative globally unique identity mapping; test reused agent IDs, free ball, held ball and stale owner flags | Correct unique carrier across teams; no unresolved-possession warnings in valid held-ball captures; no proximity guess |
| Validate routing/roles | Five actual runtime registrations, each receives exactly one controlled index | GK/DEF/MID/FWD1/FWD2 commands retain requested team/index; verify away and malformed-identifiable fallback cases |
| Verify pitch/time/scale | Confirm centered bounds, goal locations, halftime direction, gameTime seconds, tick units, duration, velocity/stamina units | Captured frames decode correctly; late lead/deficit and interception calculations trigger at expected times; set explicit scale/duration |
| Verify endpoint transport | Package the pinned SDK/app and actual platform consumer; authorize only disposable practice invocation | `/ping` healthy; command array string/SSE decoded once as intended; request/response capture proves game consumed the command |
| Validate authorization | Establish execution role/trust and caller identity for actual resources; narrowly scope InvokeAgentRuntime and required deployment/log/artifact permissions | Authorized platform calls succeed; unauthorized caller denied; retain reviewed IAM/trust policy and sanitized denial/success evidence |
| Verify credentials/secrets | Use platform-managed credential resolution only where deployment needs it; no runtime model/S3 access is required | No embedded credentials in artifacts/logs; role/region checks succeed without recording secret values |
| Pin a coherent team profile | Compare code/package/profile hashes and opponent context across five runtimes before match | Identical profile/settings/context; recorded version agrees with packaged bytes; no staggered mid-match rollout |
| Confirm command acceptance | Use controlled practice scenarios for movement, safe pass/shot, GK distribution, press, mark, intercept and balanced stance | Server acknowledgment/replay shows legal parameters, aim/duration and maintained-command effects; no “valid locally therefore accepted” inference |
| Test five-player invariants | Capture same-tick responses for held/free/unknown states and tied arrivals | At most one assigned presser/interceptor, distinct marks and retained cover; verify raw server observations are actually shared |
| Measure end-to-end budget | Record cold and warm per-role calls under actual CPU/network/log load, including errors; do not intentionally disrupt tournament traffic | Deadline evidence includes p50/p95/p99/max and timeout/idle counts; every missed ~1-second decision investigated |
| Exercise safe failures | Practice-only malformed state, missing profile, exhausted stamina, ambiguous ownership and limited log backpressure | Correctly addressed stance or explicit identity/startup error; no invalid command; counters/warnings visible; response independence confirmed |
| Validate evidence pipeline | Export one scoped match from all five runtimes with true match/opponent/profile IDs; obtain authoritative final result | Deduplicated observations/actions, zero mixed-version imports, replay gaps labeled; SQLite report agrees with captured inputs and does not overclaim outcomes |
| Validate candidate/rollback lifecycle | Export a bounded candidate, explicitly approve for practice, deploy all five together, then re-export/redeploy old stored version | Runtime hashes/versions prove candidate and rollback actually applied; retain audit, approvals and comparable opponent results |
| Demonstrate playing effectiveness | Multiple matches against balanced/attacking/defensive practice bots, then held-out opponents, with comparable profiles and conditions | Goal difference/win rate/deadline/invalid-command evidence; ranking/tiebreak requirements clarified; no one-match causal claims |

No destructive AWS operation is needed for controller verification. Any later
resource deletion, replacement or production rollout is a separate explicitly
authorized operation with a tested rollback; this task supplies no such action.

### Optional operational improvements

Durable collector/backups with explicit replay completeness, alerting on
unresolved possession/log loss/invalid commands, profile signing, opponent-
stratified canaries if the tournament permits them, and post-match model advice.
These require independently justified value and are not substitutes for the
mandatory contract, correctness and latency acceptance above.
