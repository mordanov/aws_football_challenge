#!/usr/bin/env bash
# Download all S3 learning data (observations, acknowledgements, registry) and pack to a
# dated archive. Data under final-agent-learning/ is the only non-reproducible state —
# agent code and CDK stacks can be redeployed from scratch but accumulated telemetry cannot.
#
# Usage:
#   ./backup_learning.sh [output_dir]
#
# Output: <output_dir>/learning-backup-YYYYMMDD-HHMMSS.tar.gz  (default: current dir)
#         Companion metadata file alongside the archive with bucket + prefix recorded.

set -euo pipefail

BUCKET="cdk-hnb659fds-assets-559922262840-us-east-1"
S3_PREFIX="final-agent-learning"
OUTPUT_DIR="${1:-.}"
TIMESTAMP="$(date -u +%Y%m%d-%H%M%S)"
WORK_DIR="$(mktemp -d)"
DOWNLOAD_DIR="$WORK_DIR/learning"
ARCHIVE_NAME="learning-backup-$TIMESTAMP.tar.gz"
ARCHIVE_PATH="$OUTPUT_DIR/$ARCHIVE_NAME"
META_PATH="$OUTPUT_DIR/learning-backup-$TIMESTAMP.meta.json"

# Resolve output dir to absolute path
mkdir -p "$OUTPUT_DIR"
OUTPUT_DIR="$(cd "$OUTPUT_DIR" && pwd)"
ARCHIVE_PATH="$OUTPUT_DIR/$ARCHIVE_NAME"
META_PATH="$OUTPUT_DIR/learning-backup-$TIMESTAMP.meta.json"

echo "=== Football learning data backup ==="
echo "Source:  s3://$BUCKET/$S3_PREFIX/"
echo "Archive: $ARCHIVE_PATH"
echo ""

# Verify account before touching anything
ACCOUNT="$(aws sts get-caller-identity --query Account --output text 2>&1)"
EXPECTED="559922262840"
if [ "$ACCOUNT" != "$EXPECTED" ]; then
  echo "ERROR: wrong account $ACCOUNT (expected $EXPECTED)" >&2
  exit 1
fi
echo "Account: $ACCOUNT  OK"

mkdir -p "$DOWNLOAD_DIR"

echo "Downloading via s3 sync (this may take a few minutes)..."
# WSParticipantRole has explicit deny on s3:ListBucket and some KMS keys —
# sync exits non-zero on permission errors; we keep whatever downloaded.
aws s3 sync \
  "s3://$BUCKET/$S3_PREFIX/" \
  "$DOWNLOAD_DIR/" \
  --no-progress \
  --exclude "*.tmp" || SYNC_EXIT=$?

OBJECT_COUNT="$(find "$DOWNLOAD_DIR" -type f | wc -l | tr -d ' ')"
if [ "${SYNC_EXIT:-0}" -ne 0 ]; then
  echo "WARNING: sync exited with code ${SYNC_EXIT} (permission errors expected with WSParticipantRole)"
  echo "         Archive will contain only the objects that were accessible."
fi
if [ "$OBJECT_COUNT" -eq 0 ]; then
  echo "ERROR: nothing downloaded — aborting" >&2; exit 1
fi
echo "Downloaded $OBJECT_COUNT objects"

echo ""
echo "Packing archive..."
# -C to the parent so the archive contains learning/ as the root dir
tar -czf "$ARCHIVE_PATH" -C "$WORK_DIR" "learning"

ARCHIVE_BYTES="$(wc -c < "$ARCHIVE_PATH")"
ARCHIVE_MB="$(echo "scale=1; $ARCHIVE_BYTES / 1048576" | bc)"
echo "Archive size: $ARCHIVE_MB MB  ($ARCHIVE_PATH)"

# Write metadata so the restore script knows source coordinates
python3 -c "
import json, sys
meta = {
    'created_at': '$(date -u +%Y-%m-%dT%H:%M:%SZ)',
    'source_bucket': '$BUCKET',
    's3_prefix': '$S3_PREFIX',
    'archive': '$ARCHIVE_NAME',
    'object_count': '$OBJECT_COUNT',
    'archive_bytes': $ARCHIVE_BYTES,
}
print(json.dumps(meta, indent=2))
" > "$META_PATH"
echo "Metadata:    $META_PATH"

# Cleanup temp dir
rm -rf "$WORK_DIR"

echo ""
echo "Done. To restore, run:"
echo "  ./restore_learning.sh $ARCHIVE_PATH [target_bucket]"
