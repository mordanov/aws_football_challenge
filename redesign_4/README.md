# Coordinated AWS football agent

This project in `redesign_4/` consolidates the three reference redesigns using `redesign_2`
as its baseline. It is a deterministic five-player controller with a separate
SQLite-backed offline trainer. It has **not run against the intended AWS
infrastructure**. See [the audit](docs/redesign_audit.md) for findings, selection,
local evidence and the mandatory future AWS acceptance checklist.

Requires Python 3.11+. The core runtime, CLI and trainer use only the standard
library. AWS serving is an optional, pinned extra.

```sh
cd redesign_4
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[deployment,dev]'
.venv/bin/python -m unittest discover -s tests -v
```

Local tests do not need AWS credentials. Without the deployment extra, the actual
SDK transport test is explicitly skipped; the rest of the suite still runs.
All subsequent commands run from `redesign_4/`, the project root. The package
contains only its own `agent/`; the sibling reference implementations remain unchanged.

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
| Geometry | Centered 110 x 70 sample pitch; home attacks +x, away -x. Halftime switching and shot-label orientation require real-contract verification |
| Stamina | 0..1 by default; explicitly select scale 100 for percentage input. Never guess scale per player |
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

All five deployments explicitly set `AFC_STAMINA_SCALE=100`: the live Fitness
Check sends percentage stamina, unlike the public sample's fractional values.
This normalizes 100 to 1.0 and 1 to 0.01 without guessing per player. Keep scale
1 for local simulator inputs. Omitting the AWS setting causes
`ValueError: stamina outside configured scale` and safe `SET_STANCE` responses,
which fail fitness realism checks. The documented stamina range is in the
[workshop game mechanics](https://github.com/aws-samples/sample-ai-possibilities/blob/main/agentic-football-coach/kiro-football-coach/content-reference/en/game-mechanics.md).

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
