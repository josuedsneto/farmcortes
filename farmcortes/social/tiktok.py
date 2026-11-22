"""
TikTok Content Posting API v2 integration for Farmcortes.

Official docs: https://developers.tiktok.com/doc/content-posting-api-reference/
"""
from __future__ import annotations

import os
import json
import time
import uuid
import hashlib
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from urllib.parse import urlencode, parse_qs, urlparse

import requests


# TikTok API endpoints (v2)
TIKTOK_AUTH_BASE = "https://www.tiktok.com/v2/auth"
TIKTOK_API_BASE = "https://open.tiktokapis.com/v2"

AUTHORIZE_URL = f"{TIKTOK_AUTH_BASE}/authorize/"
TOKEN_URL = f"{TIKTOK_API_BASE}/oauth/token/"
REFRESH_URL = f"{TIKTOK_API_BASE}/oauth/token/"
CREATOR_INFO_URL = f"{TIKTOK_API_BASE}/post/publish/creator_info/query/"
VIDEO_INIT_URL = f"{TIKTOK_API_BASE}/post/publish/video/init/"
VIDEO_STATUS_URL = f"{TIKTOK_API_BASE}/post/publish/status/fetch/"

# Required scopes for Content Posting API
REQUIRED_SCOPES = ["video.upload", "video.publish", "user.info.basic"]

# Token storage
# Resolve relative to this module's parent directory (farmcortes/)
MODULE_DIR = Path(__file__).parent.parent
TOKEN_PATH = MODULE_DIR / "data/secrets/tiktok-token.json"
TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)


class TikTokError(Exception):
    """TikTok API error."""
    pass


class TikTokTokenError(TikTokError):
    """Token-related error (expired, invalid, missing)."""
    pass


class TikTokRateLimitError(TikTokError):
    """Rate limit (429) error."""
    pass


def _client_credentials() -> tuple[str, str]:
    """Get client key and secret from environment."""
    client_key = os.getenv("FARMCORTES_TIKTOK_CLIENT_KEY")
    client_secret = os.getenv("FARMCORTES_TIKTOK_CLIENT_SECRET")
    if not client_key or not client_secret:
        raise TikTokError(
            "Defina FARMCORTES_TIKTOK_CLIENT_KEY e FARMCORTES_TIKTOK_CLIENT_SECRET no ambiente"
        )
    return client_key, client_secret


def _load_token() -> dict:
    """Load token from file."""
    if not TOKEN_PATH.is_file():
        return {}
    try:
        return json.loads(TOKEN_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_token(token: dict) -> None:
    """Save token to file with 0600 permissions."""
    TOKEN_PATH.write_text(json.dumps(token, ensure_ascii=False), encoding="utf-8")
    TOKEN_PATH.chmod(0o600)


def _is_token_expired(token: dict) -> bool:
    """Check if access token is expired."""
    expires_at = token.get("expires_at")
    if not expires_at:
        return True
    return datetime.fromtimestamp(expires_at) <= datetime.now() + timedelta(minutes=5)


def _refresh_access_token(refresh_token: str) -> dict:
    """Refresh access token using refresh token."""
    client_key, client_secret = _client_credentials()
    data = {
        "client_key": client_key,
        "client_secret": client_secret,
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
    }
    response = requests.post(REFRESH_URL, data=data, timeout=30)
    if response.status_code == 429:
        raise TikTokRateLimitError("Rate limit ao renovar token")
    if response.status_code != 200:
        raise TikTokTokenError(f"Falha ao renovar token: {response.status_code} {response.text}")
    result = response.json()
    if "access_token" not in result:
        raise TikTokTokenError(f"Resposta de refresh inválida: {result}")
    expires_in = result.get("expires_in", 86400)
    return {
        "access_token": result["access_token"],
        "refresh_token": result.get("refresh_token", refresh_token),
        "expires_at": time.time() + expires_in,
        "scope": result.get("scope", " ".join(REQUIRED_SCOPES)),
        "open_id": result.get("open_id", ""),
    }


def get_valid_token() -> str:
    """Get a valid access token, refreshing if needed."""
    token = _load_token()
    if not token:
        raise TikTokTokenError("Token não encontrado. Complete o fluxo OAuth primeiro.")
    if _is_token_expired(token):
        if not token.get("refresh_token"):
            raise TikTokTokenError("Token expirado e sem refresh_token. Reautorize.")
        token = _refresh_access_token(token["refresh_token"])
        _save_token(token)
    return token["access_token"]


def authorize_url(redirect_uri: str, state: Optional[str] = None) -> str:
    """Generate TikTok OAuth authorize URL."""
    client_key, _ = _client_credentials()
    if state is None:
        state = uuid.uuid4().hex[:16]
    params = {
        "client_key": client_key,
        "response_type": "code",
        "scope": ",".join(REQUIRED_SCOPES),
        "redirect_uri": redirect_uri,
        "state": state,
    }
    return f"{AUTHORIZE_URL}?{urlencode(params)}"


def exchange_code(code: str, redirect_uri: str) -> dict:
    """Exchange authorization code for access/refresh tokens."""
    client_key, client_secret = _client_credentials()
    data = {
        "client_key": client_key,
        "client_secret": client_secret,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": redirect_uri,
    }
    response = requests.post(TOKEN_URL, data=data, timeout=30)
    if response.status_code != 200:
        raise TikTokError(f"Falha ao trocar code: {response.status_code} {response.text}")
    result = response.json()
    if "access_token" not in result:
        raise TikTokError(f"Resposta de token inválida: {result}")
    expires_in = result.get("expires_in", 86400)
    token = {
        "access_token": result["access_token"],
        "refresh_token": result.get("refresh_token", ""),
        "expires_at": time.time() + expires_in,
        "scope": result.get("scope", " ".join(REQUIRED_SCOPES)),
        "open_id": result.get("open_id", ""),
    }
    _save_token(token)
    return token


def creator_info(access_token: Optional[str] = None) -> dict:
    """Query creator info (max video duration, privacy options, etc.)."""
    token = access_token or get_valid_token()
    headers = {"Authorization": f"Bearer {token}"}
    response = requests.post(CREATOR_INFO_URL, headers=headers, json={}, timeout=30)
    if response.status_code == 401:
        raise TikTokTokenError("Token inválido ou expirado")
    if response.status_code == 429:
        raise TikTokRateLimitError("Rate limit ao consultar creator info")
    if response.status_code != 200:
        raise TikTokError(f"Falha ao consultar creator info: {response.status_code} {response.text}")
    return response.json()


INBOX_INIT_URL = f"{TIKTOK_API_BASE}/post/publish/inbox/video/init/"
SINGLE_CHUNK_MAX = 64 * 1024 * 1024
CHUNK_SIZE = 10 * 1024 * 1024


def _chunk_plan(video_size: int) -> tuple[int, int]:
    """TikTok rule: one chunk up to 64 MB; otherwise 5-64 MB chunks, the last one absorbs the rest."""
    if video_size <= SINGLE_CHUNK_MAX:
        return video_size, 1
    return CHUNK_SIZE, video_size // CHUNK_SIZE


def _init_video_upload(access_token: str, video_size: int, post_info: Optional[dict] = None) -> dict:
    """Start an upload: direct post (video/init with post_info) or draft (inbox/video/init)."""
    chunk_size, chunks = _chunk_plan(video_size)
    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json; charset=UTF-8"}
    body = {"source_info": {"source": "FILE_UPLOAD", "video_size": video_size, "chunk_size": chunk_size, "total_chunk_count": chunks}}
    if post_info is not None:
        body["post_info"] = post_info
    response = requests.post(VIDEO_INIT_URL if post_info is not None else INBOX_INIT_URL, headers=headers, json=body, timeout=30)
    if response.status_code == 401:
        raise TikTokTokenError("Token inválido ou expirado")
    if response.status_code == 429:
        raise TikTokRateLimitError("Rate limit ao iniciar upload")
    if response.status_code != 200:
        raise TikTokError(f"Falha ao iniciar upload: {response.status_code} {response.text}")
    result = response.json()
    error = result.get("error") or {}
    if error.get("code") not in (None, "ok"):
        raise TikTokError(f"TikTok recusou o upload: {error}")
    return result


def _upload_chunks(upload_url: str, video_path: Path) -> None:
    """PUT the file in the chunks announced at init (Content-Range per chunk)."""
    file_size = video_path.stat().st_size
    chunk_size, chunks = _chunk_plan(file_size)
    with video_path.open("rb") as f:
        for index in range(chunks):
            start = index * chunk_size
            chunk = f.read() if index == chunks - 1 else f.read(chunk_size)
            headers = {
                "Content-Range": f"bytes {start}-{start + len(chunk) - 1}/{file_size}",
                "Content-Length": str(len(chunk)),
                "Content-Type": "video/mp4",
            }
            response = requests.put(upload_url, headers=headers, data=chunk, timeout=300)
            if response.status_code == 429:
                raise TikTokRateLimitError(f"Rate limit no chunk {index}")
            if response.status_code not in (200, 201, 204, 206, 308):
                raise TikTokError(f"Falha no chunk {index}: {response.status_code} {response.text}")


def upload_draft(
    video_path: str | Path,
    caption: str,
    *,
    direct_post: bool = False,
    privacy_level: str = "SELF_ONLY",
) -> dict:
    """
    Send an MP4 to TikTok (Content Posting API v2).

    direct_post=False (default): "Upload" to the creator's inbox (scope video.upload); the
    creator finishes the post in the TikTok app. Works for unaudited apps.
    direct_post=True: "Direct Post" with post_info (scope video.publish). Until the app passes
    TikTok's audit, privacy_level must be SELF_ONLY (private); after the audit,
    PUBLIC_TO_EVERYONE, MUTUAL_FOLLOW_FRIENDS or FOLLOWER_OF_CREATOR are allowed.
    TikTok processes the video asynchronously; publish_id identifies it (status/fetch).
    """
    video_path = Path(video_path).resolve()
    if not video_path.is_file():
        raise TikTokError(f"Arquivo de vídeo não encontrado: {video_path}")
    if video_path.suffix.lower() != ".mp4":
        raise TikTokError("Apenas arquivos MP4 são suportados")

    access_token = get_valid_token()
    post_info = None
    if direct_post:
        post_info = {
            "title": caption[:2200], "privacy_level": privacy_level,
            "disable_duet": True, "disable_stitch": True, "disable_comment": False,
        }
    init_data = _init_video_upload(access_token, video_path.stat().st_size, post_info).get("data", {})
    upload_url = init_data.get("upload_url")
    publish_id = init_data.get("publish_id")
    if not upload_url or not publish_id:
        raise TikTokError(f"Resposta de init inválida: {init_data}")
    _upload_chunks(upload_url, video_path)
    if direct_post:
        return {"platform": "tiktok", "mode": "direct_post", "publish_id": publish_id, "status": "published",
                "privacy_level": privacy_level}
    return {"platform": "tiktok", "mode": "draft", "publish_id": publish_id, "status": "draft_created",
            "message": "Vídeo enviado como rascunho. Abra o app TikTok para revisar e publicar."}


def is_configured() -> bool:
    """Check if TikTok integration is configured."""
    client_key = os.getenv("FARMCORTES_TIKTOK_CLIENT_KEY")
    client_secret = os.getenv("FARMCORTES_TIKTOK_CLIENT_SECRET")
    return bool(client_key and client_secret and TOKEN_PATH.is_file())


# Backwards-compatible alias
upload_video = upload_draft
