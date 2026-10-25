# Deploy the football agents: Claude Code runbook

Use this runbook when the user explicitly requests an AWS deployment.
Reading or updating this file does not authorize a deployment.

Deploy all five existing players from **`final_agent/` only**. Preserve
`redesign_4/`, runtime ARNs, player-seat mapping and durable match pins.
Report to the user in Russian; write deployment receipts in English.

## 1. Establish the source and deployment scope

Read repository instructions, `final_agent/README.md` and
[the learning deployment guide](automatic_learning.md). Inspect:

```sh
git status --short
git branch --show-current
git log -5 --oneline
```

Use the intended reviewed revision. Record its commit and any intentional local
changes. Preserve unrelated work and do not switch another session's worktree.
Do not assume the current directory is the deployment worktree.

Locate these files in the selected checkout:

- `final_agent/deploy/deploy.py`
- `final_agent/deploy/agentcore/agentcore.json`
- `final_agent/deploy/agentcore/aws-targets.json` (ignored deployment state)
- `final_agent/deploy/agentcore/cdk/cdk.json`
- `final_agent/agent/profile_registry.py`
- `final_agent/profiles/recommended.json`

The existing project is `Redesign4`, target `default`, with runtimes
`r4_gk`, `r4_def`, `r4_mid`, `r4_fwd1`, `r4_fwd2` for seats 0 through 4.
Keep these identities even though their names contain "Redesign4".

If ignored deployment state is missing, locate a verified previous deployment
workspace and copy only the necessary target/CLI state. Inspect its account,
Region, project and runtime mapping before use. Do not initialize a replacement
project or copy old generated code over the selected source.

**Gate:** source revision, five target runtime identities and deployment scope
are unambiguous. Otherwise stop before AWS writes.

## 2. Load credentials without exposing them

Credentials are in `aws_creds.txt` at the repository root. Inspect the format
without printing values. For a trusted file containing ordinary export
assignments, run in Bash:

```sh
set +x
unset AWS_MAX_ATTEMPTS
source ./aws_creds.txt
unset AWS_MAX_ATTEMPTS
export AWS_REGION=us-east-1
export AWS_DEFAULT_REGION=us-east-1
export AWS_PAGER=""
aws sts get-caller-identity --query Account --output text
```

If the file uses another format, parse only the expected credential fields;
do not execute arbitrary shell content. Never include secrets in logs, reports,
commits or command output.

The expected account is **559922262840**, Region **us-east-1**. Stop on an
identity mismatch or expired credentials. Request refreshed credentials rather
than searching other credential stores.

Independent shell calls do not retain environment variables. Reload credentials
before subsequent AWS operations, or execute a bounded script in the same
environment. Resolve the credential path before changing directories.

## 3. Verify tooling and local checks

Discover an existing Python 3.11+ environment with the project's extras.
Set `PYTHON` to its absolute executable path without resolving a venv symlink
to the base interpreter. Set `PROJECT` to the absolute `final_agent` directory.
These variables must be re-established in fresh shells.

Required tools: Node >=20, `agentcore` (`@aws/agentcore` CLI), `aws`, `uv`.
Use existing installations; inspect `--help` before relying on unfamiliar CLI
options. If a required dependency is missing, install through the existing
package manager and manifests rather than inventing deployment tooling.

From `PROJECT`, run:

```sh
cd "$PROJECT"
PYTHONPATH=. "$PYTHON" -m pytest -q tests --tb=short
"$PYTHON" -m ruff check agent tests deploy/learning.py deploy/s3_learning.py
"$PYTHON" -m mypy
(cd deploy/agentcore/cdk && npm test)
git diff --check
```

Use current CI evidence for the exact revision when equivalent checks have
already passed and no relevant source changes occurred.

If Python extras are missing, the manifest defines
`.[deployment,offline,learning,dev]`. If CDK dependencies are missing, use
`npm ci` in its directory. Do not reinstall dependencies unnecessarily.

**Gate:** required checks pass and deployment tools are available.

## 4. Snapshot live state and choose the rollout path

Read the current five runtimes and DEFAULT endpoints using boto3
`bedrock-agentcore-control`. The installed AWS CLI may not expose this service;
that is not a reason to change runtime configuration manually.

Record, in an ignored local directory:

- Runtime IDs/ARNs, versions, DEFAULT live versions and statuses.
- Artifact S3 locations, environment variables and lifecycle configuration.
- Current learning stack/template/configuration and log subscriptions.
- Registry ACTIVE document, selected profile and work-latch state.

Limit stored environment data to deployment-relevant non-secret fields.
Signed artifact-download URLs are temporary credentials; do not log or commit them.

Choose one path:

| Change | Required handling |
|---|---|
| Same tactical semantics and compatible profile/evaluator | Preserve current generation, namespace and ACTIVE; verify that compatibility is genuinely unchanged |
| Tactics, effective tactical flags or evaluator interpretation change | New compatibility generation and fresh team/registry namespace; coordinated player and worker rollout |
| Unclear compatibility or mixed live/source state | Stop before deployment and resolve the discrepancy |

For a semantic change, prepare and validate the generation/binding changes
before deployment. Keep serving and evaluator defaults identical. Never relabel
old observations or edit immutable profile/pin objects to make them compatible.

The checked-in `afcLearning` binding may already be enabled. Read it rather than
assuming a player deployment is independent of learning.

Historical reference only: the 2026-10-24 rollout deployed v11 using
`match_profiles_tactics_v3`, team `aleksalotl_tactics_v3`, stack
`final-agent-s3-tactics-v3`. Discover current state; never assume these are still
the correct identities for a new semantic rollout.

## 5. Prepare the learning stack when compatibility changes

Use the S3 backend described in [automatic_learning.md](automatic_learning.md).
Do not attempt the inactive DynamoDB/SNS/SQS/EventBridge architecture.

Create an ignored local JSON config from `deploy/s3_learning.example.json`:

- Supply verified account, Region, existing Memory ID, artifact bucket,
  separate private failure bucket and five log groups in seat order.
- Choose fresh team and stack names matching the player CDK binding.
- Start with `collection_enabled=false`, `learning_enabled=false`.
- Preserve the intended numeric baseline; do not accidentally reset an adapted
  profile to the packaged default. The bootstrap command uses
  `profiles/recommended.json`, so compare it with current ACTIVE before proceeding.

Set `LEARNING_CONFIG` to the absolute config path. Inspect the plan, then write:

```sh
cd "$PROJECT"
"$PYTHON" deploy/s3_learning.py plan --config "$LEARNING_CONFIG"
"$PYTHON" deploy/s3_learning.py apply --config "$LEARNING_CONFIG" --approve
"$PYTHON" deploy/s3_learning.py bootstrap --config "$LEARNING_CONFIG" --approve
```

Bootstrap creates the baseline, not an arbitrary overwrite of ACTIVE. Verify its
digest and code generation.

Retire old learning admission before the cutover and confirm its work has
settled. Setting an enable flag to false does not stop an already running worker.
Use the old stack's own deployed template/artifacts or verified old source when
disabling it: running the new packaging command against the old config would
replace its evaluator with new, incompatible code.

Preserve old ACTIVE, observations, acknowledgements, attempts and match pins.
Collection can be enabled on the new stack while learning remains disabled;
coordinate subscriptions within the actual CloudWatch quota and verify their
destinations. Do not delete evidence to clear a quota or retry a failed learning
attempt.

**Gate:** baseline exists, learning cannot promote during cutover, and the
player binding targets the intended compatible registry.

## 6. Stage and deploy all five players

Arrange a controlled rollout window without new competition matches where
possible. Five runtime updates are not atomic; old sessions may keep their
original runtime version.

Inspect status, stage the selected source, and compare staged files:

```sh
cd "$PROJECT"
"$PYTHON" deploy/deploy.py --status
"$PYTHON" deploy/deploy.py --stage-only
```

Staging copies `agent/` and the packaged profile into `deploy/app/player/`.
Review the deployment diff through the installed CLI/CDK's supported commands.
Check that it updates the existing project and five runtimes, with no unexpected
resource deletion, account change, environment override or namespace mismatch.

With explicit deployment authorization and the preceding gates satisfied:

```sh
cd "$PROJECT"
"$PYTHON" deploy/deploy.py
```

The script stages again, checks tools/account, bootstraps CDK if needed, and runs
`agentcore deploy --yes`. It performs AWS writes; `--stage-only` does not deploy.
Capture output in an ignored local log and inspect the exit status.

**Gate:** deployment succeeds for the intended existing project. A printed ARN
or a successful CLI exit alone is not final verification.

**Optional: enable live coaching.** To allow the Nova Pro `CoachLoop` to adjust
parameters mid-match, set `AFC_LIVE_COACH_BUCKET=<bucket>` on all five runtimes.
The value must match the bucket passed to `CoachLoop`. `AFC_MATCH_ID` must also be
set (or injected per-session) for the S3 key lookup to activate. Omit both to
deploy without live coaching.

## 7. Verify serving and evaluator parity before enabling learning

For every runtime:

1. Poll runtime and DEFAULT endpoint to `READY`.
2. Confirm the DEFAULT live version equals the intended deployed version.
3. Confirm ARN/seat identity is unchanged.
4. Verify expected registry/team/bucket/account binding and tactical flags.
5. Download its code artifact and compare packaged agent sources/profile with
   the intended deployment source.

For collector and learner, verify active/successful update status, binding,
learning-disabled state during rollout, and packaged tactical/registry/evaluation
sources. Confirm the evaluator package contains its simulator dependencies.

Run live-shaped diagnostic probes for all five seats using the reserved UUID:

```text
6dc3a574-299e-44e4-81aa-6eb36c5776ef
```

Use session IDs `<UUID>-team[01]-pos[0-4]` with matching payload routing.
Construct inputs using the actual adapter contract, not an assumed coordinate
scale. Verify correctly routed command responses, a common current-generation
profile digest and one shared immutable match pin. Also exercise the existing
synthetic fitness path with `scrimmage-drill-*` sessions.

Probe sessions themselves are version-pinned. On a later deployment, inspect
whether an existing diagnostic session still runs old code; do not mistake it
for the new endpoint or delete its immutable pin. If a safe current-version
probe cannot be established under the reserved identity, stop activation and
resolve the diagnostic method explicitly. Do not silently use an arbitrary
match UUID that could contaminate learning evidence.

Confirm real runtime logs reach the intended collector, S3 observations have
acknowledgements, and exact diagnostic documents can be read from Memory.
Respect the 120-second source-age verification window; zero eligible documents
before that window expires is not evidence of delivery failure.

**Gate:** five-player routing, common pin, artifact parity and exact evidence
delivery are verified; otherwise keep learning disabled.

## 8. Set a fresh promotion guard and enable the loop

For a compatibility/namespace migration, calculate:

```text
promotion_not_before_ms =
  latest completed five-player update timestamp in epoch milliseconds
  + maximum relevant legacy-session maxLifetime in seconds * 1000
  + 120000 safety margin
```

Use captured old runtime lifecycle settings and account for any still-relevant
older sessions; retain a later existing guard if necessary. Do not shorten a
safe drain interval merely because the new runtime uses a smaller lifetime.
Record the result in UTC. Never copy an earlier rollout's deadline.

Persist the guard in the new local config. After verification, set
`collection_enabled=true`, `learning_enabled=true`, and apply:

```sh
cd "$PROJECT"
"$PYTHON" deploy/s3_learning.py apply --config "$LEARNING_CONFIG" --approve
```

Confirm both Lambda environments contain the correct enabled flag and guard,
all five subscriptions point to the intended collector, and old collection/
learning remain disabled. Keep the old evidence and registry intact.

Emit fresh reserved diagnostic observations through real player invocations to
exercise CloudWatch-triggered dispatch. Allow the approximately 130-second
quietness window and inspect `work.json` and `last-learning.json`.

Healthy verification may end with `insufficient_evidence`; do not force a
candidate or promotion to demonstrate success. Continuous real matches may
extend processing; report that honestly rather than claiming the latch settled.
Check alarms, invocation errors and the new stack's async-failure archive.

## 9. Record completion or contain failure

Write a deployment receipt with source revision, runtime/endpoint versions,
unchanged ARNs, artifact hashes/parity, namespace, baseline digest/generation,
probe results, learning state, UTC promotion guard and remaining limitations.
Update deployment-status documentation without exposing credentials.

Declare completion only when all five intended endpoints and the requested
learning state are verified. State that existing sessions may retain older code.

If a rollout partially fails:

- Keep promotion disabled and report exactly which seats/versions are live.
- Investigate CloudFormation/runtime events rather than repeatedly redeploying.
- Use captured artifacts and configuration for a coordinated recovery; do not
  roll back only the evaluator or silently mix incompatible namespaces.
- Preserve durable evidence and pins. A code rollback is another controlled
  deployment, not permission to overwrite ACTIVE or an existing match binding.
- Request user direction before destructive recovery or actions outside the
  authorized deployment scope.

Do not create commits or PRs unless requested separately.
