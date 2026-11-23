#!/usr/bin/env bash
# Copies your logins into GitHub secrets so the cloud runs can use them.
# Run on your Mac from the clipmill folder:  bash scripts/set_github_secrets.sh
# Every prompt hides what you paste, and you can press Enter to skip any item.
# Nothing is printed and nothing is written to disk.
set -euo pipefail

REPO="${CLIPMILL_REPO:-Varkot-dev/clipmill}"
cd "$(dirname "$0")/.."

if ! command -v gh >/dev/null; then
  echo "Install the GitHub CLI first:  brew install gh   then   gh auth login"; exit 1
fi
gh auth status >/dev/null 2>&1 || { echo "Run 'gh auth login' first"; exit 1; }

put() {  # put NAME VALUE
  if [ -n "$2" ]; then
    printf '%s' "$2" | gh secret set "$1" -R "$REPO" >/dev/null && echo "  set $1"
  else
    echo "  skipped $1"
  fi
}

ask() {  # ask PROMPT -> hidden input in $REPLY_VALUE
  REPLY_VALUE=""
  read -r -s -p "$1 (hidden, Enter to skip): " REPLY_VALUE; echo
}

echo "Saving secrets to $REPO"

# 1. Twitch keys from config.toml
if [ -f config.toml ]; then
  TW_ID=$(python3 -c 'import re;t=open("config.toml").read();m=re.search(r"client_id\s*=\s*\"([^\"]*)\"",t);print(m.group(1) if m else "")')
  TW_SECRET=$(python3 -c 'import re;t=open("config.toml").read();m=re.search(r"client_secret\s*=\s*\"([^\"]*)\"",t);print(m.group(1) if m else "")')
  put TWITCH_CLIENT_ID "$TW_ID"
  put TWITCH_CLIENT_SECRET "$TW_SECRET"
  unset TW_ID TW_SECRET
else
  echo "  no config.toml found; skipping Twitch"
fi

# 2. YouTube login (made by: clipmill youtube-login)
if [ -f youtube_token.json ]; then
  put YOUTUBE_TOKEN_JSON "$(cat youtube_token.json)"
else
  echo "  no youtube_token.json yet; run 'clipmill youtube-login' first to enable YouTube posting"
fi

# 2b. Content Rewards login (made by: clipmill cr-login)
if [ -f cr_state.json ]; then
  put CR_STATE_JSON "$(cat cr_state.json)"
else
  echo "  no cr_state.json yet; run 'clipmill cr-login' to enable automatic campaign submission"
fi

# 3. Claude (made by: claude setup-token)
ask "Paste the Claude token from 'claude setup-token'"
put CLAUDE_CODE_OAUTH_TOKEN "$REPLY_VALUE"

# 4. Telegram bot for phone review
ask "Paste the Telegram bot token from @BotFather"
TG_TOKEN="$REPLY_VALUE"
case "$TG_TOKEN" in
  ""|*:*) ;;
  *) echo "  that does not look like a Telegram bot token (they contain a colon); skipping"; TG_TOKEN="" ;;
esac
if [ -n "$TG_TOKEN" ]; then
  put TELEGRAM_BOT_TOKEN "$TG_TOKEN"
  TG_CHAT=$(python3 - "$TG_TOKEN" <<'EOF'
import json, sys, urllib.request
token = sys.argv[1]
try:
    data = json.load(urllib.request.urlopen(f"https://api.telegram.org/bot{token}/getUpdates", timeout=20))
except Exception:
    data = {}
ids = {str(m["chat"]["id"]) for u in data.get("result", []) for m in [u.get("message") or {}] if m.get("chat")}
print(next(iter(ids)) if len(ids) == 1 else "")
EOF
)
  if [ -n "$TG_CHAT" ]; then
    put TELEGRAM_CHAT_ID "$TG_CHAT"
  else
    echo "  could not find your chat id: open your bot in Telegram, tap Start, then run this script again"
  fi
  unset TG_TOKEN TG_CHAT
fi

# 5. Instagram (optional)
ask "Paste the Instagram access token (optional)"
put INSTAGRAM_ACCESS_TOKEN "$REPLY_VALUE"
unset REPLY_VALUE

echo "Done. Check them at https://github.com/$REPO/settings/secrets/actions"
