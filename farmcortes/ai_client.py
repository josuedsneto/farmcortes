"""Groq (primary) -> Gemini (backup) JSON client, ported from Biblia's _ai_client.py.

Both providers are called over plain HTTPS with `requests`, so no SDK is needed.
Keys come from FARMCORTES_GROQ_API_KEY and FARMCORTES_GEMINI_API_KEY.
"""
from __future__ import annotations

import json
import logging
import os
import re

import requests

log = logging.getLogger(__name__)

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


class AIUnavailable(RuntimeError):
    """No provider is configured or every configured provider failed."""


def configured() -> bool:
    return bool(os.getenv("FARMCORTES_GROQ_API_KEY") or os.getenv("FARMCORTES_GEMINI_API_KEY"))


def _sanitize_json_strings(raw: str) -> str:
    """Escape literal newlines/tabs that models sometimes leave inside JSON strings."""
    result, in_string, escape_next = [], False, False
    for ch in raw:
        if escape_next:
            escape_next = False
        elif ch == "\\":
            escape_next = True
        elif ch == '"':
            in_string = not in_string
        elif in_string and ch in "\n\r\t":
            ch = {"\n": "\\n", "\r": "\\r", "\t": "\\t"}[ch]
        result.append(ch)
    return "".join(result)


def parse_json(raw: str) -> dict:
    raw = re.sub(r"```(?:json)?", "", raw or "").strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        try:
            return json.loads(_sanitize_json_strings(raw))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Resposta da IA não é JSON válido: {raw[:200]}") from exc


def _call_groq(prompt: str, key: str) -> str:
    response = requests.post(
        GROQ_URL,
        headers={"Authorization": f"Bearer {key}"},
        json={
            "model": os.getenv("FARMCORTES_GROQ_MODEL", "llama-3.3-70b-versatile"),
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.7,
            "max_tokens": 8192,
            "response_format": {"type": "json_object"},
        },
        timeout=120,
    )
    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"]


def _call_gemini(prompt: str, key: str) -> str:
    model = os.getenv("FARMCORTES_GEMINI_MODEL", "gemini-2.0-flash")
    response = requests.post(
        GEMINI_URL.format(model=model),
        headers={"x-goog-api-key": key},
        json={
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"responseMimeType": "application/json"},
        },
        timeout=120,
    )
    response.raise_for_status()
    text = response.json()["candidates"][0]["content"]["parts"][0]["text"]
    if not text:
        raise ValueError("Gemini devolveu resposta vazia")
    return text


def call_ai_json(prompt: str) -> dict:
    """Try Groq, then Gemini. Raises AIUnavailable when neither works."""
    errors = []
    for name, env, call in (
        ("Groq", "FARMCORTES_GROQ_API_KEY", _call_groq),
        ("Gemini", "FARMCORTES_GEMINI_API_KEY", _call_gemini),
    ):
        key = os.getenv(env)
        if not key:
            continue
        try:
            return parse_json(call(prompt, key))
        except Exception as exc:  # any provider failure falls through to the next one
            log.warning("%s falhou: %s", name, str(exc)[:160])
            errors.append(f"{name}: {str(exc)[:160]}")
    raise AIUnavailable("; ".join(errors) or "Nenhuma chave de IA configurada")
