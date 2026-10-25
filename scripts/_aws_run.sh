#!/usr/bin/env bash
set -euo pipefail
export PATH="$HOME/.nvm/versions/node/v26.7.0/bin:$PATH"
unset AWS_MAX_ATTEMPTS
. /Users/aleksandr/Local/EPAM/aws_football_challenge/aws_creds.txt
unset AWS_MAX_ATTEMPTS
exec "$@"
