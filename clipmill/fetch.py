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


def extract_audio(video: Path, out: Path) -> Path:
    subprocess.run(
        [_need("ffmpeg"), "-y", "-loglevel", "error", "-i", str(video),
         "-vn", "-ac", "1", "-ar", "16000", str(out)],
        check=True,
    )
    return out
