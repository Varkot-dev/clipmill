"""Command line entry point: `clipmill <command>`."""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import sys
from pathlib import Path

from . import config as config_mod
from .db import DB

log = logging.getLogger("clipmill")


def _ctx(args):
    cfg = config_mod.load(args.config)
    return cfg, DB(cfg.path("db"))


def _summary(lines: list[str], title: str) -> None:
    """Prints a run report and adds it to the GitHub Actions run page when running there."""
    text = f"## {title}\n\n" + ("\n".join(f"- {l}" for l in lines) if lines else "- Nothing new.")
    print(text)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as f:
            f.write(text + "\n\n")


def cmd_doctor(args):
    """Checks every tool and login the pipeline needs."""
    ok = True
    for tool, why in [("ffmpeg", "cutting and rendering"), ("yt-dlp", "downloading VOD windows"),
                      ("TwitchDownloaderCLI", "downloading chat replays"), ("claude", "judging moments")]:
        found = shutil.which(tool)
        print(f"{'OK     ' if found else 'MISSING'}  {tool:20} {why}")
        ok &= bool(found)
    try:
        import faster_whisper  # noqa: F401
        print("OK       faster-whisper       transcription")
    except ImportError:
        print("MISSING  faster-whisper       transcription (pip install faster-whisper)")
        ok = False
    try:
        cfg = config_mod.load(args.config)
        t = cfg.section("twitch")
        print(f"{'OK     ' if t.get('client_id') and t.get('client_secret') else 'MISSING'}  twitch keys          config.toml [twitch] or TWITCH_* secrets")
        print(f"OK       streamers            {', '.join(s.login for s in cfg.streamers if s.enabled) or 'none'}")
        from . import publish
        plats = publish.enabled_platforms(cfg)
        print(f"{'OK     ' if plats else 'LATER  '}  posting to           {', '.join(plats) or 'nothing yet (youtube-login / Instagram token)'}")
        from .review_flow import telegram_from_env
        print(f"{'OK     ' if telegram_from_env(cfg) else 'LATER  '}  phone review         Telegram bot (TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)")
    except FileNotFoundError as e:
        print(f"MISSING  config               {e}")
        ok = False
    sys.exit(0 if ok else 1)


def cmd_scan(args):
    from .pipeline import scan, twitch_client

    cfg, db = _ctx(args)
    for s, v in scan(cfg, db, twitch_client(cfg), limit=args.limit):
        print(f"{s.login:20} {v['id']:12} {v['duration_s'] / 3600:4.1f}h  {v['title'][:60]}")


def _process(cfg, db, args) -> list[str]:
    from .pipeline import pending_vods, process_vod, twitch_client

    tw = twitch_client(cfg)
    lines = []
    if args.vod:
        from .pipeline import scan
        targets = [(s, v) for s, v in scan(cfg, db, tw) if v["id"] == args.vod]
    else:
        targets = pending_vods(cfg, db, tw, max_vods=args.max_vods)
    for s, v in targets:
        try:
            made = process_vod(cfg, db, tw, s, v)
            lines.append(f"{s.display}: '{v['title'][:60]}' -> {len(made)} clip(s) ready")
        except Exception as e:
            db.set_vod_status(v["id"], "failed", str(e)[:500])
            log.exception("VOD %s failed", v["id"])
            lines.append(f"{s.display}: VOD {v['id']} failed: {str(e)[:200]}")
    return lines


def cmd_process(args):
    cfg, db = _ctx(args)
    _summary(_process(cfg, db, args), "clipmill: new clips")


def cmd_run(args):
    """Cloud entry point: make clips from new VODs, then send them to your phone for review."""
    from .review_flow import notify_new, telegram_from_env

    cfg, db = _ctx(args)
    lines = _process(cfg, db, args)
    tg = telegram_from_env(cfg)
    if tg:
        sent = notify_new(cfg, db, tg)
        if sent:
            lines.append(f"Sent {sent} clip(s) to Telegram for review")
    else:
        pending = len(db.clips("pending"))
        if pending:
            lines.append(f"{pending} clip(s) waiting; set up the Telegram bot to review them on your phone")
    _summary(lines, "clipmill run")


def cmd_approvals(args):
    """Collects Post / Skip taps from Telegram and posts the approved clips."""
    from .review_flow import apply_decisions, notify_new, publish_approved, telegram_from_env

    cfg, db = _ctx(args)
    tg = telegram_from_env(cfg)
    lines = []
    if tg:
        counts = apply_decisions(cfg, db, tg)
        if counts["approved"] or counts["rejected"]:
            lines.append(f"{counts['approved']} approved, {counts['rejected']} skipped since last check")
    lines += publish_approved(cfg, db, tg)
    if tg:
        notify_new(cfg, db, tg)  # retry anything that failed to send earlier
    _summary(lines, "clipmill approvals")


def cmd_review(args):
    from .review import serve

    cfg, db = _ctx(args)
    serve(db, args.port, cfg.path("output_dir"))


def cmd_upload(args):
    """Local fallback: posts clips you approved on the local review page."""
    from .review_flow import publish_approved

    cfg, db = _ctx(args)
    _summary(publish_approved(cfg, db, None), "clipmill upload")


def cmd_stats(args):
    from . import youtube

    cfg, db = _ctx(args)
    uploaded = [c for c in db.clips("uploaded") if c["youtube_id"]]
    if not uploaded:
        print("nothing uploaded to YouTube yet")
        return
    yt_cfg = cfg.section("youtube")
    yt = youtube.client(cfg.root / yt_cfg.get("client_secrets", "client_secret.json"),
                        cfg.root / yt_cfg.get("token_file", "youtube_token.json"))
    views = youtube.view_counts(yt, [c["youtube_id"] for c in uploaded])
    by_cat: dict[str, list[int]] = {}
    for c in uploaded:
        v = views.get(c["youtube_id"], 0)
        db.update_clip(c["id"], views=v)
        by_cat.setdefault(c["category"] or "other", []).append(v)
        print(f"{v:>9,} views  {c['streamer']:16} {c['category'] or '':9} {(c['title'] or '')[:50]}")
    print("\nAverage views by kind of moment:")
    for cat, vs in sorted(by_cat.items(), key=lambda kv: -sum(kv[1]) / len(kv[1])):
        print(f"  {cat:10} {sum(vs) / len(vs):>10,.0f}  ({len(vs)} clips)")


def cmd_compile(args):
    from .pipeline import clip_file
    from .render import compile_long

    cfg, db = _ctx(args)
    rows = [c for c in db.clips() if c["status"] in ("approved", "uploaded")
            and (not args.streamer or c["streamer"] == args.streamer)
            and clip_file(cfg, c).exists()][: args.count]
    out = cfg.path("output_dir") / (args.out or "compilation.mp4")
    compile_long([clip_file(cfg, c) for c in rows], out)
    print(f"wrote {out} from {len(rows)} clips")


def cmd_try(args):
    """Test run on one real VOD without Twitch keys: newest VOD of a streamer (or --url)."""
    from . import fetch
    from .pipeline import process_vod

    cfg, db = _ctx(args)
    s = cfg.streamer(args.streamer) if args.streamer else cfg.streamers[0]
    vod = fetch.vod_info(args.url) if args.url else fetch.latest_vod(s.login)
    print(f"Testing {s.display}: '{vod['title']}' ({vod['duration_s'] / 3600:.1f}h, VOD {vod['id']})")
    cfg.raw.setdefault("detect", {})["max_candidates_per_vod"] = args.max_candidates
    db.upsert_vod(vod, s.login)
    made = process_vod(cfg, db, None, s, vod)
    lines = [f"{s.display}: VOD {vod['id']} -> {len(made)} clip(s)"]
    for cid in made:
        c = db.clip(cid)
        lines.append(f"clip #{cid} score {c['score']:.0f} '{c['title']}' -> output/{c['path']}")
    _summary(lines, "clipmill try")


def cmd_youtube_login(args):
    from . import youtube

    cfg = config_mod.load(args.config)
    yt = cfg.section("youtube")
    token = cfg.root / yt.get("token_file", "youtube_token.json")
    youtube.login(cfg.root / yt.get("client_secrets", "client_secret.json"), token)
    print(f"Saved YouTube login to {token}. Next: scripts/set_github_secrets.sh")


def cmd_telegram_chat_id(args):
    from .telegram import find_chat_id

    token = args.token or os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        sys.exit("pass --token or set TELEGRAM_BOT_TOKEN")
    chats = find_chat_id(token)
    if not chats:
        print("No messages yet. Open your bot in Telegram, tap Start (or send it 'hi'), then run this again.")
    for cid, name in chats:
        print(f"chat id {cid}  ({name})")


def cmd_instagram_check(args):
    from .instagram import Instagram

    cfg = config_mod.load(args.config)
    ig = cfg.section("instagram")
    me = Instagram(ig.get("access_token", ""), ig.get("user_id", ""), ig.get("api_host", "https://graph.instagram.com"),
                   ig.get("api_version", "v23.0")).me()
    print(f"Instagram login works: @{me.get('username')} (user id {me.get('user_id')})")


def cmd_instagram_refresh(args):
    from .instagram import refresh_token

    cfg = config_mod.load(args.config)
    data = refresh_token(cfg.section("instagram").get("access_token", ""))
    days = int(data.get("expires_in", 0)) // 86400
    print(f"Token refreshed, valid for about {days} more days. Re-run scripts/set_github_secrets.sh "
          "if the token text changed.")


def main(argv=None):
    p = argparse.ArgumentParser(prog="clipmill", description="Stream clipping pipeline")
    p.add_argument("--config", default="config.toml", help="local secrets/overrides file (optional)")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("doctor", help="check tools, keys and logins").set_defaults(fn=cmd_doctor)
    s = sub.add_parser("scan", help="list recent VODs for every streamer")
    s.add_argument("--limit", type=int)
    s.set_defaults(fn=cmd_scan)
    for name, fn, helptext in [("process", cmd_process, "make clips from new VODs"),
                               ("run", cmd_run, "make clips and send them to your phone (cloud entry point)")]:
        s = sub.add_parser(name, help=helptext)
        s.add_argument("--vod", help="only this VOD id")
        s.add_argument("--max-vods", type=int, default=3, help="VODs to process per run")
        s.set_defaults(fn=fn)
    s = sub.add_parser("try", help="test the whole pipeline on one real VOD (no Twitch keys needed)")
    s.add_argument("--streamer", help="login from clipmill.toml (default: the first one)")
    s.add_argument("--url", help="a specific https://www.twitch.tv/videos/... URL")
    s.add_argument("--max-candidates", type=int, default=3)
    s.set_defaults(fn=cmd_try)
    sub.add_parser("approvals", help="apply Telegram Post/Skip taps and post approved clips").set_defaults(fn=cmd_approvals)
    s = sub.add_parser("review", help="local approve/reject web page")
    s.add_argument("--port", type=int, default=8787)
    s.set_defaults(fn=cmd_review)
    sub.add_parser("upload", help="post clips approved on the local review page").set_defaults(fn=cmd_upload)
    sub.add_parser("stats", help="pull YouTube view counts and compare kinds of moments").set_defaults(fn=cmd_stats)
    s = sub.add_parser("compile", help="join approved clips into one longer video")
    s.add_argument("--streamer")
    s.add_argument("--count", type=int, default=12)
    s.add_argument("--out")
    s.set_defaults(fn=cmd_compile)
    sub.add_parser("youtube-login", help="one-time YouTube login in your browser").set_defaults(fn=cmd_youtube_login)
    s = sub.add_parser("telegram-chat-id", help="find your Telegram chat id after messaging your bot")
    s.add_argument("--token")
    s.set_defaults(fn=cmd_telegram_chat_id)
    sub.add_parser("instagram-check", help="test the Instagram token").set_defaults(fn=cmd_instagram_check)
    sub.add_parser("instagram-refresh", help="extend the Instagram token by ~60 days").set_defaults(fn=cmd_instagram_refresh)

    args = p.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    args.fn(args)


if __name__ == "__main__":
    main()
