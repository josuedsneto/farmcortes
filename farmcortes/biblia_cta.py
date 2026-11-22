"""Closing call-to-action clips (the owner's Bibliagram app) for Biblia videos.

Clips live in FARMCORTES_BIBLIA_CTA_DIR (default <data>/cta) and are never committed.
Identical files are deduplicated by SHA-256; FARMCORTES_BIBLIA_CTA_EXCLUDE takes comma-separated
filename patterns (fnmatch, e.g. "*testadores*") to retire time-bound clips. The least recently
used clip is chosen and every use is recorded in biblia_cta_uses. A missing or empty folder
means no CTA, never a failed run.
"""
from __future__ import annotations

import fnmatch
import hashlib
import logging
import os
from dataclasses import dataclass
from pathlib import Path

from .db import Database, now
from .pipeline import probe

log = logging.getLogger(__name__)

APP_NAME = "Bibliagram"


@dataclass(frozen=True)
class Cta:
    sha256: str
    name: str
    path: Path
    duration: float
    has_audio: bool


def cta_dir(data_dir: Path) -> Path:
    return Path(os.getenv("FARMCORTES_BIBLIA_CTA_DIR") or data_dir / "cta")


def _excluded(name: str) -> bool:
    patterns = [p.strip() for p in os.getenv("FARMCORTES_BIBLIA_CTA_EXCLUDE", "").split(",") if p.strip()]
    return any(fnmatch.fnmatch(name.casefold(), p.casefold()) for p in patterns)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def available(folder: Path) -> list[Cta]:
    """Usable clips in the folder, one per distinct content, sorted by name."""
    if not folder.is_dir():
        return []
    clips: dict[str, Cta] = {}
    for path in sorted(folder.glob("*.mp4")):
        if _excluded(path.name):
            continue
        try:
            sha = _sha256(path)
            if sha in clips:
                continue
            info = probe(path)
            duration = float(info.get("format", {}).get("duration") or 0)
            streams = {s.get("codec_type") for s in info.get("streams", [])}
        except Exception as exc:  # an unreadable clip is skipped, not fatal
            log.warning("CTA ignorado (%s): %s", path.name, exc)
            continue
        if duration > 0 and "video" in streams:
            clips[sha] = Cta(sha, path.name, path, duration, "audio" in streams)
    return list(clips.values())


def by_least_recent_use(db: Database, clips: list[Cta]) -> list[Cta]:
    with db.connect() as conn:
        last = {row[0]: row[1] for row in conn.execute("SELECT sha256, MAX(used_at) FROM biblia_cta_uses GROUP BY sha256")}
    return sorted(clips, key=lambda c: (last.get(c.sha256, ""), c.name))


def record_use(db: Database, review_id: int, cta: Cta) -> None:
    with db.connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO biblia_cta_uses (review_id,sha256,name,duration,used_at) VALUES (?,?,?,?,?)",
            (review_id, cta.sha256, cta.name, cta.duration, now()),
        )


def description_line() -> str:
    url = os.getenv("FARMCORTES_BIBLIA_APP_URL", "").strip()
    return f"📱 Conheça o {APP_NAME}" + (f": {url}" if url else "")
