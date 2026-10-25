# AWS Services: Usage in This Project

Quick reference for each AWS / AgentCore service — whether it is used and why.

---

## Used

### Amazon Bedrock
**Used.** Core ML platform. `bedrock-runtime` Converse API is called in `agent/live_coach.py`
to invoke Nova Pro (`us.amazon.nova-pro-v1:0`) every 25 s during a match. The coach reads
CloudWatch telemetry, generates tactical parameter adjustments (`rush_s`, `press_radius`,
etc.) and writes them to S3. All other agent logic is deterministic and does not call Bedrock.

### AgentCore Runtime
**Used.** All five players (r4_gk, r4_def, r4_mid, r4_fwd1, r4_fwd2) are deployed as
AgentCore runtimes under the `Redesign4 / default` project. `BedrockAgentCoreApp` in
`agent/main.py` exposes the `invoke` entrypoint; `deploy/deploy.py` packages and pushes
the runtime via the `agentcore` CLI.

### AgentCore Memory
**Used.** A Bedrock AgentCore Memory resource (`arn:aws:bedrock-agentcore:…:memory/…`)
is the durable observation store for the self-learning pipeline. Player runtimes write
match observations there via `S3ObservationOutbox` (`agent/s3_worker.py`); the learner
reads them back through `S3MemoryObservationSource` to evaluate and potentially promote
the team profile. The resource ID is supplied as `AFC_MEMORY_ID` in the runtime
environment and as `memory_id` in `deploy/s3_learning.example.json`.

### AgentCore Observability (CloudWatch)
**Used.** Each runtime emits structured JSON decision logs to its CloudWatch log group
(one group per position, hardcoded in `agent/live_coach.py`). These logs are the primary
data source for the live coach loop (reads clock, score, possession, rush events) and feed
the CloudWatch subscription that triggers the S3 learning collector.

---

## Not Used

### AgentCore Browser
**Not used.** The agent has no need to visit web pages. All runtime inputs arrive as
JSON payloads from the competition platform; all telemetry and profile data live in
CloudWatch and S3.

### AgentCore Gateway
**Not used.** No external tools or APIs are connected via MCP. Every AWS service call
(Bedrock, S3, CloudWatch, DynamoDB) is made directly through boto3 with explicit
IAM role permissions.

### Bedrock Guardrails
**Not used.** The LLM (Nova Pro) is called in a fully automated, non-interactive pipeline:
input is structured match telemetry, output is a small JSON object with numeric parameters.
There is no user-facing text, no free-form input to filter, and no PII. Output values are
additionally clamped to safe ranges in `call_coach()` regardless of what the model returns.

### AgentCore Evaluations
**Not used.** The project ships its own evaluation pipeline: CloudWatch → S3 observations
→ `agent/learning_evaluation.py` → profile promotion via the S3 registry. AgentCore
Evaluations would replace this pipeline, not extend it. Migration could be considered
if managed A/B profiling or automatic rollback become necessary.

### AgentCore Code Interpreter
**Not used.** All agent logic is deterministic Python executed at deploy time. There is
no need to run dynamically generated or sandboxed code at inference time.

### AgentCore Identity
**Not used.** Access control is handled entirely by IAM roles attached to the runtime
and Lambda execution contexts. There are no multi-tenant users, OAuth flows, or
fine-grained per-request identity requirements.
