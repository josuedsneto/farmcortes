from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator


SCHEMA = """
CREATE TABLE IF NOT EXISTS review_queue (id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL CHECK(kind IN ('short','long','compilation')), streamer TEXT NOT NULL, platform TEXT NOT NULL CHECK(platform IN ('twitch','kick')), source_url TEXT NOT NULL, file_path TEXT NOT NULL, duration REAL NOT NULL, peak_at REAL, category TEXT NOT NULL, titles_json TEXT NOT NULL, description TEXT NOT NULL, hashtags TEXT NOT NULL, credit_text TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','approved','discarded','published','failed')), chosen_title TEXT, created_at TEXT NOT NULL, decided_at TEXT, youtube_id TEXT, error TEXT, is_test INTEGER NOT NULL DEFAULT 0, published_at TEXT);

CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    url TEXT NOT NULL,
    source_path TEXT,
    source_author TEXT,
    source_license TEXT,
    editorial_theme TEXT,
    caption_mode TEXT NOT NULL DEFAULT 'auto',
    auto_publish INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL,
    stage TEXT NOT NULL,
    progress INTEGER NOT NULL DEFAULT 0,
    clip_count INTEGER NOT NULL,
    min_duration INTEGER NOT NULL,
    max_duration INTEGER NOT NULL,
    title TEXT,
    source_duration REAL,
    error TEXT,
    outputs_json TEXT NOT NULL DEFAULT '[]',
    publication_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_jobs_created ON jobs(created_at DESC);
CREATE TABLE IF NOT EXISTS worker_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    last_seen TEXT,
    name TEXT
);
CREATE TABLE IF NOT EXISTS strategy_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    metrics_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS discovery_candidates (
    video_id TEXT PRIMARY KEY,
    url TEXT NOT NULL,
    title TEXT NOT NULL,
    author TEXT,
    source_license TEXT,
    editorial_theme TEXT,
    viral_score REAL NOT NULL DEFAULT 0,
    disposition TEXT NOT NULL DEFAULT 'signal',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    discovered_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_discovery_candidates_score
ON discovery_candidates(viral_score DESC, updated_at DESC);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def json_safe(value: Any) -> Any:
    """Convert third-party scalar/container values to stdlib JSON primitives."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_safe(item) for item in value]
    item = getattr(value, "item", None)
    if callable(item):
        return json_safe(item())
    isoformat = getattr(value, "isoformat", None)
    if callable(isoformat):
        return isoformat()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


class Database:
    def __init__(self, path: Path, *, recover_interrupted: bool = False):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript(SCHEMA)
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
            if "source_path" not in columns:
                conn.execute("ALTER TABLE jobs ADD COLUMN source_path TEXT")
            if "source_author" not in columns:
                conn.execute("ALTER TABLE jobs ADD COLUMN source_author TEXT")
            if "source_license" not in columns:
                conn.execute("ALTER TABLE jobs ADD COLUMN source_license TEXT")
            if "editorial_theme" not in columns:
                conn.execute("ALTER TABLE jobs ADD COLUMN editorial_theme TEXT")
            if "caption_mode" not in columns:
                conn.execute("ALTER TABLE jobs ADD COLUMN caption_mode TEXT NOT NULL DEFAULT 'auto'")
            if "auto_publish" not in columns:
                conn.execute("ALTER TABLE jobs ADD COLUMN auto_publish INTEGER NOT NULL DEFAULT 1")
            if "publication_json" not in columns:
                conn.execute("ALTER TABLE jobs ADD COLUMN publication_json TEXT NOT NULL DEFAULT '{}'")
            if recover_interrupted:
                conn.execute(
                    "UPDATE jobs SET status='queued', stage='Retomando processamento', progress=1 "
                    "WHERE status='processing'"
                )

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def create(
        self, job_id: str, url: str, clip_count: int, min_duration: int, max_duration: int,
        source_path: str | None = None, status: str = "queued", stage: str = "Na fila",
        title: str | None = None, source_author: str | None = None, source_license: str | None = None,
        auto_publish: bool = True, caption_mode: str = "auto", editorial_theme: str | None = None,
    ) -> dict[str, Any]:
        stamp = now()
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO jobs (id,url,source_path,source_author,source_license,editorial_theme,caption_mode,auto_publish,status,stage,progress,clip_count,min_duration,max_duration,title,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (job_id, url, source_path, source_author, source_license, editorial_theme, caption_mode, int(auto_publish), status, stage, 0, clip_count, min_duration, max_duration, title, stamp, stamp),
            )
        return self.get(job_id)

    def update(self, job_id: str, **values: Any) -> None:
        if not values:
            return
        if "outputs" in values:
            values["outputs_json"] = json.dumps(json_safe(values.pop("outputs")), ensure_ascii=False)
        if "publication" in values:
            values["publication_json"] = json.dumps(json_safe(values.pop("publication")), ensure_ascii=False)
        values["updated_at"] = now()
        allowed = {"source_path", "source_author", "source_license", "editorial_theme", "caption_mode", "auto_publish", "status", "stage", "progress", "title", "source_duration", "error", "outputs_json", "publication_json", "updated_at"}
        unknown = set(values) - allowed
        if unknown:
            raise ValueError(f"Invalid job fields: {sorted(unknown)}")
        assignments = ",".join(f"{key}=?" for key in values)
        with self.connect() as conn:
            conn.execute(f"UPDATE jobs SET {assignments} WHERE id=?", (*values.values(), job_id))

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return self._decode(row) if row else None

    def list(self, limit: int = 30) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [self._decode(row) for row in rows]

    def record_discovery_candidate(self, candidate: dict[str, Any], disposition: str) -> None:
        stamp = now()
        metadata = json.dumps(json_safe(candidate), ensure_ascii=False)
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO discovery_candidates "
                "(video_id,url,title,author,source_license,editorial_theme,viral_score,disposition,metadata_json,discovered_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(video_id) DO UPDATE SET url=excluded.url,title=excluded.title,author=excluded.author,"
                "source_license=excluded.source_license,editorial_theme=excluded.editorial_theme,"
                "viral_score=excluded.viral_score,disposition=excluded.disposition,metadata_json=excluded.metadata_json,"
                "updated_at=excluded.updated_at",
                (
                    candidate["video_id"], candidate["url"], candidate["title"], candidate.get("author"),
                    candidate.get("license"), candidate.get("theme"), float(candidate.get("viral_score") or 0),
                    disposition, metadata, stamp, stamp,
                ),
            )

    def pending_count(self) -> int:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS total FROM jobs WHERE status IN ('awaiting_download','downloading','queued','processing')"
            ).fetchone()
        return int(row["total"])

    def next_queued(self) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY created_at LIMIT 1").fetchone()
            if row:
                conn.execute(
                    "UPDATE jobs SET status='processing',stage='Preparando',progress=1,updated_at=? WHERE id=? AND status='queued'",
                    (now(), row["id"]),
                )
        return self.get(row["id"]) if row else None

    def claim_download(self, worker_name: str) -> dict[str, Any] | None:
        stamp = now()
        stale = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO worker_state (id,last_seen,name) VALUES (1,?,?) "
                "ON CONFLICT(id) DO UPDATE SET last_seen=excluded.last_seen,name=excluded.name",
                (stamp, worker_name[:120]),
            )
            conn.execute(
                "UPDATE jobs SET status='awaiting_download',stage='Aguardando o PC local',updated_at=? "
                "WHERE status='downloading' AND updated_at < ?",
                (stamp, stale),
            )
            row = conn.execute(
                "SELECT * FROM jobs WHERE status='awaiting_download' ORDER BY created_at LIMIT 1"
            ).fetchone()
            if row:
                conn.execute(
                    "UPDATE jobs SET status='downloading',stage='PC local obtendo o vídeo',progress=5,updated_at=? "
                    "WHERE id=? AND status='awaiting_download'",
                    (stamp, row["id"]),
                )
        return self.get(row["id"]) if row else None

    def worker_seen(self, worker_name: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO worker_state (id,last_seen,name) VALUES (1,?,?) "
                "ON CONFLICT(id) DO UPDATE SET last_seen=excluded.last_seen,name=excluded.name",
                (now(), worker_name[:120]),
            )

    def worker_status(self) -> dict[str, Any]:
        with self.connect() as conn:
            row = conn.execute("SELECT last_seen,name FROM worker_state WHERE id=1").fetchone()
        if not row or not row["last_seen"]:
            return {"online": False, "last_seen": None, "name": None}
        try:
            seen = datetime.fromisoformat(row["last_seen"])
            online = datetime.now(timezone.utc) - seen < timedelta(seconds=40)
        except ValueError:
            online = False
        return {"online": online, "last_seen": row["last_seen"], "name": row["name"]}

    def record_strategy_snapshot(self, metrics: dict[str, Any]) -> dict[str, Any]:
        stamp = now()
        payload = dict(metrics)
        payload["created_at"] = stamp
        with self.connect() as conn:
            cursor = conn.execute(
                "INSERT INTO strategy_snapshots (metrics_json,created_at) VALUES (?,?)",
                (json.dumps(payload, ensure_ascii=False), stamp),
            )
        payload["id"] = cursor.lastrowid
        return payload

    def latest_strategy_snapshot(self) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT id,metrics_json,created_at FROM strategy_snapshots ORDER BY id DESC LIMIT 1"
            ).fetchone()
        if not row:
            return None
        payload = json.loads(row["metrics_json"] or "{}")
        payload["id"] = row["id"]
        payload.setdefault("created_at", row["created_at"])
        return payload

    @staticmethod
    def _decode(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["outputs"] = json.loads(result.pop("outputs_json") or "[]")
        result["publication"] = json.loads(result.pop("publication_json") or "{}")
        return result
