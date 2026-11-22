from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .db import Database
from .youtube import metadata_for, publish_file, ranked_outputs, schedule_slots, settings


def _legacy_item(publication: dict) -> dict | None:
    if not publication.get("video_id") or not publication.get("file"):
        return None
    item = {key: publication[key] for key in ("platform", "video_id", "url", "privacy", "file", "title") if key in publication}
    item["status"] = "published"
    return item


def schedule_existing(data_dir: Path) -> list[dict]:
    database = Database(data_dir / "farmcortes.sqlite3")
    config = settings(data_dir)
    jobs = [job for job in reversed(database.list(1000)) if job["status"] == "done" and job.get("publication", {}).get("url")]
    reserved = {
        item["publish_at"]
        for job in jobs
        for item in job.get("publication", {}).get("items", [])
        if item.get("publish_at")
    }
    pending_by_job: list[tuple[dict, list[dict]]] = []
    publications: dict[str, list[dict]] = {}
    seen_urls: set[str] = set()
    for job in jobs:
        if job["url"] in seen_urls:
            continue
        seen_urls.add(job["url"])
        existing = list(job["publication"].get("items", []))
        if not existing:
            legacy = _legacy_item(job["publication"])
            existing = [legacy] if legacy else []
        uploaded_files = {item.get("file") for item in existing}
        pending = [output for output in ranked_outputs(job.get("outputs", [])) if output.get("file") not in uploaded_files]
        if pending:
            pending_by_job.append((job, pending))
            publications[job["id"]] = existing

    queue: list[tuple[dict, dict]] = []
    while any(pending for _, pending in pending_by_job):
        for job, pending in pending_by_job:
            if pending:
                queue.append((job, pending.pop(0)))
    if not queue:
        return []

    zone = ZoneInfo(config["timezone"])
    today = datetime.now(timezone.utc).astimezone(zone).date().isoformat()
    assigned_dates: dict[str, set[str]] = {job["id"]: {today} for job, _ in pending_by_job}
    scheduled = []
    for job, output in queue:
        slot = schedule_slots(
            1, config["schedule_times"], config["timezone"], reserved=reserved,
            excluded_local_dates=assigned_dates[job["id"]], one_per_day=True,
        )[0]
        publish_at = slot.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        reserved.add(publish_at)
        assigned_dates[job["id"]].add(slot.date().isoformat())
        folder = data_dir / "jobs" / job["id"]
        result = publish_file(
            folder / output["file"], metadata_for(job, output), token=config["token"],
            privacy="private", publish_at=slot,
        )
        publications[job["id"]].append(result)
        first = publications[job["id"]][0]
        publication = {
            "platform": "youtube", "status": "scheduled", "video_id": first["video_id"],
            "url": first["url"], "privacy": first.get("privacy", "public"), "file": first["file"],
            "title": first["title"], "items": publications[job["id"]],
            "timezone": config["timezone"], "schedule_times": list(config["schedule_times"]),
        }
        database.update(
            job["id"], publication=publication,
            stage=f"1 Short publicado e {len(publications[job['id']]) - 1} agendados",
        )
        scheduled.append({"job_id": job["id"], **result})
    return scheduled


def main() -> None:
    parser = argparse.ArgumentParser(description="Agenda cortes antigos ainda não enviados ao YouTube")
    parser.add_argument("--data-dir", type=Path, default=Path(os.getenv("FARMCORTES_DATA", Path(__file__).resolve().parent.parent / "data")))
    args = parser.parse_args()
    print(json.dumps(schedule_existing(args.data_dir.resolve()), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
