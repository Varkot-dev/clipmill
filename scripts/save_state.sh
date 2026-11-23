#!/usr/bin/env bash
# Commits the database back to the `state` branch after a cloud run.
# Only the database is stored there; clip videos live in workflow artifacts for 7 days.
set -euo pipefail
cd state
if [ ! -e .git ]; then
  git init -q -b state
fi
git add clipmill.db 2>/dev/null || { echo "No database to save"; exit 0; }
if git diff --cached --quiet; then
  echo "State unchanged"
  exit 0
fi
git -c user.name="clipmill-bot" -c user.email="clipmill-bot@users.noreply.github.com" \
  commit -q -m "state: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
git push -q "https://x-access-token:${GITHUB_TOKEN}@github.com/${GITHUB_REPOSITORY}.git" HEAD:state
echo "State saved"
