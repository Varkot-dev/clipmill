"""YouTube uploads and view counts through the YouTube Data API v3.

Notes from the docs (see SETUP.md):
- Uploads from API projects that have not passed YouTube's compliance audit are
  forced to private. Flip them to public in YouTube Studio, or apply for the audit.
- If the OAuth consent screen is left in "Testing", the login expires every 7 days.
  Publish the app to "In production" once to avoid that.
"""

from __future__ import annotations

from pathlib import Path

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
]


def client(client_secrets: Path, token_file: Path):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    creds = None
    if token_file.exists():
        creds = Credentials.from_authorized_user_file(str(token_file), SCOPES)
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except Exception:
            creds = None
    if not creds or not creds.valid:
        if not client_secrets.exists():
            raise FileNotFoundError(f"{client_secrets} not found. Download it from Google Cloud (SETUP.md).")
        flow = InstalledAppFlow.from_client_secrets_file(str(client_secrets), SCOPES)
        creds = flow.run_local_server(port=0, prompt="consent")
    token_file.write_text(creds.to_json())
    return build("youtube", "v3", credentials=creds)


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
    }
    request = yt.videos().insert(part="snippet,status", body=body,
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
