from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path

from .db import Database
from .youtube import metadata_for, optimized_title, settings, weak_title


def desired_scheduled_metadata(current: dict, job: dict, output: dict) -> tuple[dict, bool]:
    generated = metadata_for(job, output)
    old_title = str(current["title"])
    old_base = re.sub(r"\s*#Shorts\s*$", "", old_title, flags=re.IGNORECASE).strip()
    new_title = old_title
    if weak_title(old_base):
        new_title = optimized_title(job, output)
        if re.search(r"#Shorts\s*$", old_title, flags=re.IGNORECASE):
            new_title = f"{new_title} #Shorts"[:100]
    snippet = {
        "title": new_title,
        "description": generated["description"],
        "categoryId": current["categoryId"],
    }
    for key in ("tags", "defaultLanguage", "defaultAudioLanguage"):
        if current.get(key) is not None:
            snippet[key] = current[key]
    return snippet, new_title != old_title


def refresh_scheduled_metadata(data_dir: Path) -> dict:
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError

    database = Database(data_dir / "farmcortes.sqlite3")
    mapped: dict[str, tuple[dict, dict]] = {}
    for job in database.list(2000):
        outputs = {item.get("file"): item for item in job.get("outputs", [])}
        for publication in job.get("publication", {}).get("items", []):
            output = outputs.get(publication.get("file"))
            if output and publication.get("video_id"):
                mapped[str(publication["video_id"])] = (job, output)

    token = settings(data_dir)["token"]
    credentials = Credentials.from_authorized_user_file(str(token), [
        "https://www.googleapis.com/auth/youtube",
        "https://www.googleapis.com/auth/youtube.readonly",
    ])
    youtube = build("youtube", "v3", credentials=credentials, cache_discovery=False)
    ids = list(mapped)
    videos: list[dict] = []
    for index in range(0, len(ids), 50):
        videos.extend(youtube.videos().list(
            part="snippet,status", id=",".join(ids[index:index + 50]),
        ).execute().get("items", []))

    scheduled = [
        video for video in videos
        if video.get("status", {}).get("privacyStatus") == "private"
        and video.get("status", {}).get("publishAt")
    ]
    updated = retitled = unchanged = 0
    for video in scheduled:
        job, output = mapped[video["id"]]
        desired, title_changed = desired_scheduled_metadata(video["snippet"], job, output)
        current = video["snippet"]
        comparable = {key: current.get(key) for key in desired}
        if comparable == desired:
            unchanged += 1
            continue
        try:
            youtube.videos().update(
                part="snippet", body={"id": video["id"], "snippet": desired},
            ).execute()
        except HttpError as exc:
            reason = "unknown"
            try:
                reason = json.loads(exc.content).get("error", {}).get("errors", [{}])[0].get("reason", reason)
            except (ValueError, AttributeError, IndexError):
                pass
            return {
                "status": "partial", "reason": reason, "scheduled": len(scheduled),
                "updated": updated, "retitled": retitled, "unchanged": unchanged,
            }
        updated += 1
        retitled += int(title_changed)
        time.sleep(0.05)
    return {
        "status": "completed", "scheduled": len(scheduled), "updated": updated,
        "retitled": retitled, "unchanged": unchanged,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Manutenção segura dos metadados agendados")
    parser.add_argument("--data-dir", type=Path, default=Path(__file__).resolve().parent.parent / "data")
    args = parser.parse_args()
    print(json.dumps(refresh_scheduled_metadata(args.data_dir.resolve()), ensure_ascii=False))


if __name__ == "__main__":
    main()
