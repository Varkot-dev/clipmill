# clipmill: handoff notes for a new session

Read this first. It replaces the long chat history.

## What it is
Automated GTA 6 stream clipper for the "GTA 6 Moments" pages. Runs on GitHub Actions
(public repo Varkot-dev/clipmill). Finds funny moments in Twitch VODs (chat spikes plus
viewer clips), transcribes (faster-whisper small), judges with `claude -p` (Haiku, sees 5
stills), renders vertical captioned clips (ffmpeg, ASS captions), posts, then submits each
post to Content Rewards campaigns.

## Where things live
- `clipmill.toml`: streamers, limits, campaigns. State DB: `clipmill.db` on the `state` branch.
- Workflows: `clips.yml` (every 3 h), `tiktok-watch.yml` (every 10 min, also restarts overdue clip runs).
- Secrets are GitHub secrets only. Never put tokens in the repo or chat.

## Accounts
- Instagram @gta6momentsclips2026 (Meta system-user token, never expires, no insights permission).
- YouTube channel momentsg45 (authuser=1 in the browser). Shorts get 0 impressions: unsolved.
- TikTok gta6moments525: sandbox app, uploads are drafts the owner posts by hand.
  Captions go to GitHub issues labeled `tiktok`. A full draft inbox pauses TikTok for 3 h.
- Content Rewards: Playwright bot with a saved session. Campaigns: Boeshi, Bobby, Kai (paid);
  AsianJeff paused (out of budget).

## Strategy
Kai Cenat is the growth streamer (about 20 clips a day, any hour). Paid clips post noon to
midnight ET. Volume over perfection (judge min_score 5).

## Open items for the owner
- Post or delete waiting TikTok drafts; connect TikTok in Content Rewards; add a payout method.

## Rules
- Commits authored by Varkot-dev <Varkot-dev@users.noreply.github.com>, no Claude co-author.
- Never type passwords or verification codes for the owner. No em-dashes in messages to him.
