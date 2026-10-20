# 04 — Questions, Concerns & Architectural Assumptions

Per your instructions (*"You should ask me if you have any questions and concerns"*), below are the key architectural questions and concerns we identified while designing and implementing `redesign_3/`, along with how we engineered `redesign_3/` so that **both options work out-of-the-box without code rewrites**.

---

## 1. Deployment Topology: 1 Shared Endpoint (`myPlayers: [0..4]`) vs. 5 Per-Role Endpoints (`myPlayers: [k]`)

- **Context:**
  - In `initial_idea/docs/01-task.md`, the match server was described as sending `myPlayers: [0, 1, 2, 3, 4]` to a single endpoint, or `myPlayers: [k]` to 5 separate AgentCore runtimes (one per player role, as in `aws-samples/sample-ai-possibilities/agentic-football-sample-agents`).
- **Question for You:**
  - In your target tournament registration portal, do you register **5 separate AgentCore ARNs** (one per player `ai-gk`, `ai-def`, `ai-mid`, `ai-fwd1`, `ai-fwd2`), or **1 team ARN**?
- **How `redesign_3` Handles Both Right Now:**
  - `redesign_3/agent/main.py` dynamically inspects `myPlayers` on every tick (and also supports `PLAYER_ID` / `ROLE_NAME` env vars).
  - If `myPlayers` has 1 player (`[2]`), it returns the 1-element command list `[cmd_for_2]`.
  - If `myPlayers` has all 5 players (`[0, 1, 2, 3, 4]`), it returns all 5 coordinated commands `[cmd_0, cmd_1, cmd_2, cmd_3, cmd_4]` in one pass!

---

## 2. AgentCore Return Format: JSON String vs. Python List/Dict

- **Context:**
  - In `aws-samples/sample-ai-possibilities/agentic-football-sample-agents/lib/agent_base.py`, the `BedrockAgentCoreApp` entrypoint uses `yield json.dumps(commands)` (returning a JSON-encoded string of the command list), whereas `initial_idea` returned a Python `list[dict]`.
- **Question for You:**
  - Have you run a smoke test against the live tournament server yet to confirm whether your AgentCore wrapper expects `handle(payload)` to return a `list[dict]` (serialized by `BedrockAgentCoreApp`) or a `json.dumps(list)` string?
- **How `redesign_3` Handles Both Right Now:**
  - `handle(payload, context)` returns `list[dict]` by default (for full compatibility with unit tests and standard JSON handlers), and can be switched to JSON string output via `AFC_RETURN_JSON_STRING=1` or via the included `invoke_agentcore(payload, context)` wrapper.

---

## 3. Cross-Match Storage in Bedrock AgentCore

- **Context:**
  - Within a single 5-minute match, AgentCore keeps the container warm so in-memory state (`FootballTrainer`) and `/tmp/afc_trainer_memory.json` persist across all ~150–300 invocations.
  - Between separate matches, if AgentCore spins down the container before the next match starts, `/tmp` may reset unless persisted to AgentCore Memory (`MEMORY_ID`), S3, DynamoDB, or committed into `agent/tuning.json` via our `coach_review.py` script.
- **Question for You:**
  - Do we have permissions in the tournament AWS account to use **AgentCore Memory (`MEMORY_ID`)**, **S3**, or **DynamoDB** for automatic cross-container persistence, or will we run `python3 -m agent.coach_review` on CloudWatch logs between matches to update `tuning.json`?
- **How `redesign_3` Handles Both Right Now:**
  - We implemented **three persistence layers** in `agent/trainer.py`:
    1. In-memory + `/tmp/afc_trainer_memory.json` (zero-latency, works automatically across all ticks and warm matches).
    2. Optional S3 / AgentCore Memory hook (activated automatically if `AFC_S3_BUCKET` or `MEMORY_ID` is set in environment variables, using a non-blocking background thread so it never adds latency to the 500ms tick budget).
    3. Offline `agent/coach_review.py` CLI that ingests CloudWatch `DECISION`/`TRAINER` logs and writes `agent/tuning.json`.

---

## 4. Legacy vs. Official Wire Parameter Compatibility (`MOVE_TO` `target_x` vs `x`)

- **Context:**
  - As documented in `01-resolved-blockers-and-protocol.md`, the official AWS sample agents (`lib/parsing.py`, `lib/fallback.py`, `BUILD_AND_DEPLOY.md`) use `target_x` and `target_y` for `MOVE_TO`, whereas `initial_idea` guessed `x` and `y`.
- **How `redesign_3` Protects You Against Both:**
  - Every `MOVE_TO` command emitted by `redesign_3/agent/commands.py` includes `"target_x": float, "target_y": float, "sprint": bool` (the official AWS schema) **AND** also includes `"x": float, "y": float` as harmless extra keys (which `lib/parsing.py` preserves in `params` while reading `target_x`/`target_y`). That way, whether the server reads `target_x`/`target_y` or `x`/`y`, **it works 100% of the time**!
