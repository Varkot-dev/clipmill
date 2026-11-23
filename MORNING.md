# Setup checklist (about 10 minutes)

Mode: **hands-off**. Clips that Claude scores 8/10+ post themselves (max 6 a day); nothing to
review. To review each clip first instead, set `review = true` under `[auto]` in `clipmill.toml`
and set up the Telegram bot.

Already done: Twitch app and keys, Google Cloud project with YouTube Data API and login file,
3 campaigns picked (Bobby VanRiper, Boeshi, Dasgasd3m), cloud workflows, phone review,
Instagram uploader.

Tested overnight on GitHub's servers: the **try one VOD** workflow found Boeshi's latest
finished stream, downloaded its chat, found a moment, downloaded just that window, transcribed it
and rendered a vertical clip. To see it: github.com/Varkot-dev/clipmill/actions > "try one VOD" >
latest run > Artifacts. That test ran without Claude (no token yet), so the clip has no title or
hook line; with the token from step 2, Claude picks the cut, the title and the hook.

## 1. Telegram bot (optional: only if you want a text with each posted link)
1. Install Telegram, open a chat with **@BotFather**, send `/newbot`.
2. Name it anything (for example `clipmill review`) and give it a username ending in `bot`.
3. BotFather replies with a **token** (looks like `123456:ABC...`). Copy it.
4. Open your new bot (BotFather links it) and tap **Start**. The secrets script uses this to find your chat.

## 2. Claude login for the cloud (Mac, 1 min)
In Terminal: `claude setup-token`. Log in when the browser opens and copy the token it prints.
It runs on your Claude plan, no API bill.

## 3. YouTube login (Mac, 1 min)
```bash
cd ~/clipmill && git pull
python3 -m venv .venv && source .venv/bin/activate && pip install -q -e .
clipmill youtube-login
```
Pick the Google account that owns the **new clipping channel** (create the channel first at
youtube.com if needed). Google shows "unverified app": click Advanced, then continue. That is
your own app.

## 4. Send everything to GitHub (Mac, 2 min)
```bash
brew install gh && gh auth login        # skip if you already have the GitHub CLI
bash scripts/set_github_secrets.sh
```
It reads the Twitch keys and YouTube login from your folder, asks you to paste the Claude
token and the Telegram token (hidden), and finds your Telegram chat by itself.

## 5. Join the campaigns (phone or laptop, 5 min)
Sign in at contentrewards.com and join:
- BOBBY VAN RIPER GTA VI Clipping ($1.50 per 1K views)
- Boeshi GTA 6 Clipping ($1 per 1K)
- Dasgasd3m x RUCKUS GTA 6 ($2 per 1K; needs a GTA 6-only channel, which the new channel is)

Read each brief after joining. For Dasgasd3m, check his real Twitch name in the brief; if it is
not `dasgasd3m`, tell Claude and it updates `clipmill.toml`.

## 6. First test (phone, 1 min)
GitHub app or github.com/Varkot-dev/clipmill/actions:
- **make clips** > Run workflow. In 20 to 40 minutes, clips arrive in Telegram with Post / Skip.
- Tap Post on one. Within the hour, **approvals** posts it to YouTube (and Instagram, once set up)
  and replies with the links.

After that it runs by itself: new clips every 6 hours, approvals every hour.

## Things to know
- **YouTube uploads stay private** until Google audits the API project. Tap the link in Telegram
  and switch the video to Public in the YouTube Studio app. To remove this step for good, apply
  through the YouTube API Services audit form (free).
- **YouTube login lasts 7 days** while the Google app is in Testing. Publishing the app needs a
  privacy policy page; until then, redo steps 3 and 4 weekly (Telegram tells you when it expires).
- **Free GitHub minutes:** private repos get a monthly allowance. If runs stop with a billing
  message, either make the repo public (unlimited, your keys stay secret) or lower
  `vods_per_streamer` / `max_candidates_per_vod` in `clipmill.toml`.
- **Instagram (optional, later):** needs an Instagram Creator or Business account and a Meta
  developer app with the Instagram API. Run `bash scripts/set_github_secrets.sh` again with the
  token. `clipmill instagram-check` tests it.
- **Facecam layout:** clips use a blurred-background layout until each streamer's camera position
  is measured. Send Claude a screenshot of a stream and it fills in `facecam` in `clipmill.toml`.
