#!/usr/bin/env bash
# Puts the saved database (kept on the `state` branch) into ./state before a cloud run.
set -euo pipefail
if git ls-remote --exit-code --heads origin state >/dev/null 2>&1; then
  git fetch --depth=1 origin state
  git worktree add --detach state origin/state
  echo "Loaded state from the state branch"
else
  mkdir -p state
  echo "No state branch yet; starting fresh"
fi
