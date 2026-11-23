"""The end-to-end flow: new VOD -> chat spikes -> windows -> transcript -> judge -> Short."""

from __future__ import annotations

import logging
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


def scan(cfg: Config, db: DB, tw: Twitch, limit: int = 3) -> list[tuple[Streamer, dict]]:
    """Registers recent finished VODs. A VOD of a stream that is still live is skipped."""
    found = []
    for s in cfg.streamers:
        live = tw.is_live(s.login)
        vods = tw.vods(s.login, limit=limit)
        for i, v in enumerate(vods):
            if live and i == 0:
                log.info("%s is live, skipping the in-progress VOD %s", s.login, v["id"])
                continue
            if db.upsert_vod(v, s.login):
                log.info("new VOD %s from %s: %s", v["id"], s.login, v["title"])
            found.append((s, v))
    return found


def focus_hits(words: list[transcribe.Word], focus: list[str]) -> list[str]:
    text = " ".join(w.text.lower().strip(".,!?") for w in words)
    return [f for f in focus if f.lower() in text]


def description(s: Streamer, title: str) -> str:
    lines = [title, "", f"Streamer: {s.display} ({s.handle_text})",
             f"Watch the full stream at {s.handle_text}", ""]
    tag = "#GTA6" if "6" in s.game else "#GTARP"
    lines.append(f"{tag} #{s.display.replace(' ', '')} #shorts")
    return "\n".join(lines)


def process_vod(cfg: Config, db: DB, tw: Twitch, s: Streamer, vod: dict) -> int:
    d, j, tr, r = (cfg.section(k) for k in ("detect", "judge", "transcribe", "render"))
    work = cfg.path("work_dir") / vod["id"]
    work.mkdir(parents=True, exist_ok=True)
    out_dir = cfg.path("output_dir") / s.login
    out_dir.mkdir(parents=True, exist_ok=True)
    db.set_vod_status(vod["id"], "processing")

    chat_file = fetch.download_chat(vod["id"], work / "chat.json")
    messages = chat.load_chat(chat_file)
    spikes = chat.find_spikes(messages, vod.get("duration_s"), d.get("bucket_seconds", 5),
                              d.get("baseline_minutes", 10), d.get("min_zscore", 3.0),
                              d.get("min_gap_seconds", 90))
    try:
        vclips = tw.viewer_clips(s.login, vod)
    except Exception as e:  # viewer clips are a bonus signal, never a blocker
        log.warning("could not load viewer clips: %s", e)
        vclips = []
    spikes = chat.boost_with_viewer_clips(spikes, vclips, d.get("viewer_clip_boost", 2.0),
                                          d.get("lead_seconds", 50))
    spikes = spikes[: d.get("max_candidates_per_vod", 25)]
    log.info("VOD %s: %d chat messages, %d candidate moments", vod["id"], len(messages), len(spikes))

    lead, tail = d.get("lead_seconds", 50), d.get("tail_seconds", 15)
    made = 0
    for n, sp in enumerate(spikes):
        w_start = max(0.0, sp.t - lead - 15)
        w_end = min(vod.get("duration_s") or sp.t + tail + 10, sp.t + tail + 10)
        try:
            window = fetch.download_window(vod["url"], w_start, w_end, work / f"win_{n:02d}.mp4")
            audio = fetch.extract_audio(window, work / f"win_{n:02d}.wav")
            words = transcribe.transcribe(audio, tr.get("model", "small"), tr.get("device", "auto"),
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
                            font=r.get("font", "Arial Black"), words_per_chunk=r.get("caption_words", 3),
                            caption_y=render.caption_y(s.facecam, r.get("width", 1080), r.get("height", 1920)),
                            handle_text=s.handle_text)
            render.render_short(window, out, v.start_s, v.end_s, ass, facecam=s.facecam,
                                gameplay=s.gameplay, width=r.get("width", 1080),
                                height=r.get("height", 1920), crf=r.get("crf", 20),
                                preset=r.get("preset", "veryfast"))
            db.add_clip(vod_id=vod["id"], streamer=s.login, start_s=w_start + v.start_s,
                        end_s=w_start + v.end_s, score=score, category=v.category,
                        title=v.title, reason=v.reason, focus_hits=hits, path=str(out),
                        meta={"zscore": sp.zscore, "chat_category": sp.category})
            made += 1
            log.info("  #%d kept (score %d): %s", n, score, v.title)
        except Exception:
            log.error("  #%d at %.0fs failed:\n%s", n, sp.t, traceback.format_exc(limit=3))
        finally:
            for ext in (".wav",):
                Path(work / f"win_{n:02d}{ext}").unlink(missing_ok=True)
    db.set_vod_status(vod["id"], "done")
    return made
