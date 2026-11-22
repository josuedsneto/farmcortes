"""AI titles, descriptions and theological insights for the Biblia mode.

Results are cached in the `ai_cache` table. Without an AI key, or when every provider fails,
verse metadata falls back to a template, so a run never fails because of AI.
"""
from __future__ import annotations

import json
import logging

from . import ai_client
from .biblia_prompts import THEOLOGICAL_METADATA_PROMPT, THEOLOGICAL_SCRIPT_PROMPT, VERSE_PROMPT
from .db import Database, now

log = logging.getLogger(__name__)

SERIES = "Leia a Bíblia Toda em Um Ano"


def cache_get(db: Database, key: str) -> dict | None:
    with db.connect() as conn:
        row = conn.execute("SELECT value_json FROM ai_cache WHERE key=?", (key,)).fetchone()
    return json.loads(row["value_json"]) if row else None


def cache_put(db: Database, key: str, value: dict) -> None:
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO ai_cache (key,value_json,created_at) VALUES (?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,created_at=excluded.created_at",
            (key, json.dumps(value, ensure_ascii=False), now()),
        )


def reference_range(verses: list[dict]) -> str:
    first, last = verses[0], verses[-1]
    if len(verses) == 1:
        return first["referencia"]
    if first["capitulo"] == last["capitulo"]:
        return f"{first['referencia']}-{last['verso']}"
    return f"{first['referencia']} a {last['capitulo']}:{last['verso']}"


def verse_key(verses: list[dict], part: int = 1, parts: int = 1) -> str:
    first, last = verses[0], verses[-1]
    key = f"verse:{first['livro']}:{first['capitulo']}:{first['verso']}-{last['capitulo']}:{last['verso']}"
    return key + (f":parte{part}de{parts}" if parts > 1 else "")


def _suffix(part: int, parts: int) -> str:
    return f" (Parte {part}/{parts})" if parts > 1 else ""


def _fit(title: str, suffix: str = "") -> str:
    title = " ".join(str(title).split())
    title = title.removesuffix(suffix.strip()).strip() if suffix else title
    room = 100 - len(suffix)
    if len(title) > room:
        title = title[:room].rsplit(" ", 1)[0].rstrip(" |-:—")
    return title + suffix


def template_metadata(verses: list[dict], part: int = 1, parts: int = 1) -> dict:
    ref = reference_range(verses)
    suffix = _suffix(part, parts)
    text = "\n\n".join(f"{v['referencia']}\n{v['texto']}" for v in verses)
    return {
        "titulos": [
            _fit(f"{ref} | {SERIES}", suffix),
            _fit(f"Versículo do dia: {ref}", suffix),
            _fit(f"{ref} — Bíblia em um ano", suffix),
        ],
        "descricao": f"{text}\n\nAcompanhe a leitura diária: inscreva-se para não perder nenhum dia do plano {SERIES}.",
        "source": "template",
    }


def verse_metadata(verses: list[dict], db: Database, part: int = 1, parts: int = 1) -> dict:
    """Three titles plus a description for a verse video. Never raises because of AI."""
    key = verse_key(verses, part, parts)
    cached = cache_get(db, key)
    if cached:
        return cached
    fallback = template_metadata(verses, part, parts)
    if not ai_client.configured():
        return fallback
    first = verses[0]
    versos = [str(v["verso"]) for v in verses]
    prompt = VERSE_PROMPT.format(
        book=str(first["referencia"]).rsplit(" ", 1)[0].upper(),
        chapter=first["capitulo"],
        verse_range=versos[0] if len(versos) == 1 else f"{versos[0]}-{versos[-1]}",
        translation="ACF",
        verse_text="\n".join(f"{v['referencia']}: {v['texto']}" for v in verses),
    )
    try:
        raw = ai_client.call_ai_json(prompt)
        titles = raw.get("titulos") or [raw.get("titulo")]
        titles = [t.strip() for t in titles if isinstance(t, str) and t.strip()]
        description = str(raw.get("descricao") or "").strip()
        if not titles or not description:
            raise ValueError("JSON sem titulos/descricao")
        titles = list(dict.fromkeys(_fit(t, _suffix(part, parts)) for t in titles))
        titles = (titles + [t for t in fallback["titulos"] if t not in titles])[:3]
        result = {"titulos": titles, "descricao": description, "source": "ai"}
    except Exception as exc:
        log.warning("Metadados por IA indisponíveis, usando modelo: %s", exc)
        return fallback
    cache_put(db, key, result)
    return result


def theological_script(tema: str, subtema: str, palavras_chave: str, db: Database) -> dict:
    """Theological insight script (sections + verse highlights), cached. Needs an AI key."""
    if not tema or not subtema:
        raise ValueError("tema e subtema são obrigatórios")
    key = f"theo-script:{tema}:{subtema}"
    cached = cache_get(db, key)
    if cached:
        return cached
    result = ai_client.call_ai_json(
        THEOLOGICAL_SCRIPT_PROMPT.format(tema=tema, subtema=subtema, palavras_chave=palavras_chave)
    )
    if not result.get("secoes"):
        raise ValueError("Roteiro teológico sem seções")
    cache_put(db, key, result)
    return result


def theological_metadata(tema: str, subtema: str, secoes: list[dict], db: Database) -> dict:
    """Long-form title and description for a theological insight; template fallback without AI."""
    key = f"theo-meta:{tema}:{subtema}"
    cached = cache_get(db, key)
    if cached:
        return cached
    fallback = {
        "titulo": _fit(f"{tema}: {subtema} | Teologia Bíblica"),
        "descricao": f"{tema} — {subtema}.\n\nInscreva-se para acompanhar os próximos estudos.",
        "source": "template",
    }
    if not ai_client.configured():
        return fallback
    secoes_texto = "\n".join(f"- {s.get('nome', 'Sem nome')}: {s.get('texto', '')[:200]}..." for s in secoes)
    try:
        raw = ai_client.call_ai_json(
            THEOLOGICAL_METADATA_PROMPT.format(tema=tema, subtema=subtema, secoes_texto=secoes_texto)
        )
        if not raw.get("titulo") or not raw.get("descricao"):
            raise ValueError("JSON sem titulo/descricao")
        result = {"titulo": _fit(raw["titulo"]), "descricao": str(raw["descricao"]), "source": "ai"}
    except Exception as exc:
        log.warning("Metadados teológicos por IA indisponíveis: %s", exc)
        return fallback
    cache_put(db, key, result)
    return result
