# Coordinated AWS football agent

This self-contained project in `final_agent/` is the deployment source, copied
from `redesign_4/` at merged commit `5525a87` (PR #13; see
[`source.json`](source.json) for the full revision). The original `redesign_4/`
remains untouched; future player changes and deployments belong here.
It is a deterministic five-player controller with a separate
SQLite-backed offline trainer, deployed to AWS AgentCore with real-match
observations documented below. See [the audit](docs/redesign_audit.md) for
original findings, selection, local evidence and remaining acceptance checks.

Requires Python 3.11+. The core runtime, CLI and trainer use only the standard
library. AWS serving is an optional, pinned extra.

```sh
cd final_agent
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[deployment,offline,learning,dev]'
.venv/bin/python -m unittest discover -s tests -v
```

Local tests do not need AWS credentials. Without the deployment extra, the actual
SDK transport test is explicitly skipped; the rest of the suite still runs.
All subsequent commands run from `final_agent/`, the project root. The package
contains only its own `agent/`; the sibling reference implementations remain unchanged.

## Deployment boundary

`agent/`, `profiles/`, `deploy/`, tests and offline-coach tooling are independent
copies, not symlinks or imports from `redesign_4`. The pinned profile remains
`r4v7_route_stamina`; this directory extraction does not change decisions or
deploy anything to AWS.

```sh
.venv/bin/python deploy/deploy.py --stage-only
# Only when an AWS rollout is explicitly intended:
.venv/bin/python deploy/deploy.py
```

Staging reads this directory's code/profile and writes only into
`final_agent/deploy/app/player`. Retain the existing deployment project name
`Redesign4`, target name and five `r4_*` runtime names to update the existing team
rather than create replacement resources. Before a real rollout, restore the
existing ignored `deploy/agentcore/aws-targets.json` and required CLI deployment
state from the previous deployment workspace, then inspect `--status` and the
deployment diff. Credentials, target state, telemetry, SQLite databases, resource
receipts, virtual environments and generated build output are not part of this
snapshot and must not be committed.

Historical reports below describe their original source/deployment, not a new
`final_agent` rollout. The simulator's existing `redesign_4_v2` builder still uses
`redesign_4`; it is not the deployment source for `final_agent`.

## Try one local decision

The supplied invocation and manifest are **synthetic examples**, not captured AWS
traffic or real match results.

```sh
mkdir -p local
.venv/bin/python -m agent decide \
  --input examples/invocation.json \
  --match-id example-match --opponent-id example-opponent \
  > local/commands.json 2> local/decisions.jsonl
```

Stdout is one JSON command array. Stderr contains structured JSONL for training.
The callable `Runtime.handle(payload)` instead returns a JSON-array **string**.
Each invocation controls exactly one player. Five independent invocations of the
same snapshot and pinned settings compute the same presser, interceptor, cover
and distinct marking assignments.

The CLI writes the response before draining telemetry for up to one second.
Incomplete logging makes the CLI exit nonzero with a diagnostic, even if the
command was already printed. Do not ingest a mixed stderr diagnostic stream as
JSONL after a failed CLI run.

## Supported input contract

The sample-compatible envelope is either
`{"prompt": {"gameState": ..., "teamId": 0, "myPlayers": [2]}}`,
the same `prompt` as a JSON string, or that inner object directly.

| Input | Contract |
|---|---|
| Team/player routing | `teamId`: integer 0/1 or home/away (case-insensitive); one `myPlayers` element: integer 0-4 or `agentId_N` |
| Players | Exactly five distinct indices per team; `teamCode`/`agentId` or legacy `teamId`/`playerId`; finite `position.x/y`, optional velocity and stamina |
| Geometry | Canonical centered 110 x 70 sample pitch; home attacks +x, away -x. The deployed competition adapter converts compact live-match coordinates separately; see below |
| Stamina | 0..1 locally and in recognized live-match sessions; configured scale 100 for synthetic Fitness Check inputs. Never guess scale per player |
| Ball | Finite position, optional velocity; boolean `isFree`; otherwise `possessionAgentId` or `possessionPlayerId`, with optional `possessionTeamId`/`possessionTeamCode` |
| Clock | Nonnegative integer `tick`, finite nonnegative `gameTime` seconds; positive `matchDuration` seconds, default **120** from original requirements |
| Score | `{"home": nonnegative_integer, "away": nonnegative_integer}` |
| Coach | Optional `gameState.coachInstruction`; recognized exact presets listed below. Other text has no tactical effect |

**Unknown possession is not free possession.** The official server sends only
`possessionAgentId: "agentId_N"`, and `agentId_N` exists on both teams. The
holder is resolved to the same-index player within 3 m of the ball and at least
1 m nearer than its counterpart. Contested or distant cases remain unknown,
producing `unresolved_possession` telemetry and conservative shape, not guessed
passes/shots/interceptions. Explicit `isFree=true` wins over stale ownership.

Recognized coach presets are `go_all_out_attack`, `slow_the_tempo`, `press_high`,
and `shoot_on_sight`. They adjust posture, eligibility or bounded shot range
without bypassing lane safety or the single-presser assignment. The actual coach
delivery channel and any additional presets remain unverified; no shout-sending
service or arbitrary-text model is implemented.

Outputs use a conservative public-sample subset: MOVE_TO, PASS, SHOOT,
GK_DISTRIBUTE, PRESS_BALL, MARK, INTERCEPT and balanced SET_STANCE. Numeric fields
are finite JSON numbers, IDs are integers, parameters are command-specific and
duration is integer zero. No DRIBBLE, CLEAR, TACKLE or IDLE is invented from the
older descriptive requirements. No reset/destructive command is emitted.

Identifiable bad game state produces a validated balanced stance with sanitized
error telemetry. Missing/invalid routing identity raises an explicit error:
there is no safe way to manufacture the intended team/player. A valid command
shape is **not** evidence that a server accepted or executed it.

## Offline coaching

For opt-in automatic provisional Memory collection and match-wide profile
pinning, see [automatic shared-memory learning](docs/automatic_learning.md).
The deployed workshop variant uses S3 coordination, CloudWatch-triggered Lambda
workers and AgentCore Memory, without DynamoDB/SNS/SQS/EventBridge. Collection
and learning have explicit switches. The current tactics-v2 rollout uses
`match_profiles_tactics_v3` and an isolated registry namespace, with identical
serving/evaluation tactics; see the deployment status in that document.
A deterministic learner can propose one bounded
numeric remedy, evaluate it against fixed paired simulator fixtures and
conditionally promote it for new match pins only.

The tactics-v2 revision was deployed on **2026-10-24** from merged commit
`78e9b4d33d81f6d0d0768bc660d5cf5ad4f4541f`. All five runtimes and DEFAULT
endpoints are **v11, READY**, with unchanged ARNs. Stack
`final-agent-s3-tactics-v3` uses namespace `aleksalotl_tactics_v3`; collection
and learning are enabled. The old rush-v2 stack is disabled, with its evidence
and pins retained. Promotion is guarded until **2026-10-24 15:38:04.078 UTC**
(17:38:04.078 UTC+2) while legacy sessions drain. Existing sessions may retain
their original runtime version. See the learning guide for verification and
baseline identity; never deploy only a new evaluator against old players.

For opponent-specific AgentCore Memory scouting, see
[CloudWatch scouting and pre-match briefs](docs/offline_coaching.md#cloudwatch-scouting-and-pre-match-briefs).
`scout` prepares source-bound observations locally; explicit `publish` stores
them, and `prematch` reads historical opponent notes into a review-only brief
and advisor evidence. No live player Memory calls or automatic profile changes
are introduced.

```sh
.venv/bin/python -m agent learn \
  --db local/coach.db --manifest examples/match.json \
  --records local/decisions.jsonl --output local/candidate.json
.venv/bin/python -m agent report --db local/coach.db --match-id example-match
```

`learn` imports, analyzes, trains and exports. Separate `ingest` and `train`
commands are available. A six-field manifest contains `match_id`, `opponent_id`,
`team_id`, timezone-aware `played_at`, boolean `complete`, and `result`. A complete
result is `[our_goals, opponent_goals]`, **not always home/away**; a partial match
uses `result: null`. Complete means the final result is available, not that the
replay is complete. Do not invent results for the examples.

Use exactly one match and one pinned profile version per import. All five players'
records can be merged: snapshot/tick and action/player duplicates are deduplicated;
conflicting duplicates roll the transaction back. Partial matches may be finalized
with unchanged identity/time/profile. Final scores cannot decrease or contradict
observed scores. Runtime records without trusted match/opponent IDs can only be
bound with explicit `--bind-context` after independently isolating the match.

Pass/distribution attempts can be followed across explicit free-ball snapshots
for up to **4.1 elapsed game seconds** with increasing ticks and unchanged score.
Ticks need not be decision ordinals. Unknown ownership, score changes, expiry or
unchanged original ownership leave the attempt unknown. Arriving at the intended
receiver or opponent possession produces a **possible**, not confirmed, outcome.
Missing observations and another touch can still confound this inference. Goals
are never attributed to shots solely from score changes.

General corrections require at least **three distinct supporting observation
ticks in each of three complete matches within 30 days** for the same lesson.
Unrelated observations cannot satisfy this gate. Weaker evidence remains a
stored proposal; bounded, recency-weighted opponent adaptations are separate.
All candidates are recomputed from baseline settings and retained evidence,
not cumulatively applied to an unknown previously played profile.

Inspect/export/approve a version using the exact `trained-...` identifier emitted
by `learn` or `train`:

```sh
.venv/bin/python -m agent audit --db local/coach.db --version trained-REPLACE
.venv/bin/python -m agent promote --db local/coach.db \
  --version trained-REPLACE --note "approved for controlled practice"
.venv/bin/python -m agent export --db local/coach.db \
  --version trained-REPLACE --output local/candidate.json
```

`promote` records operator approval; **it does not deploy or demonstrate an
improvement**. Rollback exports an older stored version and explicitly repackages
it into all five runtimes. Preserve the SQLite database and immutable exported
profiles; do not treat an ephemeral container filesystem as durable memory.
SQLite transaction errors and file export failures surface explicitly. Profile
export uses temporary-file fsync and atomic replacement; no remote store,
background S3 refresh or automatic deployment is involved.

## Strategy profiles

A profile may carry two optional sections: `style` and `coach_policy`. Profiles
without them load with identity defaults, which reproduce pre-change decisions
exactly.

`style` fields (bounds are in `agent/profile.py`):

| Field | Meaning |
|---|---|
| `block_depth` | How far forward the team defends; 1.0 = full pitch |
| `attackers_committed` | How many players may cross halfway in possession |
| `tackle_aggression` | Slide-tackle radius; 0 = never slide |
| `tempo` | Bias between forward passing and holding |
| `formation` | `1-1-2`, `2-1-1` or `1-2-1`; fixed for the whole match |
| `carry` | Weight of the dribble-into-space option |

- `coach_policy: rules` lets every player derive the same preset from the score and
  the clock, e.g. `press_high` when trailing.
- A preset sent by the platform always wins.
- No preset ever changes formation.

Named archetypes (`balanced`, `direct_carry`, `press_aggressive`, `park_the_bus`,
`time_waster`, `hard_tackle`) live in `agent/archetypes.py`.

The evidence-based choice is `profiles/recommended.json` (`direct_carry`, no coach
policy). See `docs/strategy_report.md` for the results. Deploy it to all five
runtimes:

```sh
AFC_PROFILE=profiles/recommended.json .venv/bin/python -m agent.main
```

To keep the chosen style when retraining, pass it as the base:
`agent train|learn ... --base-profile profiles/recommended.json`.

## Offline AgentCore coaching

An explicit, operator-driven pipeline that uses three AgentCore services
(Memory, CodeInterpreter, Evaluator) to produce a **proposal** for a strategy
update. A proposal is never approved or deployed automatically. Full guide:
`docs/offline_coaching.md`.

```sh
# Provision offline resources (separate from player runtimes)
python deploy/offline_coach.py --config offline_coach.json plan
python deploy/offline_coach.py --config offline_coach.json apply --approve

# Prepare evidence and run the full coaching workflow
python -m agent.advisor.offline prepare \
  --manifest examples/match_manifest.json --report examples/match_report.json \
  --coach-claims examples/coach_claims.json --profile profiles/recommended.json \
  --team r4 --expires-at 2026-11-01T00:00:00Z --output local/evidence.json
python -m agent.advisor.offline run \
  --config offline_coach.json --evidence local/evidence.json \
  --profile profiles/recommended.json --output local/run.json
```

Exit codes: 0 = proposal, 2 = service/validation failure, 3 = rejected by evaluator.
Player runtimes (`r4_gk`/`r4_def`/`r4_mid`/`r4_fwd1`/`r4_fwd2`) are never touched.

## Advisor (optional)

An off-tick LangChain helper. It is never imported by the live runtime.

```sh
.venv/bin/python -m pip install -e '.[llm]'
export AFC_ADVISOR_MODEL=<bedrock-model-id>
.venv/bin/python -m agent.advisor pick --matrix campaign_a_report.json \
  --opponent OPPONENT_ID --output local/proposal.json
.venv/bin/python -m agent.advisor review --report match_report.json \
  --profile profiles/recommended.json --output local/proposal.json
```

- Output is validated against the profile bounds and written as a
  `status: "proposal"` file.
- Proposals are **never auto-deployed**. An operator reviews one and deploys it
  with `AFC_PROFILE`.

## Optional AgentCore serving

After local setup, `python -m agent.main` (or `afc-agentcore`) starts the SDK server.
Local ASGI tests exercise `/ping` and `/invocations` without opening a network
connection. The pinned SDK streams one SSE data event containing the JSON-array
string, the same framing as the official sample (`yield json.dumps(commands)`).

| Environment variable | Behavior |
|---|---|
| `AFC_PROFILE` | Optional path to validated exported JSON; loaded once at startup; invalid/missing supplied file fails startup |
| `AFC_MATCH_ID` | Optional trusted single-match context for telemetry only; omit if autoplay match binding is not established |
| `AFC_OPPONENT_ID` | Optional trusted opponent identity for startup profile selection and telemetry |
| `AFC_STAMINA_SCALE` | `1` (local default) or `100` (set by `deploy/` for AWS percentage inputs); invalid values fail startup |
| `AFC_COMPETITION_ADAPTER` | `0` (local default) or `1` (set by `deploy/`): recognize live-match SDK session IDs and convert compact geometry/fractional stamina; invalid values fail startup |
| `AFC_PLAYER_ID` | Optional seat `0`-`4` used **only** when an invocation omits `myPlayers`; explicit routing always wins. Set per runtime by `deploy/` |

There is intentionally no `LLM_ENABLED`, S3 bucket, Bedrock model ID or
credential environment setting in this implementation. Do not reuse pinned
match/opponent context across unrelated autoplay matches. All five runtimes must
load the same version and opponent context before a match; updating individual
endpoints mid-match breaks the shared-plan prerequisite.

The controller makes **no AWS service calls** and needs no S3, Bedrock inference,
Memory or Gateway permissions.

## AgentCore deployment

`deploy/` is an AgentCore CLI v1 project (`agentcore/agentcore.json`, CDK app in
`agentcore/cdk/`) with five CodeZip runtimes, `r4_gk`, `r4_def`, `r4_mid`,
`r4_fwd1` and `r4_fwd2` (Python 3.12, PUBLIC, HTTP, OTel off). All five share
`deploy/app/player`. `deploy.py` stages `agent/` and the chosen profile
(default `profiles/recommended.json`) there. Each runtime differs only in its
`AFC_PLAYER_ID` seat.

All five deployments explicitly set `AFC_STAMINA_SCALE=100`: the synthetic Fitness
Check sends percentage stamina, unlike live-match fractional values.
This normalizes 100 to 1.0 and 1 to 0.01 without guessing per player. Keep scale
1 for local simulator inputs. Omitting the AWS setting causes
`ValueError: stamina outside configured scale` and safe `SET_STANCE` responses,
which fail fitness realism checks. The documented stamina range is in the
[workshop game mechanics](https://github.com/aws-samples/sample-ai-possibilities/blob/main/agentic-football-coach/kiro-football-coach/content-reference/en/game-mechanics.md).
Those rules describe a stamina stat range, not a reliable guarantee of the
live observation's units; the first real match disproved a single global scale.

`AFC_COMPETITION_ADAPTER=1` separates those contracts using the SDK session ID.
Observed live IDs are `<match UUID>-team[01]-pos[0-4]`; they select fractional
stamina and compact geometry. Player x/y position and velocity are multiplied
by 8 for canonical tactics. Three-dimensional ball vectors use x/z (not
vertical y); two-dimensional vectors retain x/y. Outgoing MOVE_TO coordinates
are divided by 8 and clipped to x +/-6.4, y +/-3.5. Fitness sessions retain
sample geometry and scale 100, including the 1% edge case. No individual stamina
value or crowded formation is used to guess a format. Unrecognized non-fitness
session IDs add `unrecognized_competition_session` rather than silently selecting
the live contract.

The factor 8 is **provisional calibration** from the first match: players
repeatedly reached +/-6.4 and the ball crossed goal ends around +/-6.8, while our
tactics expected +/-55. Exact server field dimensions and 3D projection still
need confirmation against future live captures. Each decision logs
`input_format` and `input_contract` (scales and chosen ball axis), canonical
snapshots, and actual wire commands. Recognized session UUIDs now populate
`match_id`; opponent IDs are not fabricated.

Prerequisites: Node >= 20, `npm install -g @aws/agentcore aws-cdk`, `uv`, the AWS
CLI, and credentials for the competition account in **us-east-1 or us-west-2**
(the only regions the Player Portal accepts).

```bash
source <credentials file>            # export AWS_ACCESS_KEY_ID=... etc.; never commit it
python deploy/deploy.py              # stage -> cdk bootstrap if missing -> agentcore deploy -> ARNs
python deploy/deploy.py --status     # reprint the five runtime ARNs
python deploy/deploy.py --stage-only # stage only (then e.g. `cd deploy && agentcore build`)
```

The deploy creates stack `AgentCore-Redesign4-default` with five runtimes and
their execution roles. Generated state (`aws-targets.json`, `.cli/`, `.cache/`,
`cdk.out/`) and the staged code are git-ignored. Redeploy the same profile to
all five runtimes together, never mid-match.

Then, in the Player Portal (https://agentic-football.aws.dev, My Team):

1. Pick the formation (profile was evaluated with **1-1-2**).
2. Register one runtime ARN per position: GK `r4_gk`, DEF `r4_def`, MID
   `r4_mid`, FWD `r4_fwd1`, FWD `r4_fwd2`. Use the `:runtime/` ARN, not
   `:runtime-endpoint/`.
3. Run the Fitness Check, then a practice match.

The platform invokes from account 793938076386 by assuming
`afwc-cross-account-agent-invoker` in our account. The workshop account comes
with this role. Check that it exists with
`aws iam get-role --role-name afwc-cross-account-agent-invoker`.

Initial transport smoke checks used the official sample state, not the live
Fitness Check (which exposed the stamina-scale mismatch above):
- every response is a single SSE `data:` event holding a JSON command array;
- the GK holding the ball returns `GK_DISTRIBUTE`, and PASS carries `target_player_id`;
- a payload without `myPlayers` uses the seat;
- latency is about 3 s on a cold session (new microVM) and about 0.2 s warm
  from Europe, against the 5 s fitness limit;
- those sample calls produced no CloudWatch error records; this did not prove
  compatibility with the live Fitness Check.

After correcting the scale, direct AWS checks exercised all five runtimes on
both teams with stamina 100, 1 and 0 (30 invocations). Commands matched local
decisions, the goalkeeper distributed, and CloudWatch snapshots confirmed
normalized stamina 1.0, 0.01 and 0.0 without error fallbacks. These are synthetic
integration checks; rerun the portal Fitness Check to establish its verdict.

AgentCore sessions can continue serving their original runtime version after
redeployment. The portal reused the same Fitness Check session IDs across
runs, so an existing session still returned the old stamina error despite
`DEFAULT` pointing to the updated version. CloudWatch's `sessionId` identifies
the affected session. Outside a live match, stop each affected session explicitly:

```bash
aws bedrock-agentcore stop-runtime-session \
  --agent-runtime-arn '<runtime ARN>' --runtime-session-id '<CloudWatch sessionId>'
```

The next invocation, including one using the same session ID, starts on the
updated version. Verified by reproducing the old fallback on all five original
Fitness Check sessions, stopping those sessions, and receiving normal decisions
with percentage stamina using those same IDs. Do not stop unrelated match
sessions. A fresh-session smoke test alone does not establish that the portal
has stopped using a stale session.

The model-allowlist check expects a Bedrock model, and how it treats an
LLM-free agent is unverified. The portal locks matches only on a `DENIED`
verdict, and "Not verified yet" does not lock.

### First real match: evidence and corrections

`report1.pdf` records a 1-2 loss to The Benchmark FC with 51% possession.
Its AI narrative/command attribution is inconsistent: it lists 34 SHOOT
commands under our team but zero shots in its statistics, while our CloudWatch
telemetry contains no SHOOT or PASS decisions. Do not tune pressing or marking
from that narrative alone.

397 application records were available: 392 decisions and five initial
invalid-roster fallbacks, not a complete replay of all 405 expected calls.
There were 315 MOVE_TO, 40 MARK, 20 PRESS_BALL, 17 INTERCEPT and five SET_STANCE
returns. Unknown possession warnings occurred on 114 decisions. The old
decoder discarded ball z and unresolved holder IDs, preventing exact replay.
Players repeatedly hit the compact pitch boundaries, leaving every carrier
outside the hardcoded sample-pitch shooting range. Live full stamina was
incorrectly normalized to 0.01 by the earlier Fitness Check configuration.

The correction is at the protocol boundary, not a new formation or speculative
strategy change. A partial reconstruction of the 392 recorded observations with
corrected units generated two SHOOT and 15 PASS attempts instead of zero;
these are counterfactual decisions, not evidence of accepted actions or wins.
Tactical weights, formation 1-1-2 and the pinned direct-carry profile are unchanged.
Evaluate improvement in the next real match, especially shot attempts,
possession ambiguity, boundary saturation and stale-session behavior.

Deployment verification exercised 42 direct AWS calls across both teams and
both input formats, including both forwards shooting and full/depleted stamina.
All five `DEFAULT` endpoints run version 3 with the adapter enabled; existing
runtime ARNs were retained. The five known portal Fitness Check session IDs
were also refreshed and invoked successfully. These synthetic checks do not
establish the next portal verdict or a competitive improvement.

### Second real match: implemented tactical corrections

`report2.pdf` records another 1-2 loss, this time to Total Attack United.
The 399 available CloudWatch records for
`64c91b0b-c1b1-4e93-ae1c-5e79bb06f6a9` contain 394 decisions and five
tick-0 invalid-roster fallbacks. All decisions used the compact adapter.
Actual decisions include 302 MOVE_TO, 25 PASS, 4 SHOOT, 30 MARK, 16 PRESS_BALL,
16 INTERCEPT and 1 GK_DISTRIBUTE. The PDF attributes 0 PASS and 64 SHOOT to us;
its columns resemble reversed attribution but do not exactly match our capture.
Its possession, latency and scorer attribution remain report claims, not
independently established team metrics.

The implemented corrections are:

- Carrying now reuses the movement sprint guard: stamina at least 0.35 and a
  target over 5 canonical units away. Tick 7328 previously requested sprint
  with stamina 0.11782325. A tired carrier still has a non-sprint carry option.
- Keeper trajectory projection now intersects its actual reachable movement
  plane, including the compact x-limit, rather than projecting at -54 and
  commanding -52 (then clipping to -51.2). Only explicit free-ball flight is
  projected; a dribbler's velocity is not treated as a shot. Reachable incoming
  threats can request sprint through the same stamina guard.
- A keeper projected 0.8 seconds ahead and still over 8 units behind the carrier
  permits a longer shot: ordinary range plus 18, capped at 50. Angle and lane
  checks remain mandatory. Captured tick 4927 had a clear 35.7-unit opportunity
  against an advanced keeper but previously selected another carry.

With the original profile, counterfactual replay of the 394 canonical snapshots
produces 7 shooting attempts instead of 4 and 24 passes instead of 25.
This is decision replay, not a new match outcome. Frozen post-goal clocks account
for 12 of the 25 recorded passes; they do not establish an active-play passing
loop. The incoming frame for the second concession is absent.

Exact live goal width and save mechanics remain unverified. Goal-mouth width,
coordinate calibration and strict active-play roster validation are unchanged.
The five initial roster errors remain explicit; they are not hidden as successes.
The two reviewed golden-command changes are longer shots against stranded
keepers; the other 748 default-profile commands remain unchanged.

Offline profile tuning compared the corrected team against frozen attacking
and balanced stand-ins, alternating sides with paired seeds. Development used
48 matches per candidate; disjoint held-out seeds used 320 matches per profile:

| Profile | Held-out W-D-L | Goals for-against |
|---|---|---|
| Existing settings, corrected code | 151-97-72 | 476-337 |
| Shot range 33, support width 18 | 163-80-77 | 501-351 |
| Press radius 30, tempo 0.8 | 138-91-91 | 468-348 |
| Rules coach | 149-100-71 | 450-330 |

Keep the existing tactical weights, coach policy and formation. The shot/width
candidate gained 0.059 points/game, but its paired 95% interval was
[-0.094, 0.206] and it regressed against the attacking stand-in. Neither it nor
the other candidates justified promotion. `profiles/recommended.json` is tagged
`r4v3_match2_corrections` to distinguish the code corrections, not to claim a
newly proven winning archetype.

The frozen pre-correction baseline had the same held-out W-D-L and one more
goal than the corrected code. The simulator ignores sprint's effect on speed
and stamina, does not exercise compact keeper geometry, and does not reproduce
the captured advanced opposing keeper. These campaigns therefore neither prove
AWS improvement nor measure the stamina fix's benefit.
Reproducible commands, candidate JSON, frozen-source hash and paired intervals
are retained in `local_simulator/local/r4_tuning/conclusion.json` (ignored local
artifacts). Its evaluated tactics hash matches the corrected source.

After refreshing the expired credentials on 2026-10-23, the corrected source
and `r4v3_match2_corrections` profile were deployed to all five runtimes.
Each runtime is READY and its `DEFAULT` endpoint points to version 4; runtime
ARNs are unchanged. Direct AWS verification exercised 47 invocations: both
teams and both input formats at full/depleted stamina, the five refreshed
known portal Fitness Check session IDs, and the captured longer-shot and
tired-carrier regressions. Responses matched local decisions; the captured
carrier stopped requesting sprint and the stranded-keeper opportunity selected
SHOOT. These are synthetic checks, not a portal Fitness Check verdict or proof
of improved competitive results. Run the portal check and another real match.

### Third real match: 1-0 win and pressure-resilient keeper outlets

`report3.pdf` records Aleksalotl's 1-0 win against Fort Knox Athletic. The
available CloudWatch capture for `b728eb37-3829-48f2-b026-22040537fa76` contains
278 decisions across 57 ticks, plus five initial invalid-roster fallbacks.
All decisions used `r4v3_match2_corrections`. Actual commands were 199 MOVE_TO,
36 MARK, 15 PRESS_BALL, 13 INTERCEPT, 9 PASS, 3 SHOOT, 2 GK_DISTRIBUTE and
1 SET_STANCE. Twenty decisions across four ticks had unresolved possession.

Use both teams' coach advice as hypotheses, not authoritative attribution.
Aleksalotl's advice to increase shooting "from 0" contradicts our three logged
shots; the PDF's opposing command column again resembles our actual telemetry.
All three recorded shots used the stranded-keeper extension. The score first
became 1-0 at 36.380642 s, after the second shot, but sparse observations do not
prove which command scored or that the previous update caused the win.
Possession and opponent shot statistics remain report-attributed.

Fort Knox's advice to reduce MOVE_TO reliance and coordinate PRESS_BALL suggests
an adversarial test: pressure our keeper and nearest receiver. Both-direction
regressions require an alternative normally safe receiver to be preferred.
At captured tick 9786, known keeper possession instead selected SET_STANCE
because every ordinary outlet failed its filters. The correction preserves safe
distribution scoring and THROW/KICK selection, but attempts a ranked, explicitly
contested KICK if none qualifies. Keeper slow-tempo holding cannot override
distribution. Outfield holds and uncertain-possession conservatism are unchanged.
The observed final-tick hold may have been harmless; this correction addresses
the keeper action contract, not a proven cause of a lost scoring opportunity.

Counterfactual replay changes exactly one of 278 decisions: that hold becomes
a KICK to player 3. All three shots, both ordinary distributions and every
outfield decision are unchanged. This does not prove the contested kick will
succeed or withstand every coordinated press. Keep the winning formation,
weights and coach policy; the code-only correction is tagged
`r4v4_match3_keeper_outlet`. Implementation instructions and acceptance criteria
are in `../prompts/redesign_4_report3_fixes.md`.

Deployed on 2026-10-23 to all five existing runtimes: each is READY with
`DEFAULT` on version 5 and unchanged runtime ARN. Direct AWS checks exercised
57 invocations: all seats, both teams, sample/live contracts, stamina extremes,
the five refreshed known portal Fitness Check sessions, and all three captured
shots plus ordinary/congested keeper distributions mirrored across both teams.
All responses matched local commands; CloudWatch confirmed all 57 decisions
under the new profile with no telemetry drop/delivery failures. These are
synthetic integration checks, not a new portal verdict or competitive result.

### Fourth real match: 1-3 loss and short-distance keeper reaction

`report4.pdf` records Aleksalotl losing 1-3 to Total Attack United. Match
`b5a0c118-b7d8-4de4-ac76-5e528604be19` ran `r4v4_match3_keeper_outlet`.
The capture has 420 decisions across 88 observed ticks and five initial roster
errors. Actual commands: 320 MOVE_TO, 29 MARK, 28 PASS, 26 INTERCEPT,
11 PRESS_BALL, 5 SHOOT and 1 GK_DISTRIBUTE; 20 decisions warn of unresolved
possession. The report's 88 SHOOT/two PASS/no MARK attribution conflicts with
our logs; its opposing column resembles our decisions instead.

Our coach advice suggests passing instead of indiscriminate shooting, but
23 of our 28 pass decisions occur at frozen post-goal clocks. They establish
neither an active passing loop nor completed build-up. Keep the passing/shooting
weights rather than reacting to mislabeled counts. The opponent's advice about
finishing precision and support motivates accurate, fast-shot adversarial tests.

Two captured incoming-shot frames reveal the ordinary movement sprint guard
suppresses keeper urgency for short distances. Tick 4959 projects to the
reachable keeper plane in 0.392 seconds, with the full-stamina keeper 3.79
canonical units from the target, below the ordinary five-unit sprint threshold.
Tick 7560 projects to y=3.02 at that plane in 0.331 seconds, outside the existing
2.8 movement clamp but inside the existing modeled goal mouth at the goal line.
Both frames immediately precede observed score increases for the opponent.

The correction permits nonzero-distance keeper sprint for explicitly free,
incoming threatening trajectories, retaining stamina >=0.35. Threats crossing
the modeled goal mouth can be urgent even when the movement target is clamped.
The target plane, goal model, movement bounds and outfield sprint guard are
unchanged. These commands do not guarantee movement, a save or a better result;
exact platform geometry/save mechanics remain unverified. The first concession
has no captured incoming-shot frame, and no specific fix is inferred for it.

Replay changes exactly two of 420 decisions: keeper sprint false -> true at
ticks 4959 and 7560, with identical targets. All five shots, passes, marks and
distribution commands are preserved. Replaying report 3 retains its three shots
and the previous keeper-distribution correction. The code-only profile version
is `r4v5_match4_keeper_reaction`, with unchanged weights, coach policy and 1-1-2
formation. Instructions and acceptance are in
`../prompts/redesign_4_report4_fixes.md`. No offline AgentCore service integration
is included in this match correction.

All five existing runtimes were updated in place to **version 6**, with DEFAULT
READY on version 6 and unchanged ARNs. Verification made 65 direct AWS
invocations covering all seats, both teams, both input contracts, stamina
extremes, both captured shots mirrored, all five original shots mirrored, and
the previous contested keeper-distribution case. Responses matched local
commands; CloudWatch confirmed all 65 new-profile decisions with no recorded
telemetry drops or failures. Maximum observed round trip was 4.521 seconds;
this is not a per-tick deadline guarantee.

The five exact known portal Fitness Check sessions were refreshed, without
stopping unrelated match sessions. Run the portal Fitness Check before the next
match; ARNs need no replacement. Verification UUID
`c32a673f-1380-45d4-ad3d-0c68ceffe193`; exclude synthetic ticks 990008/990009 from
match research. Detailed evidence is persisted in the session files directory
as `report4-deployment-verification.json`; implementation commit `4799bb0`.

### Three-match feedback: targeted player corrections

Reports 5-7 describe results of 0-0, 1-2 and 0-1. User observations motivate
the following deterministic changes in `r4v6_three_match_feedback`, without
changing numeric profile settings, formation or coach policy:

- An unpressured clear forward corridor boosts carrying over sideways recycling;
  blocked runs, protecting a lead and slow tempo retain their existing scoring.
  A good shot or progressive pass can still win.
- A keeper projected well behind the carrier permits CENTER aim if its lane is
  clear; corner alternatives remain when the center is blocked. This reduces
  unnecessary corner targeting, not a demonstrated server-side accuracy fix.
- The keeper selects the furthest-forward outlet passing the existing distance,
  lane and pressure filters. Unsafe long options do not displace safe short ones;
  the existing contested fallback remains when all safe options fail.
- The keeper no longer sweeps free balls with INTERCEPT; it retains the existing
  bounded goal-line movement and incoming-shot reaction.
- The nearest active defender can press in midfield with the midfielder covering.
  A midfielder committed to an attack can support up to canonical x=40 rather
  than stopping at x=23; committed-attacker caps still apply.

These are observation-driven scenario corrections, not causal findings from
the reports: candidate CloudWatch match/team attribution is unresolved, and PDF
statistics conflict internally. The Bedrock profile proposal was not applied.
The new scenarios are mirrored for both teams; the default golden audit changes
36 of 750 commands for the documented shot/outlet/support/pressing policies.
The carry-enabled scenarios are checked separately. A replay of the three
provisional telemetry bundles validates 1,291 decisions and changes 31 commands;
neither the replay nor these scenarios demonstrate improved match results.
The generated `local_simulator/entrants/redesign_4_v2` copy is rebuilt from the
same agent source, with entrant hash prefix `722443191b26`.

Implementation commit `781fcfc` was deployed to all five existing runtimes:
each is READY, and each DEFAULT endpoint now selects runtime version **7** with
profile `r4v6_three_match_feedback`. Runtime ARNs are unchanged. Direct AWS
verification matched 44 responses against the local runtime, covering all
five seats, both teams, sample/compact inputs and all six feedback scenarios.
All 44 corresponding CloudWatch decisions contain the new profile version
and report zero dropped/failed telemetry records. Verification UUID:
`db73581e-dda4-4405-a717-a985421f2b3a`; detailed receipt:
`three-match-series/feedback-deployment-verification.json` in session files.
Exclude its synthetic ticks 990010-990053 from match analysis. Existing match
sessions can remain pinned to older versions; use a new match for this revision.

### Route/stamina candidate (not deployed)

`r4v7_route_stamina` adds short lateral/diagonal carry targets only when the direct
next segment is blocked and the alternative passes a stricter clearance check.
The targets stay inside the shared compact command bounds, use current/predicted
opponent positions, and do not relax shot or pass guards. No clear escape means
the previous fallback remains; this does not guarantee retaining possession.

For routine pressing, a materially fresher teammate can replace the fastest
arrival only within a 0.35-second tolerance. Close duels, deep defensive threats
and free-ball interception retain the quickest response. Calm carriers away from
goal need 0.55 stamina to request sprint; current/imminent pressure and nearby scoring chances
retain the existing 0.35 floor.

Regulation is 120 seconds. A tied game continues until the next goal or 240
seconds, so `clock > duration` is valid and must not force a duration override.
Tied extra-time posture remains balanced. A tempo-0.65 candidate changed none
of 292 captured tied-extra-time decisions; that is no evidence of competitive
benefit, so the more aggressive policy was not promoted.

Mirrored tests cover escape routes, predicted blockers, touchline bounds, fresh
press selection and its emergency exceptions, sprint reserves and extra-time
shooting. Captured-frame replay covers 1,036 decisions from matches
`a5ddac07-007f-4d78-8c97-cb7f9b1fcc42` and
`2d4dd176-ca81-4dd6-88de-e3e99b9a3e97`. It checks command identity, determinism,
clearance after wire conversion, cover/interceptor constraints, and preservation
of captured shots, keeper outlets and interceptions. It is not a counterfactual
match simulation or proof of reduced stamina consumption or improved results.
Evidence is saved in session files as `route-stamina-evaluation.json`.

These are source changes only. AWS remains on `r4v6_three_match_feedback`,
runtime version 7; deploying this candidate requires a separate operator rollout.

## Telemetry and deadline limits

Application telemetry is best-effort: one background writer, at most 128 queued
records plus one in flight, drop-newest on overflow, no retry loop. Records carry
cumulative `telemetry.dropped`, `failed` and `last_error` (exception class only).
`Runtime.log_status` exposes current counters and pending work;
`flush_logs(timeout)` and `close(timeout)` are explicit bounded lifecycle methods,
never called per live tick. Expected sink I/O/serialization failures cannot veto
a command. A permanently blocked sink can lose logs; a frozen/killed runtime
can lose its tail. Monitor counters and keep incomplete replay inference
conservative. Application logs omit the raw envelope, credentials and arbitrary
coach text; they contain football snapshots, actions and supplied match IDs.

`latency_ms` measures planning/record construction, not AWS end-to-end latency.
Queue isolation does not make process scheduling, framework logging, cold starts,
network transit or large-input parsing bounded. The pinned SDK itself currently
emits synchronous framework logs and dependency deprecation warnings. Only
future deployed timing can establish the approximately one-second deadline.

## Development and architecture

```sh
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m ruff check agent tests
.venv/bin/python -m ruff format --check --quiet agent tests
.venv/bin/python -m mypy
```

`protocol.py` owns normalization/validation, `model.py` the snapshot/profile
types, `tactics.py` the pure team plan and choices, `runtime.py`/`telemetry.py`
the response and logging boundary, `profile.py` profile validation/publication,
and `trainer.py` offline evidence and coaching. `main.py` is the optional SDK
adapter; `__main__.py` is the local CLI.

See [terminology](CONTEXT.md), [state machines](state_machines.md), and
[data flow](data_flow.md). The [single audit report](docs/redesign_audit.md)
records baseline selection, source findings, verification and unresolved AWS
acceptance work. Reference implementations are historical evidence, not packages
to import from the root application.
