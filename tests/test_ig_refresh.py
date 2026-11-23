from clipmill import instagram
from clipmill.db import DB


class FakeResp:
    def __init__(self, data): self._d = data
    def json(self): return self._d


class FakeSession:
    def __init__(self): self.calls = []
    def get(self, url, params=None, timeout=None):
        self.calls.append(params["access_token"])
        return FakeResp({"access_token": params["access_token"] + "+", "expires_in": 5184000})


def test_refreshes_weekly_and_keeps_encrypted(tmp_path):
    db = DB(tmp_path / "s.db")
    http = FakeSession()
    day = 86400
    assert instagram.live_token(db, "BASE", http, now=10 * day) == "BASE+"
    assert "BASE+" not in db.get("ig_token_enc")          # stored encrypted
    assert instagram.live_token(db, "BASE", http, now=12 * day) == "BASE+"   # no refresh yet
    assert instagram.live_token(db, "BASE", http, now=18 * day) == "BASE++"  # a week later
    assert http.calls == ["BASE", "BASE+"]


def test_new_secret_resets(tmp_path):
    db = DB(tmp_path / "s.db")
    http = FakeSession()
    instagram.live_token(db, "OLD", http, now=30 * 86400.0)
    assert instagram.live_token(db, "NEW", http, now=31 * 86400.0) == "NEW+"


def test_refresh_failure_falls_back(tmp_path):
    db = DB(tmp_path / "s.db")

    class Broken:
        def get(self, *a, **k): return FakeResp({"error": "nope"})
    assert instagram.live_token(db, "BASE", Broken(), now=30 * 86400.0) == "BASE"
