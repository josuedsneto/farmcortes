from __future__ import annotations

import argparse
import hashlib
import json
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from .db import Database
from .original import synthesize
from .pipeline import PipelineError, probe, run
from .youtube import publish_file, schedule_slots, settings as youtube_settings


MIN_SECONDS = 1080
TARGET_SECONDS = 1200
MAX_CLIPS = 24
LOCAL_LLM = "http://172.17.0.1:11434/v1/chat/completions"
LOCAL_LLM_TIMEOUT = 90


def _slug(value: str) -> str:
    clean = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    return clean[:72] or "compilacao"


def compilation_key(job: dict) -> str:
    author = str(job.get("source_author") or "").strip()
    if author:
        return author
    title = str(job.get("title") or "").strip()
    if title and not re.fullmatch(r"(?:watch\?v=)?[\w-]+(?:\?.*)?", title):
        return re.split(r"\s+[|—–-]\s+", title, maxsplit=1)[0][:100]
    return f"fonte-{job['id']}"


def select_clips(jobs: list[dict]) -> list[dict]:
    candidates = []
    for job in jobs:
        for output in job.get("outputs", []):
            quality = output.get("quality") or {}
            if quality.get("editorial_ready", True):
                candidates.append({**output, "job_id": job["id"], "job_title": job.get("title")})
    candidates.sort(key=lambda item: float(item.get("score") or 0), reverse=True)
    chosen = []
    duration = 0.0
    for item in candidates:
        signature = re.sub(r"\W+", " ", str(item.get("excerpt") or "").casefold()).strip()[:120]
        if signature and any(signature == known.get("signature") for known in chosen):
            continue
        chosen.append({**item, "signature": signature})
        duration += float(item.get("duration") or 0)
        if duration >= TARGET_SECONDS or len(chosen) >= MAX_CLIPS:
            break
    return chosen


def _extract_json(text: str) -> dict:
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise PipelineError("O roteirista local não devolveu um roteiro estruturado")
    result = json.loads(text[start:end + 1])
    if not isinstance(result, dict):
        raise PipelineError("O roteiro da compilação ficou inválido")
    return result


def complete_transitions(outline: dict, clips: list[dict]) -> dict:
    transitions = outline.get("transitions")
    transitions = [str(item).strip() for item in transitions] if isinstance(transitions, list) else []
    transitions = [item for item in transitions if item][:len(clips)]
    for item in clips[len(transitions):]:
        title = str(item.get("title") or "o próximo ponto").strip(" .")
        excerpt = re.split(r"(?<=[.!?])\s+", str(item.get("excerpt") or "").strip())[0][:260]
        transitions.append(f"Agora, o foco é {title}. O trecho parte desta ideia: {excerpt}")
    return {**outline, "transitions": transitions}


def fallback_outline(subject: str, clips: list[dict]) -> dict:
    source_title = str(clips[0].get("job_title") or subject).strip() if clips else subject
    title = source_title.title()[:70]
    transitions = []
    for item in clips:
        clip_title = str(item.get("title") or "o próximo ponto").strip(" .")
        excerpt = re.split(r"(?<=[.!?])\s+", str(item.get("excerpt") or "").strip())[0][:240]
        transitions.append(
            f"Agora, o foco é {clip_title}. O trecho desenvolve esta ideia: {excerpt}"
        )
    return {
        "title": title,
        "intro": (
            f"Nesta compilação, reunimos os principais momentos de {subject} em uma sequência organizada "
            "por temas. Entre os trechos, você vai ouvir contextos curtos que ajudam a acompanhar a conversa "
            "e perceber como cada ponto se conecta ao seguinte. Todo o roteiro foi construído exclusivamente "
            "a partir do material selecionado, preservando o sentido original das falas."
        ),
        "transitions": transitions,
        "outro": (
            "Esses foram os pontos centrais desta seleção. A sequência reuniu as falas em uma narrativa única, "
            "com contexto entre os momentos para facilitar a compreensão do tema. Inscreva-se no canal para "
            "acompanhar as próximas compilações."
        ),
    }


def editorial_outline(subject: str, clips: list[dict], *, endpoint: str = LOCAL_LLM) -> dict:
    source = [
        {"title": item.get("title"), "excerpt": str(item.get("excerpt") or "")[:900]}
        for item in clips
    ]
    prompt = (
        "Você é editor de um canal brasileiro. Crie uma compilação coerente e autoral usando SOMENTE "
        "os trechos fornecidos. Não invente fatos. Responda apenas JSON válido com: title (até 70 caracteres), "
        "intro (60 a 100 palavras), transitions (uma reflexão de 25 a 55 palavras para cada trecho, conectando-o "
        "ao anterior e explicando por que ele importa) e outro (45 a 80 palavras, com síntese e a frase "
        "'inscreva-se no canal'). O resultado deve ter narrativa editorial, não apenas anunciar trechos.\n"
        f"Tema/fonte: {subject}\nTrechos: {json.dumps(source, ensure_ascii=False)}"
    )
    payload = json.dumps({
        "model": "qwen2.5:1.5b", "temperature": 0.25, "max_tokens": 1800,
        "messages": [{"role": "user", "content": prompt}],
    }).encode()
    request = urllib.request.Request(endpoint, data=payload, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=LOCAL_LLM_TIMEOUT) as response:
            body = json.load(response)
        outline = _extract_json(body["choices"][0]["message"]["content"])
    except (OSError, TimeoutError, urllib.error.URLError, ValueError, KeyError, TypeError, PipelineError):
        outline = fallback_outline(subject, clips)
    outline = complete_transitions(outline, clips)
    if "inscreva-se no canal" not in str(outline.get("outro") or "").casefold():
        outline["outro"] = f"{outline.get('outro', '').strip()} Inscreva-se no canal para acompanhar as próximas compilações."
    return outline


def _escape(path: Path) -> str:
    return str(path.resolve()).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def _narration_segment(text: str, heading: str, output: Path, model: Path, *, cta: bool = False) -> None:
    audio = output.with_suffix(".wav")
    synthesize(text, audio, model)
    duration = float(probe(audio).get("format", {}).get("duration") or 0)
    if duration <= 1:
        raise PipelineError("A narração da compilação ficou vazia")
    heading_file = output.with_suffix(".heading.txt")
    heading_file.write_text(heading.upper(), encoding="utf-8")
    filters = (
        "drawbox=x=0:y=0:w=iw:h=ih:color=0x111827:t=fill,"
        "drawbox=x=100:y=120:w=16:h=840:color=0xE11D48:t=fill,"
        f"drawtext=fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf:textfile='{_escape(heading_file)}':"
        "fontcolor=white:fontsize=70:x=160:y=(h-text_h)/2:fix_bounds=1"
    )
    if cta:
        filters += (
            ",drawbox=x=1350:y=820:w=470:h=120:color=0xE11D48@0.95:t=fill,"
            "drawtext=fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf:"
            "text='INSCREVA-SE':fontcolor=white:fontsize=42:x=1418:y=855"
        )
    run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
        f"color=c=0x111827:s=1920x1080:r=30:d={duration:.3f}", "-i", str(audio),
        "-vf", filters, "-map", "0:v:0", "-map", "1:a:0", "-c:v", "libx264", "-preset", "superfast",
        "-crf", "22", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k", "-ar", "48000",
        "-shortest", "-movflags", "+faststart", str(output),
    ], timeout=1800)


def _landscape_clip(source: Path, output: Path) -> None:
    graph = (
        "[0:v]scale=608:1080:force_original_aspect_ratio=decrease,"
        "pad=1920:1080:(ow-iw)/2:(oh-ih)/2:color=0x111827,"
        "drawbox=x=642:y=0:w=14:h=ih:color=0xE11D48:t=fill,"
        "drawbox=x=1264:y=0:w=14:h=ih:color=0xE11D48:t=fill[v]"
    )
    run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(source),
        "-filter_complex", graph, "-map", "[v]", "-map", "0:a:0?", "-c:v", "libx264",
        "-preset", "superfast", "-crf", "22", "-pix_fmt", "yuv420p", "-r", "30",
        "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-movflags", "+faststart", str(output),
    ])


def render_compilation(data_dir: Path, subject: str, jobs: list[dict], clips: list[dict]) -> tuple[Path, dict]:
    identity = hashlib.sha256("|".join(f"{item['job_id']}:{item['file']}" for item in clips).encode()).hexdigest()[:12]
    folder = data_dir / "compilations" / _slug(subject) / identity
    manifest_path = folder / "manifest.json"
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        video = folder / "compilacao.mp4"
        if video.is_file():
            return video, manifest
    folder.mkdir(parents=True, exist_ok=True)
    outline_path = folder / "outline.json"
    if outline_path.is_file():
        outline = complete_transitions(json.loads(outline_path.read_text(encoding="utf-8")), clips)
    else:
        outline = editorial_outline(subject, clips)
        outline_path.write_text(json.dumps(outline, ensure_ascii=False, indent=2), encoding="utf-8")
    model = data_dir / "models" / "pt_BR-edresson-low.onnx"
    if not model.is_file():
        raise PipelineError("Modelo local de voz pt-BR não está instalado")
    segments: list[Path] = []
    intro = folder / "segmento-000-intro.mp4"
    _narration_segment(str(outline["intro"]), str(outline["title"]), intro, model, cta=True)
    segments.append(intro)
    for index, (clip, transition) in enumerate(zip(clips, outline["transitions"]), 1):
        narration = folder / f"segmento-{index:03d}-contexto.mp4"
        _narration_segment(str(transition), str(clip.get("title") or f"Trecho {index}"), narration, model)
        segments.append(narration)
        source = data_dir / "jobs" / clip["job_id"] / clip["file"]
        landscape = folder / f"segmento-{index:03d}-trecho.mp4"
        _landscape_clip(source, landscape)
        segments.append(landscape)
    outro = folder / "segmento-999-outro.mp4"
    _narration_segment(str(outline["outro"]), "CONTINUE COM A GENTE", outro, model, cta=True)
    segments.append(outro)
    concat_file = folder / "segmentos.txt"
    concat_file.write_text("".join(f"file '{path.name}'\n" for path in segments), encoding="utf-8")
    video = folder / "compilacao.mp4"
    run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0",
        "-i", str(concat_file), "-c", "copy", "-movflags", "+faststart", str(video),
    ])
    duration = float(probe(video).get("format", {}).get("duration") or 0)
    sources = [{"job_id": job["id"], "url": job.get("url"), "author": job.get("source_author")} for job in jobs]
    manifest = {
        "id": identity, "subject": subject, "title": outline["title"], "duration": round(duration, 1),
        "created_at": datetime.now(timezone.utc).isoformat(), "clips": clips, "sources": sources,
        "publication": {}, "editorial_method": "local-llm-script+piper-narration+curated-clips",
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return video, manifest


def metadata_for_compilation(manifest: dict) -> dict:
    sources = "\n".join(f"- {item.get('author') or 'Fonte autorizada'}: {item.get('url')}" for item in manifest["sources"])
    return {
        "title": str(manifest["title"])[:100],
        "description": (
            "Uma seleção temática com roteiro, narração e contexto editorial originais do Coletivo Cortes.\n\n"
            "Inscreva-se no canal para acompanhar as próximas compilações.\n\nFontes autorizadas:\n"
            f"{sources}\n\n#Cortes #Compilação #ColetivoCortes"
        ),
        "tags": ["cortes", "compilação", "Coletivo Cortes"], "category_id": "24",
        "language": "pt-BR", "made_for_kids": False, "contains_synthetic_media": False,
    }


def _reserved_compilation_slots(data_dir: Path) -> set[str]:
    reserved: set[str] = set()
    for manifest_path in (data_dir / "compilations").glob("*/*/manifest.json"):
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        publish_at = manifest.get("publication", {}).get("publish_at")
        if publish_at:
            reserved.add(publish_at)
    return reserved


def run_once(data_dir: Path, *, publish: bool = False, max_count: int = 1) -> list[dict]:
    database = Database(data_dir / "farmcortes.sqlite3")
    groups: dict[str, list[dict]] = {}
    for job in database.list(1000):
        if job.get("status") == "done" and job.get("outputs"):
            groups.setdefault(compilation_key(job), []).append(job)
    results = []
    for subject, jobs in groups.items():
        if len(results) >= max_count:
            break
        if subject.startswith("fonte-"):
            continue
        clips = select_clips(jobs)
        if sum(float(item.get("duration") or 0) for item in clips) < MIN_SECONDS:
            continue
        video, manifest = render_compilation(data_dir, subject, jobs, clips)
        if publish and manifest.get("publication"):
            continue
        if publish:
            config = youtube_settings(data_dir)
            reserved = {
                item.get("publish_at")
                for job in database.list(1000)
                for item in job.get("publication", {}).get("items", [])
                if item.get("publish_at")
            } | _reserved_compilation_slots(data_dir)
            slot = schedule_slots(1, (config["longform_time"],), config["timezone"], reserved=reserved, one_per_day=True)[0]
            publication = publish_file(
                video, metadata_for_compilation(manifest), token=config["token"], privacy="private",
                publish_at=slot, short_form=False,
            )
            manifest["publication"] = publication
            (video.parent / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        results.append(manifest)
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Produz compilações longas editoriais do Farmcortes")
    parser.add_argument("--data-dir", type=Path, default=Path(__file__).resolve().parent.parent / "data")
    parser.add_argument("--publish", action="store_true")
    parser.add_argument("--max-count", type=int, default=1)
    args = parser.parse_args()
    print(json.dumps(run_once(args.data_dir.resolve(), publish=args.publish, max_count=max(1, args.max_count)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
