"""Instagram Reels publishing through the Instagram API (with Instagram Login).

Flow: create a REELS container with upload_type=resumable, send the video bytes to the
rupload URL Instagram returns, wait until processing finishes, then publish.
Needs a long-lived Instagram user access token for a Creator/Business account
(about 60 days; refresh with `clipmill instagram-refresh`). See MORNING.md.
"""

from __future__ import annotations

import time
from pathlib import Path

import requests

RUPLOAD = "https://rupload.facebook.com/ig-api-upload/{version}/{container}"


class Instagram:
    def __init__(self, access_token: str, user_id: str = "", api_host: str = "https://graph.instagram.com",
                 api_version: str = "v23.0", session: requests.Session | None = None):
        if not access_token:
            raise ValueError("Instagram access token is not set")
        self.token = access_token
        self.host = api_host.rstrip("/")
        self.version = api_version
        self.http = session or requests.Session()
        self.user_id = user_id or self.me()["user_id"]

    def _url(self, path: str) -> str:
        return f"{self.host}/{self.version}/{path.lstrip('/')}"

    def _check(self, r: requests.Response) -> dict:
        data = r.json()
        if r.status_code >= 400 or "error" in data:
            err = data.get("error", {})
            raise RuntimeError(f"Instagram API error: {err.get('message', data)}")
        return data

    def me(self) -> dict:
        r = self.http.get(self._url("me"), params={"fields": "user_id,username", "access_token": self.token},
                          timeout=30)
        data = self._check(r)
        data.setdefault("user_id", data.get("id"))
        return data

    def publish_reel(self, path: Path, caption: str, share_to_feed: bool = True,
                     poll_seconds: int = 5, max_wait: int = 600) -> str:
        path = Path(path)
        created = self._check(self.http.post(self._url(f"{self.user_id}/media"), data={
            "media_type": "REELS", "upload_type": "resumable", "caption": caption[:2200],
            "share_to_feed": "true" if share_to_feed else "false", "access_token": self.token,
            "thumb_offset": "1200",  # cover frame: gameplay with the hook on screen, not a black first frame
        }, timeout=60))
        container = created["id"]
        upload_url = created.get("uri") or RUPLOAD.format(version=self.version, container=container)
        size = path.stat().st_size
        with path.open("rb") as f:
            self._check(self.http.post(upload_url, data=f, timeout=600, headers={
                "Authorization": f"OAuth {self.token}", "offset": "0", "file_size": str(size),
            }))
        waited = 0
        while True:
            status = self._check(self.http.get(self._url(container), params={
                "fields": "status_code,status", "access_token": self.token}, timeout=30))
            code = status.get("status_code")
            if code == "FINISHED":
                break
            if code in ("ERROR", "EXPIRED"):
                raise RuntimeError(f"Instagram could not process the video: {status.get('status')}")
            if waited >= max_wait:
                raise TimeoutError("Instagram is still processing the video after 10 minutes")
            time.sleep(poll_seconds)
            waited += poll_seconds
        published = self._check(self.http.post(self._url(f"{self.user_id}/media_publish"), data={
            "creation_id": container, "access_token": self.token}, timeout=60))
        return published["id"]

    def views(self, media_id: str) -> int | None:
        """Total views of a Reel, or None if Instagram will not say yet."""
        for metric in ("views", "plays"):
            try:
                data = self._check(self.http.get(self._url(f"{media_id}/insights"), params={
                    "metric": metric, "access_token": self.token}, timeout=30))
                vals = data.get("data", [{}])[0]
                if "total_value" in vals:
                    return int(vals["total_value"].get("value", 0))
                return int(vals.get("values", [{}])[0].get("value", 0))
            except Exception:
                continue
        return None

    def permalink(self, media_id: str) -> str:
        try:
            data = self._check(self.http.get(self._url(media_id), params={
                "fields": "permalink", "access_token": self.token}, timeout=30))
            return data.get("permalink", "")
        except Exception:
            return ""


def refresh_token(token: str, session: requests.Session | None = None) -> dict:
    """Extends a long-lived token by another ~60 days. Returns {access_token, expires_in}."""
    http = session or requests.Session()
    r = http.get("https://graph.instagram.com/refresh_access_token",
                 params={"grant_type": "ig_refresh_token", "access_token": token}, timeout=30)
    data = r.json()
    if "access_token" not in data:
        raise RuntimeError(f"could not refresh token: {data}")
    return data


# --- Keeping the token alive without anyone touching it ------------------------------------
# GitHub Actions cannot rewrite its own secrets, so each refreshed token is stored in the state
# database, encrypted with a key derived from the original INSTAGRAM_ACCESS_TOKEN secret (which
# never changes). Every run refreshes it once a week; a token refreshed weekly never expires.

REFRESH_EVERY_DAYS = 7


def _fernet(base_token: str):
    import base64
    import hashlib

    from cryptography.fernet import Fernet

    key = base64.urlsafe_b64encode(hashlib.sha256(("clipmill-ig:" + base_token).encode()).digest())
    return Fernet(key)


def live_token(db, base_token: str, session: requests.Session | None = None, now: float | None = None) -> str:
    """Returns the newest working token, refreshing it if a week has passed."""
    import logging

    log = logging.getLogger(__name__)
    if not base_token:
        return ""
    if base_token.startswith("EAA"):  # business system-user token from Meta: never expires
        return base_token
    now = time.time() if now is None else now
    token = base_token
    stored = db.get("ig_token_enc")
    if stored:
        try:
            token = _fernet(base_token).decrypt(stored.encode()).decode()
        except Exception:
            log.warning("Stored Instagram token belongs to an older secret; starting from the secret again")
            token = base_token
            db.put("ig_refreshed_at", 0)
    last = float(db.get("ig_refreshed_at", "0") or 0)
    if now - last >= REFRESH_EVERY_DAYS * 86400:
        try:
            token = refresh_token(token, session)["access_token"]
            db.put("ig_token_enc", _fernet(base_token).encrypt(token.encode()).decode())
            db.put("ig_refreshed_at", now)
            log.info("Instagram token refreshed")
        except Exception as e:
            log.warning("Instagram token refresh failed (will retry next run): %s", e)
    return token
