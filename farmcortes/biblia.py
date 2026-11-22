"""Biblia mode: one vertical Bible-reading Short per run, sent to the shared review queue.

Flow: verse queue in SQLite (imported once from versiculos.csv) -> verse text from the
Pesquisar na Bíblia API (ACF) -> Piper narration -> Pexels background (Pixabay, then a plain
background, as fallbacks) -> FFmpeg render 1080x1920 -> review_queue (kind='biblia').
Nothing is published here unless the item was approved, by a person or by
FARMCORTES_BIBLIA_AUTO_APPROVE; publishing goes through review.publish_approved.
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import math
import os
import random
import re
import shutil
from pathlib import Path

import requests

from . import biblia_ai, review, telegram
from .db import Database
from .original import _escape_filter, synthesize, timed_captions
from .pipeline import PipelineError, probe, run

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CSV = ROOT / "dados-iniciais" / "biblia" / "versiculos.csv"
THEMES_CSV = ROOT / "dados-iniciais" / "biblia" / "temas.csv"
VERSES_API = "https://pesquisarnabiblia.com.br/api-projeto/api/get_verses.php"
WORDS_PER_SECOND = 130 / 60  # Biblia's estimate for pt-BR narration
PART_MAX_SECONDS = 57        # planning target; the rendered video is held to MAX_VIDEO_SECONDS
MAX_VIDEO_SECONDS = 59.5
VERSE_PAUSE = 0.5
MUSIC_VOLUME = 0.15
PEXELS_THEMES = [
    "sunrise nature", "sky clouds golden", "forest light rays", "ocean calm horizon",
    "mountain peace morning", "meadow flowers sunlight", "river peaceful nature", "desert sunset warm",
]
MUSIC_THEMES = ["meditation", "ambient", "peaceful", "piano", "relaxing", "spiritual", "soft", "calm"]
PLATFORMS = ("youtube", "tiktok", "facebook", "instagram")
BOOK_IDS = {
    "gn": 1, "ex": 2, "lv": 3, "nm": 4, "dt": 5, "js": 6, "jz": 7, "rt": 8, "1sm": 9, "2sm": 10,
    "1rs": 11, "2rs": 12, "1cr": 13, "2cr": 14, "ed": 15, "ne": 16, "et": 17, "jo": 18, "sl": 19,
    "pv": 20, "ec": 21, "ct": 22, "is": 23, "jr": 24, "lm": 25, "ez": 26, "dn": 27, "os": 28,
    "jl": 29, "am": 30, "ob": 31, "jn": 32, "mq": 33, "na": 34, "hc": 35, "sf": 36, "ag": 37,
    "zc": 38, "ml": 39, "mt": 40, "mc": 41, "lc": 42, "joa": 43, "at": 44, "rm": 45, "1co": 46,
    "2co": 47, "gl": 48, "ef": 49, "fp": 50, "cl": 51, "1ts": 52, "2ts": 53, "1tm": 54, "2tm": 55,
    "tt": 56, "fm": 57, "hb": 58, "tg": 59, "1pe": 60, "2pe": 61, "1jo": 62, "2jo": 63, "3jo": 64,
    "jd": 65, "ap": 66,
}


# ── verse queue ──────────────────────────────────────────────────────────────

def estimate_seconds(text: str) -> float:
    return len(text.split()) / WORDS_PER_SECOND


def split_parts(text: str, max_seconds: float = PART_MAX_SECONDS) -> list[str]:
    """Split a long verse into parts of at most max_seconds of narration, cutting at punctuation."""
    max_words = int(max_seconds * WORDS_PER_SECOND)
    words = text.split()
    if len(words) <= max_words:
        return [text]
    parts, start = [], 0
    while len(words) - start > max_words:
        ideal = start + max_words
        cut = ideal
        for marks, window in (({".", ";", "!", "?"}, 15), ({",", ":"}, 10)):
            found = next((i for i in range(ideal, max(start, ideal - window), -1) if words[i - 1][-1] in marks), None)
            if found:
                cut = found
                break
        parts.append(" ".join(words[start:cut]))
        start = cut
    parts.append(" ".join(words[start:]))
    return parts


def import_csv(db: Database, path: Path = DEFAULT_CSV) -> int:
    """Import versiculos.csv once (no-op when the queue already has rows), keeping published flags."""
    with db.connect() as conn:
        if conn.execute("SELECT 1 FROM biblia_verses LIMIT 1").fetchone():
            return 0
        with Path(path).open(newline="", encoding="utf-8") as handle:
            rows = [
                (
                    int(row["ordem"]), row["livro"].strip().lower(), int(row["capitulo"]), int(row["verso"]),
                    *(int(str(row.get(f"publicado_{p}", "false")).strip().lower() == "true") for p in PLATFORMS),
                )
                for row in csv.DictReader(handle)
            ]
        conn.executemany(
            "INSERT OR IGNORE INTO biblia_verses (ordem,livro,capitulo,verso,published_youtube,published_tiktok,"
            "published_facebook,published_instagram) VALUES (?,?,?,?,?,?,?,?)",
            rows,
        )
    return len(rows)


def fetch_chapter(livro: str, capitulo: int) -> tuple[str, dict[int, str]]:
    """Return (book name, {verse number: text}) for one chapter of the ACF translation."""
    book_id = BOOK_IDS.get(livro)
    if not book_id:
        raise PipelineError(f"Livro desconhecido na fila: {livro}")
    headers = {"Accept": "application/json", "User-Agent": "Mozilla/5.0 (Farmcortes Biblia)"}
    key = os.getenv("FARMCORTES_BIBLIA_API_KEY")
    if key:
        headers["Authorization"] = f"Bearer {key}"
    response = requests.get(
        VERSES_API, params={"version_id": 1, "book_id": book_id, "chapter_id": capitulo}, headers=headers, timeout=20
    )
    response.raise_for_status()
    verses = response.json().get("verses", [])
    if not verses:
        raise PipelineError(f"API não devolveu {livro} {capitulo}")
    book = verses[0]["book_name"]
    return book, {int(v["verse_number"]): " ".join(str(v["text"]).replace("\xa0", " ").split()) for v in verses}


def _fill_text(conn, verse: dict, fetch) -> dict:
    verse = dict(conn.execute("SELECT * FROM biblia_verses WHERE ordem=?", (verse["ordem"],)).fetchone())
    if verse["texto"]:
        return verse
    book, texts = fetch(verse["livro"], verse["capitulo"])
    for number, text in texts.items():
        conn.execute(
            "UPDATE biblia_verses SET texto=?,referencia=? WHERE livro=? AND capitulo=? AND verso=? AND texto IS NULL",
            (text, f"{book} {verse['capitulo']}:{number}", verse["livro"], verse["capitulo"], number),
        )
    row = conn.execute("SELECT * FROM biblia_verses WHERE ordem=?", (verse["ordem"],)).fetchone()
    if not row["texto"]:
        raise PipelineError(f"Versículo {verse['livro']} {verse['capitulo']}:{verse['verso']} não veio da API")
    return dict(row)


def next_video(db: Database, fetch=None, max_seconds: float = PART_MAX_SECONDS) -> dict | None:
    """Plan the next video: consecutive pending verses up to max_seconds, or the next part of a long verse."""
    fetch = fetch or fetch_chapter
    with db.connect() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM biblia_verses WHERE published_youtube=0 AND (parts_total IS NULL OR parts_done < parts_total) "
            "ORDER BY ordem LIMIT 40"
        ).fetchall()]
        if not rows:
            return None
        first = _fill_text(conn, rows[0], fetch)
        parts = split_parts(first["texto"], max_seconds)
        if len(parts) > 1:
            done = first["parts_done"] if first["parts_total"] == len(parts) else 0
            return {"verses": [first], "text": parts[done], "part": done + 1, "parts": len(parts),
                    "complete": [first["ordem"]] if done + 1 == len(parts) else []}
        verses, total = [first], estimate_seconds(first["texto"])
        for row in rows[1:]:
            if row["ordem"] != verses[-1]["ordem"] + 1:
                break
            row = _fill_text(conn, row, fetch)
            seconds = estimate_seconds(row["texto"]) + VERSE_PAUSE
            if total + seconds > max_seconds:
                break
            verses.append(row)
            total += seconds
    return {"verses": verses, "text": " ".join(v["texto"] for v in verses), "part": 1, "parts": 1,
            "complete": [v["ordem"] for v in verses]}


def record_video(db: Database, plan: dict, review_id: int) -> None:
    """Advance the queue after a video was enqueued; map fully covered verses to the review item."""
    with db.connect() as conn:
        for verse in plan["verses"]:
            conn.execute("UPDATE biblia_verses SET parts_total=?,parts_done=? WHERE ordem=?",
                         (plan["parts"], plan["part"], verse["ordem"]))
        conn.executemany("INSERT OR IGNORE INTO biblia_videos (review_id,ordem) VALUES (?,?)",
                         [(review_id, ordem) for ordem in plan["complete"]])


# ── media ────────────────────────────────────────────────────────────────────

def _download(url: str, destination: Path, timeout: int = 60) -> None:
    with requests.get(url, timeout=timeout, stream=True) as response:
        response.raise_for_status()
        with destination.open("wb") as handle:
            for chunk in response.iter_content(1024 * 1024):
                handle.write(chunk)


def background_image(destination: Path) -> dict:
    """Download a portrait background. Returns attribution {credit, url}; empty when the plain fallback is used."""
    theme = random.choice(PEXELS_THEMES)
    pexels = os.getenv("FARMCORTES_PEXELS_API_KEY")
    if pexels:
        try:
            response = requests.get("https://api.pexels.com/v1/search", headers={"Authorization": pexels.strip()},
                                    params={"query": theme, "per_page": 15, "orientation": "portrait"}, timeout=20)
            response.raise_for_status()
            photos = response.json().get("photos", [])
            if photos:
                photo = random.choice(photos)
                _download(photo["src"]["large2x"], destination)
                return {"credit": f"Foto: {photo['photographer']} no Pexels", "url": photo["url"]}
        except Exception as exc:
            log.warning("Pexels falhou: %s", exc)
    pixabay = os.getenv("FARMCORTES_PIXABAY_API_KEY")
    if pixabay:
        try:
            response = requests.get("https://pixabay.com/api/", params={
                "key": pixabay, "q": theme, "image_type": "photo", "orientation": "vertical", "per_page": 15,
            }, timeout=20)
            response.raise_for_status()
            hits = response.json().get("hits", [])
            if hits:
                hit = random.choice(hits)
                _download(hit["largeImageURL"], destination)
                return {"credit": f"Imagem: {hit['user']} no Pixabay", "url": hit["pageURL"]}
        except Exception as exc:
            log.warning("Pixabay falhou: %s", exc)
    run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
         "-i", "gradients=s=1080x1920:c0=0x141428:c1=0x3a2a14:duration=1", "-frames:v", "1", str(destination)], timeout=120)
    return {}


def background_music(data_dir: Path) -> tuple[Path | None, str]:
    """Reuse or download one instrumental Jamendo track. Returns (path, attribution)."""
    folder = data_dir / "biblia"
    track, info = folder / "music.mp3", folder / "music.json"
    if track.is_file():
        try:
            return track, json.loads(info.read_text(encoding="utf-8")).get("credit", "")
        except (OSError, ValueError):
            return track, ""
    client = os.getenv("FARMCORTES_JAMENDO_CLIENT_ID")
    if not client:
        return None, ""
    try:
        response = requests.get("https://api.jamendo.com/v3.0/tracks/", params={
            "client_id": client, "format": "json", "limit": 20, "tags": random.choice(MUSIC_THEMES),
            "audioformat": "mp32", "vocalinstrumental": "instrumental",
        }, timeout=20)
        response.raise_for_status()
        tracks = [t for t in response.json().get("results", []) if t.get("audio")]
        if not tracks:
            return None, ""
        chosen = random.choice(tracks)
        folder.mkdir(parents=True, exist_ok=True)
        _download(chosen["audio"], track)
        credit = f"Música: {chosen.get('name', '?')} — {chosen.get('artist_name', '?')} (Jamendo, {chosen.get('license_ccurl', 'licença na fonte')})"
        info.write_text(json.dumps({"credit": credit}, ensure_ascii=False), encoding="utf-8")
        return track, credit
    except Exception as exc:
        log.warning("Jamendo falhou: %s", exc)
        track.unlink(missing_ok=True)
        return None, ""


def _ass_text(value: str) -> str:
    return value.replace("{", "(").replace("}", ")").replace("\n", " ")


def _clock(seconds: float) -> str:
    centis = int(round(seconds * 100))
    return f"{centis // 360000}:{centis // 6000 % 60:02d}:{centis // 100 % 60:02d}.{centis % 100:02d}"


def write_subtitles(path: Path, text: str, reference: str, label: str, duration: float) -> None:
    rows = [
        "[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\nWrapStyle: 0\n\n[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
        "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
        "MarginR, MarginV, Encoding\n"
        "Style: Series,DejaVu Sans,40,&H00FFFFFF,&H00FFFFFF,&H00111111,&H00000000,-1,0,0,0,100,100,2,0,1,2,0,8,80,80,140,1\n"
        "Style: Ref,DejaVu Sans,74,&H0066D9FF,&H0066D9FF,&H00111111,&H00000000,-1,0,0,0,100,100,0,0,1,3,1,8,80,80,220,1\n"
        "Style: Part,DejaVu Sans,46,&H00FFFFFF,&H00FFFFFF,&H00111111,&H90000000,-1,0,0,0,100,100,0,0,3,2,0,8,80,80,330,1\n"
        "Style: Verse,DejaVu Sans,70,&H00FFFFFF,&H00FFFFFF,&H00111111,&H80000000,-1,0,0,0,100,100,0,0,3,2,0,5,90,90,0,1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    ]
    end = _clock(duration)
    rows.append(f"Dialogue: 1,0:00:00.00,{end},Series,,0,0,0,,{_ass_text(biblia_ai.SERIES.upper())}\n")
    rows.append(f"Dialogue: 1,0:00:00.00,{end},Ref,,0,0,0,,{_ass_text(reference)}\n")
    if label:
        rows.append(f"Dialogue: 1,0:00:00.00,{end},Part,,0,0,0,,{_ass_text(label)}\n")
    for caption in timed_captions(text, duration):
        rows.append(f"Dialogue: 0,{_clock(caption.start)},{_clock(caption.end)},Verse,,0,0,0,,{_ass_text(caption.text)}\n")
    path.write_text("".join(rows), encoding="utf-8")


def render(image: Path, narration: Path, music: Path | None, text: str, reference: str, label: str, output: Path) -> float:
    """FFmpeg render 1080x1920 at most MAX_VIDEO_SECONDS long; speeds narration up slightly if needed."""
    spoken = float(probe(narration).get("format", {}).get("duration") or 0)
    if spoken <= 0:
        raise PipelineError("A narração ficou vazia")
    tempo = max(1.0, spoken / MAX_VIDEO_SECONDS)
    if tempo > 1.3:
        raise PipelineError(f"Narração longa demais para um Short: {spoken:.1f}s")
    duration = spoken / tempo + 0.6
    subtitle = output.with_suffix(".ass")
    write_subtitles(subtitle, text, reference, label, duration)
    filters = (
        "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,"
        f"eq=brightness=-0.2:saturation=0.85,subtitles='{_escape_filter(subtitle)}'[v];"
        f"[1:a]atempo={tempo:.4f},apad=pad_dur=0.6,loudnorm=I=-16:LRA=9:TP=-1.5[n]"
    )
    command = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-loop", "1", "-i", str(image), "-i", str(narration)]
    if music:
        command += ["-stream_loop", "-1", "-i", str(music)]
        filters += f";[2:a]volume={MUSIC_VOLUME}[m];[n][m]amix=inputs=2:duration=first:normalize=0[a]"
    else:
        filters += ";[n]anull[a]"
    command += [
        "-filter_complex", filters, "-map", "[v]", "-map", "[a]", "-t", f"{duration:.3f}",
        "-c:v", "libx264", "-preset", "veryfast", "-tune", "stillimage", "-crf", "21", "-pix_fmt", "yuv420p", "-r", "30",
        "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(output),
    ]
    run(command, timeout=1800)
    video = next((s for s in probe(output).get("streams", []) if s.get("codec_type") == "video"), {})
    if video.get("width") != 1080 or video.get("height") != 1920:
        raise PipelineError("O vídeo da Bíblia não ficou em 1080x1920")
    return duration


# ── run ──────────────────────────────────────────────────────────────────────

def make_video(data_dir: Path, db: Database, plan: dict) -> int:
    """Render the planned video, enqueue it for review and clean the temporary files. Returns the review id."""
    first = plan["verses"][0]
    folder = data_dir / "biblia" / f"{first['ordem']:05d}-p{plan['part']}"
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True)
    video = folder / "short.mp4"
    try:
        model = data_dir / "models" / "pt_BR-edresson-low.onnx"
        if not model.is_file():
            raise PipelineError("Modelo de voz pt-BR não está instalado")
        synthesize(plan["text"], folder / "narration.wav", model)
        attribution = background_image(folder / "background.jpg")
        music, music_credit = background_music(data_dir)
        reference = biblia_ai.reference_range(plan["verses"])
        label = f"Parte {plan['part']}/{plan['parts']}" if plan["parts"] > 1 else ""
        duration = render(folder / "background.jpg", folder / "narration.wav", music, plan["text"], reference, label, video)
    except Exception:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    finally:
        for leftover in folder.glob("*"):
            if leftover != video:
                leftover.unlink(missing_ok=True)
    meta = biblia_ai.verse_metadata(plan["verses"], db, plan["part"], plan["parts"])
    credit = "\n".join(filter(None, [
        "Texto bíblico: Almeida Corrigida Fiel (ACF).", attribution.get("credit"), music_credit,
        "Narração sintética (Piper) e edição automáticas.",
    ]))
    item_id = review.enqueue(
        kind="biblia", streamer="biblia", platform="biblia", source_url=attribution.get("url", ""),
        file_path=video, duration=duration, category="Bíblia", titles=meta["titulos"],
        description=meta["descricao"], hashtags="#Bíblia #LeiaABíblia #PalavraDeDeus #Shorts",
        credit_text=credit, db=db,
    )
    record_video(db, plan, item_id)
    return item_id


def prune_published(db: Database, data_dir: Path) -> int:
    """Delete rendered Biblia videos nobody needs any more (published everywhere enabled, or discarded)."""
    from .social import meta
    platforms = meta.enabled_platforms()
    removed = 0
    root = (data_dir / "biblia").resolve()
    with db.connect() as conn:
        rows = conn.execute("SELECT id,status,file_path,social_json FROM review_queue WHERE kind='biblia' AND status IN ('published','discarded')").fetchall()
    for row in rows:
        social = json.loads(row["social_json"] or "{}")
        pending = row["status"] == "published" and any(
            not social.get(p, {}).get("id") and social.get(p, {}).get("attempts", 0) < 3 for p in platforms
        )
        path = Path(row["file_path"]).resolve()
        if pending or root not in path.parent.parents or not path.is_file():
            continue
        shutil.rmtree(path.parent, ignore_errors=True)
        removed += 1
    return removed


def run_once(data_dir: Path, db: Database | None = None) -> dict:
    db = db or Database(data_dir / "farmcortes.sqlite3")
    csv_path = Path(os.getenv("FARMCORTES_BIBLIA_CSV") or DEFAULT_CSV)
    if csv_path.is_file():
        import_csv(db, csv_path)
    plan = next_video(db)
    result: dict = {"status": "idle"}
    if plan:
        item_id = make_video(data_dir, db, plan)
        approved = review.auto_approve(item_id, db)
        result = {"status": "approved" if approved else "pending", "review_id": item_id,
                  "reference": biblia_ai.reference_range(plan["verses"]), "part": plan["part"], "parts": plan["parts"]}
        if not approved:
            item = next(row for row in review.list_items(db, kind="biblia") if row["id"] == item_id)
            try:
                telegram.send_preview(item, data_dir)
            except Exception as exc:
                log.warning("Prévia no Telegram falhou: %s", exc)
    result["published"] = review.publish_approved(db, data_dir, kinds=("biblia",))
    result["social"] = review.publish_social(db)
    result["pruned"] = prune_published(db, data_dir)
    return result


def insight(ordem: int, db: Database, themes: Path = THEMES_CSV) -> dict:
    """Generate (or read from cache) the theological insight script and long-form metadata for a theme."""
    with themes.open(newline="", encoding="utf-8") as handle:
        row = next((r for r in csv.DictReader(handle) if int(r["ordem"]) == ordem), None)
    if not row:
        raise SystemExit(f"Tema {ordem} não existe em {themes}")
    script = biblia_ai.theological_script(row["tema"], row["subtema"], row["palavras_chave"], db)
    metadata = biblia_ai.theological_metadata(row["tema"], row["subtema"], script["secoes"], db)
    return {"tema": row["tema"], "subtema": row["subtema"], "script": script, "metadata": metadata}


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Modo Bíblia: gera um Short por execução e envia para revisão")
    parser.add_argument("--data-dir", type=Path, default=Path(os.getenv("FARMCORTES_DATA", ROOT / "data")))
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("run", help="gera um vídeo (padrão)")
    imp = sub.add_parser("import", help="importa versiculos.csv (só se a fila estiver vazia)")
    imp.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    ins = sub.add_parser("insight", help="gera roteiro e metadados teológicos de um tema de temas.csv")
    ins.add_argument("--ordem", type=int, required=True)
    args = parser.parse_args()
    data_dir = args.data_dir.resolve()
    db = Database(data_dir / "farmcortes.sqlite3")
    if args.command == "import":
        output = {"imported": import_csv(db, args.csv)}
    elif args.command == "insight":
        output = insight(args.ordem, db)
    else:
        output = run_once(data_dir, db)
    print(json.dumps(output, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
