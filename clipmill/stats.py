"""Tracks how each posted clip does, and turns that into examples for the judge.

Every run refreshes Instagram view counts for recent posts. The judge then sees the page's
best and worst posts, so its picks drift toward what this audience actually watches.
"""

from __future__ import annotations

import logging
import time

log = logging.getLogger("clipmill")


def refresh_instagram_views(cfg, db, max_clips: int = 40) -> int:
    from .instagram import Instagram, live_token

    ig_cfg = cfg.section("instagram")
    token = ig_cfg.get("access_token")
    if not token:
        return 0
    rows = db.conn.execute(
        "SELECT id, instagram_id FROM clips WHERE instagram_id IS NOT NULL "
        "AND (uploaded_at IS NULL OR uploaded_at >= datetime('now','-21 days')) "
        "ORDER BY id DESC LIMIT ?", (max_clips,)).fetchall()
    if not rows:
        return 0
    try:
        ig = Instagram(live_token(db, token), ig_cfg.get("user_id", ""),
                       ig_cfg.get("api_host", "https://graph.instagram.com"), ig_cfg.get("api_version", "v23.0"))
    except Exception as e:
        log.info("view refresh skipped: %s", e)
        return 0
    n = 0
    for r in rows:
        views = ig.views(r["instagram_id"])
        if views is not None:
            db.update_clip(r["id"], ig_views=views)
            n += 1
    db.put("views_checked_at", time.time())
    return n


def refresh_youtube_views(cfg, db, max_clips: int = 50) -> int:
    from . import youtube
    rows = db.conn.execute(
        "SELECT id, youtube_id FROM clips WHERE youtube_id IS NOT NULL ORDER BY id DESC LIMIT ?",
        (max_clips,)).fetchall()
    yt_cfg = cfg.section("youtube")
    token_file = cfg.root / yt_cfg.get("token_file", "youtube_token.json")
    if not rows or not youtube.available(token_file):
        return 0
    try:
        yt = youtube.client(cfg.root / yt_cfg.get("client_secrets", "client_secret.json"), token_file)
        counts = youtube.view_counts(yt, [r["youtube_id"] for r in rows])
    except Exception as e:
        log.info("YouTube view refresh skipped: %s", e)
        return 0
    for r in rows:
        if r["youtube_id"] in counts:
            db.update_clip(r["id"], yt_views=counts[r["youtube_id"]])
    return len(counts)


def refresh_all(cfg, db) -> int:
    n = refresh_instagram_views(cfg, db) + refresh_youtube_views(cfg, db)
    db.conn.execute("UPDATE clips SET views = COALESCE(ig_views, 0) + COALESCE(yt_views, 0) "
                    "WHERE ig_views IS NOT NULL OR yt_views IS NOT NULL")
    db.conn.commit()
    return n


def judge_history(db, streamer: str | None = None, best: int = 3, worst: int = 2) -> str:
    """A few past posts with their views, for the judge prompt. Empty until there is data."""
    rows = db.conn.execute(
        "SELECT streamer, category, title, meta, views FROM clips WHERE views IS NOT NULL "
        "AND uploaded_at <= datetime('now','-2 days') ORDER BY views DESC").fetchall()
    if len(rows) < best + worst:
        return ""
    def line(r):
        import json
        hook = (json.loads(r["meta"] or "{}") or {}).get("hook", "")
        return f'- {r["views"]:,} views: "{r["title"]}" (hook: "{hook}", {r["category"]}, {r["streamer"]})'
    top = [line(r) for r in rows[:best]]
    low = [line(r) for r in rows[-worst:]]
    return ("Past posts on this page, two or more days old, with their views. Learn what this "
            "audience likes:\nBest:\n" + "\n".join(top) + "\nWorst:\n" + "\n".join(low))


def report(db, limit: int = 5) -> list[str]:
    rows = db.conn.execute(
        "SELECT streamer, COUNT(*) n, SUM(views) v, MAX(views) top FROM clips "
        "WHERE views IS NOT NULL GROUP BY streamer ORDER BY v DESC").fetchall()
    return [f"{r['streamer']}: {r['n']} posts, {r['v'] or 0:,} views total, best {r['top'] or 0:,}"
            for r in rows[:limit]]
