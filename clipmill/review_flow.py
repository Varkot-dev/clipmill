"""Phone review loop: send new clips to Telegram, act on Post / Skip taps, publish approved clips.

Runs the same way locally and in GitHub Actions. In the cloud, the clip files live in the
workflow artifact of the run that made them, so they are downloaded again when you tap Post.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path

from . import publish
from .config import Config
from .db import DB
from .pipeline import clip_file
from .telegram import MAX_UPLOAD_BYTES, Telegram, caption_for, decisions

log = logging.getLogger("clipmill")


def telegram_from_env(cfg: Config) -> Telegram | None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN") or cfg.section("telegram").get("bot_token")
    chat = os.environ.get("TELEGRAM_CHAT_ID") or cfg.section("telegram").get("chat_id")
    return Telegram(token, chat) if token and chat else None


def ensure_local(cfg: Config, clip) -> Path:
    """Returns a path to the clip file, downloading the workflow artifact if needed."""
    path = clip_file(cfg, clip)
    if path.exists():
        return path
    if not clip["artifact_run_id"] or not shutil.which("gh"):
        raise FileNotFoundError(f"clip file {path} is gone and there is no artifact to fetch")
    dest = cfg.path("work_dir") / "artifacts" / clip["artifact_run_id"]
    if not dest.exists():
        cmd = ["gh", "run", "download", clip["artifact_run_id"], "-n", clip["artifact_name"], "-D", str(dest)]
        if os.environ.get("GITHUB_REPOSITORY"):
            cmd += ["-R", os.environ["GITHUB_REPOSITORY"]]
        subprocess.run(cmd, check=True)
    local = dest / clip["path"]
    if not local.exists():
        raise FileNotFoundError(f"{clip['path']} not found in artifact {clip['artifact_name']} "
                                "(artifacts are kept for 7 days)")
    return local


def preview_for_telegram(src: Path, work: Path) -> Path:
    """Telegram bots can send up to 50 MB; shrink only when a clip is bigger than that."""
    if src.stat().st_size <= MAX_UPLOAD_BYTES:
        return src
    out = work / f"preview_{src.name}"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-vf", "scale=720:-2",
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "28", "-c:a", "aac", "-b:a", "96k",
                    str(out)], check=True)
    return out


def notify_new(cfg: Config, db: DB, tg: Telegram, max_per_run: int = 10) -> int:
    sent = 0
    for clip in db.unnotified()[:max_per_run]:
        try:
            path = preview_for_telegram(ensure_local(cfg, clip), cfg.path("work_dir"))
            mid = tg.send_clip(path, caption_for(clip, cfg.streamer(clip["streamer"])), clip["id"])
            db.update_clip(clip["id"], notified_message_id=mid)
            sent += 1
        except Exception as e:
            log.error("could not send clip %s to Telegram: %s", clip["id"], e)
    return sent


def apply_decisions(cfg: Config, db: DB, tg: Telegram) -> dict:
    """Reads button taps since last time. Post -> approved, Skip -> rejected."""
    offset = db.get("telegram_offset")
    found, next_offset = decisions(tg.updates(int(offset) if offset else None), tg.chat_id)
    counts = {"approved": 0, "rejected": 0}
    for clip_id, action, callback_id, message_id in found:
        clip = db.clip(clip_id)
        if not clip or clip["status"] not in ("pending",):
            tg.answer(callback_id, "Already handled")
            continue
        status = "approved" if action == "post" else "rejected"
        db.update_clip(clip_id, status=status)
        counts[status] += 1
        tg.answer(callback_id, "Queued to post" if status == "approved" else "Skipped")
        tg.clear_buttons(message_id)
    if next_offset is not None:
        db.put("telegram_offset", next_offset)
    return counts


def auto_approve(db: DB, min_score: float) -> tuple[int, int]:
    """Hands-off mode: approve pending clips at or above min_score, drop the rest."""
    approved = dropped = 0
    for clip in db.clips("pending"):
        if (clip["score"] or 0) >= min_score:
            db.update_clip(clip["id"], status="approved")
            approved += 1
        else:
            db.update_clip(clip["id"], status="rejected")
            dropped += 1
    return approved, dropped


def in_prime_hours(windows, now=None) -> bool:
    """Prime posting windows in US Eastern time, e.g. [[12, 24]] = noon to midnight."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    h = (now or datetime.now(ZoneInfo("America/New_York"))).hour
    return any(a <= h < b for a, b in windows)


def _fillers(cfg) -> list[str]:
    return [s.login for s in cfg.streamers if getattr(s, "priority", "paid") == "filler"]


def publish_approved(cfg: Config, db: DB, tg: Telegram | None) -> list[str]:
    """Posts approved clips, respecting the per-run and per-day caps. Extra approvals wait."""
    auto = cfg.section("auto")
    budget = min(auto.get("max_uploads_per_run", 2), auto.get("max_uploads_per_day", 6) - db.uploads_today())
    lines = []
    if not publish.enabled_platforms(cfg):
        if db.clips("approved"):
            lines.append("Approved clips are waiting, but no YouTube or Instagram login is set up yet.")
        return lines
    if "instagram" in publish.enabled_platforms(cfg):
        # clips already on YouTube before Instagram was connected get posted there too
        for c in db.clips("uploaded"):
            if not c["instagram_id"] and c["artifact_run_id"]:
                db.update_clip(c["id"], status="approved")
    prime = in_prime_hours(auto.get("prime_hours_et", [[12, 24]]))
    filler_left = auto.get("max_filler_per_day", 4) - db.conn.execute(
        "SELECT COUNT(*) FROM clips c WHERE uploaded_at >= date('now') AND streamer IN (%s)" %
        ",".join("?" * len(_fillers(cfg)) or "''"), _fillers(cfg)).fetchone()[0] if _fillers(cfg) else 0
    queue = sorted(db.clips("approved"), key=lambda c: (c["streamer"] in _fillers(cfg), -(c["score"] or 0)))
    for clip in queue:
        is_filler = clip["streamer"] in _fillers(cfg)
        if not is_filler and not prime:
            lines.append(f"Clip #{clip['id']} waits for prime hours to post.")
            continue
        if is_filler:
            if filler_left <= 0:
                continue
            filler_left -= 1
        if budget <= 0:
            lines.append(f"Clip #{clip['id']} is approved and queued for the next run (daily cap reached).")
            continue
        try:
            path = ensure_local(cfg, clip)
            results = publish.publish(cfg, db, clip, path)
        except Exception as e:
            log.exception("publishing clip %s failed", clip["id"])
            db.update_clip(clip["id"], upload_error=str(e)[:500])
            results = {}
        fresh = db.clip(clip["id"])
        if results:
            budget -= 1
            links = "\n".join(f"{k}: {v}" for k, v in results.items())
            msg = f"Posted clip #{clip['id']}:\n{links}"

        else:
            msg = f"Could not post clip #{clip['id']}: {fresh['upload_error']}"
        if fresh["upload_error"] and results:
            msg += f"\nSome platforms failed: {fresh['upload_error']}"
        lines.append(msg)
        if tg:
            try:
                tg.send_message(msg, reply_to=clip["notified_message_id"])
            except Exception as e:
                log.warning("Telegram message failed: %s", e)
    return lines
