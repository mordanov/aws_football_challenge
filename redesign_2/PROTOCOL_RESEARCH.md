# Agentic Football Cup — Protocol Research

**Access date:** 2026-10-20
**Primary repo:** `aws-samples/sample-ai-possibilities` — `main` @ `465fc5fc1608e59409b53e562986dafbdf2ec3cf`
(`lib/state.py` last touched at commit `54c482d0`, 2026-10-19). **License: MIT-0** (repo-wide,
`LICENSE` file present) — permissive, attribution not legally required but given below; no
implementation code is reproduced beyond short (<20 line) schema/config excerpts for fact-citation.
Website `agenticfootballcup.ai` is a client-rendered SPA (Vite bundle); static fetch only returned
the HTML shell, no protocol content — **not usable as a source** without a JS-executing browser
(not attempted; out of scope for this pass). No separate "official Strands blog — Inside Agentic
Football Cup" post could be located (searched `aws.amazon.com/blogs/machine-learning`, general web
search); treat the blog as **UNKNOWN / inaccessible**, not a usable primary source.

Legend: **VERIFIED** = read directly in cited source. **INFERRED** = reasoned from verified facts
but not stated outright. **UNKNOWN** = no public source found, or sources conflict without a
tiebreaker.

---

## 1. Where the real contract actually lives

The task's three starting points resolve to very different confidence levels:

- `agentic-football-sample-agents/` (in `aws-samples/sample-ai-possibilities`) — **VERIFIED, highest confidence.** Working, importable Python source (with unit tests: `lib/test_state.py`, `lib/test_parsing.py`) for five deployed Bedrock AgentCore runtimes (GK/DEF/MID/FWD1/FWD2) plus three deployment variants (balanced / gateway-MCP-tools / AgentCore-Memory).
- `agentic-football-coach/kiro-football-coach/` (same repo) — a Kiro IDE workshop coach's reference docs (`.kiro/skills/football-rules/SKILL.md`, `.kiro/skills/football-strategy/SKILL.md`, `content-reference/en/game-mechanics.md`, `content-reference/en/strategies.md`, `.kiro/steering/workshop-content.md`). These are **narrative/teaching material**, not the game server's own spec, and — critically — **contradict the sample code in several places** (see §7). Treat as secondary, lower-confidence corroboration only where it agrees with code.
- `agenticfootballcup.ai` and the Strands blog post — **inaccessible** to this research pass (see above). Not used.
- The workshop docs (`content-reference/en/workshop-flow.md`, Phase 5) point participants to clone `github.com/aws-samples/fifa-agentic-football-strands` and `fifa-agentic-football-langchain` as the actual starter-kit repos used in the live event. **Both returned HTTP 404 via `gh api repos/...` on 2026-10-20.** Either private, renamed, or workshop-only — **confirmed gap**: the canonical per-event starter kit is not publicly reachable; `sample-ai-possibilities/agentic-football-sample-agents` is the only publicly fetchable code and may trail the real event kit in features.
- The actual match server / orchestrator (the thing that calls agent runtimes, validates commands, runs physics, emits the Player Portal viewer feed, and produces post-match reports) is **not open source anywhere found**. Everything about it below is reconstructed from the client-side contract the sample agents implement, or from the workshop docs' prose — never from server source.

---

## 2. Transport / invocation contract (VERIFIED)

Each of the 5 positions is **a separate Amazon Bedrock AgentCore Runtime** (`BedrockAgentCoreApp`
from `bedrock_agentcore.runtime`), one HTTP endpoint per player, registered in
`agentcore/agentcore.json` as `protocol: "HTTP"`, `runtimeVersion: "PYTHON_3_12"`,
`networkMode: "PUBLIC"`. There is no single team endpoint — a team is 5 independently invoked
runtimes, each told which player it owns via payload, not via URL.

**Invoke payload** (what the orchestrator/match server sends), reconstructed from
`lib/agent_base.py:create_invoke_handler` (`sample-ai-possibilities:agentic-football-sample-agents/lib/agent_base.py`, read 2026-10-20):

```json
{
  "prompt": "{\"gameState\": { ... }, \"teamId\": 0, \"myPlayers\": [0]}"
}
```

- `prompt` is a **JSON-encoded string**, not a nested object (code does
  `json.loads(prompt) if isinstance(prompt, str) else prompt` — defensively handles both, so the
  exact wire form is not 100% pinned down, but the string-encoded form is what the sample assumes).
- `teamId`: 0 or 1 (HOME/AWAY).
- `myPlayers`: list of player indices this runtime controls — code takes `myPlayers[0]` as the
  effective player id, i.e. **the sample agents assume one player per runtime** even though the
  field is a list (design allows multi-player-per-runtime; unused by the sample).

**Response**: the runtime must emit (via `yield`, AgentCore's streaming entrypoint pattern) a
**JSON array string** of command objects for that tick. `lib/parsing.py` extracts the first
`[...]` substring from the LLM's raw text via regex, tolerates Python-literal artifacts (`True`/
`False`/`None`, trailing commas, markdown fences — see `lib/json_tolerant.py`), and silently drops
any command whose `commandType` isn't in `VALID_COMMANDS`.

Error handling is a 3-tier fallback **inside each agent**, not a server-side retry: (1) LLM output
parsed, else (2) deterministic rule-based `fallback_commands()` from `lib/fallback.py`, else (3) a
single hard-coded "last resort" command (e.g. `SET_STANCE`). **This means a slow/broken LLM never
surfaces as a protocol-level timeout to the agent author** — the agent process itself must enforce
graceful degradation within the response-time budget; the match server's behavior if a runtime
simply never answers in time is **UNKNOWN** (no retry/penalty logic is visible from the client side).

---

## 3. Tick / timing model — CONTRADICTION between sources

- `agentic-football-coach/.../football-rules/SKILL.md` and `game-mechanics.md` (workshop docs, same repo) state:
  - Game runs at **60 ticks/second** internally.
  - **Agent is invoked roughly every ~2 seconds** (i.e., not every physics tick).
  - **500ms maximum** agent response time per decision.
  - Match length: **5 minutes, single half, no halftime**.
- The sample agent's own test fixture (`lib/test_helpers.py`, `GAME_STATE["tick"] = 150`,
  `"gameTime": 120.5`) is consistent with *a* tick counter advancing roughly 1.something per
  invocation at 120.5s elapsed, which is compatible with the ~2s cadence claim but does not
  independently confirm it — **the 60Hz/2s/500ms numbers are only sourced from the workshop docs,
  never from runnable code or server source. Mark as INFERRED-from-secondary-source, not VERIFIED.**
- No source (code or docs) specifies what happens on a **missed deadline** beyond "IDLE" being
  listed in `football-rules/SKILL.md` as "default if agent times out" — this detail does **not**
  appear in `game-mechanics.md` or in any sample-agent code path. **UNKNOWN / unconfirmed** whether
  IDLE-on-timeout is real server behavior or workshop simplification.

---

## 4. Game state schema (VERIFIED, with one unresolved format split)

`lib/state.py`'s own doc-comments explicitly describe **two wire formats** the code defends
against, implying the server itself may have changed format across phases of the event /
compatibility window:

- **New / current format**: `agentId` (string, `"agentId_<N>"`), `teamCode` (string, `"home"`/`"away"`,
  case-observed-inconsistent — see code comment below), `possessionAgentId`, `ball.isFree` (bool).
- **Old / legacy format**: `playerId` (int), `teamId` (int, 0/1), `possessionPlayerId`,
  `possessionTeamId`.

Verbatim code comment (`lib/state.py`, `_is_my_team`, read 2026-10-20) — included because it is a
**decisive operational gotcha**, not mere style:

> "teamCode is compared case-insensitively: the game server documents the teams as HOME/AWAY while
> the payload has been observed as home/away, and a case mismatch here is catastrophic rather than
> cosmetic — every player reads as an opponent... and the fallback degrades to SET_STANCE for the
> whole match."

Full sample game state (`lib/test_helpers.py::GAME_STATE`, abridged, new format):

```json
{
  "tick": 150, "gameTime": 120.5, "playMode": "OPEN_PLAY", "modeTeamId": null,
  "score": {"home": 1, "away": 0},
  "ball": {
    "position": {"x": 15.3, "y": -5.2, "z": 0}, "velocity": {"x": 0, "y": 0, "z": 0},
    "isFree": false, "possessionAgentId": "agentId_3", "rotation": {}, "angularVelocity": {}
  },
  "players": [
    {"agentId": "agentId_0", "teamCode": "home", "position": {"x": -50, "y": 0},
     "velocity": {"x": 0, "y": 0}, "orientation": 0, "stamina": 0.95,
     "currentAction": 0, "lastAction": "StayNearGoal", "speed": 0, "isSprinting": false}
  ],
  "teamChat": []
}
```

The workshop docs (`game-mechanics.md`) instead show an **old-format** example with `playerId`,
`teamId`, `hasBall` (a field not present anywhere in the new-format fixture or in `state.py`'s
field list) — a third, undocumented-elsewhere field. **UNKNOWN** whether `hasBall` is still live;
`state.py` never reads it, relying entirely on `possessionAgentId`/`possessionPlayerId` instead.

**Stamina scale — unresolved contradiction**: the fixture uses a **0.0–1.0 fractional** scale
(`"stamina": 0.95`), but `lib/state.py::summarize_state` reads it with `me.get("stamina", 100)` and
formats `f"stam={stam:.0f}"` (implying an assumed 0–100 integer default), and both workshop docs
(`football-rules/SKILL.md`, `game-mechanics.md`) describe stamina as **"1–100"**. No source
reconciles this. **Decisive gap**: an integration must probe the live payload before assuming
either scale — rendering `0.95` as `"stam=1"` (rounding a fraction as if it were already a
percentage) is a realistic silent bug sample code does not evidently guard against.

**`playMode`** values observed: `"OPEN_PLAY"` (fixture) vs `"PlayOn"` (workshop doc example) —
**different casing/vocabulary across sources**, exact play-mode enum is **UNKNOWN**; `state.py`
treats it as an opaque passthrough string/number, never branches on specific values.

**`modeTeamId`**: appears in the fixture (nullable), never explained in any doc or used in
`state.py` — purpose is **UNKNOWN** (likely "team awarded the current set-piece/restart", by
analogy to normal football engines, but unverified).

---

## 5. Pitch geometry and orientation (VERIFIED, consistent everywhere)

- `x ∈ [-55, 55]`, `y ∈ [-35, 35]` (110m × 70m, a standard full-size pitch).
- Team 0 (HOME) defends `x = -55`, attacks toward `x = +55`.
- Team 1 (AWAY) defends `x = +55`, attacks toward `x = -55`.
- `get_goal_positions(team_id)` in `lib/state.py` returns `(-55.0, 55.0)` for team 0, swapped for
  team 1 — matches every agent's system prompt text identically across GK/DEF/MID/FWD1/FWD2 and the
  balanced-team README. `lib/parsing.py` clamps `MOVE_TO` targets to exactly this box
  (`target_x` → `[-55, 55]`, `target_y` → `[-35, 35]`), so out-of-bounds values are client-side
  sanitized before being sent, not merely documented.
- No orientation/rotation convention for players beyond a raw `orientation` field (present in the
  fixture, unused by `state.py`) — semantics (radians? compass? degrees from +x?) are **UNKNOWN**.

---

## 6. Player/role ID mapping (VERIFIED)

| Player index | Position   | agentId pattern         | Sample default model |
|---|---|---|---|
| 0 | Goalkeeper | `agentId_0` | Nova Micro |
| 1 | Defender   | `agentId_1` | Nova Lite |
| 2 | Midfielder | `agentId_2` | Nova Pro |
| 3 | Forward 1  | `agentId_3` | Nova Micro |
| 4 | Forward 2  | `agentId_4` | Nova Lite |

This table is a **sample-repo convention** (`ai-team-strands-balanced/README.md`), not confirmed as
a server-mandated requirement — nothing in `state.py` enforces that index 0 must be the GK; the
mapping only holds because each sample agent's `main.py` hardcodes `MY_PLAYER_ID`. Whether the
server itself enforces "player 0 = GK, hands allowed" is **INFERRED** from the workshop rules doc
("Goalkeeper... only player who can use hands in the penalty area") combined with GK-specific
commands (`GK_DISTRIBUTE`) only making sense for one designated player — plausible but not
independently proven from server behavior.

`agentId` is **not globally unique** — the same `agentId_N` string is reused once per team
(`lib/state.py::_resolve_holder` docstring, verbatim): "agentId is not unique across teams — the
same `agentId_N` appears once per team — so an id match alone cannot always identify the player."
Any integration that resolves "who holds the ball" by agentId string alone **will misattribute
possession** without also checking team; the sample code's own fix (`holder_is_me()`) is the
correct pattern to copy.

---

## 7. Command / action schema

### 7.1 VERIFIED from sample source (`lib/parsing.py::VALID_COMMANDS`, read 2026-10-20)

```
MOVE_TO, PASS, SHOOT, SLIDE_TACKLE, PRESS_BALL, INTERCEPT, MARK,
FOLLOW_PLAYER, GK_DISTRIBUTE, SET_STANCE, CLEAR_OVERRIDE, RESET
```

Wire shape of each command (constructed in `lib/fallback.py::_cmd` and tagged in `lib/parsing.py`):

```json
{"commandType": "MOVE_TO", "playerId": 1, "teamId": 0,
 "parameters": {"target_x": 40.0, "target_y": 15.0, "sprint": true}, "duration": 0}
```

Parameters by command, taken from the system prompts embedded in every position's `src/main.py`
(identical text repeated in GK/DEF/MID/FWD1/FWD2, cited here from
`ai-team-strands-balanced/ai-gk/src/main.py`):

| Command | Class | Parameters |
|---|---|---|
| `MOVE_TO` | one-shot | `target_x` (float, clamped ±55), `target_y` (float, clamped ±35), `sprint` (bool) |
| `PASS` | one-shot | `target_player_id` (int), `type` ("GROUND"\|"AERIAL"\|"THROUGH") — requires possession |
| `SHOOT` | one-shot | `aim_location` ("TL"\|"TR"\|"BL"\|"BR"\|"CENTER"), `power` (0.0–1.0) — requires possession |
| `SLIDE_TACKLE` | one-shot | `target_player_id` (int, `-1` = "ball carrier" per parsing default), `sprint` (bool), `distance` (float) |
| `GK_DISTRIBUTE` | one-shot | `target_player_id` (int), `method` ("THROW"\|"KICK") — GK only, requires possession |
| `PRESS_BALL` | maintained | `intensity` (0.0–1.0) |
| `MARK` | maintained | `target_player_id` (int), `tightness` ("LOOSE"\|"TIGHT") |
| `INTERCEPT` | maintained | `aggressive` (bool) |
| `FOLLOW_PLAYER` | maintained | `target_player_id` (int), `target_team` ("HOME"\|"AWAY"), `distance` (float) |
| `SET_STANCE` | tactical | `stance` (0=Balanced, 1=Attack, 2=Defend) |
| `CLEAR_OVERRIDE` | tactical | `{}` |
| `RESET` | tactical | `{}` — "clear all overrides for team" |

`lib/parsing.py` post-processes commands: clamps `MOVE_TO` coordinates to field bounds, and fills a
default `target_player_id` for `PASS`/`MARK`/`FOLLOW_PLAYER`/`GK_DISTRIBUTE`/`SLIDE_TACKLE` if the
LLM omitted it — **this is client-side defensiveness, not evidence the server itself applies the
same defaults**; a non-Python integration must supply valid target IDs itself.

### 7.2 CONTRADICTION: workshop docs describe a different, incompatible vocabulary

`football-rules/SKILL.md` and `football-strategy/SKILL.md` (same repo, `.kiro/skills/`) describe
commands as `WALK`, `RUN`, `SPRINT`, `STOP`, `SHORT_PASS`, `LONG_PASS`, `THROUGH_PASS`, `CROSS`,
`SHOOT`, `DRIBBLE`, `TACKLE`, `SLIDE_TACKLE`, `INTERCEPT`, `MARK`, `DIVE`, `CATCH`, `PUNCH`,
`THROW`, `GOAL_KICK`, `IDLE`, `HEADER`, `CELEBRATE` — **almost entirely disjoint** from
`VALID_COMMANDS` above (only `MARK`, `INTERCEPT`, `SLIDE_TACKLE`, `SHOOT` overlap in name, and even
`SHOOT`'s parameters differ: rules doc says `direction, power(1-10)`; code says
`aim_location, power(0.0-1.0)`).

`content-reference/en/game-mechanics.md` (a different file, same `.kiro` tree) instead gives an
**8-command list that matches `VALID_COMMANDS` almost exactly** (`MOVE_TO`, `SHOOT`, `PASS`,
`PRESS_BALL`, `SET_STANCE`, `CLEAR_OVERRIDE`, `MARK`, `FOLLOW_PLAYER`) but is **missing**
`GK_DISTRIBUTE`, `SLIDE_TACKLE`, `INTERCEPT`, `RESET` entirely, and disagrees on stance numbering:
`game-mechanics.md`/`workshop-content.md` say `0=BALANCED, 1=DEFENSIVE, 2=ATTACKING`, while every
sample agent's own system prompt and the `ai-team-strands-balanced/README.md` say
`0=Balanced, 1=Attack, 2=Defend` — **1 and 2 are swapped between two primary sources in the same
repository.**

**Conclusion for integration purposes:** treat `lib/parsing.py::VALID_COMMANDS` and the per-agent
system prompts (`ai-gk/ai-def/ai-mid/ai-fwd1/ai-fwd2/src/main.py`) as the only executable,
test-covered ground truth. The three `.kiro` documents are workshop narrative at varying staleness
and must **not** be used to derive an adapter's command enum — this is the single most important,
previously-unverified fact this research changes relative to the original plan's "guessed adapter."
`SET_STANCE` numbering is a live ambiguity: **do not hardcode 1=Attack without a runtime check**,
since one of the two primary sources is wrong and there is no way from public material alone to
know which.

---

## 8. Possession, velocity, goalkeeper and stamina mechanics (VERIFIED from `lib/state.py` + `lib/fallback.py`)

- Possession is resolved via `ball.isFree` (authoritative bool when present) and
  `ball.possessionAgentId`/`possessionPlayerId` + `possessionTeamId`; a ball with a claim your code
  can't resolve is reported as `"unknown"`, never silently as `"free"` — deliberate, documented
  design choice to avoid telling a carrier to chase its own ball.
- `holder_is_me(ball, players, team_id, my_player_id)` is the **only safe way** to check "do I have
  the ball" — checking the possession index alone is true for two players at once (one per team).
- Ball carries `velocity {x,y,z}` and players carry `velocity {x,y}` plus `speed`/`isSprinting`
  flags in the fixture, but **`lib/state.py::summarize_state` never surfaces velocity to the
  LLM-facing text** — only position, stamina, and distance-to-ball are summarized. Velocity is
  present on the wire but unused by the reference implementation; an improved agent would need to
  read `game_state["ball"]["velocity"]` / `player["velocity"]` directly rather than relying on the
  provided summarizer.
- Goalkeeper mechanics are **command-level only** from the client side: `GK_DISTRIBUTE`
  (THROW/KICK) is the GK's sanctioned possession action; `INTERCEPT` for loose balls near goal;
  default stance is `2` (Defend) for GK specifically (`fallback.py::GK_CONFIG`). There is no
  visible "diving save" or "catch vs punch" command from the agent side — those (`DIVE`, `CATCH`,
  `PUNCH` in the workshop rules doc) appear to be **automatic server-side physics resolved from
  positioning**, not agent-issuable commands — consistent with `VALID_COMMANDS` containing no such
  entries. **INFERRED, not directly confirmed** (no server source).
- Stamina mechanics beyond "a number that depletes" are **UNKNOWN**: no formula for depletion rate,
  recovery, or the effect of low stamina on speed is present in any VERIFIED source. The `fallback.py`
  configs reference stamina only implicitly via "conserve stamina" prompt text, never read
  `me["stamina"]` to branch logic — i.e. **the sample's own fallback does not actually implement
  stamina-aware behavior** despite every agent's system prompt instructing the LLM to "conserve
  stamina." This is a real gap between documented intent and shipped behavior worth flagging if the
  redesign inherits this pattern uncritically.

---

## 9. Match end, webhook, report, replay (UNKNOWN for football specifically — found only by cross-game analogy)

No football-specific source (code or docs) describes a match-end webhook, final report schema, or
replay format. The **only** concrete evidence of a post-match artifact anywhere in the repository
is in the **sibling game** `agentic-cars-sample-agents/README.md` (same org, same AgentCore
pattern, almost certainly same competition platform infrastructure, but a **different game**, not
football):

> "After a match the platform's post-match report shows each car's command counts and a replay of
> the match. The game server also records an attribution log: every command it received, with the
> match clock, the team and the car that sent it."

This is **INFERRED-by-platform-analogy** evidence that the football game server likely has an
equivalent post-match report + attribution log + replay, since both games share the AgentCore
deployment scaffolding and almost certainly the same tournament platform team — but it is **not
confirmed for football**, and the exact schema (fields, format, delivery mechanism — pull via API
vs. push via webhook) is **UNKNOWN** in both games; the cars README only describes the report's
existence and general content, not its schema or transport.

**Decisive gap**: no webhook URL, callback registration mechanism, or report/replay API endpoint
was found anywhere in public material for either game. If the real integration needs to consume
match results programmatically, this must be clarified directly with the event organizers —
it cannot be reverse-engineered from what AWS has published.

---

## 10. Coaching channel vs. inter-agent communication (VERIFIED separation; no evidence of agent-to-agent comms)

- `teamChat` is a field present in the football game-state schema (`lib/test_helpers.py`, always
  empty in the fixture) but **never read** by `agentic-football-sample-agents/lib/state.py`.
- In the sibling cars game, the identical `teamChat` field **is** read
  (`agentic-cars-sample-agents/lib/state.py`): `chat = status.get("teamChat") or []`, then
  surfaced to the LLM as `"Coach shouts: ..."` — i.e., `teamChat` is the **coach → agent** channel
  (human, via Player/Viewer Portal "Coach Instructions" panel), not an agent ↔ agent channel.
- The workshop `football-rules/SKILL.md` independently confirms a coaching system with identical
  semantics for football: "During live matches, coaches can send natural language instructions to
  agents via the Viewer Portal," rate-limited to "Max 20 instructions per minute per team," "All
  instructions are public (visible to all viewers)," and scoped to "current match session only —
  no carryover" between matches.
- `football-rules/SKILL.md` additionally states explicitly: "Your agent cannot see: opponent agent
  code/strategy, future predictions, hidden stats, or other agents' decision-making" — a direct,
  if narrative-sourced, statement that **no backchannel into other agents' reasoning exists**.
- **No evidence anywhere (code or docs) of an agent-to-agent (teammate-to-teammate) messaging API.**
  Team coordination in the sample is achieved purely by each of the 5 independent runtimes reading
  the same shared `game_state["players"]` array (positions/stamina/actions of teammates), never by
  one agent calling another. **INFERRED conclusion**: inter-agent communication is not a supported
  protocol feature — not because it's explicitly forbidden in writing, but because no affordance
  for it exists in the verified game-state or command schema, and the one chat-shaped field that
  does exist (`teamChat`) is demonstrably one-way (coach → team), observed read-only, and public.
  Do not build an adapter that assumes agents can privately signal each other through the game
  state.

---

## 11. Deployment boundary / permitted persistent storage (VERIFIED)

- Deployment target is **the participant's own AWS account**, region `us-east-1` or `us-west-2`
  (`workshop-flow.md` Phase 1/5), via `agentcore deploy` (CDK-based) or legacy `deploy-all.sh`
  scripts. Each of the 5 position agents gets **its own Bedrock AgentCore Runtime ARN**
  (`arn:aws:bedrock-agentcore:<region>:<account-id>:runtime/<agent-id>`), which is what gets
  registered with the central Player Portal ("Agents → enter name and ARN → Register Agent",
  repeated once per player — 5 separate registrations per team).
- The match server and Player Portal are **centrally operated by AWS/the event organizers**
  ("Match Server + Agent Loop (managed centrally)" — `workshop-flow.md` Phase 5); only the 5 agent
  runtimes live in the participant's account. This is the deployment boundary: **participants own
  and pay for compute/storage up to and including the AgentCore Runtime; everything about the match
  itself (physics, scoring, viewer feed, any report/replay) is outside participant infrastructure
  and outside what's publicly documented.**
- Sanctioned persistent storage mechanisms demonstrated in public sample code (all **participant-
  owned, created via their own `agentcore deploy`**, not platform-provided):
  - **Amazon Bedrock AgentCore Memory** (`ai-team-strands-memory/`): a `team_memory` resource
    declared in `agentcore/agentcore.json`, injected into every agent runtime as
    `MEMORY_TEAM_MEMORY_ID`, wired via `AgentCoreMemorySessionManager` for cross-tick/cross-match
    short-term recall, shared across the 5 position runtimes.
  - **Amazon Bedrock AgentCore Gateway + Lambda** (`ai-team-strands-gateway/`): 4 Lambda-backed MCP
    tools (`calculate_pass_options`, `find_open_space`, `evaluate_shot`,
    `get_defensive_assignment`) exposed via a `tactical-tools` gateway, `AGENTCORE_GATEWAY_*_URL`
    injected per runtime, auth mode **NONE** (no token required) per the gateway README — note this
    as a real security-boundary detail: the gateway is reachable without auth to anything holding
    the URL.
  - Workshop enhancement list (`workshop-flow.md` Phase 7) additionally names
    `FileSessionManager`/`S3SessionManager` and Bedrock Knowledge Bases (RAG) as supported Strands
    SDK session/storage patterns, but **no sample code exercises these for football** — they are
    only mentioned as available options, not demonstrated or schema-defined.
- **No explicit prohibition on external storage was found** in any source. Absence of a
  prohibition is not the same as a guarantee of permission for arbitrary third-party storage (e.g.
  storing game data outside AWS) — this is **UNKNOWN** and should be confirmed with organizers
  before relying on it, especially given the task's "no AWS credentials / no infra mutation" research
  constraint prevented any first-party verification of account/billing/permission boundaries.

---

## 12. Practical integration recommendation

1. **Build the adapter against `lib/parsing.py::VALID_COMMANDS` and the per-position system
   prompts**, not against the `.kiro` workshop docs. Treat the workshop docs only as a secondary
   cross-check, and only where they agree with code (field layout, match duration, player index
   convention) — flag and re-verify anywhere they disagree (command vocabulary §7.2, stance
   numbering §7.2, stamina scale §4).
2. **Do not hardcode `SET_STANCE` stance-number semantics.** Two primary sources disagree on
   whether `1` means Attack or Defensive. If the redesign's agent needs `SET_STANCE`, probe
   behavior empirically against a live/dev match before shipping a fixed mapping, or avoid the
   command until confirmed.
3. **Normalize both old and new state formats defensively**, copying the pattern already proven in
   `lib/state.py` (`_player_idx`, `_is_my_team`, `_resolve_holder`, `holder_is_me`) rather than
   re-deriving it — this is the single most battle-tested piece of the public material and encodes
   several non-obvious failure modes (case-sensitivity, cross-team ID collision, claimed-vs-free
   ambiguity) that a naive reimplementation would likely reproduce as bugs.
4. **Treat stamina scale as unresolved** — read the live payload once in a dev/test match and branch
   on whether values exceed 1.0 before deciding a 0–1 vs 0–100 interpretation; do not assume either
   the fixture (0–1) or the docs (1–100).
5. **Do not design any inter-agent communication feature.** Build each position's agent to reason
   only from the shared `game_state["players"]` array and (optionally) the one-way, rate-limited,
   publicly-visible `teamChat`/coach-instruction channel. There is no verified or even
   plausibly-inferred private backchannel between teammate agents.
6. **Do not assume a webhook/report/replay contract.** No football-specific schema exists publicly.
   If match-result ingestion is required, this is a hard blocker requiring direct clarification from
   the event organizers — flag it as an open integration risk rather than guessing a shape from the
   cars game's README.
7. **Scope persistent storage to AgentCore Memory (and optionally Gateway/Lambda for stateless
   tool calls)** inside the participant's own AWS account, matching the only two patterns the
   public samples actually exercise and test. Avoid designing around `FileSessionManager`/
   `S3SessionManager`/Knowledge Bases unless/until a concrete need arises, since they're listed only
   as "available," never demonstrated with a working config for this game.
8. **Re-run this research against `agenticfootballcup.ai` with a JS-rendering fetch and against the
   two 404'd starter-kit repos (`fifa-agentic-football-strands`/`-langchain`) periodically** — both
   are plausible sources of a more current, authoritative protocol spec than what was reachable here,
   and their absence is the largest single gap in this pass.

---

## 13. Summary of decisive gaps (explicit list)

- Real match server source: **not public anywhere found.**
- `agenticfootballcup.ai` content: **inaccessible (SPA, static fetch only returns shell).**
- Strands "Inside Agentic Football Cup" blog post: **URL not located; treat as inaccessible.**
- `fifa-agentic-football-strands` / `fifa-agentic-football-langchain` (the repos the workshop docs
  say are the actual starter kits used at the live event): **both return HTTP 404 as of 2026-10-20.**
- Match-end webhook / report / replay schema for football: **no public source; only analogy from a
  different game (cars) in the same repo, which itself lacks a published schema.**
- `SET_STANCE` stance-number semantics: **two primary sources disagree (1=Attack vs 1=Defensive).**
- Stamina wire scale (0–1 vs 1–100): **fixture, code default, and docs disagree.**
- Deadline-miss behavior ("IDLE on timeout"): **asserted in one workshop doc, absent from the
  other workshop doc and from all sample code.**
- `hasBall`, `orientation`, `modeTeamId`, `currentAction`/`lastAction` (numeric/string action-log
  fields in the fixture) semantics: **present on the wire, never explained in any source.**

---

## 14. Follow-up: exact possession tie-break, PRESS_BALL intensity bounds, SET_STANCE=0 (VERIFIED, narrow re-check)

Re-read verbatim from `lib/state.py`, `lib/parsing.py`, `lib/fallback.py` at the same pinned commit
(`465fc5fc1608e59409b53e562986dafbdf2ec3cf`), line-cited below.

### 14.1 Possession resolution when `possessionAgentId` is reused across teams and `possessionTeamId` is absent

`_resolve_holder` (`lib/state.py:48-78`):

```python
holder_agent = ball.get("possessionAgentId")
if isinstance(holder_agent, str):
    matches = [p for p in players if p.get("agentId") == holder_agent]
else:
    pid = ball.get("possessionPlayerId")
    if pid is None:
        return None
    matches = [p for p in players if p.get("playerId") == pid]
    team = ball.get("possessionTeamId")
    if team is not None:
        matches = [p for p in matches if p.get("teamId") == team]

return matches[0] if matches else None
```

- **New format path** (`possessionAgentId` is a string): the match is **string equality on
  `agentId` only**. There is **no team discriminator available in the new format at all** —
  `ball` carries no `possessionTeamCode`/`possessionTeamId` alongside `possessionAgentId`. Because
  `agentId_N` is reused once per team (confirmed `lib/state.py:50-51` docstring), this lookup
  **always returns up to two matches** when a given index's ball ownership is ambiguous, one from
  each team.
- **The only disambiguator applied is list order**: `matches[0]` — i.e. whichever of the two
  same-`agentId` player dicts appears **first in the `players` array as the server ordered it**.
  No geometry (ball/player distance), velocity, or any other field is consulted. The docstring
  explicitly names and **rejects** a distance-based tie-break ("a positional tie-break on distance
  to the ball looks appealing but picks the wrong team whenever possession has just changed and the
  ball has not yet caught up") — so this is a **deliberate design choice**, not an oversight, but it
  means **the resolved holder can be the wrong team's player** whenever the true holder does not
  happen to sort first in `players[]` for that tick. The code's own `TODO` (`lib/state.py:63-64`)
  states the ambiguity is only fully fixable "once the server exposes a globally unique player id,
  or sends a team discriminator alongside possession" — i.e. **the public sample code itself
  documents this as an unresolved server-side gap**, not something a client can close.
- **Old format path** (`possessionPlayerId` present, `possessionAgentId` absent/not a string): same
  structural risk, but *if* `possessionTeamId` is present it filters `matches` down to one team
  first (`lib/state.py:74-76`), removing the ambiguity. **If `possessionTeamId` is also absent**
  (the exact case asked about), the old-format path degenerates to the **identical problem**: a
  plain `playerId` match across both teams, again resolved only by `matches[0]` list order — no
  additional field or geometry kicks in.
- **Downstream mitigation that does exist**: `holder_is_me()` (`lib/state.py:127-138`) re-checks
  `_is_my_team(holder, team_id)` against whatever `_resolve_holder` already picked, which prevents a
  *wrongly-team-resolved* holder from being treated as "I have the ball" by the opposing player —
  but it **cannot correct** a wrong resolution; it only prevents the wrong team's player from acting
  on a mis-attributed holder. If `_resolve_holder` already locked onto the wrong team's player, a
  caller using `get_possession_info()`'s reported side (`"MY"`/`"OPP"`, `lib/state.py:116-123`) can
  still receive an incorrect side label with no way to detect the error from the client alone.

**Answer in one line**: no additional field or geometric signal is used for the tie-break in either
format when the team discriminator is absent — resolution falls back purely to **array order** in
the `players` list, which the source code itself flags as a known, server-side-unresolved ambiguity.

### 14.2 PRESS_BALL intensity — allowed range and whether zero is permitted

- Documented range (every position's system prompt, e.g.
  `ai-team-strands-balanced/ai-gk/src/main.py`, and `ai-team-strands-balanced/README.md`):
  `intensity (0.0-1.0)`. This is **prompt text only** — a request/suggestion to the LLM, not a
  schema constraint.
- `lib/parsing.py::_tag_commands` (`lib/parsing.py:54-77`) **clamps only `MOVE_TO`'s
  `target_x`/`target_y`** (`lib/parsing.py:64-70`, via `_clamp()` at `lib/parsing.py:50-51`). **No
  clamping, validation, or range check exists anywhere in the client code for `PRESS_BALL`'s
  `intensity`** (or for any other numeric parameter — `power`, `distance`, etc.). Whatever value the
  LLM emits is passed through unmodified.
- All fallback configs (`lib/fallback.py:39-126`) use non-zero `press_intensity` values
  (`0.7`, `0.6`, `0.8`), and the one `PRESS_BALL` last-resort command uses `intensity: 0.5`
  (`lib/fallback.py:102`) — **the sample code never emits or tests `intensity: 0.0`.**

**Answer**: `0.0` sits inside the documented inclusive range `0.0–1.0` and nothing in the client
code rejects it, but — unlike `MOVE_TO` coordinates — **no client-side clamp or validation governs
`intensity` at all**, so this is a prompt convention, not an enforced schema boundary, and whether a
real match server accepts/rejects `0.0` (vs. silently no-opping it, vs. erroring) is **UNKNOWN** —
no public source exercises or confirms that boundary value.

### 14.3 Is `SET_STANCE` `stance=0` unambiguously "Balanced"?

**Yes — `0 = Balanced` is the one point of full agreement across every source checked**, including
the one workshop document (§7.2) that disagrees with the sample code on what `1` and `2` mean:

- Every position's system prompt (identical text in `ai-gk/ai-def/ai-mid/ai-fwd1/ai-fwd2/src/main.py`):
  `"SET_STANCE: stance (0=Balanced, 1=Attack, 2=Defend)"`.
- `ai-team-strands-balanced/README.md` command table: `"SET_STANCE — stance (0=Balanced, 1=Attack, 2=Defend)"`.
- `content-reference/en/game-mechanics.md` and `.kiro/steering/workshop-content.md` (the sources
  that invert 1/2 relative to code): both still write `"0=BALANCED"` as the first value.
- `lib/fallback.py::FallbackConfig.default_stance` defaults to `0` at the dataclass level
  (`lib/fallback.py:67`), and is used as the **generic "player not found" safe default**
  (`lib/fallback.py:163`) and as the **final catch-all** in `_on_ball`'s unreachable branch
  (`lib/fallback.py:254`, comment: `"Shouldn't reach here"`) — i.e. the code's own internal
  "nothing else applies, pick something safe" logic independently treats `0` as the neutral/no-risk
  choice, consistent with "Balanced" and inconsistent with either "Attack" or "Defend" being the
  default fallback semantics.
- Per-position overrides only ever set `default_stance` to `1` or `2` for positions with an
  inherent bias (GK/DEF → `2`, FWD1/FWD2 → `1`, `lib/fallback.py:83,92,113,125`), never to a
  different value for "no bias" (MID stays at the dataclass default `0`, `lib/fallback.py:101`) —
  further corroborating `0` as the codebase's consistent "no particular tactical lean" value.

**Conclusion**: `stance=0` → Balanced is **confirmed unambiguous** across all public sources,
including the ones that disagree on `1`/`2`. Only the **assignment of `1` vs `2` to Attack vs.
Defend** remains contradicted between the sample code/READMEs (`1=Attack, 2=Defend`) and the two
workshop `.kiro` documents (`1=DEFENSIVE, 2=ATTACKING`) — unchanged from §7.2, not resolved by this
follow-up. An adapter can safely hardcode `stance=0` for "balanced/neutral" without a runtime check;
it should **not** hardcode `1`/`2` without one.
