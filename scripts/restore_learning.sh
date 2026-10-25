#!/usr/bin/env bash
# Restore S3 learning data from a backup archive produced by backup_learning.sh.
# Safe to run against a fresh bucket — uses s3 sync (additive, no deletes).
#
# Usage:
#   ./restore_learning.sh <archive.tar.gz> [target_bucket]
#
# target_bucket defaults to the source bucket recorded in the companion .meta.json.
# In a new environment the CDK bucket name may differ (new account ID in the name),
# so pass it explicitly:
#   ./restore_learning.sh learning-backup-20261025-....tar.gz cdk-hnb659fds-assets-NEWACCOUNT-us-east-1

set -euo pipefail

ARCHIVE="${1:-}"
if [ -z "$ARCHIVE" ]; then
  echo "Usage: $0 <archive.tar.gz> [target_bucket]" >&2
  exit 1
fi
if [ ! -f "$ARCHIVE" ]; then
  echo "ERROR: archive not found: $ARCHIVE" >&2
  exit 1
fi

# Derive metadata file path (same basename, .meta.json extension)
ARCHIVE_DIR="$(dirname "$ARCHIVE")"
ARCHIVE_BASE="$(basename "$ARCHIVE" .tar.gz)"
META_FILE="$ARCHIVE_DIR/$ARCHIVE_BASE.meta.json"

# Read metadata if present
DEFAULT_BUCKET=""
S3_PREFIX="final-agent-learning"
if [ -f "$META_FILE" ]; then
  DEFAULT_BUCKET="$(python3 -c "import json; d=json.load(open('$META_FILE')); print(d.get('source_bucket',''))" 2>/dev/null || echo "")"
  S3_PREFIX="$(python3 -c "import json; d=json.load(open('$META_FILE')); print(d.get('s3_prefix','final-agent-learning'))" 2>/dev/null || echo "final-agent-learning")"
  echo "Metadata: $META_FILE"
  echo "  source bucket: $DEFAULT_BUCKET"
  echo "  prefix:        $S3_PREFIX"
fi

TARGET_BUCKET="${2:-$DEFAULT_BUCKET}"
if [ -z "$TARGET_BUCKET" ]; then
  echo "ERROR: target bucket not specified and not found in metadata." >&2
  echo "Pass it as second argument: $0 $ARCHIVE <bucket-name>" >&2
  exit 1
fi

echo "=== Football learning data restore ==="
echo "Archive: $ARCHIVE"
echo "Target:  s3://$TARGET_BUCKET/$S3_PREFIX/"
echo ""
echo "WARNING: This will upload learning data to $TARGET_BUCKET."
echo "         Existing objects with the same keys are overwritten."
printf "Continue? [y/N] "
read -r CONFIRM
if [ "$CONFIRM" != "y" ] && [ "$CONFIRM" != "Y" ]; then
  echo "Aborted."
  exit 0
fi

# Verify AWS access
ACCOUNT="$(aws sts get-caller-identity --query Account --output text 2>&1)"
echo "AWS account: $ACCOUNT"
echo ""

WORK_DIR="$(mktemp -d)"
trap 'rm -rf "$WORK_DIR"' EXIT

echo "Extracting archive..."
tar -xzf "$ARCHIVE" -C "$WORK_DIR"

# Archive contains a 'learning' directory at the root
EXTRACT_DIR="$WORK_DIR/learning"
if [ ! -d "$EXTRACT_DIR" ]; then
  echo "ERROR: unexpected archive layout — expected 'learning/' directory inside archive" >&2
  exit 1
fi

LOCAL_COUNT="$(find "$EXTRACT_DIR" -type f | wc -l | tr -d ' ')"
echo "Files to upload: $LOCAL_COUNT"
echo ""

echo "Uploading via s3 sync..."
aws s3 sync \
  "$EXTRACT_DIR/" \
  "s3://$TARGET_BUCKET/$S3_PREFIX/" \
  --no-progress

echo ""
echo "Restore complete. Uploaded $LOCAL_COUNT files to s3://$TARGET_BUCKET/$S3_PREFIX/"
