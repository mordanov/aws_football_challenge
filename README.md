# AWS Football Challenge — Football Agent

A deterministic five-player football agent deployed on AWS Bedrock AgentCore with an S3-backed self-learning loop.

## Repository layout

```
football_agent/   — the agent (runtime code, deploy scripts, tests)
local_simulator/  — offline simulator for local match replay and tactic analysis
scripts/          — analysis utilities
docs/             — design documents
```

## How the agents work

Five independent agents cover the field, each running as a separate **Bedrock AgentCore Runtime**:

| Runtime | Player ID | Role |
|---------|-----------|------|
| `football_agent_gk` | 0 | Goalkeeper |
| `football_agent_def` | 1 | Defender |
| `football_agent_mid` | 2 | Midfielder |
| `football_agent_fwd1` | 3 | Forward 1 |
| `football_agent_fwd2` | 4 | Forward 2 |

Each agent is **stateless per tick**: it receives the full game state (positions, ball, score, clock) and returns a command (move, pass, shoot, press, …) in milliseconds. All coordination happens through the shared game physics — no inter-agent communication.

### Decision pipeline

```
Game state (JSON)
  └─> decode()          — validate and normalise coordinates
  └─> profile registry  — load per-opponent tactical settings from S3
  └─> tactics.decide()  — deterministic decision tree (support, carry, press)
  └─> command (JSON)    — returned to AgentCore
```

Tactical parameters (pass margin, shot range, press radius, support width, formation) are stored in **JSON profiles** in S3 and can be updated without redeploying the runtime.

### In-match advisor (optional)

An async LLM component (`agent/advisor/`) observes the match and updates the `coach_policy` flag (`attack` / `defend`) that the deterministic agent reads each tick. It fires once per match segment and adds no latency to the decision path.

---

## Self-learning loop

After each match the agent writes a **decision log** (one JSON record per tick) to S3. A scheduled Lambda processes these logs and updates the tactical profile for each opponent:

```
AgentCore runtime
  │  writes decision records to S3 (observations/)
  ▼
Worker Lambda (collector)
  │  triggered by CloudWatch Logs subscription filter
  │  aggregates records per match
  ▼
Learner Lambda  (runs every 15 min via EventBridge)
  │  reads observations, scores each tactic variant
  │  writes updated profile back to S3 (registry/)
  │  stores long-term insights in Bedrock AgentCore Memory
  ▼
Profile registry (S3)  ←  runtime reads at each game
```

---

## Deploy from scratch

**Prerequisites**: AWS CLI configured for account `559922262840`, `uv` installed (`pip install uv`).

```bash
cd football_agent
./deploy/cloudformation/deploy_cfn.sh <team_id> [app_name]
```

This runs four steps in order:

1. **storage stack** — creates two S3 buckets (artifacts + failure archive)
2. **package + upload** — builds the agent ZIP and uploads it to S3
3. **agentcore stack** — deploys 5 runtimes, IAM roles, log groups
4. **learning stack** — deploys Worker/Learner Lambdas, EventBridge schedule, CloudWatch alarms, and the AgentCore Memory (first create may take up to 45 min)

### CloudFormation stacks

| Stack | File | Purpose |
|-------|------|---------|
| `<AppName>-storage` | `storage.yaml` | S3 buckets |
| `<AppName>-agentcore` | `agentcore.yaml` | 5 runtimes + IAM |
| `<AppName>-learning` | `learning.yaml` | Lambdas + Memory |

After deploy, the Memory ID is printed and available as a stack output:

```bash
aws cloudformation describe-stacks --stack-name <AppName>-learning \
  --query 'Stacks[0].Outputs[?OutputKey==`MemoryId`].OutputValue' --output text
```

### CI/CD

`main` branch pushes trigger the full test suite and then deploy via GitHub Actions (`.github/workflows/tests.yml`). Secrets required: `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN`, `AWS_REGION`.

---

## Local development

```bash
cd football_agent
pip install -e ".[offline,dev,learning]"
pytest tests/
```

Run the local simulator (two football_agent instances vs each other):

```bash
cd local_simulator
pip install -e .
# see local_simulator/README.md for match and campaign commands
```
