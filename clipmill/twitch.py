"""Twitch Helix API: find a streamer's VODs and the clips viewers made from them.

Uses an app access token (client credentials). Get Videos and Get Clips are public
endpoints, so no user login is needed.
"""

from __future__ import annotations

import re
import time
from datetime import datetime, timedelta, timezone

import requests

API = "https://api.twitch.tv/helix"
TOKEN_URL = "https://id.twitch.tv/oauth2/token"


def parse_duration(s: str) -> float:
    """'3h8m33s' -> 11313.0"""
    total = 0.0
    for value, unit in re.findall(r"(\d+)([hms])", s or ""):
        total += int(value) * {"h": 3600, "m": 60, "s": 1}[unit]
    return total


class Twitch:
    def __init__(self, client_id: str, client_secret: str, session: requests.Session | None = None):
        if not client_id or not client_secret:
            raise ValueError("twitch.client_id and twitch.client_secret must be set in config.toml")
        self.client_id = client_id
        self.client_secret = client_secret
        self.http = session or requests.Session()
        self._token = None
        self._token_expiry = 0.0

    def _auth_headers(self) -> dict:
        if not self._token or time.time() > self._token_expiry - 60:
            r = self.http.post(
                TOKEN_URL,
                params={
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "grant_type": "client_credentials",
                },
                timeout=30,
            )
            r.raise_for_status()
            data = r.json()
            self._token = data["access_token"]
            self._token_expiry = time.time() + data.get("expires_in", 3600)
        return {"Authorization": f"Bearer {self._token}", "Client-Id": self.client_id}

    def _get(self, path: str, params: dict) -> dict:
        r = self.http.get(f"{API}/{path}", headers=self._auth_headers(), params=params, timeout=30)
        if r.status_code == 429:
            time.sleep(int(r.headers.get("Ratelimit-Reset", time.time() + 5)) - time.time() + 1)
            return self._get(path, params)
        r.raise_for_status()
        return r.json()

    def user_id(self, login: str) -> str:
        data = self._get("users", {"login": login})["data"]
        if not data:
            raise LookupError(f"no Twitch user named '{login}'")
        return data[0]["id"]

    def is_live(self, login: str) -> bool:
        return bool(self._get("streams", {"user_login": login})["data"])

    def vods(self, login: str, limit: int = 5) -> list[dict]:
        """Most recent archived broadcasts, newest first."""
        uid = self.user_id(login)
        data = self._get("videos", {"user_id": uid, "type": "archive", "first": limit})["data"]
        return [
            {
                "id": v["id"],
                "title": v["title"],
                "created_at": v["created_at"],
                "duration_s": parse_duration(v["duration"]),
                "url": v["url"],
            }
            for v in data
        ]

    def viewer_clips(self, login: str, vod: dict, max_pages: int = 5) -> list[dict]:
        """Clips viewers made during this VOD, with their offset into the VOD in seconds."""
        uid = self.user_id(login)
        start = datetime.fromisoformat(vod["created_at"].replace("Z", "+00:00"))
        end = start + timedelta(seconds=vod["duration_s"] + 60)
        params = {
            "broadcaster_id": uid,
            "started_at": start.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "ended_at": end.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "first": 100,
        }
        out = []
        for _ in range(max_pages):
            page = self._get("clips", params)
            for c in page["data"]:
                if c.get("video_id") == vod["id"] and c.get("vod_offset") is not None:
                    out.append(
                        {
                            "offset_s": float(c["vod_offset"]),
                            "duration_s": float(c.get("duration", 30)),
                            "views": int(c.get("view_count", 0)),
                            "title": c.get("title", ""),
                        }
                    )
            cursor = page.get("pagination", {}).get("cursor")
            if not cursor:
                break
            params["after"] = cursor
        return out
