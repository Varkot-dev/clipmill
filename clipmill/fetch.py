"""Downloads chat replays and only the short windows of VOD we need.

Downloading a whole 8 hour VOD is slow and huge. yt-dlp can fetch just a time
range from Twitch's HLS stream, so we grab each candidate window (about 1 to 2
minutes) instead.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def _need(binary: str) -> str:
    path = shutil.which(binary)
    if not path:
        raise FileNotFoundError(f"'{binary}' is not installed or not on PATH. See SETUP.md.")
    return path


def hms(seconds: float) -> str:
    seconds = max(0.0, seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h):02d}:{int(m):02d}:{s:06.3f}"


def download_chat(vod_id: str, out: Path, binary: str = "TwitchDownloaderCLI") -> Path:
    if out.exists() and out.stat().st_size > 0:
        return out
    subprocess.run(
        [_need(binary), "chatdownload", "--id", vod_id, "-o", str(out), "--collision", "Overwrite"],
        check=True,
    )
    return out


def download_window(vod_url: str, start: float, end: float, out: Path, max_height: int = 1080) -> Path:
    if out.exists() and out.stat().st_size > 0:
        return out
    subprocess.run(
        [
            _need("yt-dlp"),
            "--quiet", "--no-warnings",
            "-f", f"best[height<={max_height}]/best",
            "--download-sections", f"*{hms(start)}-{hms(end)}",
            "--force-keyframes-at-cuts",
            "-o", str(out),
            vod_url,
        ],
        check=True,
    )
    return out


def latest_vod(login: str) -> dict:
    """Finds a streamer's newest VOD with yt-dlp alone (no Twitch keys needed)."""
    import json

    listing = subprocess.run(
        [_need("yt-dlp"), "-J", "--flat-playlist", "--playlist-end", "1",
         f"https://www.twitch.tv/{login}/videos?filter=archives&sort=time"],
        check=True, capture_output=True, text=True).stdout
    entries = json.loads(listing).get("entries") or []
    if not entries:
        raise LookupError(f"no VODs found for {login}")
    return vod_info(entries[0].get("url") or f"https://www.twitch.tv/videos/{entries[0]['id'].lstrip('v')}")


def vod_info(url: str) -> dict:
    import json

    info = json.loads(subprocess.run([_need("yt-dlp"), "-J", "--no-warnings", url], check=True,
                                     capture_output=True, text=True).stdout)
    vid = str(info["id"]).lstrip("v")
    return {"id": vid, "title": info.get("title", ""), "duration_s": float(info.get("duration") or 0),
            "created_at": info.get("upload_date") or "", "url": f"https://www.twitch.tv/videos/{vid}"}


def extract_audio(video: Path, out: Path) -> Path:
    subprocess.run(
        [_need("ffmpeg"), "-y", "-loglevel", "error", "-i", str(video),
         "-vn", "-ac", "1", "-ar", "16000", str(out)],
        check=True,
    )
    return out
