"""SQLite state: which VODs were processed and where each clip is in its life."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS vods (
    id TEXT PRIMARY KEY,
    streamer TEXT NOT NULL,
    title TEXT,
    created_at TEXT,
    duration_s REAL,
    status TEXT NOT NULL DEFAULT 'new',      -- new, processing, done, failed
    error TEXT
);
CREATE TABLE IF NOT EXISTS clips (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    vod_id TEXT NOT NULL,
    streamer TEXT NOT NULL,
    start_s REAL NOT NULL,
    end_s REAL NOT NULL,
    score REAL,
    category TEXT,
    title TEXT,
    reason TEXT,
    focus_hits TEXT,
    path TEXT,
    status TEXT NOT NULL DEFAULT 'pending',  -- pending, approved, rejected, uploaded
    youtube_id TEXT,
    views INTEGER,
    meta TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS kv (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""

# Columns added after the first version; added to old databases on open.
MIGRATIONS = {
    "instagram_id": "TEXT",
    "uploaded_at": "TEXT",
    "notified_message_id": "INTEGER",
    "artifact_run_id": "TEXT",
    "artifact_name": "TEXT",
    "upload_error": "TEXT",
}


class DB:
    def __init__(self, path: Path):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        have = {r["name"] for r in self.conn.execute("PRAGMA table_info(clips)")}
        for col, kind in MIGRATIONS.items():
            if col not in have:
                self.conn.execute(f"ALTER TABLE clips ADD COLUMN {col} {kind}")
        self.conn.commit()

    def get(self, key: str, default: str | None = None) -> str | None:
        row = self.conn.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def put(self, key: str, value) -> None:
        self.conn.execute("INSERT OR REPLACE INTO kv (key, value) VALUES (?,?)", (key, str(value)))
        self.conn.commit()

    def uploads_today(self) -> int:
        return self.conn.execute(
            "SELECT COUNT(*) FROM clips WHERE uploaded_at >= date('now')").fetchone()[0]

    def unnotified(self):
        return self.conn.execute(
            "SELECT * FROM clips WHERE status='pending' AND notified_message_id IS NULL ORDER BY score DESC, id"
        ).fetchall()

    def upsert_vod(self, vod: dict, streamer: str) -> bool:
        """Returns True if the VOD is new."""
        cur = self.conn.execute(
            "INSERT OR IGNORE INTO vods (id, streamer, title, created_at, duration_s) VALUES (?,?,?,?,?)",
            (vod["id"], streamer, vod.get("title"), vod.get("created_at"), vod.get("duration_s")),
        )
        self.conn.commit()
        return cur.rowcount == 1

    def set_vod_status(self, vod_id: str, status: str, error: str | None = None):
        self.conn.execute("UPDATE vods SET status=?, error=? WHERE id=?", (status, error, vod_id))
        self.conn.commit()

    def vods(self, status: str | None = None):
        q, args = "SELECT * FROM vods", ()
        if status:
            q, args = q + " WHERE status=?", (status,)
        return self.conn.execute(q + " ORDER BY created_at", args).fetchall()

    def add_clip(self, **c) -> int:
        c.setdefault("meta", {})
        c["meta"] = json.dumps(c["meta"])
        c["focus_hits"] = json.dumps(c.get("focus_hits") or [])
        cols = ",".join(c)
        cur = self.conn.execute(
            f"INSERT INTO clips ({cols}) VALUES ({','.join('?' * len(c))})", tuple(c.values())
        )
        self.conn.commit()
        return cur.lastrowid

    def clips(self, status: str | None = None):
        q, args = "SELECT * FROM clips", ()
        if status:
            q, args = q + " WHERE status=?", (status,)
        return self.conn.execute(q + " ORDER BY score DESC, id", args).fetchall()

    def clip(self, clip_id: int):
        return self.conn.execute("SELECT * FROM clips WHERE id=?", (clip_id,)).fetchone()

    def update_clip(self, clip_id: int, **fields):
        sets = ",".join(f"{k}=?" for k in fields)
        self.conn.execute(f"UPDATE clips SET {sets} WHERE id=?", (*fields.values(), clip_id))
        self.conn.commit()
