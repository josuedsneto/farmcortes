from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Iterable


HOOK_TERMS = {
    "acredita", "absurdo", "aconteceu", "atenção", "como", "descobri", "erro",
    "impossível", "inacreditável", "melhor", "motivo", "nunca", "pior", "porque",
    "problema", "razão", "segredo", "surpresa", "verdade",
}
ARC_TERMS = {
    "aí", "até que", "depois", "então", "mas", "no fim", "por isso", "porém",
    "quando", "resultado", "só que",
}
WEAK_OPENINGS = (
    "aí ", "bom ", "e aí ", "então ", "galera ", "né ", "olá ", "tipo ",
)
INCOMPLETE_ENDINGS = (
    "aí", "e", "então", "mas", "ou", "porque", "porém", "que", "só que",
)


def clean_transcript(text: str) -> str:
    value = re.sub(r"\s+", " ", text or "").strip(" -–—")
    value = re.sub(r"\b([Nn]é|[Tt]ipo),?\s*", "", value)
    return re.sub(r"\s+([,.;!?])", r"\1", value).strip()


def _tokens(text: str) -> list[str]:
    return re.findall(r"[\wÀ-ÿ]+", text.casefold())


def _clamp(value: float) -> float:
    return round(max(0.0, min(1.0, value)), 3)


def score_narrative(text: str, duration: float, minimum: int, maximum: int) -> dict:
    clean = clean_transcript(text)
    lower = clean.casefold()
    tokens = _tokens(clean)
    first_words = " ".join(tokens[:12])
    hook_hits = sum(1 for term in HOOK_TERMS if re.search(rf"\b{re.escape(term)}\b", first_words))
    hook = 0.45 + min(0.45, hook_hits * 0.2)
    if clean.endswith("?") or clean.endswith("!"):
        hook += 0.15
    if any(lower.startswith(prefix) for prefix in WEAK_OPENINGS):
        hook -= 0.3

    ending = tokens[-2:] if tokens else []
    complete = 0.95 if re.search(r"[.!?][\"']?$", clean) else 0.45
    if (ending and " ".join(ending) in INCOMPLETE_ENDINGS) or (tokens and tokens[-1] in INCOMPLETE_ENDINGS):
        complete -= 0.55

    arc_hits = sum(1 for term in ARC_TERMS if re.search(rf"\b{re.escape(term)}\b", lower))
    narrative = 0.35 + min(0.6, arc_hits * 0.16)
    if len(tokens) >= 35:
        narrative += 0.1

    clarity = 0.9
    if tokens and tokens[0] in {"ele", "ela", "eles", "elas", "isso", "aquilo"}:
        clarity -= 0.3
    filler_ratio = len(re.findall(r"\b(?:né|tipo|assim|entendeu)\b", lower)) / max(1, len(tokens))
    clarity -= min(0.4, filler_ratio * 5)

    preferred = 55.0 if minimum <= 55 <= maximum else (minimum + maximum) / 2
    duration_fit = max(0.0, 1 - abs(duration - preferred) / max(preferred, 1))
    if duration > 65:
        duration_fit *= 0.75 + 0.25 * _clamp((narrative + complete) / 2)

    hook = _clamp(hook)
    complete = _clamp(complete)
    narrative = _clamp(narrative)
    clarity = _clamp(clarity)
    duration_fit = _clamp(duration_fit)
    total = round(10 * (0.3 * hook + 0.25 * complete + 0.2 * narrative + 0.15 * clarity + 0.1 * duration_fit), 2)
    flags: list[str] = []
    if hook < 0.45:
        flags.append("gancho_fraco")
    if complete < 0.6:
        flags.append("final_incompleto")
    if clarity < 0.6:
        flags.append("contexto_ambiguo")
    if duration > 60:
        flags.append("verificar_content_id")
    # faster-whisper timestamps can carry NumPy scalars through duration math.
    # Force the public strategy payload back to a stdlib boolean.
    editorial_ready = bool(hook >= 0.5 and complete >= 0.6 and clarity >= 0.6 and total >= 6.0)
    return {
        "hook": hook,
        "completeness": complete,
        "narrative": narrative,
        "clarity": clarity,
        "duration_fit": duration_fit,
        "total": total,
        "flags": flags,
        "editorial_ready": editorial_ready,
        "duration_band": "curto" if duration < 45 else "padrão" if duration <= 65 else "estendido",
    }


def _shorten(value: str, limit: int = 50) -> str:
    value = clean_transcript(value).strip(".,;:!?\"' ")
    if len(value) <= limit:
        return value
    clipped = value[: limit + 1].rsplit(" ", 1)[0].rstrip(".,;:!?")
    return clipped if len(clipped) >= 18 else value[:limit].rstrip()


def _title_quality(value: str) -> float:
    clean = clean_transcript(value).strip(".,;:!?\"' ")
    tokens = _tokens(clean)
    lowered = clean.casefold()
    score = min(len(tokens), 10)
    if len(tokens) < 4:
        score -= 5
    if any(lowered == prefix.strip() or lowered.startswith(prefix) for prefix in WEAK_OPENINGS):
        score -= 4
    if re.search(r"\b(?:como|por que|porque|verdade|segredo|mudou|aconteceu|nunca|melhor|pior)\b", lowered):
        score += 3
    if re.search(r"[?!]$", value.strip()):
        score += 2
    if 22 <= len(clean) <= 50:
        score += 2
    return score


def title_options(text: str) -> list[str]:
    clean = clean_transcript(text)
    sentences = [part.strip() for part in re.split(r"(?<=[.!?])\s+", clean) if part.strip()]
    clauses = [part.strip() for part in re.split(r"\s+(?:mas|porém|só que|porque)\s+", clean, flags=re.IGNORECASE) if part.strip()]
    candidates: list[str] = []
    values = [*sentences, *reversed(clauses)]
    values.sort(key=_title_quality, reverse=True)
    for value in values:
        title = _shorten(value)
        if title and title.casefold() not in {known.casefold() for known in candidates}:
            candidates.append(title[:1].upper() + title[1:])
        if len(candidates) == 3:
            break
    if not candidates:
        candidates.append("Corte selecionado")
    return candidates


def hook_options(text: str) -> list[str]:
    titles = title_options(text)
    options = list(titles)
    if titles:
        subject = _shorten(titles[0], 38)
        options.extend((f"Preste atenção: {subject}", f"O ponto decisivo: {subject}"))
    unique: list[str] = []
    for value in options:
        value = _shorten(value, 58)
        if value.casefold() not in {known.casefold() for known in unique}:
            unique.append(value)
        if len(unique) == 3:
            break
    return unique


def build_longform_plan(job: dict, outputs: Iterable[dict]) -> dict:
    clips = sorted(outputs, key=lambda item: float(item.get("score", 0)), reverse=True)
    available = round(sum(float(item.get("duration", 0)) for item in clips), 1)
    target = 480
    source_title = str(job.get("title") or "tema selecionado").strip()
    sections = [
        {
            "order": index,
            "title": item.get("title") or f"Trecho {index}",
            "file": item.get("file"),
            "duration": item.get("duration"),
            "role": "gancho" if index == 1 else "desenvolvimento",
        }
        for index, item in enumerate(clips, 1)
    ]
    return {
        "title": _shorten(f"O que estes momentos revelam sobre {source_title}", 70),
        "target_seconds": target,
        "available_clip_seconds": available,
        "missing_seconds": max(0, round(target - available, 1)),
        "status": "ready" if available >= 360 else "needs_more_material",
        "sections": sections,
        "production_notes": [
            "Adicionar abertura original que apresente a pergunta central.",
            "Conectar cada trecho com narração ou comentário original.",
            "Encerrar com síntese própria e chamada para o próximo vídeo.",
            "Usar apenas material próprio, licenciado ou substancialmente transformado.",
        ],
    }


def performance_report(snapshot: dict | None) -> dict:
    if not snapshot:
        return {
            "status": "empty",
            "message": "Registre uma fotografia do YouTube Studio para iniciar o ciclo de aprendizado.",
            "recommendations": [],
        }
    views = max(0, int(snapshot.get("views") or 0))
    watch_hours = max(0.0, float(snapshot.get("watch_hours") or 0))
    subscribers = int(snapshot.get("subscribers_gained") or 0)
    supplied_avd = float(snapshot.get("avg_view_duration_seconds") or 0)
    computed_avd = round(watch_hours * 3600 / views, 1) if views else 0.0
    conversion = round(subscribers * 1000 / views, 2) if views else 0.0
    warnings: list[str] = []
    if supplied_avd and computed_avd and abs(supplied_avd - computed_avd) > 5:
        warnings.append("A duração média informada não coincide com horas ÷ visualizações; confira o mesmo escopo no Studio.")
    recommendations = [
        "Priorize cortes com gancho forte, final completo e conversão acima da mediana do canal.",
        "Use Shorts para descoberta e direcione para vídeos longos autorais de 6–12 minutos.",
        "Compare faixas de 45–54 s, 55–65 s e 66–90 s sem republicar o mesmo trecho.",
    ]
    if views >= 500 and conversion < 3:
        recommendations.insert(
            0,
            "A conversão em inscritos está baixa para a meta interna: mantenha um CTA visual curto no início e repita-o perto do encerramento.",
        )
    return {
        "status": "ok",
        "period_days": int(snapshot.get("period_days") or 28),
        "views": views,
        "watch_hours": watch_hours,
        "subscribers_gained": subscribers,
        "subscriber_conversion_per_1000": conversion,
        "supplied_avg_view_duration_seconds": supplied_avd,
        "computed_avg_view_duration_seconds": computed_avd,
        "warnings": warnings,
        "recommendations": recommendations,
        "created_at": snapshot.get("created_at") or datetime.now(timezone.utc).isoformat(),
    }
