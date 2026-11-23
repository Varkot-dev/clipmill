"""Command line entry point: `clipmill <command>`."""

from __future__ import annotations

import argparse
import logging
import shutil
import sys
from pathlib import Path

from . import config as config_mod
from .db import DB


def _ctx(args):
    cfg = config_mod.load(args.config)
    return cfg, DB(cfg.path("db"))


def cmd_doctor(args):
    """Checks every tool and login the pipeline needs."""
    ok = True
    for tool, why in [("ffmpeg", "cutting and rendering"), ("yt-dlp", "downloading VOD windows"),
                      ("TwitchDownloaderCLI", "downloading chat replays"), ("claude", "judging moments")]:
        found = shutil.which(tool)
        print(f"{'OK ' if found else 'MISSING'}  {tool:20} {why}")
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
        print(f"{'OK ' if t.get('client_id') else 'MISSING'}  twitch app keys      config.toml [twitch]")
        print(f"OK       streamers            {', '.join(s.login for s in cfg.streamers) or 'none yet'}")
        yt = cfg.section("youtube")
        secrets = cfg.root / yt.get("client_secrets", "client_secret.json")
        print(f"{'OK ' if secrets.exists() else 'LATER  '}  youtube oauth        {secrets.name} (only needed to upload)")
    except FileNotFoundError as e:
        print(f"MISSING  config.toml          {e}")
        ok = False
    sys.exit(0 if ok else 1)


def cmd_scan(args):
    from .pipeline import scan, twitch_client

    cfg, db = _ctx(args)
    for s, v in scan(cfg, db, twitch_client(cfg), limit=args.limit):
        print(f"{s.login:20} {v['id']:12} {v['duration_s'] / 3600:4.1f}h  {v['title'][:60]}")


def cmd_process(args):
    from .pipeline import process_vod, scan, twitch_client

    cfg, db = _ctx(args)
    tw = twitch_client(cfg)
    targets = scan(cfg, db, tw, limit=args.limit)
    done = {r["id"] for r in db.vods("done")}
    for s, v in targets:
        if args.vod and v["id"] != args.vod:
            continue
        if v["id"] in done and not args.force:
            continue
        try:
            made = process_vod(cfg, db, tw, s, v)
            print(f"{v['id']}: {made} clips ready for review")
        except Exception as e:
            db.set_vod_status(v["id"], "failed", str(e))
            logging.exception("VOD %s failed", v["id"])


def cmd_review(args):
    from .review import serve

    _, db = _ctx(args)
    serve(db, args.port)


def cmd_upload(args):
    from . import youtube
    from .pipeline import description

    cfg, db = _ctx(args)
    yt_cfg = cfg.section("youtube")
    yt = youtube.client(cfg.root / yt_cfg.get("client_secrets", "client_secret.json"),
                        cfg.root / yt_cfg.get("token_file", "youtube_token.json"))
    for c in db.clips("approved")[: args.max]:
        s = cfg.streamer(c["streamer"])
        title = youtube.short_title(c["title"])
        vid = youtube.upload(yt, Path(c["path"]), title, description(s, c["title"]),
                             yt_cfg.get("default_tags", []) + [s.display],
                             category_id=yt_cfg.get("category_id", "20"),
                             privacy=args.privacy or yt_cfg.get("privacy", "private"))
        db.update_clip(c["id"], status="uploaded", youtube_id=vid)
        print(f"uploaded clip {c['id']}: https://youtube.com/shorts/{vid}  ({s.campaign or 'no campaign'})")


def cmd_stats(args):
    from . import youtube

    cfg, db = _ctx(args)
    uploaded = db.clips("uploaded")
    if not uploaded:
        print("nothing uploaded yet")
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
        print(f"{v:>9,} views  {c['streamer']:16} {c['category'] or '':9} {c['title'][:50]}")
    print("\nAverage views by kind of moment:")
    for cat, vs in sorted(by_cat.items(), key=lambda kv: -sum(kv[1]) / len(kv[1])):
        print(f"  {cat:10} {sum(vs) / len(vs):>10,.0f}  ({len(vs)} clips)")


def cmd_compile(args):
    from .render import compile_long

    cfg, db = _ctx(args)
    rows = [c for c in db.clips() if c["status"] in ("approved", "uploaded")
            and (not args.streamer or c["streamer"] == args.streamer)][: args.count]
    out = cfg.path("output_dir") / (args.out or "compilation.mp4")
    compile_long([Path(c["path"]) for c in rows], out)
    print(f"wrote {out} from {len(rows)} clips")


def main(argv=None):
    p = argparse.ArgumentParser(prog="clipmill", description="Stream clipping pipeline")
    p.add_argument("--config", default="config.toml")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("doctor", help="check tools and config").set_defaults(fn=cmd_doctor)
    s = sub.add_parser("scan", help="list recent VODs for every streamer")
    s.add_argument("--limit", type=int, default=3)
    s.set_defaults(fn=cmd_scan)
    s = sub.add_parser("process", help="find moments in new VODs and render clips")
    s.add_argument("--vod", help="only this VOD id")
    s.add_argument("--limit", type=int, default=3)
    s.add_argument("--force", action="store_true", help="re-process VODs already done")
    s.set_defaults(fn=cmd_process)
    s = sub.add_parser("review", help="open the approve/reject page")
    s.add_argument("--port", type=int, default=8787)
    s.set_defaults(fn=cmd_review)
    s = sub.add_parser("upload", help="upload approved clips to YouTube")
    s.add_argument("--max", type=int, default=5)
    s.add_argument("--privacy", choices=["private", "unlisted", "public"])
    s.set_defaults(fn=cmd_upload)
    sub.add_parser("stats", help="pull view counts and compare kinds of moments").set_defaults(fn=cmd_stats)
    s = sub.add_parser("compile", help="join approved clips into one longer video")
    s.add_argument("--streamer")
    s.add_argument("--count", type=int, default=12)
    s.add_argument("--out")
    s.set_defaults(fn=cmd_compile)

    args = p.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    args.fn(args)


if __name__ == "__main__":
    main()
