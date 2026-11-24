"""YouTube uploads and view counts through the YouTube Data API v3.

Notes from the docs (see SETUP.md):
- Uploads from API projects that have not passed YouTube's compliance audit are
  forced to private. Flip them to public in YouTube Studio, or apply for the audit.
- The OAuth app is published, so the login does not expire on its own (only if revoked).
  Publish the app to "In production" once to avoid that.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
]


def available(token_file: Path) -> bool:
    return bool(os.environ.get("YOUTUBE_TOKEN_JSON")) or token_file.exists()


def login(client_secrets: Path, token_file: Path) -> None:
    """One-time browser login on your Mac. Saves a refresh token to token_file."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    if not client_secrets.exists():
        raise FileNotFoundError(f"{client_secrets} not found. Download it from Google Cloud (SETUP.md).")
    flow = InstalledAppFlow.from_client_secrets_file(str(client_secrets), SCOPES)
    creds = flow.run_local_server(port=0, prompt="consent", access_type="offline")
    token_file.write_text(creds.to_json())


def client(client_secrets: Path, token_file: Path):
    """Cloud runs pass the saved token in YOUTUBE_TOKEN_JSON and never open a browser."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    env_token = os.environ.get("YOUTUBE_TOKEN_JSON")
    if env_token:
        creds = Credentials.from_authorized_user_info(json.loads(env_token), SCOPES)
    elif token_file.exists():
        creds = Credentials.from_authorized_user_file(str(token_file), SCOPES)
    else:
        if os.environ.get("CI"):
            raise RuntimeError("YOUTUBE_TOKEN_JSON secret is not set")
        login(client_secrets, token_file)
        creds = Credentials.from_authorized_user_file(str(token_file), SCOPES)
    if not creds.valid:
        try:
            creds.refresh(Request())
        except Exception as e:
            raise RuntimeError(
                "YouTube login expired. On your Mac run `clipmill youtube-login`, then "
                "`scripts/set_github_secrets.sh` (the login was revoked or made while the app was in "
                "Testing)."
            ) from e
        if not env_token:
            token_file.write_text(creds.to_json())
    return build("youtube", "v3", credentials=creds, cache_discovery=False)


def short_title(title: str) -> str:
    title = title.strip() or "GTA moment"
    return (title[:90] + " #shorts") if "#shorts" not in title.lower() else title[:100]


def upload(yt, path: Path, title: str, description: str, tags: list[str], *,
           category_id: str = "20", privacy: str = "private") -> str:
    from googleapiclient.http import MediaFileUpload

    body = {
        "snippet": {"title": title[:100], "description": description[:4900], "tags": tags[:30],
                    "categoryId": category_id},
        "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": False},
        # these are paid campaign clips: tick YouTube's paid promotion disclosure
        "paidProductPlacementDetails": {"hasPaidProductPlacement": True},
    }
    request = yt.videos().insert(part="snippet,status,paidProductPlacementDetails", body=body,
                                 media_body=MediaFileUpload(str(path), chunksize=-1, resumable=True))
    response = None
    while response is None:
        _, response = request.next_chunk()
    return response["id"]


def view_counts(yt, video_ids: list[str]) -> dict[str, int]:
    out = {}
    for i in range(0, len(video_ids), 50):
        batch = video_ids[i:i + 50]
        resp = yt.videos().list(part="statistics", id=",".join(batch)).execute()
        for item in resp.get("items", []):
            out[item["id"]] = int(item["statistics"].get("viewCount", 0))
    return out
