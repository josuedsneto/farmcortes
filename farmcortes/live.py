"""Live capture and incremental editorial cuts; no publishing or access workarounds.

Server configuration: FARMCORTES_LIVE_LIMIT=2 and
FARMCORTES_LIVE_WINDOW_SECONDS=7200 (minimum 600). Set
FARMCORTES_LIVE_WHISPER_MODEL to a locally provisioned Whisper model, or inherit
FARMCORTES_WHISPER_MODEL (default small, CPU/int8). Models are never downloaded.
Run a single application worker so stop/preview requests reach the owner;
file locks also enforce capture capacity across manager instances.
Copy segmentation follows source keyframes, so 60-second boundaries are approximate.
"""
from __future__ import annotations

import csv
import fcntl
import logging
import math
import os
import queue
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, replace
from pathlib import Path
from urllib.parse import urlparse

from .db import Database
from .pipeline import (
    Caption, PipelineError, YOUTUBE_HOSTS, captions_from_segments,
    output_records, probe, render, run, select_candidates, write_srt,
)

log = logging.getLogger(__name__)
MIN_FREE = 5 * 1024**3
LIVE_HOSTS = YOUTUBE_HOSTS | {"twitch.tv", "www.twitch.tv", "kick.com", "www.kick.com"}


def validate_live_url(value: str) -> str:
    try:
        parsed = urlparse(value.strip())
        if (parsed.scheme != "https" or parsed.hostname not in LIVE_HOSTS
                or parsed.username or parsed.password or parsed.port not in (None, 443)
                or not parsed.path.strip("/")):
            raise ValueError
    except ValueError as exc:
        raise ValueError("Use uma URL HTTPS de live do Twitch, Kick ou YouTube") from exc
    return value.strip()


@dataclass(frozen=True)
class Segment:
    path: Path
    start: float
    duration: float
    epoch: int = 0


def rotate_window(segments: deque, window: float, protected: set[Path] | None = None) -> None:
    """Discard only media outside the rolling window, preserving in-flight inputs."""
    if not segments:
        return
    cutoff = segments[-1].start + segments[-1].duration - window
    while segments and segments[0].start + segments[0].duration <= cutoff:
        if segments[0].path in (protected or set()):
            break
        old = segments.popleft()
        old.path.unlink(missing_ok=True)
        old.path.with_suffix(".srt").unlink(missing_ok=True)


def producer_command(url: str) -> list[str]:
    if urlparse(url).hostname in YOUTUBE_HOSTS:
        return [sys.executable, "-m", "yt_dlp", "--ignore-config", "--proxy", "", "--no-playlist",
                "--socket-timeout", "20", "--live-from-start", "-f", "b", "-o", "-", url]
    return [sys.executable, "-m", "streamlink", "--no-config", "--stdout", "--http-timeout", "20",
            "--stream-timeout", "30", url, "best"]


def segment_command(source: str, folder: Path, seconds: float = 60) -> list[str]:
    return ["ffmpeg", "-nostdin", "-y", "-hide_banner", "-loglevel", "error",
            "-i", source, "-map", "0:v:0", "-map", "0:a:0?", "-c", "copy",
            "-f", "segment", "-segment_time", str(seconds), "-reset_timestamps", "1",
            "-segment_list", str(folder / "closed.csv"), "-segment_list_type", "csv",
            "-segment_list_size", "1024",
            str(folder / "segment-%06d.ts")]


def terminate(process: subprocess.Popen | None) -> None:
    if process is None or process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=3)
    except ProcessLookupError:
        pass


class LiveTranscriber:
    """Same CPU/int8, language and glossary configuration as the batch pipeline."""
    def __init__(self):
        self.model = None

    def __call__(self, source: Path, subtitle: Path) -> list[Caption]:
        if self.model is None:
            from faster_whisper import WhisperModel
            name = (os.getenv("FARMCORTES_LIVE_WHISPER_MODEL") or os.getenv("FARMCORTES_WHISPER_MODEL", "small")).strip() or "small"
            # Live must never trigger a model download. Provision the model locally first.
            try:
                self.model = WhisperModel(name, device="cpu", compute_type="int8", cpu_threads=4,
                                          local_files_only=True)
            except Exception as exc:
                raise PipelineError("Modelo Whisper local indisponível. Configure FARMCORTES_LIVE_WHISPER_MODEL com um modelo já instalado (padrão: small/int8).") from exc
        segments, info = self.model.transcribe(
            str(source), language="pt", beam_size=5, best_of=5, temperature=0,
            vad_filter=True, word_timestamps=True, condition_on_previous_text=True,
            initial_prompt="Transcrição fiel em português do Brasil. Preserve nomes próprios e pontuação. Não invente palavras.",
        )
        if float(getattr(info, "language_probability", 1) or 0) < .55:
            return []
        captions = captions_from_segments(segments)
        write_srt(captions, subtitle)
        return captions


def rms_levels(source: Path) -> list[float]:
    import numpy as np
    if not any(s.get("codec_type") == "audio" for s in probe(source).get("streams", [])):
        return []
    result = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-i", str(source),
                             "-vn", "-ac", "1", "-ar", "8000", "-f", "f32le", "pipe:1"],
                            capture_output=True, check=True, timeout=90)
    samples = np.frombuffer(result.stdout, dtype="<f4")
    return [float(np.sqrt(np.mean(samples[i:i + 8000] ** 2))) for i in range(0, len(samples), 8000)]


class LiveSession:
    def __init__(self, manager, job: dict, *, local_source: Path | None = None,
                 segment_seconds: float = 60, transcriber=None, render_fn=render):
        self.manager, self.db, self.job = manager, manager.db, job
        self.folder = manager.data / "jobs" / job["id"]
        self.folder.mkdir(parents=True, exist_ok=True)
        self.local_source = local_source  # Internal, offline test seam; never accepted by the HTTP API.
        self.segment_seconds = segment_seconds
        self.transcribe = transcriber or LiveTranscriber()
        self.render = render_fn
        self.stop = threading.Event()
        self.finished = threading.Event()
        self.pending = queue.Queue(maxsize=3)
        self.error: Exception | None = None
        self.retained = deque()
        self.context = deque()
        self.protected: set[Path] = set()
        self.preview_protected: set[Path] = set()
        self.lock = threading.Lock()
        self.preview_lock = threading.Lock()
        self.last_cut = 0.0
        self.clock = 0.0

    def check_space(self):
        if shutil.disk_usage(self.folder).free < MIN_FREE:
            raise PipelineError("LIVE interrompida: menos de 5 GB livres no disco. Libere espaço antes de retomar.")

    def _rotate(self):
        # Caller holds lock. Context, queued inputs and preview readers all own media.
        rotate_window(self.retained, self.manager.window, self.protected | self.preview_protected)

    def _consume(self):
        try:
            while True:
                segment = self.pending.get()
                if segment is None:
                    break
                self.check_space()
                captions = self.transcribe(segment.path, segment.path.with_suffix(".srt"))
                levels = rms_levels(segment.path)
                if self.context and self.context[-1][0].epoch != segment.epoch:
                    with self.lock:
                        self.protected.difference_update(s.path for s, _, _ in self.context)
                    self.context.clear()  # Never join speech across a network gap.
                self.context.append((segment, captions, levels))
                while len(self.context) > math.ceil(self.job["max_duration"] / self.segment_seconds) + 1:
                    old, _, _ = self.context.popleft()
                    with self.lock:
                        self.protected.discard(old.path)
                self._cut()
                with self.lock:
                    self._rotate()
        except Exception as exc:
            self.error = exc
            self.stop.set()

    def _cut(self):
        base = self.context[0][0].start
        captions, levels = [], []
        for segment, items, rms in self.context:
            offset = segment.start - base
            captions.extend(Caption(c.start + offset, c.end + offset, c.text) for c in items)
            levels.extend(rms)
        end = self.context[-1][0].start + self.context[-1][0].duration - base
        usable = [c for c in captions if c.start + base >= self.last_cut]
        try:
            candidates = select_candidates(usable, 80, self.job["min_duration"], self.job["max_duration"])
        except PipelineError:
            return  # Silence or an incomplete thought is normal during a live.
        complete = [c for c in candidates if c.start + base >= self.last_cut
                    and c.end <= end and c.text.rstrip().endswith((".", "!", "?"))]
        if not complete:
            return
        average = sum(levels) / max(1, len(levels))
        def boost(candidate):
            peak = max(levels[int(candidate.start):math.ceil(candidate.end)] or [0])
            return min(2., peak / max(average, 1e-6)) * .5
        candidate = max(complete, key=lambda c: c.score + boost(c))
        candidate = replace(candidate, score=candidate.score + boost(candidate),
                            quality={**(candidate.quality or {}), "rms_boost": round(boost(candidate), 3)})
        self.check_space()
        listing = self.folder / "live-concat.txt"
        listing.write_text("".join(f"file '{s.path.relative_to(self.folder).as_posix()}'\n" for s, _, _ in self.context))
        source = self.folder / "live-window.ts"
        try:
            run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-f", "concat", "-safe", "1",
                 "-i", str(listing), "-c", "copy", str(source)], timeout=90)
            current = self.db.get(self.job["id"])
            number = len(current["outputs"]) + 1
            name = f"corte-{number:04d}"
            self.render(source, captions, candidate, self.folder / f"{name}.mp4", self.folder / f"{name}.srt")
            record = output_records([replace(candidate, start=candidate.start + base, end=candidate.end + base)])[0]
            record.update(file=f"{name}.mp4", subtitle=f"{name}.srt")
            self.last_cut = candidate.end + base
            self.db.update(self.job["id"], outputs=current["outputs"] + [record], stage=f"Gravando · {number} cortes gerados")
        finally:
            source.unlink(missing_ok=True)
            listing.unlink(missing_ok=True)

    def capture(self, epoch: int) -> int:
        folder = self.folder / f"capture-{epoch:04d}"
        folder.mkdir()
        producer = recorder = None
        seen = set()
        start_clock = self.clock
        try:
            self.check_space()
            if self.local_source:
                source = str(self.local_source.resolve())
                stdin = None
            else:
                env = {k: v for k, v in os.environ.items() if k.lower() not in {"http_proxy", "https_proxy", "all_proxy"}}
                producer = subprocess.Popen(producer_command(self.job["url"]), stdout=subprocess.PIPE,
                                            stderr=subprocess.DEVNULL, start_new_session=True, env=env)
                source, stdin = "pipe:0", producer.stdout
            recorder = subprocess.Popen(segment_command(source, folder, self.segment_seconds), stdin=stdin,
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            if producer:
                producer.stdout.close()
            last_closed = time.monotonic()
            while True:
                self.check_space()
                if self.stop.is_set():
                    terminate(recorder)
                    terminate(producer)
                done = recorder.poll() is not None
                listing = folder / "closed.csv"
                if listing.exists():
                    rows = list(csv.reader(listing.read_text().splitlines()))
                    seen.intersection_update(row[0] for row in rows if row)
                    for row in rows:
                        if len(row) != 3 or row[0] in seen:
                            continue
                        try:
                            start, end = float(row[1]), float(row[2])
                        except ValueError:
                            continue  # Writer may still be flushing its last row.
                        path = folder / Path(row[0]).name
                        if not path.exists() or end <= start:
                            continue
                        seen.add(row[0])
                        last_closed = time.monotonic()
                        segment = Segment(path, start_clock + start, end - start, epoch)
                        self.clock = segment.start + segment.duration
                        with self.lock:
                            self.protected.add(path)
                            self.retained.append(segment)
                            self._rotate()
                        try:
                            self.pending.put_nowait(segment)
                        except queue.Full as exc:
                            raise PipelineError("LIVE interrompida: processamento atrasado mais de 3 segmentos; reduza o modelo ou a carga do servidor.") from exc
                        self.db.update(self.job["id"], source_duration=self.clock)
                if done:
                    try:
                        producer_code = producer.wait(timeout=5) if producer else 0
                    except subprocess.TimeoutExpired:
                        return 1
                    return recorder.returncode or producer_code or (0 if seen else 1)
                if time.monotonic() - last_closed > max(120, self.segment_seconds * 3):
                    return 1  # A stalled connection must also enter reconnect/backoff.
                self.stop.wait(.2)
        finally:
            terminate(recorder)
            terminate(producer)
            if producer and producer.stdout:
                producer.stdout.close()

    def run(self):
        consumer = threading.Thread(target=self._consume, daemon=True)
        consumer.start()
        try:
            for attempt in range(7):
                code = self.capture(attempt)
                if self.stop.is_set() or code == 0:
                    break
                if urlparse(self.job["url"]).hostname in YOUTUBE_HOSTS:
                    raise PipelineError("YouTube indisponível ou bloqueado: acesso sem cookies, proxy ou PO token não foi possível.")
                if attempt == 6:
                    raise PipelineError("LIVE sem conexão após 6 tentativas de retomada.")
                delay = min(60, 2 ** (attempt + 1))
                self.db.update(self.job["id"], stage=f"Reconectando em {delay} s")
                if self.stop.wait(delay):
                    break
            if self.error:
                raise self.error
        except Exception as exc:
            self.error = exc
        finally:
            # Drain already closed segments before releasing the concurrency slot.
            while consumer.is_alive():
                try:
                    self.pending.put(None, timeout=.2)
                    break
                except queue.Full:
                    continue
            consumer.join()
            with self.lock:
                self.protected.clear()
                self._rotate()
            with self.manager.lock:
                count = len(self.db.get(self.job["id"])["outputs"])
                self.db.update(self.job["id"], status="live_failed" if self.error else "live_done",
                               stage="Erro na LIVE" if self.error else f"LIVE encerrada · {count} cortes gerados",
                               progress=100, error=str(self.error)[:600] if self.error else None)
                self.finished.set()


class LiveManager:
    def __init__(self, db: Database, data: Path, *, limit: int | None = None, window: float | None = None):
        self.db, self.data = db, data
        self.limit = max(1, limit if limit is not None else int(os.getenv("FARMCORTES_LIVE_LIMIT", "2")))
        self.window = max(600, window if window is not None else float(os.getenv("FARMCORTES_LIVE_WINDOW_SECONDS", "7200")))
        self.sessions: dict[str, LiveSession] = {}
        self.lock = threading.Lock()

    def start(self, url: str, rights_confirmed: bool, minimum: int = 45, maximum: int = 65, **session_options) -> dict:
        if not rights_confirmed:
            raise ValueError("Confirme: tenho autorização para cortar esta live")
        url = validate_live_url(url)
        if not 15 <= minimum <= 90 or not minimum < maximum <= 180:
            raise ValueError("A duração escolhida é inválida")
        lockdir = self.data / "live-locks"
        lockdir.mkdir(parents=True, exist_ok=True)
        slot = None
        with self.lock:
            for number in range(self.limit):
                handle = (lockdir / f"slot-{number}.lock").open("a+")
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    slot = handle
                    break
                except BlockingIOError:
                    handle.close()
            if slot is None:
                raise PipelineError(f"Limite de {self.limit} lives simultâneas atingido")
            try:
                if shutil.disk_usage(self.data).free < MIN_FREE:
                    raise PipelineError("LIVE indisponível: menos de 5 GB livres no disco")
                job = self.db.create("live-" + uuid.uuid4().hex[:12], url, 1, minimum, maximum,
                                     status="live_recording", stage="Gravando LIVE", title="LIVE · " + url,
                                     auto_publish=False)
                slot.seek(0)
                slot.truncate()
                slot.write(job["id"])
                slot.flush()
                session = LiveSession(self, job, **session_options)
                self.sessions[job["id"]] = session
                def work():
                    try:
                        session.run()
                    finally:
                        slot.close()
                        with self.lock:
                            self.sessions.pop(job["id"], None)
                threading.Thread(target=work, name=job["id"], daemon=True).start()
                return job
            except Exception:
                slot.close()
                raise

    def stop_job(self, job_id: str) -> dict:
        with self.lock:
            session = self.sessions.get(job_id)
            job = self.db.get(job_id)
            if not job or not job_id.startswith("live-"):
                raise KeyError(job_id)
            if session and job["status"] in {"live_recording", "live_stopping"}:
                self.db.update(job_id, status="live_stopping", stage="Parando LIVE · finalizando segmentos")
                session.stop.set()
            elif job["status"] in {"live_recording", "live_stopping"}:
                raise PipelineError("Esta live pertence a outro processo; use uma única instância do servidor LIVE")
            return self.db.get(job_id)

    def recover_interrupted(self):
        active = set()
        for path in (self.data / "live-locks").glob("slot-*.lock"):
            with path.open("r+") as handle:
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    active.add(handle.read().strip())
        for job in self.db.list(10000):
            if job["status"] in {"live_recording", "live_stopping"} and job["id"] not in active:
                self.db.update(job["id"], status="live_failed", stage="LIVE interrompida",
                               error="Servidor interrompido. Inicie uma nova LIVE; os cortes anteriores foram preservados.")

    def shutdown(self):
        with self.lock:
            sessions = list(self.sessions.values())
        for session in sessions:
            session.stop.set()
        for session in sessions:
            session.finished.wait(5)
