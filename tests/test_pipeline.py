import json
import os
import stat
import subprocess
import threading
import urllib.request
from pathlib import Path

import pytest

from clipmill import captions, chat, judge, render
from clipmill.config import Streamer
from clipmill.db import DB
from clipmill.transcribe import Word, as_timed_text
from clipmill.twitch import parse_duration


def synthetic_chat(spikes_at=(600, 1500), duration=3600, seed=0):
    import random

    rng = random.Random(seed)
    msgs = [chat.Message(rng.uniform(0, duration), rng.choice(["hi", "nice", "chat is slow", "lol"]))
            for _ in range(duration // 4)]
    for t in spikes_at:
        msgs += [chat.Message(t + rng.uniform(0, 6), "KEKW OMEGALUL") for _ in range(80)]
    return msgs


def test_parse_duration():
    assert parse_duration("3h8m33s") == 11313
    assert parse_duration("45s") == 45


def test_find_spikes_locates_bursts():
    spikes = chat.find_spikes(synthetic_chat(), 3600)
    times = sorted(round(s.t) for s in spikes)
    assert any(abs(t - 603) < 10 for t in times)
    assert any(abs(t - 1503) < 10 for t in times)
    assert all(s.category == "funny" for s in spikes if abs(s.t - 603) < 10)


def test_quiet_chat_has_no_spikes():
    msgs = [chat.Message(t, "hello") for t in range(0, 3600, 3)]
    assert chat.find_spikes(msgs, 3600) == []


def test_viewer_clip_boost_reorders():
    spikes = [chat.Spike(t=100, zscore=5, rate=1, category="funny", score=5),
              chat.Spike(t=900, zscore=4, rate=1, category="funny", score=4)]
    out = chat.boost_with_viewer_clips(spikes, [{"offset_s": 870, "views": 5000}], boost=2)
    assert out[0].t == 900


def test_load_twitchdownloader_format(tmp_path):
    f = tmp_path / "chat.json"
    f.write_text(json.dumps({"comments": [
        {"content_offset_seconds": 12.5, "message": {"body": "KEKW"}},
        {"content_offset_seconds": 13, "message": {"fragments": [{"text": "W "}, {"text": "W"}]}},
    ]}))
    msgs = chat.load_chat(f)
    assert [m.t for m in msgs] == [12.5, 13.0]
    assert msgs[1].text == "W W"


WORDS = [Word(i * 0.5, i * 0.5 + 0.4, f"w{i}") for i in range(20)] + \
        [Word(12 + i * 0.5, 12 + i * 0.5 + 0.4, f"x{i}") for i in range(20)]


def test_snap_to_words_moves_into_pauses():
    s, e = judge.snap_to_words(WORDS, 1.3, 21.0)
    assert s == pytest.approx(0 - 0.15, abs=1e-6) or s == 0.0
    assert e == pytest.approx(21.9 + 0.4)


def test_ass_captions_shift_to_clip_time():
    ass = captions.build_ass(WORDS, clip_start=12, clip_end=20, handle_text="twitch.tv/x")
    lines = [l for l in ass.splitlines() if l.startswith("Dialogue")]
    assert lines[0].startswith("Dialogue: 0,0:00:00.00")
    assert "X0" in lines[0] and "w19" not in ass.lower()
    assert lines[-1].endswith("twitch.tv/x")


def test_timed_text():
    assert as_timed_text(WORDS[:3]).startswith("[00:00.0] w0 w1 w2")


def test_parse_claude_output_shapes():
    payload = {"keep": True, "score": 8}
    assert judge.parse_claude_output(json.dumps({"structured_output": payload})) == payload
    assert judge.parse_claude_output(json.dumps([{"type": "system"},
                                                 {"type": "result", "structured_output": payload}])) == payload
    with pytest.raises(RuntimeError):
        judge.parse_claude_output(json.dumps({"result": "no"}))


def test_judge_with_fake_claude(tmp_path, monkeypatch):
    fake = tmp_path / "claude"
    out = {"keep": True, "score": 9, "start_s": 1.0, "end_s": 21.5, "title": "He did not see that coming",
           "category": "funny", "spoiler_risk": False, "reason": "clean setup and payoff"}
    fake.write_text("#!/bin/sh\ncat > /dev/null\necho '" + json.dumps({"structured_output": out}) + "'\n")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ['PATH']}")
    s = Streamer(login="x", display="X", handle_text="twitch.tv/x", focus=["Lucia"])
    v = judge.judge(words=WORDS, chat_sample=["KEKW"], spike_rel=20, category="funny", zscore=6,
                    window_len=40, streamer=s, cfg_judge={"mode": "claude"})
    assert v.keep and v.score == 9 and v.title.startswith("He did")

    out["spoiler_risk"] = True
    fake.write_text("#!/bin/sh\ncat > /dev/null\necho '" + json.dumps({"structured_output": out}) + "'\n")
    v = judge.judge(words=WORDS, chat_sample=[], spike_rel=20, category="funny", zscore=6,
                    window_len=40, streamer=s, cfg_judge={"mode": "claude"})
    assert v.keep is False  # story spoilers are always dropped


@pytest.fixture(scope="module")
def sample_video(tmp_path_factory):
    p = tmp_path_factory.mktemp("media") / "src.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30",
                    "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000", "-t", "30",
                    "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(p)], check=True)
    return p


def _probe(p):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height:format=duration",
                          "-of", "json", str(p)], capture_output=True, text=True, check=True).stdout
    return json.loads(out)


@pytest.mark.parametrize("facecam", [[0.72, 0.68, 0.27, 0.30], []])
def test_render_vertical_with_captions(tmp_path, sample_video, facecam):
    ass = captions.write_ass(tmp_path / "c.ass", WORDS, 2, 14, handle_text="twitch.tv/x",
                             caption_y=render.caption_y(facecam))
    out = render.render_short(sample_video, tmp_path / "out.mp4", 2, 14, ass, facecam=facecam,
                              gameplay=[0.12, 0, 0.76, 1], preset="ultrafast", crf=30)
    info = _probe(out)
    video = next(s for s in info["streams"] if s["codec_type"] == "video")
    assert (video["width"], video["height"]) == (1080, 1920)
    assert any(s["codec_type"] == "audio" for s in info["streams"])
    assert float(info["format"]["duration"]) == pytest.approx(12, abs=0.5)


def test_compile(tmp_path, sample_video):
    a = render.render_short(sample_video, tmp_path / "a.mp4", 0, 4, None, facecam=[], gameplay=[0, 0, 1, 1],
                            preset="ultrafast", crf=35)
    b = render.render_short(sample_video, tmp_path / "b.mp4", 5, 9, None, facecam=[], gameplay=[0, 0, 1, 1],
                            preset="ultrafast", crf=35)
    out = render.compile_long([a, b], tmp_path / "comp.mp4", preset="ultrafast", crf=35)
    assert float(_probe(out)["format"]["duration"]) == pytest.approx(8, abs=0.6)


def test_db_and_review_server(tmp_path, sample_video):
    from clipmill.review import make_handler
    from http.server import ThreadingHTTPServer

    db = DB(tmp_path / "t.db")
    assert db.upsert_vod({"id": "1", "title": "t", "created_at": "2026-11-20T00:00:00Z", "duration_s": 10}, "x")
    assert not db.upsert_vod({"id": "1"}, "x")
    cid = db.add_clip(vod_id="1", streamer="x", start_s=0, end_s=10, score=8, category="funny",
                      title="Old title", reason="r", focus_hits=["Lucia"], path=str(sample_video))
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(db))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    page = urllib.request.urlopen(base + "/").read().decode()
    assert "1 clips to review" in page and "focus: Lucia" in page
    req = urllib.request.Request(base + f"/media/{cid}", headers={"Range": "bytes=0-99"})
    resp = urllib.request.urlopen(req)
    assert resp.status == 206 and len(resp.read()) == 100
    body = json.dumps({"id": cid, "status": "approved", "title": "New title"}).encode()
    urllib.request.urlopen(urllib.request.Request(base + "/decide", data=body,
                                                  headers={"Content-Type": "application/json"}))
    server.shutdown()
    row = db.clip(cid)
    assert row["status"] == "approved" and row["title"] == "New title"


def test_cli_help():
    out = subprocess.run(["python", "-m", "clipmill.cli", "--help"], capture_output=True, text=True,
                         cwd=Path(__file__).parent.parent)
    assert "process" in out.stdout and "review" in out.stdout
