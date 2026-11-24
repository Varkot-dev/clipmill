"""TikTok through the official Content Posting API, in draft (inbox) mode.

An unaudited TikTok app may not post publicly, but it may send videos to the creator's
TikTok inbox as drafts. The owner opens the draft, pastes the caption the bot sent him and
taps Post. A short watcher job then finds the new public video and submits it to the
streamer's Content Rewards campaign.

Login: `clipmill tiktok-login` (once, on the Mac) stores TIKTOK_CLIENT_KEY,
TIKTOK_CLIENT_SECRET and TIKTOK_REFRESH_TOKEN as GitHub secrets. TikTok rotates refresh
tokens, so each refreshed one is kept in the state database, encrypted with a key derived
from the client secret.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import time
from pathlib import Path

import requests

log = logging.getLogger("clipmill")

API = "https://open.tiktokapis.com/v2"
AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
REDIRECT_URI = "https://varkot-dev.github.io/gta6moments/tiktok.html"
SCOPES = "user.info.basic,video.upload,video.list"


def _check(r: requests.Response) -> dict:
    try:
        data = r.json()
    except ValueError:
        raise RuntimeError(f"TikTok API error {r.status_code}: {r.text[:200]}")
    err = data.get("error")
    if r.status_code >= 400 or (isinstance(err, dict) and err.get("code") not in (None, "ok")) \
            or (isinstance(err, str) and err):
        raise RuntimeError(f"TikTok API error: {data}")
    return data


def auth_url(client_key: str, state: str = "clipmill") -> str:
    from urllib.parse import urlencode
    return AUTH_URL + "?" + urlencode({"client_key": client_key, "scope": SCOPES, "response_type": "code",
                                       "redirect_uri": REDIRECT_URI, "state": state})


def exchange_code(client_key: str, client_secret: str, code: str, http=None) -> dict:
    http = http or requests
    return _check(http.post(f"{API}/oauth/token/", data={
        "client_key": client_key, "client_secret": client_secret, "code": code,
        "grant_type": "authorization_code", "redirect_uri": REDIRECT_URI}, timeout=30))


def refresh(client_key: str, client_secret: str, refresh_token: str, http=None) -> dict:
    http = http or requests
    return _check(http.post(f"{API}/oauth/token/", data={
        "client_key": client_key, "client_secret": client_secret,
        "grant_type": "refresh_token", "refresh_token": refresh_token}, timeout=30))


def _fernet(secret: str):
    from cryptography.fernet import Fernet
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(("clipmill-tt:" + secret).encode()).digest()))


def access_token(db, client_key: str, client_secret: str, base_refresh: str, http=None) -> str:
    """Refreshes on every call (access tokens last 24h) and keeps the newest refresh token."""
    f = _fernet(client_secret)
    stored = db.get("tt_refresh_enc")
    candidates = []
    if stored:
        try:
            candidates.append(f.decrypt(stored.encode()).decode())
        except Exception:
            pass
    candidates.append(base_refresh)
    last_err = None
    for rt in candidates:
        try:
            data = refresh(client_key, client_secret, rt, http)
        except Exception as e:
            last_err = e
            continue
        if data.get("refresh_token") and data["refresh_token"] != rt:
            db.put("tt_refresh_enc", f.encrypt(data["refresh_token"].encode()).decode())
        return data["access_token"]
    raise RuntimeError(f"TikTok login expired; run `clipmill tiktok-login` on your Mac ({last_err})")


def upload_draft(token: str, path: Path, http=None) -> str:
    """Sends a video to the creator's TikTok inbox. Returns the publish_id."""
    http = http or requests
    path = Path(path)
    size = path.stat().st_size
    init = _check(http.post(f"{API}/post/publish/inbox/video/init/", headers={
        "Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=UTF-8"},
        json={"source_info": {"source": "FILE_UPLOAD", "video_size": size, "chunk_size": size,
                              "total_chunk_count": 1}}, timeout=60))["data"]
    with path.open("rb") as fh:
        r = http.put(init["upload_url"], data=fh, timeout=600, headers={
            "Content-Range": f"bytes 0-{size - 1}/{size}", "Content-Type": "video/mp4"})
    if r.status_code >= 300:
        raise RuntimeError(f"TikTok upload failed: {r.status_code} {r.text[:200]}")
    return init["publish_id"]


def recent_videos(token: str, max_count: int = 20, http=None) -> list[dict]:
    http = http or requests
    data = _check(http.post(f"{API}/video/list/",
                            params={"fields": "id,create_time,share_url,video_description,title"},
                            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                            json={"max_count": max_count}, timeout=30))
    return data.get("data", {}).get("videos", [])


def caption(s, title: str) -> str:
    """The caption the owner pastes. 'Streamer: <name>' lets the watcher match the post."""
    tags = ["#gta6", "#gtavi", "#gaming", "#twitch", f"#{s.display.replace(' ', '').lower()}",
            *[h for h in s.hashtags], "#gtaclips", "#fyp"]
    seen, out = set(), []
    for t in tags:
        if t.lower() not in seen:
            seen.add(t.lower())
            out.append(t)
    return f"#ad {title}\nStreamer: {s.display} ({s.handle_text})\n{' '.join(out)}"


def match_streamer(description: str, streamers) -> object | None:
    d = (description or "").lower()
    for s in streamers:
        if f"streamer: {s.display.lower()}" in d or s.handle_text.lower() in d:
            return s
    return None


def is_fresh(video: dict, minutes: float = 28) -> bool:
    return time.time() - float(video.get("create_time", 0)) <= minutes * 60
