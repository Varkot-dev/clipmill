import json
import sqlite3
from pathlib import Path

import pytest

from clipmill import captions, chat, config, judge, review_flow, telegram
from clipmill.config import Streamer
from clipmill.db import DB
from clipmill.instagram import Instagram
from clipmill.transcribe import Word

REPO = Path(__file__).parent.parent


# ---------- config layering ----------

def test_committed_config_has_the_three_campaign_streamers():
    cfg = config.load(None, root=REPO)
    assert [s.login for s in cfg.streamers] == ["bobby_vanriper", "darealboeshi", "dasgasd3m"]
    assert cfg.section("auto")["platforms"] == ["youtube", "instagram"]


def test_local_file_only_supplies_secrets(tmp_path, monkeypatch):
    (tmp_path / "clipmill.toml").write_text((REPO / "clipmill.toml").read_text())
    (tmp_path / "config.toml").write_text(
        '[twitch]\nclient_id = "abc"\nclient_secret = "xyz"\n'
        '[detect]\nmax_candidates_per_vod = 99\n'
        '[[streamers]]\nlogin = "examplestreamer"\ndisplay = "X"\nhandle_text = "x"\n')
    monkeypatch.delenv("TWITCH_CLIENT_ID", raising=False)
    cfg = config.load(tmp_path / "config.toml")
    assert cfg.section("twitch")["client_id"] == "abc"
    assert cfg.section("detect")["max_candidates_per_vod"] == 12      # old template ignored
    assert "examplestreamer" not in [s.login for s in cfg.streamers]  # placeholder ignored


def test_env_secrets_win(tmp_path, monkeypatch):
    (tmp_path / "clipmill.toml").write_text((REPO / "clipmill.toml").read_text())
    monkeypatch.setenv("TWITCH_CLIENT_ID", "from-env")
    monkeypatch.setenv("CLIPMILL_DB", "state/clipmill.db")
    cfg = config.load("none", root=tmp_path)
    assert cfg.section("twitch")["client_id"] == "from-env"
    assert cfg.path("db") == tmp_path / "state" / "clipmill.db"
    assert (tmp_path / "state").is_dir()


# ---------- db migration ----------

def test_old_database_gets_new_columns(tmp_path):
    p = tmp_path / "old.db"
    con = sqlite3.connect(p)
    con.execute("CREATE TABLE clips (id INTEGER PRIMARY KEY, vod_id TEXT, streamer TEXT, start_s REAL, "
                "end_s REAL, score REAL, category TEXT, title TEXT, reason TEXT, focus_hits TEXT, path TEXT, "
                "status TEXT DEFAULT 'pending', youtube_id TEXT, views INTEGER, meta TEXT, created_at TEXT)")
    con.commit()
    con.close()
    db = DB(p)
    cols = {r["name"] for r in db.conn.execute("PRAGMA table_info(clips)")}
    assert {"instagram_id", "uploaded_at", "notified_message_id", "artifact_run_id"} <= cols
    db.put("telegram_offset", 42)
    assert db.get("telegram_offset") == "42"


# ---------- chat: unique chatters ----------

def test_one_spammer_cannot_fake_a_spike():
    base = [chat.Message(t, "hello", f"user{t % 40}") for t in range(0, 3600, 3)]
    spam = [chat.Message(1800 + i * 0.05, "KEKW KEKW KEKW", "spammer") for i in range(80)]
    crowd = [chat.Message(2400 + i * 0.05, "KEKW", f"fan{i}") for i in range(80)]
    times = [round(s.t) for s in chat.find_spikes(base + spam + crowd, 3600)]
    assert any(abs(t - 2403) < 10 for t in times)
    assert not any(abs(t - 1803) < 10 for t in times)


def test_twitchdownloader_author_is_read(tmp_path):
    f = tmp_path / "c.json"
    f.write_text(json.dumps({"comments": [{"content_offset_seconds": 1, "commenter": {"name": "bob"},
                                           "message": {"body": "W"}}]}))
    assert chat.load_chat(f)[0].author == "bob"


# ---------- captions hook ----------

def test_hook_is_shown_for_the_whole_clip():
    words = [Word(0, 0.4, "hi")]
    ass = captions.build_ass(words, 0, 12, hook="He thought the cop was an NPC")
    hook = [l for l in ass.splitlines() if ",Hook," in l]
    assert hook and hook[0].startswith("Dialogue: 2,0:00:00.00,0:00:12.00,Hook")


# ---------- judge fallback ----------

def test_judge_falls_back_when_claude_is_missing(monkeypatch):
    monkeypatch.setenv("PATH", "/nonexistent")
    s = Streamer(login="x", display="X", handle_text="x")
    words = [Word(i, i + 0.5, "w") for i in range(40)]
    v = judge.judge(words=words, chat_sample=[], spike_rel=30, category="funny", zscore=6,
                    window_len=40, streamer=s, cfg_judge={"mode": "claude"})
    assert v.keep and v.reason.startswith("chat spike")


# ---------- telegram ----------

def upd(uid, data, chat_id=111, mid=5):
    return {"update_id": uid, "callback_query": {"id": f"cb{uid}", "data": data,
                                                 "message": {"message_id": mid, "chat": {"id": chat_id}}}}


def test_decisions_only_from_my_chat():
    found, offset = telegram.decisions(
        [upd(10, "post:3"), upd(11, "skip:4"), upd(12, "post:9", chat_id=999), {"update_id": 13}], 111)
    assert [(c, a) for c, a, *_ in found] == [(3, "post"), (4, "skip")]
    assert offset == 14


class FakeTelegram:
    chat_id = "111"

    def __init__(self, updates=()):
        self._updates = list(updates)
        self.sent, self.messages, self.answers, self.cleared = [], [], [], []

    def updates(self, offset=None):
        return [u for u in self._updates if offset is None or u["update_id"] >= offset]

    def answer(self, cid, text=""):
        self.answers.append((cid, text))

    def clear_buttons(self, mid):
        self.cleared.append(mid)

    def send_clip(self, path, caption, clip_id):
        self.sent.append((Path(path).name, clip_id, caption))
        return 500 + clip_id

    def send_message(self, text, reply_to=None):
        self.messages.append((text, reply_to))
        return 1


@pytest.fixture
def cloud(tmp_path, monkeypatch):
    (tmp_path / "clipmill.toml").write_text((REPO / "clipmill.toml").read_text())
    for k in ("YOUTUBE_TOKEN_JSON", "INSTAGRAM_ACCESS_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    cfg = config.load("none", root=tmp_path)
    db = DB(cfg.path("db"))
    out = cfg.path("output_dir") / "darealboeshi"
    out.mkdir(parents=True)
    ids = []
    for n in range(3):
        (out / f"v_{n}.mp4").write_bytes(b"fake video")
        ids.append(db.add_clip(vod_id="v", streamer="darealboeshi", start_s=0, end_s=20, score=8,
                               category="funny", title=f"Clip {n}", reason="r", focus_hits=["Lucia"],
                               path=f"darealboeshi/v_{n}.mp4"))
    return cfg, db, ids


def test_notify_then_post_and_skip(cloud, monkeypatch):
    cfg, db, ids = cloud
    tg = FakeTelegram()
    assert review_flow.notify_new(cfg, db, tg) == 3
    assert "focus: Lucia" in tg.sent[0][2]
    assert review_flow.notify_new(cfg, db, tg) == 0  # never sends twice

    tg._updates = [upd(1, f"post:{ids[0]}"), upd(2, f"skip:{ids[1]}"), upd(3, f"post:{ids[0]}")]
    counts = review_flow.apply_decisions(cfg, db, tg)
    assert counts == {"approved": 1, "rejected": 1}
    assert db.clip(ids[0])["status"] == "approved" and db.clip(ids[1])["status"] == "rejected"
    assert db.get("telegram_offset") == "4"

    posted = []
    monkeypatch.setattr(review_flow.publish, "enabled_platforms", lambda cfg: ["youtube"])

    def fake_publish(cfg, db, clip, path):
        posted.append(clip["id"])
        db.update_clip(clip["id"], status="uploaded", youtube_id="abc", uploaded_at="2099-01-01 00:00:00")
        return {"youtube": "https://youtube.com/shorts/abc"}

    monkeypatch.setattr(review_flow.publish, "publish", fake_publish)
    lines = review_flow.publish_approved(cfg, db, tg)
    assert posted == [ids[0]]
    assert "youtube.com/shorts/abc" in lines[0]
    assert tg.messages[0][1] == 500 + ids[0]  # reply goes under the clip's message


def test_daily_cap_holds_extra_approvals(cloud, monkeypatch):
    cfg, db, ids = cloud
    cfg.raw["auto"]["max_uploads_per_day"] = 1
    for i in ids:
        db.update_clip(i, status="approved")
    monkeypatch.setattr(review_flow.publish, "enabled_platforms", lambda cfg: ["youtube"])

    def fake_publish(cfg, db, clip, path):
        db.update_clip(clip["id"], status="uploaded", uploaded_at=db.conn.execute(
            "SELECT datetime('now')").fetchone()[0])
        return {"youtube": "u"}

    monkeypatch.setattr(review_flow.publish, "publish", fake_publish)
    lines = review_flow.publish_approved(cfg, db, None)
    assert len(db.clips("uploaded")) == 1
    assert sum("queued for the next run" in l for l in lines) == 2


def test_no_platform_means_clips_wait(cloud):
    cfg, db, ids = cloud
    db.update_clip(ids[0], status="approved")
    lines = review_flow.publish_approved(cfg, db, None)
    assert "no YouTube or Instagram login" in lines[0]
    assert db.clip(ids[0])["status"] == "approved"


# ---------- instagram ----------

class FakeResp:
    def __init__(self, data, code=200):
        self._d, self.status_code = data, code

    def json(self):
        return self._d


class FakeSession:
    def __init__(self):
        self.calls, self.status_polls = [], 0

    def get(self, url, params=None, timeout=None):
        self.calls.append(("GET", url))
        if url.endswith("/me"):
            return FakeResp({"user_id": "17841", "username": "gta.clips"})
        if url.endswith("/C1"):
            self.status_polls += 1
            return FakeResp({"status_code": "FINISHED" if self.status_polls > 1 else "IN_PROGRESS"})
        return FakeResp({"permalink": "https://instagram.com/reel/xyz"})

    def post(self, url, data=None, timeout=None, headers=None):
        self.calls.append(("POST", url, headers))
        if url.endswith("/media"):
            assert data["media_type"] == "REELS" and data["upload_type"] == "resumable"
            return FakeResp({"id": "C1", "uri": "https://rupload.facebook.com/ig-api-upload/v23.0/C1"})
        if "rupload" in url:
            assert headers["Authorization"].startswith("OAuth ") and headers["offset"] == "0"
            return FakeResp({"success": True})
        if url.endswith("/media_publish"):
            assert data["creation_id"] == "C1"
            return FakeResp({"id": "M9"})
        raise AssertionError(url)


def test_instagram_reel_flow(tmp_path):
    video = tmp_path / "c.mp4"
    video.write_bytes(b"x" * 1000)
    s = FakeSession()
    ig = Instagram("TOKEN", session=s)
    assert ig.user_id == "17841"
    assert ig.publish_reel(video, "caption", poll_seconds=0) == "M9"
    upload = next(c for c in s.calls if "rupload" in c[1])
    assert upload[2]["file_size"] == "1000"


def test_instagram_error_is_reported(tmp_path):
    class Bad(FakeSession):
        def post(self, url, data=None, timeout=None, headers=None):
            return FakeResp({"error": {"message": "Invalid OAuth access token"}}, 400)

    video = tmp_path / "c.mp4"
    video.write_bytes(b"x")
    with pytest.raises(RuntimeError, match="Invalid OAuth"):
        Instagram("T", user_id="1", session=Bad()).publish_reel(video, "c", poll_seconds=0)


def test_load_wav_reads_ffmpeg_output(tmp_path):
    import subprocess
    from clipmill.fetch import extract_audio
    from clipmill.transcribe import load_wav

    src = tmp_path / "a.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
                    str(src)], check=True)
    samples = load_wav(extract_audio(src, tmp_path / "a.wav"))
    assert 31000 < len(samples) < 33000 and samples.dtype.name == "float32" and abs(samples).max() <= 1.0


def test_auto_approve_keeps_only_strong_clips(cloud):
    cfg, db, ids = cloud
    db.update_clip(ids[0], score=9)
    db.update_clip(ids[1], score=7)
    db.update_clip(ids[2], score=8)
    assert review_flow.auto_approve(db, 8) == (2, 1)
    assert [db.clip(i)["status"] for i in ids] == ["approved", "rejected", "approved"]


def test_disclosure_shows_at_the_start():
    ass = captions.build_ass([Word(0, 0.4, "hi")], 0, 20, disclosure="#ad", disclosure_seconds=3)
    ad = [l for l in ass.splitlines() if ",Ad," in l]
    assert ad and ad[0].startswith("Dialogue: 3,0:00:00.00,0:00:03.00,Ad")


def test_disabled_streamer_is_kept_but_off():
    cfg = config.load(None, root=REPO)
    assert not cfg.streamer("dasgasd3m").enabled
    assert cfg.streamer("darealboeshi").enabled


def test_content_rewards_bot_fills_the_form(tmp_path):
    """Runs the real submit() against a copy of the Content Rewards submit sheet."""
    import os
    import shutil
    import threading
    from functools import partial
    from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

    chrome = os.environ.get("CLIPMILL_CHROME") or shutil.which("google-chrome")
    pytest.importorskip("playwright")
    if not chrome:
        pytest.skip("no Chrome available")
    os.environ["CLIPMILL_CHROME"] = chrome
    from clipmill import contentrewards

    handler = partial(SimpleHTTPRequestHandler, directory=str(REPO / "tests" / "fixtures"))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_address[1]}/cr_submit_form.html"
    try:
        assert contentrewards.submit(url, "https://www.instagram.com/reel/ABC/", {"cookies": [], "origins": []},
                                     timeout_ms=10000) == "submitted"
    finally:
        server.shutdown()
