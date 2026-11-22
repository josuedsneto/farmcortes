from pathlib import Path
import json

import numpy as np

from farmcortes.db import Database
from farmcortes.pipeline import Caption, Candidate, _smart_reframe_filter, apply_caption_glossary, classify_import_error, humanize_error, parse_srt, select_candidates, select_timeline_candidates, validate_source_url, validate_youtube_url, write_ass
from farmcortes.youtube import (
    _finish_resumable_upload, best_output, metadata_for, optimized_title,
    ranked_outputs, schedule_slots, settings,
)
from farmcortes.original import Story, build_script, metadata as original_metadata, timed_captions
from farmcortes.discovery import begin_discovery_run, complete_discovery_run, discovery_status, is_portuguese, viral_score
from farmcortes.strategy import build_longform_plan, performance_report, score_narrative, title_options
from farmcortes.compilations import _extract_json, compilation_key, complete_transitions, fallback_outline, metadata_for_compilation, select_clips
from farmcortes.editorial import active_campaign, discovery_topics
from farmcortes.source_compilations import _chapter_window, select_source_groups, used_source_ids
from farmcortes.maintenance import desired_scheduled_metadata


def test_url_validation_accepts_only_youtube():
    assert validate_youtube_url("https://youtu.be/abc") == "https://youtu.be/abc"
    for value in ("file:///etc/passwd", "https://example.com/video", "javascript:alert(1)"):
        try:
            validate_youtube_url(value)
        except ValueError:
            pass
        else:
            raise AssertionError(value)


def test_source_validation_accepts_supported_link_sources():
    accepted = (
        "https://youtu.be/abc",
        "https://drive.google.com/file/d/example/view?usp=sharing",
        "https://www.dropbox.com/s/example/video.mp4?dl=0",
    )
    assert [validate_source_url(value) for value in accepted] == list(accepted)
    for value in ("http://drive.google.com/file/d/x", "https://example.com/video.mp4", "file:///etc/passwd"):
        try:
            validate_source_url(value)
        except ValueError:
            pass
        else:
            raise AssertionError(value)


def test_candidate_selection_is_non_overlapping_and_complete():
    captions = []
    for index in range(90):
        ending = "?" if index % 13 == 0 else "."
        text = f"Este é um ponto importante com resultado número {index}{ending}"
        captions.append(Caption(index * 2.0, index * 2.0 + 1.8, text))
    selected = select_candidates(captions, count=3, minimum=25, maximum=45)
    assert len(selected) == 3
    assert all(25 <= item.end - item.start <= 45 for item in selected)
    assert all(a.end <= b.start or b.end <= a.start for a, b in zip(selected, selected[1:]))


def test_timeline_selection_avoids_transcription_and_distributes_clips():
    selected = select_timeline_candidates(10_800, count=3, minimum=60, maximum=180)
    assert [(item.start, item.end) for item in selected] == [
        (2640.0, 2760.0), (5340.0, 5460.0), (8040.0, 8160.0),
    ]
    assert all(item.text == "" for item in selected)
    assert all(item.quality["selection_method"] == "timeline_without_transcription" for item in selected)


def test_interface_offers_three_minute_clips():
    template = Path(__file__).parents[1] / "farmcortes" / "templates" / "index.html"
    body = template.read_text(encoding="utf-8")
    assert '<option value="60">1 min</option>' in body
    assert '<option value="90">1 min 30 s</option>' in body
    assert '<option value="120">2 min</option>' in body
    assert '<option value="150">2 min 30 s</option>' in body
    assert '<option value="180">3 min</option>' in body
    assert '<option value="45" selected>45 s</option>' in body
    assert '<option value="65" selected>65 s</option>' in body


def test_long_subtitle_is_paginated(tmp_path):
    source = tmp_path / "long.srt"
    source.write_text("1\n00:00:00,000 --> 00:00:10,000\nUma legenda muito longa precisa aparecer em blocos curtos para não cobrir toda a imagem.\n", encoding="utf-8")
    captions = parse_srt(source)
    assert len(captions) == 4
    assert all(len(item.text.split()) <= 4 for item in captions)
    assert captions[0].start == 0
    assert captions[-1].end == 10


def test_caption_glossary_repairs_known_names_without_touching_other_words():
    assert apply_caption_glossary("Janone falou com o Dileira") == "Janones falou com o Dilera"


def test_best_output_and_youtube_metadata():
    outputs = [
        {"file": "corte-01.mp4", "title": "Primeiro", "duration": 40, "score": 5.2},
        {"file": "corte-02.mp4", "title": "Melhor gancho", "duration": 35, "score": 7.8},
    ]
    selected = best_output(outputs)
    assert selected["file"] == "corte-02.mp4"
    metadata = metadata_for({"title": "Entrevista autorizada"}, selected)
    assert metadata["title"] == "Melhor gancho"
    assert "Entrevista autorizada" in metadata["description"]
    assert metadata["made_for_kids"] is False
    assert "Inscreva-se no canal" in metadata["description"]
    assert "comentários" in metadata["description"]


def test_youtube_title_avoids_loose_transcript_fragment():
    output = {
        "title": "E aí?",
        "title_options": ["E aí?", "A verdade mudou completamente a conversa"],
        "excerpt": "E aí? A verdade mudou completamente a conversa.",
    }
    assert optimized_title({"editorial_theme": "humor"}, output) == "A verdade mudou completamente a conversa"


def test_weak_title_receives_editorial_context():
    assert optimized_title(
        {"source_author": "Murilo Couto"}, {"title": "Com certeza"},
    ) == "O momento mais inesperado — Murilo Couto"


def test_scheduled_metadata_preserves_specific_title_and_repairs_weak_one():
    current = {"title": "E aí? #Shorts", "description": "antiga", "categoryId": "24"}
    desired, retitled = desired_scheduled_metadata(
        current,
        {"title": "Show", "source_author": "Murilo Couto"},
        {"title": "E aí?", "excerpt": "E aí?"},
    )
    assert retitled is True
    assert desired["title"] == "O momento mais inesperado — Murilo Couto #Shorts"
    assert "Inscreva-se no canal" in desired["description"]


def test_youtube_settings_from_data_directory(tmp_path):
    (tmp_path / "youtube-settings.json").write_text(
        '{"enabled": true, "privacy": "public", "schedule_times": ["12:00", "18:00"], "longform_time": "20:00", "timezone": "America/Sao_Paulo"}',
        encoding="utf-8",
    )
    config = settings(tmp_path)
    assert config["enabled"] is True
    assert config["privacy"] == "public"
    assert config["schedule_times"] == ("12:00", "18:00")
    assert config["longform_time"] == "20:00"
    assert config["timezone"] == "America/Sao_Paulo"


def test_youtube_schedule_ranks_best_first_and_uses_future_local_slots():
    from datetime import datetime, timezone

    outputs = [
        {"file": "corte-01.mp4", "score": 5.2, "duration": 40},
        {"file": "corte-02.mp4", "score": 7.8, "duration": 35},
        {"file": "corte-03.mp4", "score": 6.1, "duration": 50},
    ]
    assert [item["file"] for item in ranked_outputs(outputs)] == ["corte-02.mp4", "corte-03.mp4", "corte-01.mp4"]
    slots = schedule_slots(
        3, ("12:00", "18:00"), "America/Sao_Paulo",
        now=datetime(2026, 11, 7, 14, 0, tzinfo=timezone.utc),
        reserved={"2026-11-07T21:00:00Z"},
    )
    assert [slot.astimezone(timezone.utc).isoformat() for slot in slots] == [
        "2026-11-07T15:00:00+00:00", "2026-11-08T15:00:00+00:00", "2026-11-08T21:00:00+00:00",
    ]
    spaced = schedule_slots(
        3, ("12:00", "18:00", "21:00"), "America/Sao_Paulo",
        now=datetime(2026, 11, 7, 4, 0, tzinfo=timezone.utc),
        excluded_local_dates={"2026-11-07"}, one_per_day=True,
    )
    assert [slot.date().isoformat() for slot in spaced] == ["2026-11-08", "2026-11-09", "2026-11-10"]


def test_burned_caption_ass_uses_top_safe_area(tmp_path):
    target = tmp_path / "captions.ass"
    write_ass([Caption(1, 8, "Texto curto")], target)
    body = target.read_text(encoding="utf-8")
    assert "PlayResX: 1080" in body
    assert "PlayResY: 1920" in body
    assert "Alignment, MarginL, MarginR, MarginV" in body
    assert ",8,80,80,190,1" in body
    assert "INSCREVA-SE NO CANAL" in body
    assert "Style: Subscribe" in body


def test_long_clip_repeats_subscribe_cta_near_the_end(tmp_path):
    target = tmp_path / "captions.ass"
    write_ass([Caption(0, 40, "Texto curto")], target, limit=40)
    body = target.read_text(encoding="utf-8")
    assert body.count(",Subscribe,") == 2
    assert "NÃO PERCA O PRÓXIMO CORTE" in body


def test_original_story_script_captions_and_attribution():
    story = Story(
        year=1892,
        event="Foi inaugurado o Viaduto do Chá, em São Paulo.",
        title="Viaduto do Chá",
        extract="Foi o primeiro viaduto da cidade. A estrutura original era metálica. A versão atual é de concreto.",
        article_url="https://pt.wikipedia.org/wiki/Viaduto_do_Chá",
        image_url="https://upload.wikimedia.org/example.jpg",
        image_page_url="https://commons.wikimedia.org/wiki/File:example.jpg",
    )
    from datetime import date
    day = date(2026, 11, 6)
    script = build_script(story, day)
    assert "6 de novembro de 1892" in script
    captions = timed_captions(script, 45)
    assert captions[0].start == 0
    assert captions[-1].end == 45
    assert all(len(item.text.split()) <= 4 for item in captions)
    description = original_metadata(story, day)["description"]
    assert story.article_url in description
    assert story.image_page_url in description


def test_youtube_bot_check_is_classified_without_exposing_raw_error():
    raw = "ERROR: Sign in to confirm you’re not a bot. Use --cookies-from-browser"
    assert classify_import_error(raw) == "youtube_bot_check"
    message = humanize_error(raw)
    assert "bloqueou" in message
    assert "Google Drive" in message
    assert "cookies" not in message


def test_smart_reframe_falls_back_when_source_is_invalid(tmp_path):
    missing = tmp_path / "missing.mp4"
    candidate = Candidate(0, 30, 1, "Trecho", "Título")
    assert _smart_reframe_filter(missing, candidate, tmp_path / "out.mp4", "captions.ass") is None


def test_viral_score_rewards_view_velocity_and_engagement():
    from datetime import datetime, timezone

    now = datetime(2026, 11, 7, tzinfo=timezone.utc)
    viral = {"published_at": "2026-11-05T00:00:00Z", "views": 100_000, "likes": 8_000, "comments": 900}
    old = {"published_at": "2026-10-01T00:00:00Z", "views": 100_000, "likes": 1_000, "comments": 50}
    assert viral_score(viral, now=now) > viral_score(old, now=now)


def test_discovery_requires_portuguese_audio_or_text():
    assert is_portuguese({"defaultAudioLanguage": "pt-BR", "title": "Qualquer título"})
    assert is_portuguese({"title": "A história da tecnologia e o que você precisa saber"})
    assert not is_portuguese({"defaultAudioLanguage": "en", "title": "How cruise ships make money"})


def test_discovery_run_is_locked_and_records_status(tmp_path):
    first = begin_discovery_run(tmp_path, trigger="manual")
    assert first is not None
    assert begin_discovery_run(tmp_path, trigger="scheduled") is None
    lock, run = first
    assert discovery_status(tmp_path)["state"] == "running"

    selected = complete_discovery_run(
        tmp_path, lock, run,
        discover_fn=lambda *args, **kwargs: [{"video_id": "eligible"}],
    )

    assert selected == [{"video_id": "eligible"}]
    status = discovery_status(tmp_path)
    assert status["state"] == "completed"
    assert status["trigger"] == "manual"
    assert status["queued_count"] == 1
    second = begin_discovery_run(tmp_path, trigger="scheduled")
    assert second is not None
    second[0].close()


def test_interface_offers_manual_discovery_control():
    root = Path(__file__).parents[1] / "farmcortes"
    template = (root / "templates" / "index.html").read_text(encoding="utf-8")
    script = (root / "static" / "app.js").read_text(encoding="utf-8")
    assert 'id="discovery-run"' in template
    assert "Rodar varredura agora" in template
    assert "fetch('/api/discovery/run', { method: 'POST' })" in script


def test_editorial_campaign_switches_to_gta_only_during_launch_week():
    from datetime import datetime, timezone

    policy = {
        "timezone": "America/Sao_Paulo",
        "default_topics": [{"theme": "humor", "query": "comédia"}, {"theme": "ciencia", "query": "ciência"}],
        "campaigns": [{
            "id": "gta", "theme": "gta-vi", "start": "2026-11-19", "end": "2026-11-25",
            "share": 0.67, "queries": ["GTA A", "GTA B", "GTA C"],
        }],
    }
    before = datetime(2026, 11, 18, 12, tzinfo=timezone.utc)
    launch = datetime(2026, 11, 20, 12, tzinfo=timezone.utc)
    assert active_campaign(policy, before) is None
    assert [item["theme"] for item in discovery_topics(policy, count=3, now=before)] == ["humor", "ciencia"]
    assert [item["theme"] for item in discovery_topics(policy, count=3, now=launch)] == ["gta-vi", "gta-vi", "humor"]


def test_narrative_strategy_rewards_complete_hook_and_flags_long_clip():
    strong = score_narrative(
        "Você não acredita no que aconteceu, mas no fim descobrimos a verdade.", 72, 45, 90,
    )
    weak = score_narrative("Tipo, ele falou isso e", 52, 45, 65)
    assert strong["total"] > weak["total"]
    assert strong["editorial_ready"] is True
    assert "verificar_content_id" in strong["flags"]
    assert weak["editorial_ready"] is False


def test_title_options_are_complete_unique_and_short():
    options = title_options(
        "Você não acredita no que aconteceu. Mas a explicação mudou completamente o resultado.",
    )
    assert 1 <= len(options) <= 3
    assert len({item.casefold() for item in options}) == len(options)
    assert all(len(item) <= 50 for item in options)
    assert all("#Shorts" not in item for item in options)


def test_title_options_prefer_a_clear_hook_over_filler():
    options = title_options(
        "E aí? Ninguém esperava o que aconteceu depois. A verdade mudou completamente a conversa.",
    )
    assert options[0] != "E aí"
    assert "aconteceu" in options[0].casefold() or "verdade" in options[0].casefold()


def test_performance_report_detects_scope_mismatch():
    report = performance_report({
        "period_days": 28, "views": 4833, "watch_hours": 28.1,
        "subscribers_gained": 9, "avg_view_duration_seconds": 39,
    })
    assert report["computed_avg_view_duration_seconds"] == 20.9
    assert report["subscriber_conversion_per_1000"] == 1.86
    assert report["warnings"]
    assert "CTA visual curto" in report["recommendations"][0]


def test_longform_plan_reports_missing_material():
    plan = build_longform_plan(
        {"title": "Entrevista sobre tecnologia"},
        [{"file": "corte-01.mp4", "title": "Um ponto", "duration": 58, "score": 7.1}],
    )
    assert plan["status"] == "needs_more_material"
    assert plan["target_seconds"] == 480
    assert plan["missing_seconds"] == 422


def test_longform_project_specs_total_twenty_minutes():
    projects = json.loads((Path(__file__).parent.parent / "longform-projects.json").read_text(encoding="utf-8"))
    assert projects["target_seconds"] == 1200
    for project in projects["projects"]:
        assert sum(item["seconds"] for item in project["structure"]) == 1200


def test_database_serializes_numpy_quality_flags(tmp_path):
    database = Database(tmp_path / "farmcortes.sqlite3")
    database.create("job-1", "upload:test.mp4", 1, 45, 65)
    database.update(
        "job-1",
        outputs=[{"file": "corte-01.mp4", "quality": {"editorial_ready": np.bool_(True)}}],
    )
    assert database.get("job-1")["outputs"][0]["quality"]["editorial_ready"] is True


def test_database_persists_caption_mode(tmp_path):
    database = Database(tmp_path / "farmcortes.sqlite3")
    database.create("job-1", "upload:test.mp4", 1, 45, 65, caption_mode="none")
    assert database.get("job-1")["caption_mode"] == "none"


def test_database_persists_editorial_theme_and_discovery_signal(tmp_path):
    database = Database(tmp_path / "farmcortes.sqlite3")
    database.create("job-1", "https://youtu.be/example", 8, 45, 75, editorial_theme="humor")
    assert database.get("job-1")["editorial_theme"] == "humor"
    database.record_discovery_candidate({
        "video_id": "example", "url": "https://youtu.be/example", "title": "Teste", "author": "Canal",
        "license": "youtube", "theme": "humor", "viral_score": 8.2,
    }, "signal")
    with database.connect() as conn:
        row = conn.execute("SELECT disposition,editorial_theme FROM discovery_candidates").fetchone()
    assert dict(row) == {"disposition": "signal", "editorial_theme": "humor"}


def test_source_compilation_requires_three_cc_long_sources_of_same_theme(tmp_path):
    jobs = []
    for index in range(3):
        source = tmp_path / f"source-{index}.mp4"
        source.write_bytes(b"video")
        jobs.append({
            "id": f"job-{index}", "status": "done", "editorial_theme": "pegadinhas",
            "source_license": "Creative Commons Attribution (CC BY)", "source_path": str(source),
            "source_duration": 1200, "updated_at": f"2026-11-{index + 1:02d}", "outputs": [],
        })
    policy = {"source_compilations": {"min_sources": 3, "min_total_seconds": 1800, "target_seconds": 3600, "max_source_seconds": 1200}}
    assert list(select_source_groups(jobs, policy)) == ["pegadinhas"]
    jobs[0]["source_license"] = "Standard YouTube License"
    assert select_source_groups(jobs, policy) == {}


def test_source_compilation_centers_chapter_near_best_original_moment():
    start, duration = _chapter_window({
        "source_duration": 3600,
        "outputs": [{"score": 9.5, "source_start": 1800}, {"score": 7, "source_start": 300}],
    }, 1200)
    assert duration == 1200
    assert start == 1400


def test_unpublished_source_compilation_remains_retryable(tmp_path):
    folder = tmp_path / "source-compilations" / "humor" / "abc"
    folder.mkdir(parents=True)
    manifest = {"sources": [{"job_id": "job-1"}], "publication": {}}
    (folder / "manifest.json").write_text(__import__("json").dumps(manifest), encoding="utf-8")
    assert used_source_ids(tmp_path) == set()
    manifest["publication"] = {"video_id": "youtube-1"}
    (folder / "manifest.json").write_text(__import__("json").dumps(manifest), encoding="utf-8")
    assert used_source_ids(tmp_path) == {"job-1"}


def test_compilation_groups_by_author_and_selects_unique_high_score_clips():
    job = {
        "id": "job-1", "source_author": "Canal Ciência", "title": "Uma conversa",
        "outputs": [
            {"file": "1.mp4", "score": 8, "duration": 60, "excerpt": "A descoberta mudou tudo."},
            {"file": "2.mp4", "score": 7, "duration": 60, "excerpt": "A descoberta mudou tudo."},
            {"file": "3.mp4", "score": 6, "duration": 60, "excerpt": "Outro ponto importante."},
        ],
    }
    assert compilation_key(job) == "Canal Ciência"
    selected = select_clips([job])
    assert [item["file"] for item in selected] == ["1.mp4", "3.mp4"]


def test_compilation_outline_parser_and_metadata_include_editorial_context():
    outline = _extract_json('texto {"title":"Ciência sem pressa","intro":"Abertura","transitions":["Contexto"],"outro":"Inscreva-se no canal"} fim')
    assert outline["title"] == "Ciência sem pressa"
    metadata = metadata_for_compilation({
        "title": outline["title"],
        "sources": [{"author": "Canal Ciência", "url": "https://youtu.be/example"}],
    })
    assert "narração e contexto editorial originais" in metadata["description"]
    assert "Inscreva-se no canal" in metadata["description"]


def test_compilation_fills_only_missing_transitions_from_clip_context():
    clips = [
        {"title": "Primeiro ponto", "excerpt": "A primeira explicação termina aqui."},
        {"title": "Segundo ponto", "excerpt": "A segunda explicação traz outro contexto. Continuação."},
    ]
    outline = complete_transitions({"transitions": ["Transição autoral existente"]}, clips)
    assert outline["transitions"][0] == "Transição autoral existente"
    assert len(outline["transitions"]) == 2
    assert "Segundo ponto" in outline["transitions"][1]
    assert "A segunda explicação" in outline["transitions"][1]


def test_compilation_fallback_outline_uses_only_supplied_clip_context():
    clips = [{
        "title": "Primeiro ponto", "excerpt": "A fala começa por esta ideia. Depois continua.",
        "job_title": "UMA CONVERSA IMPORTANTE",
    }]
    outline = fallback_outline("Canal Exemplo", clips)
    assert outline["title"] == "Uma Conversa Importante"
    assert outline["transitions"] == [
        "Agora, o foco é Primeiro ponto. O trecho desenvolve esta ideia: A fala começa por esta ideia."
    ]
    assert "inscreva-se no canal" in outline["outro"].casefold()


def test_youtube_upload_resumes_after_transient_transport_failure(monkeypatch):
    class Request:
        calls = 0

        def next_chunk(self, *, num_retries):
            self.calls += 1
            if self.calls == 1:
                raise OSError("temporary TLS disconnect")
            if self.calls == 2:
                return object(), None
            return object(), {"id": "video-1"}

    monkeypatch.setattr("farmcortes.youtube.time_module.sleep", lambda _: None)
    request = Request()
    assert _finish_resumable_upload(request) == {"id": "video-1"}
    assert request.calls == 3
