from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_unused_stub_modules_are_gone():  # DEAD-01
    assert not (ROOT / "farmcortes" / "longcuts.py").exists()
    assert not (ROOT / "farmcortes" / "scan.py").exists()


def test_no_references_to_removed_stubs():  # DEAD-02
    patterns = ("longcuts", "farmcortes.scan", "from .scan", "import scan", "scan.")
    hits = [
        f"{path.relative_to(ROOT)}: {pattern}"
        for folder in ("farmcortes", "tests", "deploy", "docs")
        for path in (ROOT / folder).rglob("*")
        if path.is_file() and path.suffix in {".py", ".md", ".service", ".timer", ".conf", ".ps1", ".js", ".html"}
        and path.name != "test_dead_code.py"
        for pattern in patterns
        if pattern in path.read_text(encoding="utf-8", errors="ignore")
    ]
    assert hits == []


from types import SimpleNamespace as NS
from unittest.mock import patch

import pytest

from farmcortes import live, pipeline
from farmcortes.pipeline import Caption, captions_from_segments


def word(text, start, end):
    return NS(word=text, start=start, end=end)


def segment(start, end, text="", words=()):
    return NS(start=start, end=end, text=text, words=list(words))


@pytest.fixture(autouse=True)
def no_glossary(monkeypatch):
    monkeypatch.setattr(pipeline, "caption_glossary", lambda: {})


def test_sentence_becomes_one_caption():  # DEAD-04
    words = [word("Oi", 0.0, 0.4), word(" tudo", 0.4, 0.8), word(" bem.", 0.8, 1.2)]
    assert captions_from_segments([segment(0.0, 1.2, words=words)]) == [Caption(0.0, 1.2, "Oi tudo bem.")]


def test_group_closes_at_3_3_seconds():  # DEAD-05
    words = [word(" a", 0, 1), word(" b", 1, 2), word(" c", 2, 3), word(" d", 3, 3.5), word(" e", 3.5, 4)]
    assert captions_from_segments([segment(0, 4, words=words)]) == [
        Caption(0, 3.5, "a b c d"), Caption(3.5, 4, "e"),
    ]


def test_segment_without_words_uses_its_text():  # DEAD-06
    assert captions_from_segments([segment(5.0, 7.0, text=" Olá ")]) == [Caption(5.0, 7.0, "Olá")]


def test_empty_segment_without_words_is_skipped():  # DEAD-07
    assert captions_from_segments([segment(5.0, 7.0, text="   ")]) == []


def test_glossary_is_applied(monkeypatch):  # DEAD-08
    monkeypatch.setattr(pipeline, "caption_glossary", lambda: {"farm cortes": "Farmcortes"})
    words = [word("farm", 0, 0.5), word(" cortes!", 0.5, 1.0)]
    assert captions_from_segments([segment(0, 1, words=words)]) == [Caption(0, 1, "Farmcortes!")]


def test_empty_input_returns_empty_list():  # DEAD-09
    assert captions_from_segments([]) == []


def test_batch_and_live_share_the_conversion(tmp_path, monkeypatch):  # DEAD-03
    segments = [segment(0.0, 1.2, words=[word("Oi", 0.0, 0.4), word(" tudo", 0.4, 0.8), word(" bem.", 0.8, 1.2)]),
                segment(2.0, 3.0, text="   ")]
    expected = [Caption(0.0, 1.2, "Oi tudo bem.")]
    info = NS(language_probability=1)
    monkeypatch.setenv("FARMCORTES_WHISPER_MODEL", "/local/model")
    with patch("faster_whisper.WhisperModel") as model:
        model.return_value.transcribe.side_effect = lambda *a, **k: (iter(segments), info)
        assert pipeline.transcribe(tmp_path / "a.mp4", tmp_path / "a.srt") == expected
        assert live.LiveTranscriber()(tmp_path / "b.ts", tmp_path / "b.srt") == expected
    sources = [p for p in (pipeline.__file__, live.__file__)]
    assert sum(open(p, encoding="utf-8").read().count(">= 3.3") for p in sources) == 1


def test_punctuation_closes_group_mid_segment():  # DEAD-04 (rule independent of segment end)
    words = [word("Oi.", 0.0, 0.4), word(" tudo", 0.4, 0.8), word(" bem", 0.8, 1.2)]
    assert captions_from_segments([segment(0.0, 1.2, words=words)]) == [
        Caption(0.0, 0.4, "Oi."), Caption(0.4, 1.2, "tudo bem"),
    ]


def test_group_closes_exactly_at_3_3_seconds():  # DEAD-05 (boundary is >= 3.3)
    words = [word(" a", 0, 1), word(" b", 1, 2), word(" c", 2, 3.3), word(" d", 3.3, 3.5)]
    assert captions_from_segments([segment(0, 3.5, words=words)]) == [
        Caption(0, 3.3, "a b c"), Caption(3.3, 3.5, "d"),
    ]


def test_group_below_3_3_seconds_stays_open():  # DEAD-05 (no early split)
    words = [word(" a", 0, 1.6), word(" b", 1.6, 3.2), word(" c", 3.2, 3.25)]
    assert captions_from_segments([segment(0, 3.25, words=words)]) == [Caption(0, 3.25, "a b c")]


def test_glossary_is_applied_to_wordless_segment(monkeypatch):  # DEAD-08 (text path)
    monkeypatch.setattr(pipeline, "caption_glossary", lambda: {"farm cortes": "Farmcortes"})
    assert captions_from_segments([segment(1, 2, text=" farm cortes ")]) == [Caption(1, 2, "Farmcortes")]
