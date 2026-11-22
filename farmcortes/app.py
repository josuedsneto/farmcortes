from __future__ import annotations

import logging
import os
import hmac
import secrets
import threading
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from .db import Database
from .discovery import begin_discovery_run, complete_discovery_run, discovery_status
from .pipeline import Processor, is_youtube_url, validate_source_url
from .strategy import build_longform_plan, performance_report

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s: %(message)s")

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.getenv("FARMCORTES_DATA", ROOT / "data")).resolve()
DB = Database(DATA / "farmcortes.sqlite3", recover_interrupted=True)
PROCESSOR = Processor(DB, DATA)
stop_event = threading.Event()


class StrategySnapshot(BaseModel):
    period_days: int = Field(28, ge=1, le=365)
    views: int = Field(ge=0)
    watch_hours: float = Field(ge=0)
    subscribers_gained: int = 0
    avg_view_duration_seconds: float = Field(0, ge=0)
    engaged_views: int | None = Field(None, ge=0)
    stayed_to_watch_pct: float | None = Field(None, ge=0, le=100)
    average_percentage_viewed: float | None = Field(None, ge=0, le=500)


def load_worker_token() -> str:
    path = DATA / "secrets" / "local-worker-token"
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(secrets.token_urlsafe(48), encoding="utf-8")
        path.chmod(0o600)
    return path.read_text(encoding="utf-8").strip()


WORKER_TOKEN = load_worker_token()


def require_worker(request: Request) -> str:
    supplied = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
    if not supplied or not hmac.compare_digest(supplied, WORKER_TOKEN):
        raise HTTPException(401, "Worker não autorizado")
    return request.headers.get("x-worker-name", "Windows Worker")[:120]


def worker() -> None:
    while not stop_event.is_set():
        job = DB.next_queued()
        if job:
            PROCESSOR.process(job)
        else:
            stop_event.wait(2)


@asynccontextmanager
async def lifespan(_: FastAPI):
    thread = threading.Thread(target=worker, name="farmcortes-worker", daemon=True)
    thread.start()
    yield
    stop_event.set()
    thread.join(timeout=5)


app = FastAPI(title="Farmcortes", docs_url=None, redoc_url=None, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=ROOT / "farmcortes" / "static"), name="static")
templates = Jinja2Templates(directory=ROOT / "farmcortes" / "templates")


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "queue": len([job for job in DB.list() if job["status"] in {"queued", "processing"}])}


@app.get("/api/worker/status")
def worker_status() -> dict:
    return DB.worker_status()


@app.get("/worker.ps1", response_class=PlainTextResponse)
def worker_installer(request: Request):
    template = (ROOT / "farmcortes" / "worker.ps1").read_text(encoding="utf-8")
    base_url = str(request.base_url).rstrip("/")
    script = template.replace("__BASE_URL__", base_url).replace("__WORKER_TOKEN__", WORKER_TOKEN)
    return PlainTextResponse(
        script,
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="farmcortes-worker.ps1"', "Cache-Control": "no-store"},
    )


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(request, "index.html", {"jobs": DB.list()})


@app.get("/api/jobs")
def jobs() -> list[dict]:
    return DB.list()


@app.get("/api/discovery/status")
def get_discovery_status() -> dict:
    return discovery_status(DATA)


def _manual_discovery(lock, run: dict) -> None:
    try:
        complete_discovery_run(DATA, lock, run, per_day=3, lookback_days=365, enqueue=True)
    except Exception:
        logging.getLogger(__name__).exception("A varredura manual falhou")


@app.post("/api/discovery/run", status_code=202)
def run_discovery_now() -> dict:
    started = begin_discovery_run(DATA, trigger="manual")
    if not started:
        raise HTTPException(409, "Já existe uma varredura em andamento")
    lock, run = started
    thread = threading.Thread(
        target=_manual_discovery, args=(lock, run),
        name=f"farmcortes-discovery-{run['run_id']}", daemon=True,
    )
    thread.start()
    return run


@app.get("/api/jobs/{job_id}")
def job(job_id: str) -> dict:
    result = DB.get(job_id)
    if not result:
        raise HTTPException(404, "Trabalho não encontrado")
    return result


@app.get("/api/jobs/{job_id}/longform-plan")
def longform_plan(job_id: str) -> dict:
    result = DB.get(job_id)
    if not result:
        raise HTTPException(404, "Trabalho não encontrado")
    if not result.get("outputs"):
        raise HTTPException(409, "Os cortes ainda não estão prontos")
    return build_longform_plan(result, result["outputs"])


@app.get("/api/strategy/report")
def strategy_report() -> dict:
    return performance_report(DB.latest_strategy_snapshot())


@app.post("/api/strategy/metrics", status_code=201)
def record_strategy_metrics(snapshot: StrategySnapshot) -> dict:
    stored = DB.record_strategy_snapshot(snapshot.model_dump())
    return performance_report(stored)


@app.post("/api/jobs", status_code=202)
def create_job(
    url: str = Form(...), rights_confirmed: bool = Form(False), clip_count: int = Form(3),
    min_duration: int = Form(45), max_duration: int = Form(65),
) -> dict:
    if not rights_confirmed:
        raise HTTPException(400, "Confirme que você tem autorização para usar o conteúdo")
    try:
        url = validate_source_url(url)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not 1 <= clip_count <= 8:
        raise HTTPException(400, "Escolha entre 1 e 8 cortes")
    if not 15 <= min_duration <= 90 or not 20 <= max_duration <= 180 or min_duration >= max_duration:
        raise HTTPException(400, "A duração escolhida é inválida")
    if is_youtube_url(url):
        return DB.create(
            uuid.uuid4().hex[:12], url, clip_count, min_duration, max_duration,
            status="awaiting_download", stage="Aguardando o PC local",
        )
    return DB.create(uuid.uuid4().hex[:12], url, clip_count, min_duration, max_duration)


@app.post("/api/uploads", status_code=202)
async def create_upload_job(
    video: UploadFile = File(...), subtitle: UploadFile | None = File(None), rights_confirmed: bool = Form(False), clip_count: int = Form(3),
    min_duration: int = Form(45), max_duration: int = Form(65),
) -> dict:
    if not rights_confirmed:
        raise HTTPException(400, "Confirme que você tem autorização para usar o conteúdo")
    suffix = Path(video.filename or "").suffix.lower()
    if suffix not in {".mp4", ".mov", ".mkv", ".webm", ".m4v"}:
        raise HTTPException(400, "Envie um vídeo MP4, MOV, MKV ou WebM")
    if not 1 <= clip_count <= 8 or not 15 <= min_duration < max_duration <= 180:
        raise HTTPException(400, "As opções de corte são inválidas")
    job_id = uuid.uuid4().hex[:12]
    folder = DATA / "jobs" / job_id
    folder.mkdir(parents=True, exist_ok=True)
    destination = folder / f"source-upload{suffix}"
    size = 0
    with destination.open("wb") as target:
        while chunk := await video.read(1024 * 1024):
            size += len(chunk)
            if size > 4 * 1024**3:
                destination.unlink(missing_ok=True)
                raise HTTPException(413, "O arquivo ultrapassa o limite de 4 GB")
            target.write(chunk)
    if size < 1024:
        destination.unlink(missing_ok=True)
        raise HTTPException(400, "O arquivo enviado está vazio")
    if subtitle and subtitle.filename:
        if Path(subtitle.filename).suffix.lower() != ".srt":
            destination.unlink(missing_ok=True)
            raise HTTPException(400, "A legenda opcional precisa estar em SRT")
        subtitle_path = folder / "source-upload.pt.srt"
        with subtitle_path.open("wb") as target:
            subtitle_size = 0
            while chunk := await subtitle.read(256 * 1024):
                subtitle_size += len(chunk)
                if subtitle_size > 20 * 1024**2:
                    destination.unlink(missing_ok=True)
                    subtitle_path.unlink(missing_ok=True)
                    raise HTTPException(413, "A legenda ultrapassa o limite de 20 MB")
                target.write(chunk)
    return DB.create(job_id, f"upload:{video.filename}", clip_count, min_duration, max_duration, str(destination))


@app.post("/api/jobs/{job_id}/retry", status_code=202)
def retry(job_id: str) -> dict:
    result = DB.get(job_id)
    if not result:
        raise HTTPException(404, "Trabalho não encontrado")
    if result["status"] == "processing":
        raise HTTPException(409, "O trabalho ainda está em processamento")
    source = Path(result.get("source_path") or "")
    if source.is_file():
        DB.update(job_id, status="queued", stage="Recuperando cortes já renderizados", progress=0, error=None, outputs=[])
    elif is_youtube_url(result["url"]):
        DB.update(job_id, status="awaiting_download", stage="Aguardando o PC local", progress=0, error=None, outputs=[])
    else:
        DB.update(job_id, status="queued", stage="Na fila para nova tentativa", progress=0, error=None, outputs=[])
    return DB.get(job_id)


@app.get("/api/worker/jobs/next")
def worker_next(request: Request) -> dict:
    worker_name = require_worker(request)
    job = DB.claim_download(worker_name)
    if not job:
        return {"job": None}
    return {"job": {"id": job["id"], "url": job["url"]}}


@app.post("/api/worker/jobs/{job_id}/upload", status_code=202)
async def worker_upload(
    job_id: str, request: Request, video: UploadFile = File(...), subtitle: UploadFile | None = File(None),
    title: str | None = Form(None), source_author: str | None = Form(None),
    source_license: str | None = Form(None),
) -> dict:
    worker_name = require_worker(request)
    job = DB.get(job_id)
    if not job or job["status"] not in {"downloading", "awaiting_download"}:
        raise HTTPException(409, "O trabalho não está aguardando este arquivo")
    suffix = Path(video.filename or "").suffix.lower()
    if suffix not in {".mp4", ".mov", ".mkv", ".webm", ".m4v"}:
        raise HTTPException(400, "Formato de vídeo inválido")
    folder = DATA / "jobs" / job_id
    folder.mkdir(parents=True, exist_ok=True)
    destination = folder / f"source-local{suffix}"
    size = 0
    with destination.open("wb") as target:
        while chunk := await video.read(1024 * 1024):
            size += len(chunk)
            if size > 4 * 1024**3:
                destination.unlink(missing_ok=True)
                raise HTTPException(413, "O arquivo ultrapassa o limite de 4 GB")
            target.write(chunk)
    if size < 1024:
        destination.unlink(missing_ok=True)
        raise HTTPException(400, "O arquivo enviado está vazio")
    if subtitle and subtitle.filename:
        subtitle_suffix = Path(subtitle.filename).suffix.lower()
        if subtitle_suffix in {".srt", ".vtt"}:
            subtitle_path = folder / f"source-local{subtitle_suffix}"
            subtitle_path.write_bytes(await subtitle.read(20 * 1024**2 + 1))
            if subtitle_path.stat().st_size > 20 * 1024**2:
                subtitle_path.unlink(missing_ok=True)
                destination.unlink(missing_ok=True)
                raise HTTPException(413, "A legenda ultrapassa o limite de 20 MB")
            if subtitle_suffix == ".vtt":
                from .pipeline import run
                converted = folder / "source-local.srt"
                run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(subtitle_path), str(converted)], timeout=120)
    DB.worker_seen(worker_name)
    metadata = {
        "title": (title or job.get("title") or "")[:300] or None,
        "source_author": (source_author or job.get("source_author") or "")[:200] or None,
        "source_license": (source_license or job.get("source_license") or "")[:200] or None,
    }
    DB.update(
        job_id, source_path=str(destination), status="queued", stage="Vídeo recebido do PC",
        progress=15, error=None, **metadata,
    )
    return {"status": "accepted", "job_id": job_id}


@app.post("/api/worker/jobs/{job_id}/fail")
def worker_fail(job_id: str, request: Request, error: str = Form("Falha ao obter o vídeo")) -> dict:
    worker_name = require_worker(request)
    job = DB.get(job_id)
    if not job:
        raise HTTPException(404, "Trabalho não encontrado")
    DB.worker_seen(worker_name)
    clean = " ".join(error.split())[-400:]
    DB.update(job_id, status="failed", stage="Falha no PC local", progress=100, error=f"Worker local: {clean}")
    return {"status": "recorded"}


@app.get("/jobs/{job_id}/files/{filename}")
def file(job_id: str, filename: str):
    if not re_safe(filename):
        raise HTTPException(400, "Nome de arquivo inválido")
    result = DB.get(job_id)
    if not result:
        raise HTTPException(404, "Trabalho não encontrado")
    allowed = {item.get("file") for item in result["outputs"]} | {item.get("subtitle") for item in result["outputs"]}
    if filename not in allowed:
        raise HTTPException(404, "Arquivo não encontrado")
    path = DATA / "jobs" / job_id / filename
    if not path.is_file():
        raise HTTPException(404, "Arquivo não encontrado")
    return FileResponse(path, filename=filename, media_type="video/mp4" if filename.endswith(".mp4") else "text/plain")


def re_safe(value: str) -> bool:
    return value.replace("-", "").replace(".", "").isalnum() and "/" not in value and "\\" not in value


# LIVE uses independent capture jobs; outputs share the existing review/file routes.
from .live import LiveManager, PipelineError as LiveError

LIVE = LiveManager(DB, DATA)


@app.get("/live", response_class=HTMLResponse)
def live_screen(request: Request):
    return templates.TemplateResponse(request, "live.html", {"limit": LIVE.limit, "window_minutes": round(LIVE.window / 60)})


@app.get("/api/live")
def live_jobs() -> list[dict]:
    return [job for job in DB.list(1000) if job["id"].startswith("live-")]


@app.post("/api/live", status_code=202)
def create_live(
    url: str = Form(...), rights_confirmed: bool = Form(False),
    min_duration: int = Form(45), max_duration: int = Form(65),
) -> dict:
    try:
        return LIVE.start(url, rights_confirmed, min_duration, max_duration)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except LiveError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.post("/api/live/{job_id}/stop")
def stop_live(job_id: str) -> dict:
    try:
        return LIVE.stop_job(job_id)
    except KeyError as exc:
        raise HTTPException(404, "Live não encontrada") from exc
    except LiveError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.get("/api/live/{job_id}/preview")
def live_preview(job_id: str):
    from .pipeline import run
    session = LIVE.sessions.get(job_id)
    if not session or not session.retained:
        raise HTTPException(404, "Aguardando o primeiro segmento de 60 segundos")
    with session.preview_lock:
        with session.lock:
            source = session.retained[-1].path
            session.preview_protected.add(source)
        try:
            target = session.folder / "live-preview.mp4"
            temporary = session.folder / "live-preview-tmp.mp4"
            if getattr(session, "preview_source", None) != source or not target.exists():
                session.check_space()
                run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", str(source),
                     "-c", "copy", "-movflags", "+faststart", str(temporary)], timeout=30)
                temporary.replace(target)
                session.preview_source = source
        except LiveError as exc:
            raise HTTPException(409, "Não foi possível preparar a prévia da LIVE") from exc
        finally:
            temporary.unlink(missing_ok=True)
            with session.lock:
                session.preview_protected.discard(source)
                session._rotate()
    return FileResponse(target, media_type="video/mp4", headers={"Cache-Control": "no-store"})


from fastapi import APIRouter


@asynccontextmanager
async def live_lifespan(_: FastAPI):
    LIVE.recover_interrupted()
    try:
        yield
    finally:
        LIVE.shutdown()


app.include_router(APIRouter(lifespan=live_lifespan))

@app.get('/api/review')
def review_list(request: Request):
    require_worker(request)
    from . import review
    return review.list_items(DB)

class ReviewDecision(BaseModel):
    id: int
    choice: int | str

@app.post('/api/review')
def review_decide(payload: ReviewDecision, request: Request):
    require_worker(request)
    from . import review
    try:
        return {'chosen_title': review.decide(payload.id, payload.choice, DB)}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
