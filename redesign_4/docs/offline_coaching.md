# Offline coaching — operator guide

Offline coaching uses three AWS AgentCore services to produce a **proposal**
for a strategy profile update. A proposal is never approved or deployed
automatically. The operator reviews, decides, and deploys manually.

See [design spec](superpowers/specs/2026-10-23-offline-agentcore-coaching-design.md)
and [implementation plan](superpowers/plans/2026-10-23-offline-agentcore-coaching.md)
for full rationale.

---

## Glossary

| Term | Meaning |
|------|---------|
| **Evidence packet** | Signed, expiry-stamped bundle of scouting claims, match identity and source digests; the unit of evidence the advisor reasons from. Never contains raw video or PII. |
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

---

## Setup

```sh
# 1. Copy and fill in your account, region, team_id and resource names
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

# Check config and declared resource IDs (no paid calls)
python -m agent.advisor.offline doctor --config offline_coach.json
```

---

## Explicit AWS setup and run

```sh
# Full workflow: retrieve → advise → evaluate → artifact
python -m agent.advisor.offline run \
  --config offline_coach.json \
  --evidence local/evidence.json \
  --profile profiles/recommended.json \
  --output local/run.json

# Exit codes: 0=proposal, 2=failure, 3=rejected
```

---

## Generated-script review and sandboxed execution

```sh
# Generate analysis script (one model call, no execution)
python -m agent.advisor.offline generate-analysis \
  --config offline_coach.json \
  --evidence local/evidence.json \
  --instruction "Identify top opponent pressing triggers" \
  --output local/analysis_script.json

# REVIEW the script yourself before executing it.

# Execute in sandbox (requires explicit consent flag)
python -m agent.advisor.offline analyze \
  --config offline_coach.json \
  --evidence local/evidence.json \
  --script local/analysis_script.json \
  --execute-generated-code \
  --output local/analysis_result.json
```

The `--execute-generated-code` flag is required and documents explicit
operator consent. The script runs in a SANDBOX CodeInterpreter with no
execution role and no filesystem mounts.

---

## Scoped publication

```sh
# Publish an approved scouting note to shared Memory
python -m agent.advisor.offline publish \
  --config offline_coach.json \
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
  --config offline_coach.json \
  --run local/run.json \
  --output local/run_rescored.json
```

Use this as a recovery path if the evaluator was unavailable during `run`.
A low score produces a `"rejected"` artifact (exit 3), never a failure.

---

## Costs

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
| Memory denied or empty | `retrieve` raises `OfflineServiceError(stage="retrieve")`; run artifact `status="failed"` |
| Model malformed response | Advisor raises `OfflineServiceError(stage="advise")`; `status="failed"` |
| Model throttled | Same as above; operator retries |
| Cache miss (first call) | Normal; cache write proceeds |
| Cache mode disabled | `OfflineConfig.cache_mode="disabled"` skips cache; no failure |
| Interpreter timeout | `execute` raises `OfflineServiceError(stage="analyze")`; `status="failed"` |
| Interpreter cleanup failure | Logged; does not affect artifact |
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

---

## Relationship to live players

The offline coaching pipeline is **entirely separate** from the five deployed
players. It shares the same `agent/model.py` profile schema, but:

- It never calls `agent.runtime`, `agent.__main__` or `agent.trainer`.
- It never imports `langchain` or any GenAI instrumentation from the live path.
- No evaluation score approves or deploys a profile.
- The CloudFormation stack and player IAM roles are untouched.
