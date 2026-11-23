# clipmill

Turns long Twitch streams into short, captioned, vertical clips. It finds the moments chat lost it over, has Claude decide which ones are real and where to cut, renders them as Shorts, and sends each one to your phone. Tap Post and it goes to YouTube Shorts and Instagram Reels.

It runs on GitHub's servers on a schedule, so your computer can be off.

**Start with [MORNING.md](MORNING.md)** for the remaining one-time setup.

## How it works

```
every 6 hours (GitHub Actions: "make clips")
  new VODs from the streamers in clipmill.toml           (Twitch API)
  -> chat replay                                         (TwitchDownloaderCLI)
  -> chat spikes: distinct people reacting (KEKW, W, WHAT) vs the stream's normal pace
  -> boost moments viewers already clipped               (Twitch Get Clips)
  -> download only those 1-2 min windows                 (yt-dlp --download-sections)
  -> transcribe with word timings                        (faster-whisper, local, free)
  -> judge: keep? where to cut? title? hook?             (claude -p on your Claude plan)
  -> drop GTA 6 story spoilers automatically
  -> render 1080x1920 with word-by-word captions, a hook line on top, the streamer's handle
  -> send to Telegram with Post / Skip buttons

every hour (GitHub Actions: "approvals")
  -> read your taps -> post approved clips to YouTube and Instagram (daily cap) -> reply with links
```

State (which VODs are done, which clips were posted) is a small SQLite file kept on the `state` branch. Clip videos are kept as workflow artifacts for 7 days.

## Files

| File | What it is |
|---|---|
| `clipmill.toml` | All settings and the streamer list. Edit this to add or remove streamers. No secrets. |
| `config.toml` | Your Mac only (git-ignored): Twitch keys, optional Instagram/Telegram for local runs. |
| `client_secret.json`, `youtube_token.json` | Your Mac only (git-ignored): YouTube login. |
| `.github/workflows/` | `clips.yml` (make clips), `approvals.yml` (post), `try.yml` (manual test on one VOD). |
| `scripts/set_github_secrets.sh` | Copies your logins into GitHub secrets, hidden input. |

## Commands (on your Mac)

```bash
clipmill doctor                         # what's set up and what's missing
clipmill try --streamer darealboeshi    # test on the newest VOD, no keys needed
clipmill run                            # same as the cloud job
clipmill approvals                      # same as the cloud job
clipmill review                         # local approve/reject page (alternative to Telegram)
clipmill stats                          # YouTube views by kind of moment
clipmill compile --streamer darealboeshi --count 12   # weekly best-of video
clipmill youtube-login | telegram-chat-id | instagram-check | instagram-refresh
```

## Cost

Free apart from what you already pay for Claude: open-source tools, free Twitch/YouTube/Instagram/Telegram APIs, local transcription, and the judge runs on your Claude plan through `claude -p` (cloud runs use a `claude setup-token` token). GitHub Actions is free within the monthly allowance for private repos. Content Rewards takes 10% of earnings.

## Tests

```bash
pip install -e ".[dev]" && pytest
```
