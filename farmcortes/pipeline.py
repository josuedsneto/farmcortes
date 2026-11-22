from __future__ import annotations

import html
import json
import logging
import math
import os
import re
import subprocess
from functools import lru_cache
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from .db import Database
from .strategy import clean_transcript, hook_options, score_narrative, title_options

log = logging.getLogger(__name__)

YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be", "music.youtube.com"}
STORAGE_HOSTS = {
    "drive.google.com",
    "docs.google.com",
    "dropbox.com",
    "www.dropbox.com",
    "dl.dropboxusercontent.com",
}
ALLOWED_HOSTS = YOUTUBE_HOSTS | STORAGE_HOSTS
HOOK_WORDS = {
    "como", "porque", "segredo", "verdade", "erro", "nunca", "sempre", "melhor", "pior",
    "dinheiro", "resultado", "mudou", "descobri", "ninguém", "importante", "problema", "solução",
    "surpreendente", "impossível", "atenção", "aprendi", "motivo", "razão",
}
WEAK_WORDS = {"inscreva", "canal", "patrocinador", "obrigado", "bem-vindos", "introdução", "cupom"}


class PipelineError(RuntimeError):
    pass


@lru_cache(maxsize=1)
def caption_glossary() -> dict[str, str]:
    path = Path(__file__).resolve().parent.parent / "caption-glossary.json"
    if not path.is_file():
        return {}
    content = json.loads(path.read_text(encoding="utf-8"))
    return {str(source): str(target) for source, target in content.items() if source and target}


def apply_caption_glossary(text: str) -> str:
    result = text
    for source, target in caption_glossary().items():
        result = re.sub(rf"(?<!\w){re.escape(source)}(?!\w)", target, result, flags=re.IGNORECASE)
    return result


@dataclass(frozen=True)
class Caption:
    start: float
    end: float
    text: str


@dataclass(frozen=True)
class Candidate:
    start: float
    end: float
    score: float
    text: str
    title: str
    title_options: tuple[str, ...] = ()
    hook_options: tuple[str, ...] = ()
    quality: dict | None = None


def validate_source_url(value: str) -> str:
    try:
        parsed = urlparse(value.strip())
    except ValueError as exc:
        raise ValueError("Link inválido") from exc
    if parsed.scheme != "https" or (parsed.hostname or "").lower() not in ALLOWED_HOSTS:
        raise ValueError("Use um link HTTPS do YouTube, Google Drive ou Dropbox")
    return value.strip()


def validate_youtube_url(value: str) -> str:
    """Backward-compatible validator kept for existing callers and tests."""
    result = validate_source_url(value)
    if (urlparse(result).hostname or "").lower() not in YOUTUBE_HOSTS:
        raise ValueError("Use um link público do YouTube")
    return result


def is_youtube_url(value: str) -> bool:
    try:
        return (urlparse(value).hostname or "").lower() in YOUTUBE_HOSTS
    except ValueError:
        return False


def run(command: list[str], *, cwd: Path | None = None, timeout: int = 7200) -> subprocess.CompletedProcess[str]:
    log.info("running %s", command[0])
    try:
        return subprocess.run(command, cwd=cwd, check=True, text=True, capture_output=True, timeout=timeout)
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or str(exc)).strip()[-1800:]
        raise PipelineError(detail) from exc
    except subprocess.TimeoutExpired as exc:
        raise PipelineError(f"A etapa excedeu {timeout // 60} minutos") from exc


def probe(path: Path) -> dict:
    result = run([
        "ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type,width,height",
        "-of", "json", str(path),
    ], timeout=120)
    return json.loads(result.stdout)


def classify_import_error(detail: str) -> str:
    lower = detail.casefold()
    if "confirm you’re not a bot" in lower or "confirm you're not a bot" in lower or "sign in to confirm" in lower:
        return "youtube_bot_check"
    if "private video" in lower:
        return "private"
    if "video unavailable" in lower or "not available" in lower:
        return "unavailable"
    if "copyright" in lower:
        return "copyright"
    return "other"


def acquire_media(url: str, folder: Path) -> tuple[Path, float, str]:
    info_result = run(["yt-dlp", "--js-runtimes", "node", "--no-playlist", "--dump-single-json", "--skip-download", url], cwd=folder, timeout=180)
    info = json.loads(info_result.stdout)
    duration = float(info.get("duration") or 0)
    run([
        "yt-dlp", "--js-runtimes", "node", "--no-playlist", "--max-filesize", "4G", "--write-subs",
        "--sub-langs", "pt.*,en.*", "--sub-format", "srt", "--convert-subs", "srt",
        "-f", "bv*[height<=1080]+ba/b[height<=1080]", "--merge-output-format", "mp4",
        "-o", "source.%(ext)s", url,
    ], cwd=folder)
    source = next((p for p in folder.glob("source.*") if p.suffix.lower() in {".mp4", ".mkv", ".webm", ".mov"}), None)
    if not source:
        raise PipelineError("O link não entregou um arquivo de vídeo utilizável")
    return source, duration, str(info.get("title") or "Vídeo sem título")[:300]


def _clock(value: str) -> float:
    parts = value.replace(",", ".").split(":")
    if len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    return 0.0


def parse_srt(path: Path) -> list[Caption]:
    text = path.read_text(encoding="utf-8", errors="replace")
    blocks = re.split(r"\n\s*\n", text.replace("\r", "").strip())
    captions: list[Caption] = []
    for block in blocks:
        lines = block.splitlines()
        timing_index = next((i for i, line in enumerate(lines) if " --> " in line), None)
        if timing_index is None:
            continue
        start_text, end_text = lines[timing_index].split(" --> ", 1)
        clean = " ".join(lines[timing_index + 1 :])
        clean = html.unescape(re.sub(r"<[^>]+>", "", clean)).strip()
        clean = re.sub(r"\s+", " ", clean)
        if clean:
            start = _clock(start_text.strip())
            end = _clock(end_text.split()[0].strip())
            words = clean.split()
            if len(words) <= 6:
                captions.append(Caption(start, end, clean))
                continue
            groups = [words[index : index + 4] for index in range(0, len(words), 4)]
            weights = [sum(len(word) + 1 for word in group) for group in groups]
            cursor = start
            total_weight = max(1, sum(weights))
            for index, (group, weight) in enumerate(zip(groups, weights)):
                group_end = end if index == len(groups) - 1 else cursor + (end - start) * weight / total_weight
                captions.append(Caption(cursor, group_end, " ".join(group)))
                cursor = group_end
    return captions


def transcribe(source: Path, output: Path, *, context: str | None = None) -> list[Caption]:
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise PipelineError("O vídeo não tem legenda e o transcritor local não está instalado") from exc
    model_name = os.getenv("FARMCORTES_WHISPER_MODEL", "small").strip() or "small"
    model = WhisperModel(model_name, device="cpu", compute_type="int8", cpu_threads=4)
    prompt = (
        "Transcrição fiel em português do Brasil. Preserve nomes próprios, pontuação, números e o sentido "
        "das piadas. Não invente palavras."
    )
    if context:
        prompt += f" Contexto do vídeo: {context[:220]}."
    segments, info = model.transcribe(
        str(source), language="pt", beam_size=5, best_of=5, temperature=0,
        vad_filter=True, word_timestamps=True, condition_on_previous_text=True,
        initial_prompt=prompt,
    )
    if float(getattr(info, "language_probability", 1.0) or 0) < 0.55:
        raise PipelineError("O áudio não pôde ser reconhecido com confiança suficiente em português")
    captions = captions_from_segments(segments)
    write_srt(captions, output)
    return captions


def captions_from_segments(segments) -> list[Caption]:
    """Group faster-whisper words into captions of up to ~3.3 s or one sentence."""
    captions: list[Caption] = []
    for segment in segments:
        words = list(segment.words or [])
        if words:
            bucket: list = []
            for word in words:
                bucket.append(word)
                duration = bucket[-1].end - bucket[0].start
                if duration >= 3.3 or re.search(r"[.!?]$", word.word.strip()):
                    captions.append(Caption(
                        bucket[0].start, bucket[-1].end,
                        apply_caption_glossary("".join(w.word for w in bucket).strip()),
                    ))
                    bucket = []
            if bucket:
                captions.append(Caption(
                    bucket[0].start, bucket[-1].end,
                    apply_caption_glossary("".join(w.word for w in bucket).strip()),
                ))
        elif segment.text.strip():
            captions.append(Caption(segment.start, segment.end, apply_caption_glossary(segment.text.strip())))
    return captions


def _title(text: str) -> str:
    return title_options(text)[0]


def _score(text: str, duration: float, target: float) -> float:
    lower = text.casefold()
    tokens = re.findall(r"[\wÀ-ÿ]+", lower)
    if not tokens:
        return -1
    hook = sum(1 for word in HOOK_WORDS if re.search(rf"\b{re.escape(word)}\b", lower))
    weak = sum(1 for word in WEAK_WORDS if word in lower)
    numeric = min(2, len(re.findall(r"\b\d+(?:[.,]\d+)?%?\b", text)))
    questions = min(2, text.count("?"))
    uniqueness = len(set(tokens)) / len(tokens)
    completeness = 1.0 if re.search(r"[.!?][\"']?$", text.strip()) else 0.2
    duration_fit = max(0.0, 1 - abs(duration - target) / target)
    density = min(1.0, len(tokens) / max(duration * 2.0, 1))
    strategy = score_narrative(text, duration, max(15, int(target - 20)), min(180, int(target + 20)))
    return 2.0 * duration_fit + 1.1 * uniqueness + 0.45 * hook + 0.2 * numeric + 0.2 * questions + 0.6 * completeness + 0.6 * density + 0.35 * strategy["total"] - 1.1 * weak


def select_candidates(captions: list[Caption], count: int, minimum: int, maximum: int) -> list[Candidate]:
    if not captions:
        raise PipelineError("Não foi encontrada fala utilizável")
    target = (minimum + maximum) / 2
    all_candidates: list[Candidate] = []
    for start_index in range(0, len(captions), max(1, len(captions) // 80)):
        start = max(0.0, captions[start_index].start - 0.12)
        parts: list[str] = []
        for end_index in range(start_index, len(captions)):
            item = captions[end_index]
            parts.append(item.text)
            duration = item.end + 0.12 - start
            if duration > maximum:
                break
            if duration >= minimum and (re.search(r"[.!?]$", item.text.strip()) or duration >= target):
                body = clean_transcript(" ".join(parts))
                quality = score_narrative(body, duration, minimum, maximum)
                titles = tuple(title_options(body))
                hooks = tuple(hook_options(body))
                all_candidates.append(Candidate(
                    start, item.end + 0.12, _score(body, duration, target), body, titles[0],
                    titles, hooks, quality,
                ))
    chosen: list[Candidate] = []
    eligible = [item for item in all_candidates if (item.quality or {}).get("editorial_ready")]
    for candidate in sorted(eligible, key=lambda item: item.score, reverse=True):
        if any(min(candidate.end, other.end) > max(candidate.start, other.start) for other in chosen):
            continue
        chosen.append(candidate)
        if len(chosen) >= count:
            break
    if not chosen:
        raise PipelineError("O vídeo não contém trechos com gancho, contexto e final completos na duração escolhida")
    return sorted(chosen, key=lambda item: item.start)


def select_timeline_candidates(duration: float, count: int, minimum: int, maximum: int) -> list[Candidate]:
    """Create evenly distributed clips when the user explicitly disables transcription."""
    if duration <= 0 or count <= 0:
        raise PipelineError("A duração do vídeo não permite selecionar trechos sem legenda")
    clip_duration = min(float(maximum), max(float(minimum), (minimum + maximum) / 2))
    if clip_duration > duration:
        clip_duration = duration
    candidates = []
    for index in range(count):
        center = duration * (index + 1) / (count + 1)
        start = min(max(0.0, center - clip_duration / 2), max(0.0, duration - clip_duration))
        end = min(duration, start + clip_duration)
        candidates.append(Candidate(
            start=start, end=end, score=0.0, text="",
            title=f"Trecho selecionado {index + 1}", quality={
                "editorial_ready": True, "selection_method": "timeline_without_transcription",
            },
        ))
    return candidates


def _srt_clock(seconds: float) -> str:
    millis = max(0, round(seconds * 1000))
    hours, rest = divmod(millis, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    secs, ms = divmod(rest, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"


def write_srt(captions: list[Caption], path: Path, offset: float = 0.0, limit: float | None = None) -> None:
    rows = []
    for item in captions:
        start, end = max(0, item.start - offset), item.end - offset
        if end <= 0 or (limit is not None and start >= limit):
            continue
        if limit is not None:
            end = min(end, limit)
        rows.append(f"{len(rows)+1}\n{_srt_clock(start)} --> {_srt_clock(end)}\n{item.text}\n")
    path.write_text("\n".join(rows), encoding="utf-8")


def write_ass(
    captions: list[Caption], path: Path, offset: float = 0.0,
    limit: float | None = None, hook: str | None = None, subscribe_cta: bool = True,
) -> None:
    header = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Captions,DejaVu Sans,58,&H00FFFFFF,&H00FFFFFF,&H00111111,&H70000000,-1,0,0,0,100,100,0,0,3,1,0,8,80,80,190,1
Style: Hook,DejaVu Sans,68,&H00FFFFFF,&H00FFFFFF,&H00111111,&H90000000,-1,0,0,0,100,100,0,0,3,2,0,2,90,90,260,1
Style: Subscribe,DejaVu Sans,56,&H00FFFFFF,&H00FFFFFF,&H00111111,&HCCB3202A,-1,0,0,0,100,100,0,0,3,2,0,2,90,90,330,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    rows = [header]
    if hook:
        clean_hook = hook.replace("{", "(").replace("}", ")").replace("\n", r"\N")
        rows.append(f"Dialogue: 1,0:00:00.00,0:00:02.40,Hook,,0,0,0,,{clean_hook}\n")
    duration = limit if limit is not None else max((item.end - offset for item in captions), default=0.0)
    if subscribe_cta and duration >= 5:
        start = min(2.8, max(0.5, duration - 4.5))
        end = min(duration - 0.2, start + 4.2)
        rows.append(
            f"Dialogue: 2,{_ass_clock(start)},{_ass_clock(end)},Subscribe,,0,0,0,,"
            r"{\fad(180,220)}GOSTOU? INSCREVA-SE NO CANAL"
            "\n"
        )
        if duration >= 24:
            final_start = max(end + 1.0, duration - 5.0)
            final_end = duration - 0.35
            rows.append(
                f"Dialogue: 2,{_ass_clock(final_start)},{_ass_clock(final_end)},Subscribe,,0,0,0,,"
                r"{\fad(180,220)}INSCREVA-SE • NÃO PERCA O PRÓXIMO CORTE"
                "\n"
            )
    for item in captions:
        start, end = max(0, item.start - offset), item.end - offset
        if end <= 0 or (limit is not None and start >= limit):
            continue
        if limit is not None:
            end = min(end, limit)
        clean = item.text.replace("{", "(").replace("}", ")").replace("\n", r"\N")
        rows.append(f"Dialogue: 0,{_ass_clock(start)},{_ass_clock(end)},Captions,,0,0,0,,{clean}\n")
    path.write_text("".join(rows), encoding="utf-8")


def _ass_clock(seconds: float) -> str:
    centis = max(0, round(seconds * 100))
    hours, rest = divmod(centis, 360_000)
    minutes, rest = divmod(rest, 6_000)
    secs, cs = divmod(rest, 100)
    return f"{hours}:{minutes:02d}:{secs:02d}.{cs:02d}"


def _escape_filter_path(path: Path) -> str:
    return str(path.resolve()).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def _smart_reframe_filter(source: Path, candidate: Candidate, output: Path, subtitle_path: str) -> str | None:
    """Build AutoClip's face/scene-aware crop graph, or return None for safe fallback."""
    try:
        from autoclip.pipeline.export import ExportRequest, build_video_filtergraph
        from autoclip.pipeline.reframe import ReframeConfig, build_crop_path

        crop_path = build_crop_path(
            source,
            start_s=candidate.start,
            end_s=candidate.end,
            transcript=None,
            config=ReframeConfig(aspect_w=9, aspect_h=16, sample_fps=3.0),
        )
        request = ExportRequest(
            source=source,
            destination=output,
            start_s=candidate.start,
            end_s=candidate.end,
            crop_path=crop_path,
            words=[],
            style=None,  # Captions are generated by Farmcortes below.
            ratio="9:16",
            burn_captions=False,
        )
        graph = build_video_filtergraph(request, subtitle_name=None)
        return f"{graph};[vout]subtitles='{subtitle_path}'[v]"
    except Exception as exc:
        log.warning("AutoClip smart reframe unavailable; using fitted fallback: %s", exc)
        return None


def render(source: Path, captions: list[Caption], candidate: Candidate, output: Path, subtitle: Path) -> None:
    duration = candidate.end - candidate.start
    relevant = [item for item in captions if item.end > candidate.start and item.start < candidate.end]
    write_srt(relevant, subtitle, offset=candidate.start, limit=duration)
    burn_subtitle = subtitle.with_suffix(".burn.ass")
    write_ass(relevant, burn_subtitle, offset=candidate.start, limit=duration, hook=candidate.hook_options[0] if candidate.hook_options else candidate.title)
    subtitle_path = _escape_filter_path(burn_subtitle)
    video_filter = _smart_reframe_filter(source, candidate, output, subtitle_path)
    if video_filter is None:
        video_filter = (
            "[0:v]split=2[bg][fg];"
            "[bg]scale=270:480:force_original_aspect_ratio=increase,crop=270:480,gblur=sigma=10,scale=1080:1920,eq=brightness=-0.18[back];"
            "[fg]scale=1080:1920:force_original_aspect_ratio=decrease[front];"
            "[back][front]overlay=(W-w)/2:(H-h)/2,"
            f"subtitles='{subtitle_path}'[v]"
        )
    fade_out = max(0.0, duration - 0.03)
    run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{candidate.start:.3f}", "-i", str(source),
        "-t", f"{duration:.3f}", "-filter_complex", video_filter, "-map", "[v]", "-map", "0:a:0?",
        "-af", f"loudnorm=I=-16:LRA=9:TP=-1.5,afade=t=in:st=0:d=0.03,afade=t=out:st={fade_out:.3f}:d=0.03",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-pix_fmt", "yuv420p", "-r", "30",
        "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(output),
    ])
    result = probe(output)
    streams = result.get("streams", [])
    if not output.exists() or output.stat().st_size < 100_000 or not any(s.get("codec_type") == "video" for s in streams):
        raise PipelineError("O corte renderizado não passou na verificação")
    output.with_suffix(".subscribe-v2").write_text("burned-ass-v2\n", encoding="utf-8")


def ensure_subscribe_cta(video: Path) -> None:
    """Backfill the CTA into an already-rendered clip exactly once."""
    marker = video.with_suffix(".subscribe-v2")
    if marker.is_file():
        return
    duration = float(probe(video).get("format", {}).get("duration") or 0)
    if duration < 5:
        raise PipelineError(f"O corte {video.name} é curto demais para receber a chamada de inscrição")
    text_file = video.parent / "subscribe-cta.txt"
    text_file.write_text("INSCREVA-SE NO CANAL • NÃO PERCA O PRÓXIMO CORTE", encoding="utf-8")
    escaped = _escape_filter_path(text_file)
    start = max(0.5, duration - 5.0)
    end = duration - 0.35
    enabled = f"between(t,{start:.3f},{end:.3f})"
    filtergraph = (
        f"drawbox=x=70:y=ih-450:w=iw-140:h=145:color=0xB3202A@0.94:t=fill:enable='{enabled}',"
        f"drawtext=fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf:textfile='{escaped}':"
        f"fontcolor=white:fontsize=48:x=(w-text_w)/2:y=h-402:enable='{enabled}'"
    )
    temporary = video.with_name(f"{video.stem}.subscribe-tmp.mp4")
    run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(video),
        "-vf", filtergraph, "-map", "0:v:0", "-map", "0:a:0?", "-c:v", "libx264",
        "-preset", "veryfast", "-crf", "22", "-pix_fmt", "yuv420p", "-c:a", "copy",
        "-movflags", "+faststart", str(temporary),
    ])
    result = probe(temporary)
    if temporary.stat().st_size < 100_000 or not any(s.get("codec_type") == "video" for s in result.get("streams", [])):
        temporary.unlink(missing_ok=True)
        raise PipelineError(f"O CTA não pôde ser aplicado ao corte {video.name}")
    temporary.replace(video)
    marker.write_text("overlay-v2\n", encoding="utf-8")


def output_records(candidates: list[Candidate]) -> list[dict]:
    return [
        {
            "file": f"corte-{index:02d}.mp4", "subtitle": f"corte-{index:02d}.srt",
            "title": candidate.title, "duration": round(float(candidate.end - candidate.start), 1),
            "source_start": round(float(candidate.start), 3), "source_end": round(float(candidate.end), 3),
            "score": round(float(candidate.score), 2), "excerpt": candidate.text,
            "title_options": list(candidate.title_options), "hook_options": list(candidate.hook_options),
            "quality": candidate.quality or {},
        }
        for index, candidate in enumerate(candidates, 1)
    ]


class Processor:
    def __init__(self, database: Database, data_dir: Path):
        self.database = database
        self.data_dir = data_dir

    def update(self, job_id: str, stage: str, progress: int, **extra) -> None:
        self.database.update(job_id, stage=stage, progress=progress, **extra)

    def _finish(self, job: dict, folder: Path, outputs: list[dict]) -> None:
        # Publishing happens only through review.publish_approved (AD-003).
        self.database.update(
            job["id"], status="done", stage="Cortes prontos para revisão", progress=100,
            outputs=outputs, publication={}, error=None,
        )

    def _recover_rendered_outputs(self, job: dict, folder: Path) -> list[dict] | None:
        transcript = folder / "transcript.srt"
        if not transcript.is_file():
            subtitles = sorted(folder.glob("source*.srt"), key=lambda p: ("pt" not in p.name.lower(), len(p.name)))
            transcript = subtitles[0] if subtitles else transcript
        if not transcript.is_file():
            return None
        captions = parse_srt(transcript)
        candidates = select_candidates(captions, job["clip_count"], job["min_duration"], job["max_duration"])
        records = output_records(candidates)
        if not records or any(not (folder / item["file"]).is_file() or not (folder / item["subtitle"]).is_file() for item in records):
            return None
        for item in records:
            ensure_subscribe_cta(folder / item["file"])
        return records

    def process(self, job: dict) -> None:
        job_id = job["id"]
        folder = self.data_dir / "jobs" / job_id
        folder.mkdir(parents=True, exist_ok=True)
        try:
            recovered = self._recover_rendered_outputs(job, folder)
            if recovered:
                self.update(job_id, "Recuperando cortes já renderizados", 96)
                self._finish(job, folder, recovered)
                return
            if job.get("source_path"):
                source = Path(job["source_path"]).resolve()
                if source.parent != folder.resolve() or not source.is_file():
                    raise PipelineError("O arquivo enviado não está mais disponível")
                media_info = probe(source)
                duration = float(media_info.get("format", {}).get("duration") or 0)
                upload_name = Path(str(job.get("url", "")).removeprefix("upload:")).stem or source.stem
                title = str(job.get("title") or upload_name)[:300]
                self.update(job_id, "Arquivo recebido", 20, title=title, source_duration=duration)
            else:
                self.update(job_id, "Lendo o vídeo", 5)
                source, duration, title = acquire_media(job["url"], folder)
                self.update(job_id, "Vídeo e legendas recebidos", 24, title=title, source_duration=duration)
            if duration <= 0 or duration > 4 * 3600:
                raise PipelineError("O vídeo precisa ter duração entre alguns segundos e 4 horas")
            subtitles = sorted(folder.glob("source*.srt"), key=lambda p: ("pt" not in p.name.lower(), len(p.name)))
            if job.get("caption_mode") == "none":
                self.update(job_id, "Selecionando trechos sem legenda", 55)
                captions = []
                candidates = select_timeline_candidates(
                    duration, job["clip_count"], job["min_duration"], job["max_duration"],
                )
            elif subtitles:
                self.update(job_id, "Lendo as legendas", 34)
                captions = parse_srt(subtitles[0])
                self.update(job_id, "Escolhendo os melhores trechos", 55)
                candidates = select_candidates(captions, job["clip_count"], job["min_duration"], job["max_duration"])
            else:
                self.update(job_id, "Transcrevendo localmente", 34)
                captions = transcribe(source, folder / "transcript.srt", context=str(job.get("title") or ""))
                self.update(job_id, "Escolhendo os melhores trechos", 55)
                candidates = select_candidates(captions, job["clip_count"], job["min_duration"], job["max_duration"])
            outputs = []
            for index, candidate in enumerate(candidates, 1):
                self.update(job_id, f"Renderizando corte {index} de {len(candidates)}", 58 + math.floor(38 * (index - 1) / len(candidates)))
                output = folder / f"corte-{index:02d}.mp4"
                subtitle = folder / f"corte-{index:02d}.srt"
                render(source, captions, candidate, output, subtitle)
            outputs = output_records(candidates)
            self._finish(job, folder, outputs)
        except Exception as exc:  # worker boundary: every failure becomes visible and retryable
            log.exception("job %s failed", job_id)
            self.database.update(job_id, status="failed", stage="Falha no processamento", progress=100, error=humanize_error(str(exc)))


def humanize_error(detail: str) -> str:
    category = classify_import_error(detail)
    if category == "youtube_bot_check":
        return "O YouTube bloqueou este servidor. Use o link compartilhável do vídeo no Google Drive ou Dropbox."
    if category in {"private", "unavailable"}:
        return "O vídeo está privado, restrito ou indisponível para importação."
    if category == "copyright":
        return "O YouTube restringiu o acesso a este conteúdo."
    clean = re.sub(r"\s+", " ", detail).strip()
    return clean[-600:] or "O processamento falhou sem detalhes adicionais."
