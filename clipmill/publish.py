"""Posts one approved clip to every platform that has a login set up."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from . import youtube
from .config import Config, Streamer
from .db import DB

log = logging.getLogger("clipmill")


def _tags(*tags: str) -> str:
    seen, out = set(), []
    for t in tags:
        if t and t.lower() not in seen:
            seen.add(t.lower())
            out.append(t)
    return " ".join(out)


def description(s: Streamer, title: str) -> str:
    tag = "#GTA6" if "6" in s.game else "#GTARP"
    return "\n".join([
        title, "",
        "#ad Paid clip.",
        f"Streamer: {s.display} ({s.handle_text})",
        f"Watch the full stream at {s.handle_text}", "",
        _tags(tag, f"#{s.display.replace(' ', '')}", *s.hashtags, "#shorts"),
    ])


def instagram_caption(s: Streamer, title: str) -> str:
    tag = "#gta6 #gtavi" if "6" in s.game else "#gtarp"
    tags = _tags(*tag.split(), "#gaming", "#twitch", *s.hashtags, f"#{s.display.replace(' ', '').lower()}",
                 "#gtaclips", "#funnymoments")
    return (f"#ad {title}\n\nStreamer: {s.display} ({s.handle_text})\n"
            f"Follow for new GTA 6 moments every day\n\n{tags}")


def enabled_platforms(cfg: Config) -> list[str]:
    wanted = cfg.section("auto").get("platforms", ["youtube", "instagram"])
    yt = cfg.section("youtube")
    out = []
    if "youtube" in wanted and youtube.available(cfg.root / yt.get("token_file", "youtube_token.json")):
        out.append("youtube")
    if "instagram" in wanted and cfg.section("instagram").get("access_token"):
        out.append("instagram")
    return out


def publish(cfg: Config, db: DB, clip, path: Path) -> dict[str, str]:
    """Returns {platform: url}. Platforms already done for this clip are skipped, so a retry
    after one platform failed never double-posts on the other."""
    s = cfg.streamer(clip["streamer"])
    title = (clip["title"] or f"{s.display} GTA moment").strip()
    results, errors = {}, []

    if "youtube" in enabled_platforms(cfg) and not clip["youtube_id"]:
        try:
            yt_cfg = cfg.section("youtube")
            yt = youtube.client(cfg.root / yt_cfg.get("client_secrets", "client_secret.json"),
                                cfg.root / yt_cfg.get("token_file", "youtube_token.json"))
            vid = youtube.upload(yt, path, youtube.short_title(title), description(s, title),
                                 yt_cfg.get("default_tags", []) + [s.display],
                                 category_id=yt_cfg.get("category_id", "20"),
                                 privacy=yt_cfg.get("privacy", "public"))
            db.update_clip(clip["id"], youtube_id=vid)
            results["youtube"] = f"https://youtube.com/shorts/{vid}"
            note = submit_to_campaign(cfg, db, clip["id"], s, "youtube", results["youtube"])
            if note:
                results["content rewards (YouTube)"] = note
        except Exception as e:
            log.exception("YouTube upload failed for clip %s", clip["id"])
            errors.append(f"YouTube: {e}")
    elif clip["youtube_id"]:
        results["youtube"] = f"https://youtube.com/shorts/{clip['youtube_id']}"

    if "instagram" in enabled_platforms(cfg) and not clip["instagram_id"]:
        try:
            from .instagram import Instagram, live_token

            ig_cfg = cfg.section("instagram")
            ig = Instagram(live_token(db, ig_cfg["access_token"]), ig_cfg.get("user_id", ""),
                           ig_cfg.get("api_host", "https://graph.instagram.com"),
                           ig_cfg.get("api_version", "v23.0"))
            mid = ig.publish_reel(path, instagram_caption(s, title), ig_cfg.get("share_to_feed", True))
            link = ig.permalink(mid)
            db.update_clip(clip["id"], instagram_id=mid, instagram_url=link or None)
            results["instagram"] = link or f"instagram media {mid}"
            if link:
                note = submit_to_campaign(cfg, db, clip["id"], s, "instagram", link)
                if note:
                    results["content rewards"] = note
        except Exception as e:
            log.exception("Instagram upload failed for clip %s", clip["id"])
            errors.append(f"Instagram: {e}")
    elif clip["instagram_id"]:
        results["instagram"] = f"instagram media {clip['instagram_id']}"

    if results:
        db.update_clip(clip["id"], status="uploaded",
                       uploaded_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                       upload_error="; ".join(errors) or None)
    else:
        db.update_clip(clip["id"], upload_error="; ".join(errors) or "no platform is set up")
    return results


def submit_to_campaign(cfg: Config, db: DB, clip_id: int, s: Streamer, platform: str, post_url: str) -> str:
    """Submits a just-published post to the streamer's Content Rewards campaign (must happen
    within 30 minutes of posting). Returns a short note for the report, or "" if not set up."""
    cr = cfg.section("contentrewards")
    if not cr.get("submit", False) or platform not in cr.get("platforms", ["instagram"]) or not s.campaign_url:
        return ""
    from . import contentrewards

    state = contentrewards.state_from_env_or_file(cfg.root / cr.get("state_file", "cr_state.json"))
    if not state:
        db.update_clip(clip_id, cr_error="no Content Rewards login saved (clipmill cr-login)")
        return "not submitted: Content Rewards login not set up (run clipmill cr-login)"
    try:
        contentrewards.submit(s.campaign_url, post_url, state, cfg.path("work_dir") / "screenshots")
        db.update_clip(clip_id, cr_submitted=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                       cr_error=None)
        return f"submitted to {s.display}'s campaign"
    except Exception as e:
        log.exception("Content Rewards submission failed for clip %s", clip_id)
        db.update_clip(clip_id, cr_error=str(e)[:500])
        return f"NOT submitted ({str(e)[:150]}). Submit by hand within 30 min: {s.campaign_url}"
