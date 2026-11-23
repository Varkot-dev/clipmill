# Setup checklist

Everything here is free. Do it once, in this order. `clipmill doctor` tells you what is still missing.

## 1. Tools on your computer

| Tool | What it does | Install (macOS) |
|---|---|---|
| Python 3.11+ | runs clipmill | `brew install python` |
| ffmpeg | cuts and renders clips | `brew install ffmpeg` |
| yt-dlp | downloads only the minute or two of VOD around each moment | installed with clipmill |
| TwitchDownloaderCLI | downloads the chat replay of a VOD | download the macOS build from https://github.com/lay295/TwitchDownloader/releases, unzip, `chmod +x`, move it onto your PATH (for example `/usr/local/bin`) |
| Claude Code | judges which moments are real and where to cut | `npm install -g @anthropic-ai/claude-code`, then run `claude` once and log in with your Claude plan |

Then install clipmill:

```bash
git clone <this repo> && cd clipmill
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp config.example.toml config.toml
```

Notes:
- Transcription (faster-whisper) runs locally. On a Mac with no NVIDIA GPU it runs on the CPU. That is fine here because only the 1 to 2 minute windows around chat spikes are transcribed, not the whole stream. Use `model = "small"` or `"base"`.
- The judge calls `claude -p` without `--bare`, so it uses your logged-in plan and costs nothing extra. Every call counts toward your plan's usage limits. `max_candidates_per_vod` caps how many calls one VOD makes. If a future Claude Code update makes bare mode the default for `-p` and the judge starts asking for an API key, set `mode = "heuristic"` in `[judge]` or add an API key.

## 2. Twitch developer app (finds VODs and viewer clips)

1. Log in at https://dev.twitch.tv/console/apps (turn on two-factor auth on your Twitch account first; Twitch requires it).
2. Register Your Application. Name: anything (for example `clipmill-yourname`). OAuth Redirect URL: `http://localhost`. Category: Application Integration. Client type: Confidential.
3. Open the app, copy the Client ID, click New Secret, copy the secret.
4. Paste both into `[twitch]` in `config.toml`.

clipmill only uses public endpoints (Get Videos, Get Clips, Get Streams) with an app token, so no Twitch login flow is needed.

## 3. Pick streamers and campaigns (this is where the money comes from)

1. Make an account at https://contentrewards.com and connect the YouTube channel (and TikTok if you want) you will post from.
2. Filter campaigns for Gaming or search "GTA". Join streamer campaigns, not music promos. Read each brief: required tags, link in bio, banned content, minimum length, platforms.
3. For each campaign, add a `[[streamers]]` block to `config.toml`. Put the campaign name in `campaign` so upload output reminds you where to submit.
4. Open one of the streamer's VODs, pause on a normal frame, and measure where the facecam sits as fractions of the frame (`[x, y, width, height]`, each from 0 to 1). Put that in `facecam`. Put the game area in `gameplay`. Use `facecam = []` if there is no camera.
5. Put characters, bits, or names you want to test in `focus` (for example `["Lucia", "Jason"]`). Moments that mention them get a small boost and are tagged on the review page, so `clipmill stats` can tell you whether they do better.

Only clip streamers who run a campaign or have said clipping is fine.

## 4. YouTube channel and upload access (only needed for `clipmill upload`)

1. Create the channel at https://youtube.com (profile picture menu, Create a channel). A Brand Account is fine.
2. Go to https://console.cloud.google.com, create a project (for example `clipmill`).
3. APIs & Services, Library, enable **YouTube Data API v3**.
4. APIs & Services, OAuth consent screen (Google Auth Platform): User type External, fill in app name and your email. Add yourself as a test user.
5. Clients, Create client, type **Desktop app**. Download the JSON and save it in the repo folder as `client_secret.json` (git-ignored).
6. Audience, **Publish app** (switch from Testing to In production). If you skip this, your login expires every 7 days and uploads start failing. Google will show an unverified-app warning when you log in; that is expected for your own personal app. Click Advanced, then continue.
7. Run `clipmill upload --max 1`. A browser opens; pick the new channel's account. The token is saved to `youtube_token.json` (git-ignored).

Limits to know:
- **Uploads are forced to private** until the Google Cloud project passes YouTube's API compliance audit. Until then, after `clipmill upload`, open YouTube Studio and switch the videos to Public (or schedule them). You can apply for the audit later with the YouTube API Services Audit and Quota Extension Form once the channel is working.
- The default quota allows plenty of uploads per day for this use. `stats` uses about 1 unit per 50 videos.

## 5. Rules that keep the channel alive

- **No GTA 6 story spoilers.** Rockstar takes down endings, edited cutscene compilations, and big story reveals, and isolated cinematics. The judge marks `spoiler_risk` and drops those clips automatically, but check on the review page too. Streamer reactions and gameplay are fine.
- **No music.** Skip or reject clips with copyrighted music playing on stream.
- **Add value.** Clips must be edited moments with captions and context, not raw reuploads, to stay inside YouTube's reused content rules.
- **Three copyright strikes in 90 days deletes the channel.** If any streamer files a takedown, remove them from `config.toml` right away.
