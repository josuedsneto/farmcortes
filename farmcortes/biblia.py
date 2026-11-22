"""Biblia mode: one vertical Bible-reading Short per run, sent to the shared review queue.

Flow: verse queue in SQLite (imported once from versiculos.csv) -> verse text from the
Pesquisar na Bíblia API (ACF) -> edge-tts narration (Piper fallback) -> Pexels background (Pixabay, then a plain
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
import subprocess
from pathlib import Path

import requests

from . import biblia_ai, biblia_cta, review, telegram
from .db import Database
from .original import _escape_filter, synthesize
from .pipeline import PipelineError, probe, run

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CSV = ROOT / "dados-iniciais" / "biblia" / "versiculos.csv"
THEMES_CSV = ROOT / "dados-iniciais" / "biblia" / "temas.csv"
VERSES_API = "https://pesquisarnabiblia.com.br/api-projeto/api/get_verses.php"
WORDS_PER_SECOND = 130 / 60  # Biblia's estimate for pt-BR narration
PART_MAX_SECONDS = 57        # planning target; the rendered video is held to MAX_VIDEO_SECONDS
MAX_VIDEO_SECONDS = 59.5      # whole video, CTA included
CTA_MARGIN = 2.5              # planning slack between the word estimate and the real narration
MIN_VERSE_BUDGET = 15         # below this a CTA is skipped rather than squeezing the reading
VERSE_PAUSE = 0.5
MUSIC_VOLUME = 0.15
EDGE_VOICE = "pt-BR-AntonioNeural"  # Biblia's edge-tts voice, used with its default rate/pitch (+0%, +0Hz)
CAPTION_CHARS = 26                  # longest caption line before a phrase is split
WEAK_LINE_ENDS = {
    "a", "o", "as", "os", "e", "de", "do", "da", "dos", "das", "em", "no", "na", "nos", "nas", "que", "me", "te",
    "se", "lhe", "um", "uma", "por", "para", "com", "ao", "aos", "à", "às", "meu", "minha", "teu", "tua", "seu", "sua",
}
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


_REKEY_OFFSET = 10_000_000


def import_csv(db: Database, path: Path = DEFAULT_CSV) -> int:
    """Load versiculos.csv into the queue and return how many verses were added.

    The first run inserts every row with its published flags. Later runs merge by
    (livro, capitulo, verso): new verses are added with the CSV flags, existing rows keep their
    state in the DB (published flags, text, parts) and take the CSV 'ordem', and verses no longer
    in the CSV are deleted when they have no history, or parked at a negative 'ordem' otherwise.
    Nothing already published is queued again. Unchanged CSV -> no writes.
    """
    with Path(path).open(newline="", encoding="utf-8") as handle:
        rows = [
            (
                int(row["ordem"]), row["livro"].strip().lower(), int(row["capitulo"]), int(row["verso"]),
                *(int(str(row.get(f"publicado_{p}", "false")).strip().lower() == "true") for p in PLATFORMS),
            )
            for row in csv.DictReader(handle)
        ]
    wanted = {(livro, cap, verso): ordem for ordem, livro, cap, verso, *_ in rows}
    with db.connect() as conn:
        existing = {(r["livro"], r["capitulo"], r["verso"]): r["ordem"]
                    for r in conn.execute("SELECT ordem,livro,capitulo,verso FROM biblia_verses WHERE ordem>0")}
        if existing == wanted:
            return 0
        conn.execute("BEGIN IMMEDIATE")
        referenced = {r[0] for r in conn.execute("SELECT ordem FROM biblia_videos")}
        for key, old in existing.items():
            if key in wanted:
                continue
            state = conn.execute(
                "SELECT published_youtube+published_tiktok+published_facebook+published_instagram AS published,parts_done "
                "FROM biblia_verses WHERE ordem=?", (old,)).fetchone()
            if not state["published"] and not state["parts_done"] and old not in referenced:
                conn.execute("DELETE FROM biblia_verses WHERE ordem=?", (old,))
            else:  # keep the history, out of the queue
                conn.execute("UPDATE biblia_verses SET ordem=?,parts_total=parts_done WHERE ordem=?", (-old, old))
                conn.execute("UPDATE biblia_videos SET ordem=? WHERE ordem=?", (-old, old))
        moves = [(wanted[key] + _REKEY_OFFSET, old) for key, old in existing.items() if key in wanted and wanted[key] != old]
        for table in ("biblia_verses", "biblia_videos"):
            conn.executemany(f"UPDATE {table} SET ordem=? WHERE ordem=?", moves)
            conn.execute(f"UPDATE {table} SET ordem=ordem-? WHERE ordem>=?", (_REKEY_OFFSET, _REKEY_OFFSET))
        new = [row for row in rows if (row[1], row[2], row[3]) not in existing]
        conn.executemany(
            "INSERT INTO biblia_verses (ordem,livro,capitulo,verso,published_youtube,published_tiktok,"
            "published_facebook,published_instagram) VALUES (?,?,?,?,?,?,?,?)",
            new,
        )
        conn.execute("COMMIT")
    return len(new)


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


def _fill_text(conn, verse: dict, fetch) -> dict | None:
    """Return the verse with its text, fetching its chapter once. A verse the API does not have
    (different versification) is marked skipped (parts_total=0) instead of blocking the queue."""
    verse =dict(conn.execute("SELECT * FROM biblia_verses WHERE ordem=?", (verse["ordem"],)).fetchone())
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
        log.warning("Versículo %s %s:%s não veio da API; pulando", verse["livro"], verse["capitulo"], verse["verso"])
        conn.execute("UPDATE biblia_verses SET parts_total=0,parts_done=0 WHERE ordem=?", (verse["ordem"],))
        return None
    return dict(row)


def next_video(db: Database, fetch=None, max_seconds: float = PART_MAX_SECONDS) -> dict | None:
    """Plan the next video: consecutive pending verses up to max_seconds, or the next part of a long verse."""
    fetch = fetch or fetch_chapter
    with db.connect() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM biblia_verses WHERE published_youtube=0 AND (parts_total IS NULL OR parts_done < parts_total) "
            "ORDER BY ordem LIMIT 40"
        ).fetchall()]
        first = None
        while rows and first is None:
            first = _fill_text(conn, rows.pop(0), fetch)
        if first is None:
            return None
        in_progress = first["parts_done"] and first["part_budget"] and (first["parts_total"] or 0) > 1
        budget = first["part_budget"] if in_progress else max_seconds
        parts = split_parts(first["texto"], budget)
        if len(parts) > 1:
            done = first["parts_done"] if first["parts_total"] == len(parts) else 0
            return {"verses": [first], "text": parts[done], "part": done + 1, "parts": len(parts), "budget": budget,
                    "complete": [first["ordem"]] if done + 1 == len(parts) else []}
        verses, total = [first], estimate_seconds(first["texto"])
        for row in rows:
            if row["ordem"] != verses[-1]["ordem"] + 1:
                break
            row = _fill_text(conn, row, fetch)
            if row is None:
                break
            seconds = estimate_seconds(row["texto"]) + VERSE_PAUSE
            if total + seconds > max_seconds:
                break
            verses.append(row)
            total += seconds
    return {"verses": verses, "text": " ".join(v["texto"] for v in verses), "part": 1, "parts": 1,
            "budget": max_seconds, "complete": [v["ordem"] for v in verses]}


def record_video(db: Database, plan: dict, review_id: int) -> None:
    """Advance the queue after a video was enqueued; map fully covered verses to the review item."""
    with db.connect() as conn:
        for verse in plan["verses"]:
            conn.execute("UPDATE biblia_verses SET parts_total=?,parts_done=?,part_budget=? WHERE ordem=?",
                         (plan["parts"], plan["part"], plan.get("budget") if plan["parts"] > 1 else None, verse["ordem"]))
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


def caption_groups(text: str, max_chars: int = CAPTION_CHARS) -> list[list[int]]:
    """Word-index groups for captions: break after . ; : , ? ! first, then split long phrases into
    balanced chunks at word boundaries, never ending a line on a weak word (artigo, preposição...)."""
    words = text.split()
    phrases, current = [], []
    for index, word in enumerate(words):
        current.append(index)
        if word[-1] in ".;:,?!":
            phrases.append(current)
            current = []
    if current:
        phrases.append(current)
    groups = []
    for phrase in phrases:
        size = len(" ".join(words[i] for i in phrase))
        chunks = min(len(phrase), math.ceil(size / max_chars))
        target = size / chunks
        lines, line, length = [], [], 0
        for i in phrase:
            if line and len(lines) < chunks - 1 and length + len(words[i]) / 2 > target * (len(lines) + 1):
                lines.append(line)
                line = []
            line.append(i)
            length += len(words[i]) + 1
        lines.append(line)
        for a, b in zip(lines, lines[1:]):  # move a dangling weak word to the next line
            while len(a) > 1 and words[a[-1]].casefold().strip(".,;:?!") in WEAK_LINE_ENDS:
                b.insert(0, a.pop())
        groups.extend(line for line in lines if line)
    return groups


def caption_timings(text: str, duration: float, words: list | None = None) -> list[tuple[float, float, str]]:
    """(start, end, text) per caption. Uses the TTS word timings when they match the text, otherwise
    spreads the captions over the narration by character count."""
    tokens = text.split()
    groups = caption_groups(text)
    norm = [re.sub(r"\W", "", token.casefold()) for token in tokens]
    starts = None
    if words and "".join(norm) == "".join(re.sub(r"\W", "", w[0].casefold()) for w in words):
        # map each token to the TTS word that holds its first and last letter (handles split/merged words)
        bounds, cursor = [], 0
        for word in words:
            cursor += len(re.sub(r"\W", "", word[0].casefold()))
            bounds.append(cursor)

        def word_at(char):
            return next(i for i, end in enumerate(bounds) if end > char) if char < cursor else len(words) - 1

        starts, ends, position = [], [], 0
        for token in norm:
            starts.append(words[word_at(position)][1])
            ends.append(words[word_at(position + max(len(token), 1) - 1)][2])
            position += len(token)
    if starts is None:
        weights = [len(token) + 1 for token in tokens]
        total, cursor, starts, ends = sum(weights), 0.0, [], []
        for weight in weights:
            starts.append(cursor)
            cursor += duration * weight / total
            ends.append(cursor)
    result = []
    for n, group in enumerate(groups):
        start = starts[group[0]]
        end = starts[groups[n + 1][0]] if n + 1 < len(groups) else min(duration, ends[group[-1]] + 0.4)
        result.append((start, max(end, start + 0.3), " ".join(tokens[i] for i in group)))
    return result


def write_subtitles(path: Path, text: str, reference: str, label: str, duration: float, words: list | None = None) -> None:
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
    for start, end, caption in caption_timings(text, max(duration - 0.6, 0.5), words):
        rows.append(f"Dialogue: 0,{_clock(start)},{_clock(end)},Verse,,0,0,0,,{_ass_text(caption)}\n")
    path.write_text("".join(rows), encoding="utf-8")


def dark_trim(rows: list[int], threshold: int = 28, limit: float = 0.35) -> tuple[float, float]:
    """Vertical span (top, bottom fractions) of the image without its near-black top/bottom bands
    (dark foreground or sky), trimming at most `limit` of the height in total."""
    n = len(rows)
    top, bottom = 0, n
    while top < n and rows[top] < threshold:
        top += 1
    while bottom > top and rows[bottom - 1] < threshold:
        bottom -= 1
    if bottom <= top:  # a dark image all over: leave it alone
        return 0.0, 1.0
    cut_top, cut_bottom = top / n, (n - bottom) / n
    if cut_top + cut_bottom > limit:  # trim both bands proportionally down to the limit
        scale = limit / (cut_top + cut_bottom)
        cut_top, cut_bottom = cut_top * scale, cut_bottom * scale
    return cut_top, 1 - cut_bottom


def _row_brightness(image: Path, rows: int = 64) -> list[int]:
    try:
        raw = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", str(image), "-vf", f"scale=1:{rows},format=gray", "-f", "rawvideo", "-"],
            capture_output=True, timeout=60, check=True,
        ).stdout
        return list(raw[:rows]) if len(raw) >= rows else []
    except Exception:
        return []


def background_filter(image: Path) -> str:
    """Cover 1080x1920 (scale up + center crop, never letterboxed) after trimming near-black bands."""
    rows = _row_brightness(image)
    top, bottom = dark_trim(rows) if rows else (0.0, 1.0)
    crop = f"crop=iw:ih*{bottom - top:.4f}:0:ih*{top:.4f}," if (top, bottom) != (0.0, 1.0) else ""
    return f"{crop}scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920"


def render(image: Path, narration: Path, music: Path | None, text: str, reference: str, label: str, output: Path,
           cta: "biblia_cta.Cta | None" = None, words: list | None = None) -> float:
    """One FFmpeg pass: verse segment (captions, music) then the optional CTA clip, normalized to
    1080x1920, 30 fps, SAR 1, 48 kHz stereo, -16 LUFS. The whole video stays within MAX_VIDEO_SECONDS;
    the narration is sped up slightly if needed. Returns the total duration."""
    spoken = float(probe(narration).get("format", {}).get("duration") or 0)
    if spoken <= 0:
        raise PipelineError("A narração ficou vazia")
    verse_max = MAX_VIDEO_SECONDS - (cta.duration if cta else 0)
    tempo = max(1.0, spoken / (verse_max - 0.6))
    if tempo > 1.3:
        raise PipelineError(f"Narração longa demais para um Short: {spoken:.1f}s")
    verse = spoken / tempo + 0.6
    subtitle = output.with_suffix(".ass")
    timings = [(w, s / tempo, e / tempo) for w, s, e in words] if words else None
    write_subtitles(subtitle, text, reference, label, verse, timings)
    audio_out = "aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo"
    filters = (
        f"[0:v]{background_filter(image)},"
        f"eq=brightness=-0.2:saturation=0.85,subtitles='{_escape_filter(subtitle)}',fps=30,format=yuv420p,setsar=1[vv];"
        f"[1:a]atempo={tempo:.4f},apad=pad_dur=0.6,loudnorm=I=-16:LRA=9:TP=-1.5[n]"
    )
    command = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
               "-loop", "1", "-framerate", "30", "-t", f"{verse:.3f}", "-i", str(image), "-i", str(narration)]
    if music:
        command += ["-stream_loop", "-1", "-i", str(music)]
        filters += f";[2:a]volume={MUSIC_VOLUME}[m];[n][m]amix=inputs=2:duration=first:normalize=0,{audio_out}[va]"
    else:
        filters += f";[n]{audio_out}[va]"
    if cta:
        index = 3 if music else 2
        command += ["-i", str(cta.path)]
        filters += (
            f";[{index}:v]scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2,"
            "setsar=1,fps=30,format=yuv420p[cv]"
        )
        if cta.has_audio:
            filters += f";[{index}:a]loudnorm=I=-16:LRA=9:TP=-1.5,{audio_out}[ca]"
        else:
            filters += f";anullsrc=r=48000:cl=stereo:d={cta.duration:.3f},{audio_out}[ca]"
        filters += ";[vv][va][cv][ca]concat=n=2:v=1:a=1[v][a]"
    else:
        filters += ";[vv]null[v];[va]anull[a]"
    command += [
        "-filter_complex", filters, "-map", "[v]", "-map", "[a]",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-pix_fmt", "yuv420p", "-r", "30",
        "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2", "-movflags", "+faststart", str(output),
    ]
    run(command, timeout=1800)
    info = probe(output)
    video = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), {})
    if video.get("width") != 1080 or video.get("height") != 1920:
        raise PipelineError("O vídeo da Bíblia não ficou em 1080x1920")
    return float(info.get("format", {}).get("duration") or verse + (cta.duration if cta else 0))


def verse_budget(cta: "biblia_cta.Cta | None") -> float:
    """Planned narration seconds for the verse part, leaving room for the CTA inside 60 s."""
    if not cta:
        return PART_MAX_SECONDS
    return min(PART_MAX_SECONDS, MAX_VIDEO_SECONDS - cta.duration - CTA_MARGIN)


def edge_narration(text: str, output: Path) -> list[tuple[str, float, float]]:
    """Synthesize with edge-tts (unofficial Microsoft Edge endpoint). Returns (word, start, end) in seconds."""
    import asyncio

    import edge_tts

    async def stream():
        audio, words = bytearray(), []
        voice = os.getenv("FARMCORTES_BIBLIA_VOICE") or EDGE_VOICE
        async for chunk in edge_tts.Communicate(text, voice, boundary="WordBoundary").stream():
            if chunk["type"] == "audio":
                audio += chunk["data"]
            elif chunk["type"] == "WordBoundary":
                start = chunk["offset"] / 10_000_000
                words.append((chunk["text"], start, start + chunk["duration"] / 10_000_000))
        return bytes(audio), words

    audio, words = asyncio.run(stream())
    if not audio:
        raise PipelineError("edge-tts não devolveu áudio")
    output.write_bytes(audio)
    return words


def narrate(text: str, folder: Path, data_dir: Path) -> tuple[Path, list | None]:
    """edge-tts by default (FARMCORTES_BIBLIA_TTS=edge|piper); Piper takes over if edge-tts fails.
    Returns the audio path and the word timings (None for Piper)."""
    if (os.getenv("FARMCORTES_BIBLIA_TTS") or "edge").strip().casefold() != "piper":
        try:
            path = folder / "narration.mp3"
            return path, edge_narration(text, path)
        except Exception as exc:
            log.warning("edge-tts falhou, usando Piper: %s", exc)
    model = data_dir / "models" / "pt_BR-edresson-low.onnx"
    if not model.is_file():
        raise PipelineError("Modelo de voz pt-BR não está instalado")
    path = folder / "narration.wav"
    synthesize(text, path, model)
    return path, None


# ── run ──────────────────────────────────────────────────────────────────────

def make_video(data_dir: Path, db: Database, plan: dict, cta: biblia_cta.Cta | None = None) -> int:
    """Render the planned video, enqueue it for review and clean the temporary files. Returns the review id."""
    first = plan["verses"][0]
    folder = data_dir / "biblia" / f"{first['ordem']:05d}-p{plan['part']}"
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True)
    video = folder / "short.mp4"
    try:
        narration, words = narrate(plan["text"], folder, data_dir)
        attribution = background_image(folder / "background.jpg")
        music, music_credit = background_music(data_dir)
        reference = biblia_ai.reference_range(plan["verses"])
        label = f"Parte {plan['part']}/{plan['parts']}" if plan["parts"] > 1 else ""
        duration = render(folder / "background.jpg", narration, music, plan["text"], reference, label, video, cta, words)
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
        description="\n\n".join(filter(None, [meta["descricao"], biblia_cta.description_line() if cta else ""])), hashtags="#Bíblia #LeiaABíblia #PalavraDeDeus #Shorts",
        credit_text=credit, db=db,
    )
    record_video(db, plan, item_id)
    if cta:
        biblia_cta.record_use(db, item_id, cta)
    return item_id


def choose_cta(db: Database, data_dir: Path) -> "biblia_cta.Cta | None":
    """Least recently used CTA that still leaves a reasonable reading time; None when there is none."""
    clips = biblia_cta.by_least_recent_use(db, biblia_cta.available(biblia_cta.cta_dir(data_dir)))
    return next((c for c in clips if verse_budget(c) >= MIN_VERSE_BUDGET), None)


def plan_with_cta(db: Database, data_dir: Path) -> tuple[dict | None, "biblia_cta.Cta | None"]:
    """Pick the CTA first, then plan the verse part inside what is left of the 60 s."""
    cta = choose_cta(db, data_dir)
    plan = next_video(db, max_seconds=verse_budget(cta))
    if plan and cta and plan["budget"] > verse_budget(cta):
        # a long verse already split with a bigger budget: use a CTA that fits, or none
        clips = biblia_cta.by_least_recent_use(db, biblia_cta.available(biblia_cta.cta_dir(data_dir)))
        cta = next((c for c in clips if verse_budget(c) >= plan["budget"]), None)
    return plan, cta


def prune_published(db: Database, data_dir: Path) -> int:
    """Delete rendered Biblia videos nobody needs any more (published everywhere enabled, or discarded)."""
    platforms = review.social_platforms()
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
    plan, cta = plan_with_cta(db, data_dir)
    result: dict = {"status": "idle"}
    if plan:
        item_id = make_video(data_dir, db, plan, cta)
        approved = review.auto_approve(item_id, db)
        result = {"status": "approved" if approved else "pending", "review_id": item_id,
                  "reference": biblia_ai.reference_range(plan["verses"]), "part": plan["part"], "parts": plan["parts"],
                  "cta": cta.name if cta else None}
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
