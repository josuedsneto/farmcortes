from __future__ import annotations

import argparse
import fcntl
import json
import math
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, TextIO

from .db import Database
from .editorial import active_campaign, discovery_topics, load_policy
from .youtube import settings as youtube_settings


PORTUGUESE_MARKERS = {
    "a", "ao", "aos", "as", "como", "com", "da", "das", "de", "do", "dos", "e",
    "em", "essa", "esse", "esta", "este", "história", "negócios", "o", "os", "para",
    "por", "porque", "que", "sem", "sobre", "tecnologia", "uma", "você",
}


def _discovery_dir(data_dir: Path) -> Path:
    path = data_dir / "discovery"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _write_status(data_dir: Path, status: dict) -> dict:
    directory = _discovery_dir(data_dir)
    destination = directory / "status.json"
    temporary = directory / f"status-{uuid.uuid4().hex}.tmp"
    temporary.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(destination)
    return status


def discovery_status(data_dir: Path) -> dict:
    directory = _discovery_dir(data_dir)
    status_path = directory / "status.json"
    if status_path.is_file():
        try:
            return json.loads(status_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
    latest_path = directory / "latest.json"
    if latest_path.is_file():
        try:
            latest = json.loads(latest_path.read_text(encoding="utf-8"))
            return {
                "state": "completed",
                "trigger": "scheduled",
                "finished_at": latest.get("generated_at"),
                "candidate_count": int(latest.get("candidate_count", 0)),
                "queued_count": len(latest.get("selected_for_cutting", [])),
            }
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            pass
    return {"state": "idle"}


def begin_discovery_run(data_dir: Path, *, trigger: str) -> tuple[TextIO, dict] | None:
    directory = _discovery_dir(data_dir)
    lock = (directory / "run.lock").open("a+", encoding="utf-8")
    try:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close()
        return None
    run = {
        "state": "running",
        "run_id": uuid.uuid4().hex[:12],
        "trigger": trigger,
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
    _write_status(data_dir, run)
    return lock, run


def complete_discovery_run(
    data_dir: Path, lock: TextIO, run: dict, *, per_day: int = 3,
    lookback_days: int = 365, enqueue: bool = True,
    discover_fn: Callable[..., list[dict]] | None = None,
) -> list[dict]:
    runner = discover_fn or discover
    try:
        selected = runner(
            data_dir, per_day=per_day, lookback_days=lookback_days, enqueue=enqueue,
        )
        latest = {}
        latest_path = _discovery_dir(data_dir) / "latest.json"
        if latest_path.is_file():
            try:
                latest = json.loads(latest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                latest = {}
        _write_status(data_dir, {
            **run,
            "state": "completed",
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "candidate_count": int(latest.get("candidate_count", len(selected))),
            "queued_count": len(selected),
            "search_error_count": len(latest.get("search_errors", [])),
        })
        return selected
    except Exception as exc:
        _write_status(data_dir, {
            **run,
            "state": "failed",
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "error": f"{type(exc).__name__}: {exc}"[:400],
        })
        raise
    finally:
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        lock.close()


def is_portuguese(snippet: dict) -> bool:
    declared = str(snippet.get("defaultAudioLanguage") or snippet.get("defaultLanguage") or "").casefold()
    if declared:
        return declared == "pt" or declared.startswith("pt-")
    text = f"{snippet.get('title', '')} {snippet.get('description', '')}".casefold()
    words = {part.strip(".,!?;:()[]{}\"'") for part in text.split()}
    return len(words & PORTUGUESE_MARKERS) >= 4


def viral_score(item: dict, *, now: datetime | None = None) -> float:
    current = now or datetime.now(timezone.utc)
    published = datetime.fromisoformat(item["published_at"].replace("Z", "+00:00"))
    age_days = max(0.25, (current - published).total_seconds() / 86400)
    views = max(0, int(item.get("views", 0)))
    likes = max(0, int(item.get("likes", 0)))
    comments = max(0, int(item.get("comments", 0)))
    velocity = views / (age_days + 1.5)
    engagement_signal = likes + comments * 3
    return math.log10(velocity + 1) * 2.2 + min(math.log10(engagement_signal + 1), 3)


def _credentials(token: Path):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    scopes = [
        "https://www.googleapis.com/auth/youtube",
        "https://www.googleapis.com/auth/youtube.readonly",
    ]
    credentials = Credentials.from_authorized_user_file(str(token), scopes)
    if credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())
        token.write_text(credentials.to_json(), encoding="utf-8")
        token.chmod(0o600)
    if not credentials.valid:
        raise RuntimeError("Autorização do YouTube inválida; reconecte a conta")
    return credentials


def discover(
    data_dir: Path, *, per_day: int = 3, lookback_days: int = 365,
    enqueue: bool = True, now: datetime | None = None,
) -> list[dict]:
    from googleapiclient.discovery import build

    config = youtube_settings(data_dir)
    youtube = build("youtube", "v3", credentials=_credentials(config["token"]), cache_discovery=False)
    published_after = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).isoformat().replace("+00:00", "Z")
    policy = load_policy()
    configured_topics = os.getenv("FARMCORTES_DISCOVERY_TOPICS")
    topics = (
        [{"theme": "manual", "query": part.strip()} for part in configured_topics.split("|") if part.strip()]
        if configured_topics else discovery_topics(policy, count=per_day, now=now)
    )
    search_items: dict[str, dict] = {}
    search_errors: list[dict] = []
    for topic in topics:
        for duration in ("medium", "long"):
            try:
                response = youtube.search().list(
                    part="snippet", type="video", q=topic["query"], maxResults=15, order="viewCount",
                    publishedAfter=published_after, relevanceLanguage="pt", regionCode="BR",
                    safeSearch="strict", videoDuration=duration, videoEmbeddable="true",
                ).execute(num_retries=2)
            except (OSError, TimeoutError) as exc:
                search_errors.append({"query": topic["query"], "duration": duration, "error": type(exc).__name__})
                continue
            for result in response.get("items", []):
                video_id = result.get("id", {}).get("videoId")
                if video_id:
                    search_items.setdefault(video_id, {**result, "_theme": topic["theme"], "_query": topic["query"]})
    detail_items = []
    video_ids = list(search_items)
    for start in range(0, len(video_ids), 50):
        try:
            details = youtube.videos().list(
                part="snippet,status,statistics,contentDetails", id=",".join(video_ids[start:start + 50]), maxResults=50,
            ).execute(num_retries=2)
        except (OSError, TimeoutError) as exc:
            search_errors.append({"query": "video-details", "duration": "n/a", "error": type(exc).__name__})
            continue
        detail_items.extend(details.get("items", []))
    candidates = []
    minimum_views = max(0, int(os.getenv("FARMCORTES_DISCOVERY_MIN_VIEWS", "5000")))
    for item in detail_items:
        snippet = item.get("snippet", {})
        if not is_portuguese(snippet):
            continue
        statistics = item.get("statistics", {})
        if int(statistics.get("viewCount", 0)) < minimum_views:
            continue
        candidate = {
            "video_id": item["id"],
            "url": f"https://www.youtube.com/watch?v={item['id']}",
            "title": str(snippet.get("title") or "Vídeo Creative Commons")[:300],
            "author": str(snippet.get("channelTitle") or "Canal não informado")[:200],
            "license": item.get("status", {}).get("license", "youtube"),
            "published_at": snippet.get("publishedAt"),
            "views": int(statistics.get("viewCount", 0)),
            "likes": int(statistics.get("likeCount", 0)),
            "comments": int(statistics.get("commentCount", 0)),
            "theme": search_items[item["id"]]["_theme"],
            "query": search_items[item["id"]]["_query"],
        }
        if candidate["published_at"]:
            candidate["viral_score"] = round(viral_score(candidate), 4)
            candidates.append(candidate)
    database = Database(data_dir / "farmcortes.sqlite3")
    known_urls = {job["url"] for job in database.list(1000)}
    trends = []
    trend_authors: set[str] = set()
    for item in sorted(candidates, key=lambda value: value["viral_score"], reverse=True):
        if item["author"] in trend_authors:
            continue
        trends.append(item)
        trend_authors.add(item["author"])
        if len(trends) == per_day:
            break
    selected = []
    selected_authors: set[str] = set()
    pending_before_run = database.pending_count()
    capacity = max(0, int(policy.get("max_pending_jobs", 8)) - pending_before_run)
    selection_limit = min(per_day, capacity) if enqueue else per_day
    if selection_limit:
        for item in sorted(candidates, key=lambda value: value["viral_score"], reverse=True):
            if item["license"] != "creativeCommon" or item["url"] in known_urls or item["author"] in selected_authors:
                continue
            selected.append({**item, "license": "Creative Commons Attribution (CC BY)"})
            selected_authors.add(item["author"])
            if len(selected) == selection_limit:
                break
    if enqueue:
        selected_ids = {item["video_id"] for item in selected}
        for item in candidates:
            database.record_discovery_candidate(item, "queued" if item["video_id"] in selected_ids else "signal")
        for item in selected:
            job = database.create(
                uuid.uuid4().hex[:12], item["url"], 8, 45, 75,
                status="awaiting_download", stage="Descoberto automaticamente · aguardando PC local",
                title=item["title"], source_author=item["author"], source_license=item["license"],
                editorial_theme=item["theme"], auto_publish=False,
            )
            item["job_id"] = job["id"]
    report_dir = _discovery_dir(data_dir)
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(), "trends": trends,
        "selected_for_cutting": selected, "candidate_count": len(candidates),
        "topics": topics, "campaign": active_campaign(policy, now),
        "search_errors": search_errors,
        "pending_before_run": pending_before_run,
        "queue_capacity": capacity, "enqueue": enqueue,
        "note": "Licença padrão do YouTube é somente sinal editorial; apenas CC BY pode entrar na fila de cortes.",
    }
    (report_dir / "latest.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return selected


def main() -> None:
    parser = argparse.ArgumentParser(description="Descobre vídeos CC BY com potencial viral e alimenta o Farmcortes")
    parser.add_argument("--data-dir", type=Path, default=Path(os.getenv("FARMCORTES_DATA", Path(__file__).resolve().parent.parent / "data")))
    parser.add_argument("--count", type=int, default=3)
    parser.add_argument("--lookback-days", type=int, default=365)
    parser.add_argument("--dry-run", action="store_true", help="Pesquisa e gera relatório sem criar jobs")
    args = parser.parse_args()
    if not 1 <= args.count <= 10:
        raise SystemExit("--count deve ficar entre 1 e 10")
    data_dir = args.data_dir.resolve()
    started = begin_discovery_run(data_dir, trigger="scheduled")
    if not started:
        raise SystemExit("Já existe uma varredura em andamento")
    lock, run = started
    print(json.dumps(complete_discovery_run(
        data_dir, lock, run, per_day=args.count, lookback_days=args.lookback_days,
        enqueue=not args.dry_run,
    ), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
