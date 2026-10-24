#!/usr/bin/env bash
# Update GitHub Actions secrets from aws_creds.txt (export KEY=VALUE format).
# Usage: ./update_gh_secrets.sh [path/to/aws_creds.txt]
set -euo pipefail

CREDS="${1:-$(dirname "$0")/aws_creds.txt}"
REPO="mordanov/aws_football_challenge"

[[ -f "$CREDS" ]] || { echo "Not found: $CREDS"; exit 1; }

_val() { grep "^export $1=" "$CREDS" | cut -d= -f2-; }

echo "Updating secrets in $REPO from $CREDS ..."
_val AWS_ACCESS_KEY_ID     | gh secret set AWS_ACCESS_KEY_ID     --repo "$REPO"
_val AWS_SECRET_ACCESS_KEY | gh secret set AWS_SECRET_ACCESS_KEY --repo "$REPO"
_val AWS_SESSION_TOKEN     | gh secret set AWS_SESSION_TOKEN     --repo "$REPO"
_val AWS_DEFAULT_REGION    | gh secret set AWS_REGION            --repo "$REPO"
echo "Done. Secrets updated: AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_SESSION_TOKEN, AWS_REGION"
