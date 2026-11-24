"""The end-to-end flow: new VOD -> chat spikes -> windows -> transcript -> judge -> Short."""

from __future__ import annotations

import logging
import os
import traceback
from pathlib import Path

from . import chat, fetch, judge, render, transcribe
from .captions import write_ass
from .config import Config, Streamer
from .db import DB
from .twitch import Twitch

log = logging.getLogger("clipmill")


def twitch_client(cfg: Config) -> Twitch:
    t = cfg.section("twitch")
    return Twitch(t.get("client_id", ""), t.get("client_secret", ""))


def scan(cfg: Config, db: DB, tw: Twitch, limit: int | None = None) -> list[tuple[Streamer, dict]]:
    """Registers recent finished VODs. A VOD of a stream that is still live is skipped.
    One streamer failing (a typo in a login, a Twitch hiccup) never stops the others."""
    limit = limit or cfg.section("detect").get("vods_per_streamer", 2)
    found = []
    for s in cfg.streamers:
        if not s.enabled:
            continue
        try:
            live = tw.is_live(s.login)
            vods = tw.vods(s.login, limit=limit)
        except Exception as e:
            log.error("could not check %s on Twitch: %s", s.login, e)
            continue
        for i, v in enumerate(vods):
            if live and i == 0:
                log.info("%s is live, skipping the in-progress VOD %s", s.login, v["id"])
                continue
            if db.upsert_vod(v, s.login):
                log.info("new VOD %s from %s: %s", v["id"], s.login, v["title"])
            found.append((s, v))
    return found


def pending_vods(cfg: Config, db: DB, tw: Twitch, max_vods: int | None = None) -> list[tuple[Streamer, dict]]:
    done = {r["id"] for r in db.vods("done")}
    todo = [(s, v) for s, v in scan(cfg, db, tw) if v["id"] not in done]
    todo.sort(key=lambda sv: sv[1].get("created_at") or "", reverse=True)  # newest first
    return todo[:max_vods] if max_vods else todo


def focus_hits(words: list[transcribe.Word], focus: list[str]) -> list[str]:
    text = " ".join(w.text.lower().strip(".,!?") for w in words)
    return [f for f in focus if f.lower() in text]


def process_vod(cfg: Config, db: DB, tw: Twitch, s: Streamer, vod: dict) -> list[int]:
    d, j, tr, r = (cfg.section(k) for k in ("detect", "judge", "transcribe", "render"))
    work = cfg.path("work_dir") / vod["id"]
    work.mkdir(parents=True, exist_ok=True)
    out_root = cfg.path("output_dir")
    out_dir = out_root / s.login
    out_dir.mkdir(parents=True, exist_ok=True)
    db.set_vod_status(vod["id"], "processing")
    in_ci = bool(os.environ.get("CI"))

    chat_file = fetch.download_chat(vod["id"], work / "chat.json")
    messages = chat.load_chat(chat_file)
    spikes = chat.find_spikes(messages, vod.get("duration_s"), d.get("bucket_seconds", 5),
                              d.get("baseline_minutes", 10), d.get("min_zscore", 3.0),
                              d.get("min_gap_seconds", 90))
    vclips = []
    if tw is not None:
        try:
            vclips = tw.viewer_clips(s.login, vod)
        except Exception as e:  # viewer clips are a bonus signal, never a blocker
            log.info("could not load viewer clips: %s", e)
    spikes = chat.boost_with_viewer_clips(spikes, vclips, d.get("viewer_clip_boost", 2.0),
                                          d.get("lead_seconds", 50))
    spikes = spikes[: d.get("max_candidates_per_vod", 12)]
    log.info("VOD %s (%s): %d chat messages, %d candidate moments", vod["id"], s.login,
             len(messages), len(spikes))

    lead, tail = d.get("lead_seconds", 50), d.get("tail_seconds", 15)
    made = []
    for n, sp in enumerate(spikes):
        w_start = max(0.0, sp.t - lead - 15)
        w_end = min(vod.get("duration_s") or sp.t + tail + 10, sp.t + tail + 10)
        window = work / f"win_{n:02d}.mp4"
        try:
            fetch.download_window(vod["url"], w_start, w_end, window)
            audio = fetch.extract_audio(window, work / f"win_{n:02d}.wav")
            words = transcribe.transcribe(audio, tr.get("model", "base"), tr.get("device", "auto"),
                                          tr.get("compute_type", "auto"))
            hits = focus_hits(words, s.focus)
            v = judge.judge(words=words, chat_sample=sp.sample, spike_rel=sp.t - w_start,
                            category=sp.category, zscore=sp.zscore, window_len=w_end - w_start,
                            streamer=s, cfg_judge=j)
            score = v.score + (1 if hits else 0)
            if not v.keep or score < j.get("min_score", 6):
                log.info("  #%d at %.0fs dropped (score %s): %s", n, sp.t, v.score, v.reason)
                continue
            out = out_dir / f"{vod['id']}_{n:02d}.mp4"
            ass = write_ass(work / f"win_{n:02d}.ass", words, v.start_s, v.end_s,
                            width=r.get("width", 1080), height=r.get("height", 1920),
                            font=r.get("font", "Arial Black"), words_per_chunk=r.get("caption_words", 2),
                            caption_y=render.caption_y(s.facecam, r.get("width", 1080), r.get("height", 1920)),
                            handle_text=s.handle_text, hook=v.hook,
                            disclosure=r.get("disclosure", ""),
                            disclosure_seconds=r.get("disclosure_seconds", 3))
            render.render_short(window, out, v.start_s, v.end_s, ass, facecam=s.facecam,
                                gameplay=s.gameplay, width=r.get("width", 1080),
                                height=r.get("height", 1920), crf=r.get("crf", 20),
                                preset=r.get("preset", "veryfast"))
            run_id = os.environ.get("GITHUB_RUN_ID")
            cid = db.add_clip(vod_id=vod["id"], streamer=s.login, start_s=w_start + v.start_s,
                              end_s=w_start + v.end_s, score=score, category=v.category,
                              title=v.title, reason=v.reason, focus_hits=hits,
                              path=str(out.relative_to(out_root)),
                              artifact_run_id=run_id, artifact_name=f"clips-{run_id}" if run_id else None,
                              meta={"zscore": sp.zscore, "chat_category": sp.category, "hook": v.hook})
            made.append(cid)
            log.info("  #%d kept as clip %d (score %d): %s", n, cid, score, v.title)
        except Exception:
            log.error("  #%d at %.0fs failed:\n%s", n, sp.t, traceback.format_exc(limit=3))
        finally:
            for f in (work / f"win_{n:02d}.wav", work / f"win_{n:02d}.ass"):
                f.unlink(missing_ok=True)
            if in_ci:
                window.unlink(missing_ok=True)  # runners have limited disk
    db.set_vod_status(vod["id"], "done")
    return made


def clip_file(cfg: Config, clip) -> Path:
    """Clips store paths relative to output_dir (older rows may hold absolute paths)."""
    p = Path(clip["path"])
    return p if p.is_absolute() else cfg.path("output_dir") / p
