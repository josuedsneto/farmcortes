from __future__ import annotations

import os
import json
import re
import time as time_module
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


DEFAULT_SCHEDULE_TIMES = ("12:00", "18:00", "21:00")
DEFAULT_TIMEZONE = "America/Sao_Paulo"

WEAK_TITLE_OPENINGS = (
    "aí", "bom", "com certeza", "e aí", "então", "eu acho", "não não",
    "olha", "para ninguém", "tipo",
)
WEAK_TITLE_EXACT = {
    "a gente", "com certeza", "e aí", "então", "é", "é mesmo", "eu não",
    "gostou", "não não não", "o que", "olha o que ele falou",
    "olha o que ela falou",
}
THEME_LABELS = {
    "humor": "Humor",
    "conversas": "Conversas",
    "podcast": "Podcast",
    "curiosidades": "História real",
    "ciencia-tecnologia": "Ciência e tecnologia",
    "entretenimento": "Cultura e entretenimento",
}


def _finish_resumable_upload(request, *, max_transport_retries: int = 5) -> dict:
    """Resume the same upload session after transient transport failures."""
    from googleapiclient.errors import HttpError

    response = None
    failures = 0
    while response is None:
        try:
            _, response = request.next_chunk(num_retries=3)
            failures = 0
        except Exception as exc:
            status = getattr(getattr(exc, "resp", None), "status", None)
            retryable = isinstance(exc, OSError) or (
                isinstance(exc, HttpError) and status in {408, 429, 500, 502, 503, 504}
            )
            if not retryable or failures >= max_transport_retries:
                raise
            failures += 1
            time_module.sleep(min(2 ** failures, 30))
    return response


def _schedule_times(value: object) -> tuple[str, ...]:
    raw = value if isinstance(value, (list, tuple)) else str(value or "").split(",")
    result = []
    for item in raw:
        text = str(item).strip()
        try:
            parsed = time.fromisoformat(text)
        except ValueError as exc:
            raise RuntimeError(f"Horário de publicação inválido: {text}") from exc
        if parsed.second or parsed.microsecond:
            raise RuntimeError(f"Horário de publicação inválido: {text}")
        result.append(f"{parsed.hour:02d}:{parsed.minute:02d}")
    if not result:
        raise RuntimeError("Defina pelo menos um horário de publicação no YouTube")
    return tuple(sorted(set(result)))


def settings(data_dir: Path) -> dict:
    path = data_dir / "youtube-settings.json"
    stored = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    token = Path(os.getenv("FARMCORTES_YOUTUBE_TOKEN", data_dir / "secrets" / "youtube-token.json")).resolve()
    explicit = os.getenv("FARMCORTES_YOUTUBE_ENABLED")
    enabled = explicit.casefold() in {"1", "true", "yes"} if explicit is not None else bool(stored.get("enabled", token.is_file()))
    privacy = os.getenv("FARMCORTES_YOUTUBE_PRIVACY", str(stored.get("privacy", "private"))).casefold()
    schedule_value = os.getenv("FARMCORTES_YOUTUBE_SCHEDULE_TIMES")
    schedule_times = _schedule_times(schedule_value if schedule_value is not None else stored.get("schedule_times", DEFAULT_SCHEDULE_TIMES))
    longform_time = _schedule_times([stored.get("longform_time", "20:00")])[0]
    timezone_name = os.getenv("FARMCORTES_YOUTUBE_TIMEZONE", str(stored.get("timezone", DEFAULT_TIMEZONE))).strip()
    try:
        ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise RuntimeError(f"Fuso horário do YouTube inválido: {timezone_name}") from exc
    return {
        "enabled": enabled, "token": token, "privacy": privacy,
        "schedule_times": schedule_times, "longform_time": longform_time, "timezone": timezone_name,
    }


def best_output(outputs: list[dict]) -> dict:
    if not outputs:
        raise ValueError("Nenhum corte disponível para publicação")
    return max(outputs, key=lambda item: (float(item.get("score", 0)), float(item.get("duration", 0))))


def ranked_outputs(outputs: list[dict]) -> list[dict]:
    """Return the immediate publication first, followed by the remaining clips by score."""
    return sorted(outputs, key=lambda item: (float(item.get("score", 0)), float(item.get("duration", 0))), reverse=True)


def _title_score(value: str) -> float:
    """Prefer a complete, contextual phrase over a loose transcript fragment."""
    clean = re.sub(r"\s+", " ", value or "").strip(" -–—.,;:!?…\"'")
    words = re.findall(r"[\wÀ-ÿ]+", clean)
    lowered = clean.casefold()
    score = min(len(words), 10)
    if len(words) < 4:
        score -= 8
    if lowered in WEAK_TITLE_EXACT:
        score -= 10
    if any(lowered == opening or lowered.startswith(f"{opening} ") for opening in WEAK_TITLE_OPENINGS):
        score -= 4
    if re.search(r"\b(?:ele|ela|eles|elas|isso|aquilo)\b", lowered) and not re.search(r"\b[A-ZÁÉÍÓÚÂÊÔÃÕÇ][a-záéíóúâêôãõç]{2,}\b", clean):
        score -= 2
    if re.search(r"\b(?:como|por que|porque|verdade|segredo|mudou|aconteceu|nunca|melhor|pior)\b", lowered):
        score += 3
    if 24 <= len(clean) <= 58:
        score += 2
    return score


def weak_title(value: str) -> bool:
    clean = re.sub(r"\s+", " ", value or "").strip(" -–—.,;:!?…\"'")
    words = re.findall(r"[\wÀ-ÿ]+", clean)
    lowered = clean.casefold()
    return (
        not words or lowered in WEAK_TITLE_EXACT
        or bool(re.fullmatch(r"trecho selecionado \d+", lowered))
        or (len(words) == 1 and not re.search(r"[?!]", value or ""))
    )


def _source_context(job: dict) -> str | None:
    author = re.sub(r"\s+", " ", str(job.get("source_author") or "")).strip(" -–—")
    if 3 <= len(author) <= 32:
        return author
    title = re.sub(r"\s+", " ", str(job.get("title") or "")).strip(" -–—")
    if not title or re.fullmatch(r"(?:watch\?v=)?[\w-]+(?:\?.*)?", title):
        return None
    title = re.split(r"\s+[|—–]\s+", title, maxsplit=1)[0]
    if len(title) > 38:
        title = title[:39].rsplit(" ", 1)[0]
    return title.title()


def optimized_title(job: dict, output: dict) -> str:
    candidates = [str(output.get("title") or "")]
    candidates.extend(str(item) for item in output.get("title_options", []) if item)
    excerpt = re.sub(r"\s+", " ", str(output.get("excerpt") or "")).strip()
    if excerpt:
        candidates.extend(part.strip() for part in re.split(r"(?<=[.!?])\s+", excerpt) if part.strip())
    candidates = [item for item in candidates if item.strip()]
    selected = max(candidates or ["Corte selecionado"], key=_title_score)
    selected = re.sub(r"\s+", " ", selected).strip(" -–—.,;:!?…\"'")
    if weak_title(selected):
        theme = THEME_LABELS.get(str(job.get("editorial_theme") or "").casefold())
        context = _source_context(job) or theme
        selected = f"O momento mais inesperado — {context}" if context else "O momento que mudou a conversa"
    if len(selected) > 58:
        selected = selected[:59].rsplit(" ", 1)[0].rstrip(" -–—.,;:!?")
    return selected[:1].upper() + selected[1:]


def schedule_slots(
    count: int, schedule_times: tuple[str, ...], timezone_name: str, *,
    now: datetime | None = None, reserved: set[str] | None = None,
    excluded_local_dates: set[str] | None = None, one_per_day: bool = False,
) -> list[datetime]:
    """Pick future local schedule slots, avoiding dates already reserved by earlier jobs."""
    if count <= 0:
        return []
    zone = ZoneInfo(timezone_name)
    current = (now or datetime.now(timezone.utc)).astimezone(zone)
    earliest = current + timedelta(minutes=15)
    reserved_utc = {value.replace("+00:00", "Z") for value in (reserved or set())}
    slots: list[datetime] = []
    excluded_dates = set(excluded_local_dates or set())
    day = current.date()
    while len(slots) < count:
        if day.isoformat() in excluded_dates:
            day += timedelta(days=1)
            continue
        for clock in schedule_times:
            hour, minute = map(int, clock.split(":"))
            candidate = datetime.combine(day, time(hour, minute), tzinfo=zone)
            candidate_key = candidate.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
            if candidate >= earliest and candidate_key not in reserved_utc:
                slots.append(candidate)
                reserved_utc.add(candidate_key)
                if len(slots) == count:
                    break
                if one_per_day:
                    break
        day += timedelta(days=1)
    return slots


def metadata_for(job: dict, output: dict) -> dict:
    title = optimized_title(job, output)
    source_title = str(job.get("title") or "conteúdo autorizado").strip()
    attribution = ""
    if job.get("source_license"):
        attribution = (
            f"\nAutor/canal original: {job.get('source_author') or 'não informado'}\n"
            f"Fonte: {job.get('url')}\n"
            f"Licença na fonte: {job['source_license']}\n"
        )
    description = (
        "Gostou do corte? Inscreva-se no canal para não perder os próximos.\n\n"
        f"Trecho autorizado de: {source_title}.\n"
        f"{attribution}\n"
        "Trecho selecionado, reenquadrado, legendado e contextualizado pelo Coletivo Cortes.\n"
        "Qual foi a parte mais inesperada? Conte nos comentários.\n\n"
        "#Shorts #Cortes #ColetivoCortes"
    )
    return {
        "title": title,
        "description": description,
        "tags": ["Shorts", "cortes", "Coletivo Cortes"],
        "category_id": "24",
        "language": "pt-BR",
        "made_for_kids": False,
        "contains_synthetic_media": False,
    }


_REVIEW_AUTHORIZATION = object()

def publish_file(
    video: Path, metadata: dict, *, token: Path, privacy: str = "private",
    publish_at: datetime | None = None, short_form: bool = True, _approval=None,
) -> dict:
    """Publish an approved, already-rendered video with explicit metadata."""
    if _approval is not _REVIEW_AUTHORIZATION:
        raise RuntimeError("Publicação exige aprovação na fila de revisão")
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload

    video = video.resolve()
    token = token.resolve()
    if not video.is_file():
        raise RuntimeError("Arquivo de vídeo para publicação não existe")
    if not token.is_file():
        raise RuntimeError("Token OAuth do YouTube não encontrado")
    if privacy not in {"private", "unlisted", "public"}:
        raise RuntimeError("Privacidade do YouTube inválida")
    if publish_at is not None:
        if publish_at.tzinfo is None:
            raise RuntimeError("O agendamento do YouTube precisa incluir fuso horário")
        if publish_at <= datetime.now(timezone.utc).astimezone(publish_at.tzinfo):
            raise RuntimeError("O agendamento do YouTube precisa estar no futuro")
        privacy = "private"

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

    youtube = build("youtube", "v3", credentials=credentials, cache_discovery=False)
    status = {
        "privacyStatus": privacy,
        "selfDeclaredMadeForKids": metadata["made_for_kids"],
        "containsSyntheticMedia": metadata["contains_synthetic_media"],
    }
    if publish_at is not None:
        status["publishAt"] = publish_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    request = youtube.videos().insert(
        part="snippet,status",
        body={
            "snippet": {
                "title": metadata["title"],
                "description": metadata["description"],
                "tags": metadata["tags"],
                "categoryId": metadata["category_id"],
                "defaultLanguage": metadata["language"],
                "defaultAudioLanguage": metadata["language"],
            },
            "status": status,
        },
        media_body=MediaFileUpload(str(video), mimetype="video/mp4", chunksize=8 * 1024 * 1024, resumable=True),
        notifySubscribers=False,
    )
    response = _finish_resumable_upload(request)
    video_id = response["id"]
    result = {
        "platform": "youtube",
        "video_id": video_id,
        "url": f"https://youtube.com/{'shorts/' if short_form else 'watch?v='}{video_id}",
        "privacy": privacy,
        "file": video.name,
        "title": metadata["title"],
    }
    if publish_at is not None:
        result["publish_at"] = status["publishAt"]
        result["status"] = "scheduled"
    else:
        result["status"] = "published"
    return result
