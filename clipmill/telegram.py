"""Phone review over Telegram: each clip arrives as a playable video with Post / Skip buttons.

Uses the free Telegram Bot API. Button taps are collected by the next `clipmill approvals`
run (getUpdates polling), so nothing needs to stay online between runs.
Only button taps from your own chat id are accepted.
"""

from __future__ import annotations

import json
from pathlib import Path

import requests

API = "https://api.telegram.org/bot{token}/{method}"
MAX_UPLOAD_BYTES = 49 * 1024 * 1024  # bots can send videos up to 50 MB


class Telegram:
    def __init__(self, token: str, chat_id: str | int, session: requests.Session | None = None):
        if not token or not chat_id:
            raise ValueError("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set")
        self.token = token
        self.chat_id = str(chat_id)
        self.http = session or requests.Session()

    def _call(self, method: str, files=None, timeout: int = 60, **params) -> dict:
        r = self.http.post(API.format(token=self.token, method=method), data=params, files=files,
                           timeout=timeout)
        data = r.json()
        if not data.get("ok"):
            raise RuntimeError(f"Telegram {method} failed: {data.get('description')}")
        return data["result"]

    def send_message(self, text: str, reply_to: int | None = None) -> int:
        params = {"chat_id": self.chat_id, "text": text[:4000], "disable_web_page_preview": "true"}
        if reply_to:
            params["reply_to_message_id"] = reply_to
        return self._call("sendMessage", **params)["message_id"]

    def send_clip(self, path: Path, caption: str, clip_id: int) -> int:
        keyboard = {"inline_keyboard": [[
            {"text": "✅ Post", "callback_data": f"post:{clip_id}"},
            {"text": "❌ Skip", "callback_data": f"skip:{clip_id}"},
        ]]}
        with Path(path).open("rb") as f:
            result = self._call(
                "sendVideo", files={"video": (Path(path).name, f, "video/mp4")}, timeout=300,
                chat_id=self.chat_id, caption=caption[:1000], supports_streaming="true",
                reply_markup=json.dumps(keyboard),
            )
        return result["message_id"]

    def updates(self, offset: int | None = None) -> list[dict]:
        params = {"timeout": 0, "allowed_updates": json.dumps(["callback_query", "message"])}
        if offset is not None:
            params["offset"] = offset
        return self._call("getUpdates", **params)

    def answer(self, callback_id: str, text: str = "") -> None:
        try:
            self._call("answerCallbackQuery", callback_query_id=callback_id, text=text[:190])
        except RuntimeError:
            pass  # callback ids expire after a while; the decision still counts

    def clear_buttons(self, message_id: int) -> None:
        try:
            self._call("editMessageReplyMarkup", chat_id=self.chat_id, message_id=message_id,
                       reply_markup=json.dumps({"inline_keyboard": []}))
        except RuntimeError:
            pass


def decisions(updates: list[dict], chat_id: str | int) -> tuple[list[tuple[int, str, str, int]], int | None]:
    """Turns raw updates into [(clip_id, "post"|"skip", callback_id, message_id)] and the next offset.

    Taps from any chat other than yours are ignored.
    """
    out, next_offset = [], None
    for u in updates:
        next_offset = max(next_offset or 0, u["update_id"] + 1)
        cq = u.get("callback_query")
        if not cq:
            continue
        msg = cq.get("message") or {}
        if str(msg.get("chat", {}).get("id")) != str(chat_id):
            continue
        action, _, cid = (cq.get("data") or "").partition(":")
        if action in ("post", "skip") and cid.isdigit():
            out.append((int(cid), action, cq["id"], msg.get("message_id")))
    return out, next_offset


def find_chat_id(token: str, session: requests.Session | None = None) -> list[tuple[str, str]]:
    """After you send /start to your bot, this returns [(chat_id, name)] seen by the bot."""
    t = Telegram(token, "0", session)
    seen = {}
    for u in t.updates():
        msg = u.get("message") or (u.get("callback_query") or {}).get("message") or {}
        chat = msg.get("chat") or {}
        if chat.get("id"):
            seen[str(chat["id"])] = chat.get("first_name") or chat.get("username") or chat.get("title") or ""
    return list(seen.items())


def caption_for(clip, streamer) -> str:
    focus = json.loads(clip["focus_hits"] or "[]")
    lines = [
        f"{clip['title'] or '(no title yet)'}",
        f"{streamer.display} . score {clip['score']:.0f}/10 . {clip['category'] or ''}"
        + (f" . focus: {', '.join(focus)}" if focus else ""),
        f"{clip['reason'] or ''}",
        f"{clip['end_s'] - clip['start_s']:.0f}s clip . #{clip['id']}",
    ]
    return "\n".join(l for l in lines if l.strip())
