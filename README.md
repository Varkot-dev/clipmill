# clipmill

Turns long Twitch streams into short, captioned, vertical clips. It finds the moments chat lost it over, has Claude decide which ones are real and where to cut, renders them as Shorts, and puts them on a review page. You approve on your phone, then upload.

Built for GTA 6 and GTA RP streamer clipping campaigns, but works for any Twitch streamer.

## How it works

```
new VOD on Twitch
  -> download chat replay                 (TwitchDownloaderCLI)
  -> find chat spikes                     (KEKW / OMEGALUL / W / WHAT bursts vs the stream's normal pace)
  -> boost spikes viewers already clipped (Twitch Get Clips)
  -> download only those 1-2 min windows  (yt-dlp --download-sections)
  -> transcribe with word timings         (faster-whisper, local)
  -> judge: keep? where to cut? title?    (claude -p on your Claude plan, JSON schema output)
  -> drop story spoilers automatically
  -> render 1080x1920: facecam top, gameplay bottom, word-by-word captions, streamer handle
  -> review page on your phone            (approve, reject, fix title)
  -> upload approved clips to YouTube     (YouTube Data API)
  -> pull views, compare kinds of moments (funny vs hype vs focus topics)
```

## Daily use

```bash
source .venv/bin/activate
clipmill process          # checks every streamer for new VODs and makes clips
clipmill review           # open the printed address on your phone, approve or reject
clipmill upload --max 5   # uploads approved clips (private until the API audit, see SETUP.md)
clipmill stats            # views per clip and average views by kind of moment
clipmill compile --streamer examplestreamer --count 12   # weekly best-of video
```

After uploading, submit each Short's link to its Content Rewards campaign (the upload output prints the campaign name next to each link).

To run it automatically, schedule `clipmill process` (for example every 2 hours with cron or launchd). The only manual steps left are review and campaign submission.

## Setup

See [SETUP.md](SETUP.md). Short version: install ffmpeg, TwitchDownloaderCLI and Claude Code, create a Twitch developer app, copy `config.example.toml` to `config.toml`, add streamers whose campaigns you joined, then run `clipmill doctor`.

## Cost

Free apart from what you already pay for Claude. Every tool is open source, the Twitch and YouTube APIs are free, transcription runs locally, and the judge uses your Claude plan through `claude -p`. Content Rewards takes 10% of what you earn.

## Experiments to run

- Different streamers and campaigns, to find which audiences watch clips.
- `focus` keywords (characters, bits, running jokes) to test whether certain topics outperform.
- Chat category (funny, hype, shock) vs views, from `clipmill stats`.
- Clip length: the judge aims for 15 to 60 seconds; check which lengths hold viewers in YouTube Studio.

## Tests

```bash
pytest
```

Covers spike detection, viewer-clip boosting, the Claude judge (with a fake `claude` binary), caption timing, both vertical layouts, compilation, and the review server.
