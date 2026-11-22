"""Facebook Reels and Instagram Reels publishers (Meta Graph API), ported from Biblia.

Off unless configured: Facebook needs FARMCORTES_FACEBOOK_PAGE_ID, Instagram needs
FARMCORTES_INSTAGRAM_USER_ID, and both need FARMCORTES_META_ACCESS_TOKEN (a long-lived Page
token; the Instagram account must be linked to that Page). These functions are only called by
review.publish_social, which only sends approved items.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import requests

GRAPH = "https://graph.facebook.com/{version}"


class MetaError(RuntimeError):
    pass


def _version() -> str:
    return os.getenv("FARMCORTES_META_GRAPH_VERSION", "v21.0")


def _token() -> str:
    token = os.getenv("FARMCORTES_META_ACCESS_TOKEN")
    if not token:
        raise MetaError("Defina FARMCORTES_META_ACCESS_TOKEN")
    return token


def enabled_platforms() -> list[str]:
    if not os.getenv("FARMCORTES_META_ACCESS_TOKEN"):
        return []
    return [
        name for name, env in (("facebook", "FARMCORTES_FACEBOOK_PAGE_ID"), ("instagram", "FARMCORTES_INSTAGRAM_USER_ID"))
        if os.getenv(env)
    ]


def _check(response: requests.Response, step: str) -> dict:
    if response.status_code >= 400:
        raise MetaError(f"{step}: {response.status_code} {response.text[:300]}")
    return response.json() if response.content else {}


def _upload(url: str, video: Path, token: str) -> None:
    with video.open("rb") as body:
        _check(requests.post(
            url, data=body, timeout=600,
            headers={"Authorization": f"OAuth {token}", "offset": "0", "file_size": str(video.stat().st_size)},
        ), "upload")


def publish_facebook_reel(video: Path, title: str, description: str) -> dict:
    page_id, token = os.getenv("FARMCORTES_FACEBOOK_PAGE_ID"), _token()
    if not page_id:
        raise MetaError("Defina FARMCORTES_FACEBOOK_PAGE_ID")
    base = GRAPH.format(version=_version())
    start = _check(requests.post(f"{base}/{page_id}/video_reels", data={"upload_phase": "start", "access_token": token}, timeout=60), "start")
    video_id = start["video_id"]
    _upload(start.get("upload_url") or f"https://rupload.facebook.com/video-upload/{_version()}/{video_id}", video, token)
    _check(requests.post(f"{base}/{page_id}/video_reels", data={
        "upload_phase": "finish", "video_id": video_id, "video_state": "PUBLISHED",
        "title": title[:250], "description": description[:2200], "access_token": token,
    }, timeout=120), "finish")
    return {"platform": "facebook", "id": video_id}


def publish_instagram_reel(video: Path, caption: str, *, poll_seconds: float = 10, max_polls: int = 60) -> dict:
    user_id, token = os.getenv("FARMCORTES_INSTAGRAM_USER_ID"), _token()
    if not user_id:
        raise MetaError("Defina FARMCORTES_INSTAGRAM_USER_ID")
    base = GRAPH.format(version=_version())
    container = _check(requests.post(f"{base}/{user_id}/media", data={
        "media_type": "REELS", "upload_type": "resumable", "caption": caption[:2200], "access_token": token,
    }, timeout=60), "container")
    container_id = container["id"]
    _upload(container.get("uri") or f"https://rupload.facebook.com/ig-api-upload/{_version()}/{container_id}", video, token)
    for _ in range(max_polls):
        status = _check(requests.get(f"{base}/{container_id}", params={"fields": "status_code", "access_token": token}, timeout=30), "status")
        if status.get("status_code") == "FINISHED":
            break
        if status.get("status_code") in {"ERROR", "EXPIRED"}:
            raise MetaError(f"Instagram recusou o vídeo: {status}")
        time.sleep(poll_seconds)
    else:
        raise MetaError("Instagram não terminou de processar o vídeo")
    published = _check(requests.post(f"{base}/{user_id}/media_publish", data={"creation_id": container_id, "access_token": token}, timeout=60), "publish")
    return {"platform": "instagram", "id": published["id"]}


def publish(platform: str, video: Path, title: str, description: str) -> dict:
    if platform == "facebook":
        return publish_facebook_reel(video, title, description)
    if platform == "instagram":
        return publish_instagram_reel(video, f"{title}\n\n{description}")
    raise MetaError(f"Plataforma desconhecida: {platform}")
