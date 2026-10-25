#!/usr/bin/env bash
# Deploy all final-agent stacks from scratch.
# Usage: ./deploy_cfn.sh <team_id> <memory_id> [app_name]
# Example:
#   ./deploy_cfn.sh aleksalotl_tactics_v3 Memory_ourteam-abcdefghij FinalAgent
#
# Prerequisites:
#   - aws CLI configured with credentials for the target account
#   - uv (Python packager) available: pip install uv
#   - Run from the final_agent/ directory

set -euo pipefail

TEAM_ID="${1:?Usage: deploy_cfn.sh TEAM_ID MEMORY_ID [APP_NAME]}"
MEMORY_ID="${2:?Usage: deploy_cfn.sh TEAM_ID MEMORY_ID [APP_NAME]}"
APP_NAME="${3:-FinalAgent}"

REGION="${AWS_DEFAULT_REGION:-us-east-1}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FINAL_AGENT_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"

EXPECTED_ACCOUNT="559922262840"
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
if [[ "${ACCOUNT}" != "${EXPECTED_ACCOUNT}" ]]; then
  echo "ERROR: unexpected account ${ACCOUNT} (expected ${EXPECTED_ACCOUNT})" >&2
  exit 1
fi
echo "Account: ${ACCOUNT}  Region: ${REGION}"

STORAGE_STACK="${APP_NAME}-storage"
AGENTCORE_STACK="${APP_NAME}-agentcore"
LEARNING_STACK="${APP_NAME}-learning"

# ---------------------------------------------------------------------------
# Step 1: Deploy storage stack (S3 buckets)
# ---------------------------------------------------------------------------
echo ""
echo "=== Step 1: Storage stack ==="
aws cloudformation deploy \
  --stack-name "${STORAGE_STACK}" \
  --template-file "${SCRIPT_DIR}/storage.yaml" \
  --parameter-overrides TeamId="${TEAM_ID}" \
  --no-fail-on-empty-changeset \
  --region "${REGION}"

ARTIFACT_BUCKET=$(aws cloudformation describe-stacks \
  --stack-name "${STORAGE_STACK}" \
  --region "${REGION}" \
  --query 'Stacks[0].Outputs[?OutputKey==`ArtifactBucketName`].OutputValue' \
  --output text)

FAILURE_BUCKET=$(aws cloudformation describe-stacks \
  --stack-name "${STORAGE_STACK}" \
  --region "${REGION}" \
  --query 'Stacks[0].Outputs[?OutputKey==`FailureBucketName`].OutputValue' \
  --output text)

echo "Artifact bucket: ${ARTIFACT_BUCKET}"
echo "Failure bucket:  ${FAILURE_BUCKET}"

# ---------------------------------------------------------------------------
# Step 2: Package and upload agent code ZIP
# ---------------------------------------------------------------------------
echo ""
echo "=== Step 2: Package and upload agent code ==="

# Stage standalone serving code into deploy/app/player/
(cd "${FINAL_AGENT_DIR}" && python deploy/deploy.py --stage-only)

# Build the ZIP using uv (same approach as deploy.py)
ZIP_FILE="/tmp/final-agent-${TEAM_ID}.zip"
STAGED_DIR="${FINAL_AGENT_DIR}/deploy/app/player"
CODE_KEY="code/${TEAM_ID}/agent.zip"

# Install deps into a temp dir and zip everything together
TMP_INSTALL=$(mktemp -d)
trap 'rm -rf "${TMP_INSTALL}"' EXIT

echo "  Installing Python deps..."
uv pip install --python python3.12 --target "${TMP_INSTALL}" \
  "${FINAL_AGENT_DIR}[learning]" --no-dev --quiet

echo "  Building ZIP..."
# Copy staged agent code over installed packages (so our code wins on conflicts)
cp -r "${STAGED_DIR}/." "${TMP_INSTALL}/"
(cd "${TMP_INSTALL}" && zip -qr "${ZIP_FILE}" .)

ZIP_SIZE=$(du -sh "${ZIP_FILE}" | cut -f1)
echo "  ZIP size: ${ZIP_SIZE} → s3://${ARTIFACT_BUCKET}/${CODE_KEY}"

aws s3 cp "${ZIP_FILE}" "s3://${ARTIFACT_BUCKET}/${CODE_KEY}" \
  --region "${REGION}"

# ---------------------------------------------------------------------------
# Step 3: Deploy AgentCore runtimes
# ---------------------------------------------------------------------------
echo ""
echo "=== Step 3: AgentCore runtimes ==="
aws cloudformation deploy \
  --stack-name "${AGENTCORE_STACK}" \
  --template-file "${SCRIPT_DIR}/agentcore.yaml" \
  --capabilities CAPABILITY_NAMED_IAM \
  --parameter-overrides \
      AppName="${APP_NAME}" \
      TeamId="${TEAM_ID}" \
      ArtifactBucket="${ARTIFACT_BUCKET}" \
      CodeS3Key="${CODE_KEY}" \
  --no-fail-on-empty-changeset \
  --region "${REGION}"

# ---------------------------------------------------------------------------
# Step 4: Deploy learning infrastructure
# ---------------------------------------------------------------------------
echo ""
echo "=== Step 4: Learning infrastructure ==="
aws cloudformation deploy \
  --stack-name "${LEARNING_STACK}" \
  --template-file "${SCRIPT_DIR}/learning.yaml" \
  --capabilities CAPABILITY_NAMED_IAM \
  --parameter-overrides \
      AppName="${APP_NAME}" \
      TeamId="${TEAM_ID}" \
      ArtifactBucket="${ARTIFACT_BUCKET}" \
      FailureBucket="${FAILURE_BUCKET}" \
      CodeS3Key="${CODE_KEY}" \
      MemoryId="${MEMORY_ID}" \
  --no-fail-on-empty-changeset \
  --region "${REGION}"

echo ""
echo "=== Deploy complete ==="
echo "  Runtimes: ${APP_NAME}_gk / _def / _mid / _fwd1 / _fwd2"
echo "  Learner schedule: every 15 minutes"
echo ""
echo "  NOTE: Create AgentCore memory before deploying if not already done:"
echo "    agentcore memory create --name ${MEMORY_ID} --region ${REGION}"
