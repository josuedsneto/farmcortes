from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from .compilations import (
    _escape,
    _narration_segment,
    _reserved_compilation_slots,
    _slug,
    editorial_outline,
)
from .db import Database
from .editorial import load_policy
from .pipeline import PipelineError, probe, run
from .youtube import publish_file, schedule_slots, settings as youtube_settings


def _is_cc_by(value: str | None) -> bool:
    normalized = str(value or "").casefold()
    return "creative commons" in normalized or "cc by" in normalized


def used_source_ids(data_dir: Path) -> set[str]:
    used: set[str] = set()
    for manifest_path in (data_dir / "source-compilations").glob("*/*/manifest.json"):
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if manifest.get("publication", {}).get("video_id"):
            used.update(str(item["job_id"]) for item in manifest.get("sources", []) if item.get("job_id"))
    return used


def _reserved_source_slots(data_dir: Path) -> set[str]:
    reserved: set[str] = set()
    for manifest_path in (data_dir / "source-compilations").glob("*/*/manifest.json"):
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        publish_at = manifest.get("publication", {}).get("publish_at")
        if publish_at:
            reserved.add(publish_at)
    return reserved


def select_source_groups(jobs: list[dict], policy: dict, *, used: set[str] | None = None) -> dict[str, list[dict]]:
    config = policy.get("source_compilations", {})
    minimum_sources = int(config.get("min_sources", 3))
    minimum_seconds = float(config.get("min_total_seconds", 1800))
    target_seconds = float(config.get("target_seconds", 3600))
    maximum_source_seconds = float(config.get("max_source_seconds", 1200))
    groups: dict[str, list[dict]] = {}
    excluded = used or set()
    for job in jobs:
        path = Path(str(job.get("source_path") or ""))
        if (
            job.get("id") in excluded or job.get("status") != "done" or not job.get("editorial_theme")
            or not _is_cc_by(job.get("source_license")) or not path.is_file()
            or float(job.get("source_duration") or 0) < 300
        ):
            continue
        groups.setdefault(str(job["editorial_theme"]), []).append(job)
    eligible: dict[str, list[dict]] = {}
    for theme, candidates in groups.items():
        candidates.sort(key=lambda item: item.get("updated_at") or "")
        chosen: list[dict] = []
        duration = 0.0
        for item in candidates:
            chosen.append(item)
            duration += min(float(item.get("source_duration") or 0), maximum_source_seconds)
            if len(chosen) >= minimum_sources and duration >= target_seconds:
                break
        if len(chosen) >= minimum_sources and duration >= minimum_seconds:
            eligible[theme] = chosen
    return eligible


def _source_context(job: dict) -> str:
    excerpts = [str(item.get("excerpt") or "").strip() for item in job.get("outputs", [])]
    excerpts = [text for text in excerpts if text][:3]
    return " ".join(excerpts)[:1800] or str(job.get("title") or "Fonte longa selecionada")


def _chapter_window(job: dict, maximum_seconds: float) -> tuple[float, float]:
    source_duration = float(job.get("source_duration") or 0)
    duration = min(source_duration, maximum_seconds)
    scored = sorted(job.get("outputs", []), key=lambda item: float(item.get("score") or 0), reverse=True)
    anchor = float(scored[0].get("source_start") or 0) if scored else 0.0
    start = max(0.0, min(anchor - duration / 3, max(0.0, source_duration - duration)))
    return start, duration


def _source_segment(job: dict, output: Path, maximum_seconds: float) -> dict:
    source = Path(job["source_path"])
    start, duration = _chapter_window(job, maximum_seconds)
    credit = output.with_suffix(".credit.txt")
    credit.write_text(
        f"FONTE: {job.get('source_author') or 'AUTOR IDENTIFICADO NA DESCRIÇÃO'}\n{job.get('title') or ''}"[:300],
        encoding="utf-8",
    )
    filters = (
        "scale=1920:1080:force_original_aspect_ratio=decrease,"
        "pad=1920:1080:(ow-iw)/2:(oh-ih)/2:color=0x111827,"
        "drawbox=x=0:y=0:w=iw:h=112:color=0x111827@0.88:t=fill,"
        f"drawtext=fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf:textfile='{_escape(credit)}':"
        "fontcolor=white:fontsize=30:x=48:y=25:line_spacing=8:fix_bounds=1"
    )
    run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{start:.3f}", "-i", str(source),
        "-t", f"{duration:.3f}", "-vf", filters, "-map", "0:v:0", "-map", "0:a:0?", "-c:v", "libx264",
        "-preset", "superfast", "-crf", "22", "-pix_fmt", "yuv420p", "-r", "30", "-c:a", "aac",
        "-b:a", "160k", "-ar", "48000", "-movflags", "+faststart", str(output),
    ], timeout=7200)
    return {"start": round(start, 1), "duration": round(duration, 1)}


def render_source_compilation(data_dir: Path, theme: str, jobs: list[dict], policy: dict) -> tuple[Path, dict]:
    identity = hashlib.sha256("|".join(sorted(job["id"] for job in jobs)).encode()).hexdigest()[:12]
    folder = data_dir / "source-compilations" / _slug(theme) / identity
    video = folder / "compilacao-fontes.mp4"
    manifest_path = folder / "manifest.json"
    if video.is_file() and manifest_path.is_file():
        return video, json.loads(manifest_path.read_text(encoding="utf-8"))
    folder.mkdir(parents=True, exist_ok=True)
    pseudo_clips = [
        {"title": job.get("title"), "excerpt": _source_context(job), "job_title": job.get("title")}
        for job in jobs
    ]
    outline = editorial_outline(theme.replace("-", " ").title(), pseudo_clips)
    outline["title"] = f"{theme.replace('-', ' ').title()}: uma seleção especial"[:70]
    model = data_dir / "models" / "pt_BR-edresson-low.onnx"
    if not model.is_file():
        raise PipelineError("Modelo local de voz pt-BR não está instalado")
    maximum = float(policy.get("source_compilations", {}).get("max_source_seconds", 1200))
    segments: list[Path] = []
    sources: list[dict] = []
    intro = folder / "segmento-000-intro.mp4"
    _narration_segment(str(outline["intro"]), str(outline["title"]), intro, model, cta=True)
    segments.append(intro)
    for index, (job, transition) in enumerate(zip(jobs, outline["transitions"]), 1):
        context = folder / f"segmento-{index:03d}-contexto.mp4"
        _narration_segment(str(transition), str(job.get("title") or f"Capítulo {index}"), context, model)
        segments.append(context)
        chapter = folder / f"segmento-{index:03d}-fonte.mp4"
        window = _source_segment(job, chapter, maximum)
        segments.append(chapter)
        sources.append({
            "job_id": job["id"], "url": job.get("url"), "author": job.get("source_author"),
            "title": job.get("title"), "license": job.get("source_license"), **window,
        })
    outro = folder / "segmento-999-outro.mp4"
    _narration_segment(str(outline["outro"]), "CONTINUE COM A GENTE", outro, model, cta=True)
    segments.append(outro)
    concat = folder / "segmentos.txt"
    concat.write_text("".join(f"file '{item.name}'\n" for item in segments), encoding="utf-8")
    run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0",
        "-i", str(concat), "-c", "copy", "-movflags", "+faststart", str(video),
    ], timeout=7200)
    duration = float(probe(video).get("format", {}).get("duration") or 0)
    manifest = {
        "id": identity, "theme": theme, "title": outline["title"], "duration": round(duration, 1),
        "created_at": datetime.now(timezone.utc).isoformat(), "sources": sources, "publication": {},
        "editorial_method": "raw-long-sources+original-script+piper-narration+source-attribution",
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return video, manifest


def metadata(manifest: dict) -> dict:
    sources = "\n".join(
        f"- {item.get('author') or 'Fonte'} — {item.get('title')}: {item.get('url')} ({item.get('license')})"
        for item in manifest["sources"]
    )
    return {
        "title": str(manifest["title"])[:100],
        "description": (
            "Compilação temática criada diretamente de fontes longas, com seleção, roteiro, narração, "
            "contexto e identidade visual originais do Coletivo Cortes.\n\n"
            "Inscreva-se no canal para acompanhar as próximas seleções.\n\nFontes e licenças:\n"
            f"{sources}\n\n#ColetivoCortes #Compilação"
        ),
        "tags": ["compilação", "vídeos longos", "Coletivo Cortes"], "category_id": "24",
        "language": "pt-BR", "made_for_kids": False, "contains_synthetic_media": False,
    }


def run_once(data_dir: Path, *, publish: bool = False, max_count: int = 1) -> list[dict]:
    database = Database(data_dir / "farmcortes.sqlite3")
    policy = load_policy()
    groups = select_source_groups(database.list(2000), policy, used=used_source_ids(data_dir))
    results: list[dict] = []
    for theme, jobs in groups.items():
        if len(results) >= max_count:
            break
        video, manifest = render_source_compilation(data_dir, theme, jobs, policy)
        if publish and not manifest.get("publication"):
            config = youtube_settings(data_dir)
            reserved = {
                item.get("publish_at") for job in database.list(2000)
                for item in job.get("publication", {}).get("items", []) if item.get("publish_at")
            } | _reserved_compilation_slots(data_dir) | _reserved_source_slots(data_dir)
            slot = schedule_slots(
                1, (config["longform_time"],), config["timezone"], reserved=reserved, one_per_day=True,
            )[0]
            manifest["publication"] = publish_file(
                video, metadata(manifest), token=config["token"], privacy="private",
                publish_at=slot, short_form=False,
            )
            (video.parent / "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8",
            )
        results.append(manifest)
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Compila fontes longas temáticas do Farmcortes")
    parser.add_argument("--data-dir", type=Path, default=Path(__file__).resolve().parent.parent / "data")
    parser.add_argument("--publish", action="store_true")
    parser.add_argument("--max-count", type=int, default=1)
    args = parser.parse_args()
    print(json.dumps(
        run_once(args.data_dir.resolve(), publish=args.publish, max_count=max(1, args.max_count)),
        ensure_ascii=False, indent=2,
    ))


if __name__ == "__main__":
    main()
