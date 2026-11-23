"""Loads config.toml into plain dataclasses."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Streamer:
    login: str
    display: str
    handle_text: str
    campaign: str = ""
    game: str = "GTA"
    focus: list[str] = field(default_factory=list)
    facecam: list[float] = field(default_factory=list)
    gameplay: list[float] = field(default_factory=lambda: [0.0, 0.0, 1.0, 1.0])


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
        raise KeyError(f"streamer '{login}' is not in config.toml")


def load(path: str | Path = "config.toml") -> Config:
    path = Path(path).resolve()
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found. Copy config.example.toml to config.toml and fill it in."
        )
    raw = tomllib.loads(path.read_text())
    streamers = [Streamer(**s) for s in raw.get("streamers", [])]
    cfg = Config(root=path.parent, raw=raw, streamers=streamers)
    for key in ("work_dir", "output_dir"):
        cfg.path(key).mkdir(parents=True, exist_ok=True)
    return cfg
