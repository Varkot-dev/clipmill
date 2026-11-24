"""Loads settings.

Three layers, later ones win:
1. clipmill.toml   - committed: all settings and the streamer list (no secrets)
2. config.toml     - local only, git-ignored: your keys ([twitch], [instagram], [telegram] only)
3. environment     - GitHub Actions secrets (TWITCH_CLIENT_ID, ...) and CLIPMILL_* paths
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib

BASE_FILE = "clipmill.toml"
SECRET_SECTIONS = {"twitch", "instagram", "telegram", "tiktok"}

# environment variable -> (section, key)
ENV_OVERRIDES = {
    "TWITCH_CLIENT_ID": ("twitch", "client_id"),
    "TWITCH_CLIENT_SECRET": ("twitch", "client_secret"),
    "INSTAGRAM_ACCESS_TOKEN": ("instagram", "access_token"),
    "INSTAGRAM_USER_ID": ("instagram", "user_id"),
    "TIKTOK_CLIENT_KEY": ("tiktok", "client_key"),
    "TIKTOK_CLIENT_SECRET": ("tiktok", "client_secret"),
    "TIKTOK_REFRESH_TOKEN": ("tiktok", "refresh_token"),
    "CLIPMILL_DB": ("paths", "db"),
    "CLIPMILL_WORK_DIR": ("paths", "work_dir"),
    "CLIPMILL_OUTPUT_DIR": ("paths", "output_dir"),
    "CLIPMILL_JUDGE_MODE": ("judge", "mode"),
}


@dataclass
class Streamer:
    login: str
    display: str
    handle_text: str
    campaign: str = ""
    campaign_url: str = ""
    game: str = "GTA"
    focus: list[str] = field(default_factory=list)
    facecam: list[float] = field(default_factory=list)
    gameplay: list[float] = field(default_factory=lambda: [0.0, 0.0, 1.0, 1.0])
    enabled: bool = True
    notes: str = ""
    hashtags: list = field(default_factory=list)  # tags a campaign requires in every caption


@dataclass
class Config:
    root: Path
    raw: dict
    streamers: list[Streamer]

    def section(self, name: str) -> dict:
        return self.raw.get(name, {})

    def path(self, key: str) -> Path:
        p = Path(self.section("paths").get(key, key))
        return p if p.is_absolute() else self.root / p

    def streamer(self, login: str) -> Streamer:
        for s in self.streamers:
            if s.login.lower() == login.lower():
                return s
        raise KeyError(f"streamer '{login}' is not in clipmill.toml")


def _merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v  # lists (like streamers) are replaced, not merged
    return out


def load(path: str | Path | None = "config.toml", root: str | Path | None = None) -> Config:
    """path is the local override file; it is optional (cloud runs have none)."""
    override_path = Path(path).resolve() if path else None
    root = Path(root).resolve() if root else (override_path.parent if override_path else Path.cwd())
    raw: dict = {}
    base = root / BASE_FILE
    if base.exists():
        raw = tomllib.loads(base.read_text())
    if override_path and override_path.exists():
        local = tomllib.loads(override_path.read_text())
        # Only login sections come from the local file; settings and streamers always come
        # from clipmill.toml so the Mac and the cloud behave the same.
        raw = _merge(raw, {k: v for k, v in local.items() if k in SECRET_SECTIONS})
    if not raw:
        raise FileNotFoundError(f"neither {base} nor {override_path} was found")
    for env, (section, key) in ENV_OVERRIDES.items():
        if os.environ.get(env):
            raw.setdefault(section, {})[key] = os.environ[env]
    streamers = [Streamer(**s) for s in raw.get("streamers", [])]
    cfg = Config(root=root, raw=raw, streamers=streamers)
    for key in ("work_dir", "output_dir"):
        cfg.path(key).mkdir(parents=True, exist_ok=True)
    cfg.path("db").parent.mkdir(parents=True, exist_ok=True)
    return cfg
