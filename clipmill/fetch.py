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
            # No --force-keyframes-at-cuts: that re-encodes (minutes per window). The cut may
            # start a few seconds early, which is fine because transcript and cut points are
            # all measured on this downloaded file, and the final render re-encodes anyway.
            "--download-sections", f"*{hms(start)}-{hms(end)}",
            "-o", str(out),
            vod_url,
        ],
        check=True,
    )
    return out


def latest_vod(login: str, min_minutes: float = 45, look_back: int = 4) -> dict:
    """Finds a streamer's newest finished VOD with yt-dlp alone (no Twitch keys needed).
    Streams that are still live, or shorter than min_minutes, are skipped."""
    import json

    listing = subprocess.run(
        [_need("yt-dlp"), "-J", "--flat-playlist", "--playlist-end", str(look_back),
         f"https://www.twitch.tv/{login}/videos?filter=archives&sort=time"],
        check=True, capture_output=True, text=True).stdout
    entries = json.loads(listing).get("entries") or []
    if not entries:
        raise LookupError(f"no VODs found for {login}")
    for e in entries:
        info = vod_info(e.get("url") or f"https://www.twitch.tv/videos/{str(e['id']).lstrip('v')}")
        if not info["is_live"] and info["duration_s"] >= min_minutes * 60:
            return info
    raise LookupError(f"no finished VOD of at least {min_minutes:.0f} minutes among the last {look_back}")


def vod_info(url: str) -> dict:
    import json

    info = json.loads(subprocess.run([_need("yt-dlp"), "-J", "--no-warnings", url], check=True,
                                     capture_output=True, text=True).stdout)
    vid = str(info["id"]).lstrip("v")
    import time

    # A VOD whose end is within the last 10 minutes is almost certainly still being recorded.
    ends = (info.get("timestamp") or 0) + (info.get("duration") or 0)
    if info.get("timestamp") and time.time() - ends < 600:
        info["is_live"] = True
    return {"id": vid, "title": info.get("title", ""), "duration_s": float(info.get("duration") or 0),
            "created_at": info.get("upload_date") or "", "url": f"https://www.twitch.tv/videos/{vid}",
            "is_live": bool(info.get("is_live") or info.get("live_status") in ("is_live", "is_upcoming"))}


def extract_audio(video: Path, out: Path) -> Path:
    subprocess.run(
        [_need("ffmpeg"), "-y", "-loglevel", "error", "-i", str(video),
         "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(out)],
        check=True,
    )
    return out


def extract_frames(video: Path, times: list[float], out_dir: Path, width: int = 640) -> list[tuple[Path, float]]:
    """Saves small JPEG stills at the given window times so the judge can see the gameplay."""
    paths = []
    for i, t in enumerate(times):
        out = out_dir / f"{video.stem}_f{i}.jpg"
        r = subprocess.run([_need("ffmpeg"), "-y", "-loglevel", "error", "-ss", f"{max(0.0, t):.2f}",
                            "-i", str(video), "-frames:v", "1", "-vf", f"scale={width}:-2", "-q:v", "5",
                            str(out)])
        if r.returncode == 0 and out.exists():
            paths.append((out, t))
    return paths
