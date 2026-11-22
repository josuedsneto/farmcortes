import pytest

from farmcortes import pipeline, youtube
from farmcortes.db import Database
from farmcortes.pipeline import Candidate, Processor

FINAL = {"status": "done", "progress": 100, "stage": "Cortes prontos para revisão", "publication": {}}


@pytest.fixture
def youtube_enabled(monkeypatch):
    monkeypatch.setenv("FARMCORTES_YOUTUBE_ENABLED", "true")
    monkeypatch.setattr(youtube, "publish_file", lambda *a, **k: pytest.fail("publish_file must not be called"))


def _final_state(job):
    return {key: job[key] for key in FINAL}


def test_finished_job_is_ready_for_review_without_publishing(tmp_path, monkeypatch, youtube_enabled):  # PUB-01, PUB-02
    database = Database(tmp_path / "farmcortes.sqlite3")
    folder = tmp_path / "jobs" / "job-1"
    folder.mkdir(parents=True)
    source = folder / "source-upload.mp4"
    source.write_bytes(b"0" * 2048)
    database.create("job-1", "upload:video.mp4", 1, 30, 60, str(source), caption_mode="none")
    monkeypatch.setattr(pipeline, "probe", lambda path: {"format": {"duration": "300"}})
    monkeypatch.setattr(pipeline, "render", lambda *args: None)

    Processor(database, tmp_path).process(database.next_queued())

    assert _final_state(database.get("job-1")) == FINAL
    assert len(database.get("job-1")["outputs"]) == 1


def test_recovered_job_gets_the_same_final_state(tmp_path, monkeypatch, youtube_enabled):  # PUB-03
    database = Database(tmp_path / "farmcortes.sqlite3")
    folder = tmp_path / "jobs" / "job-2"
    folder.mkdir(parents=True)
    (folder / "transcript.srt").write_text("1\n00:00:00,000 --> 00:00:40,000\nTexto completo.\n", encoding="utf-8")
    (folder / "corte-01.mp4").write_bytes(b"0")
    (folder / "corte-01.srt").write_text("", encoding="utf-8")
    database.create("job-2", "https://drive.google.com/file/d/x", 1, 30, 60)
    candidate = Candidate(0.0, 40.0, 5.0, "Texto completo.", "Título")
    monkeypatch.setattr(pipeline, "select_candidates", lambda *args: [candidate])
    monkeypatch.setattr(pipeline, "ensure_subscribe_cta", lambda path: None)

    Processor(database, tmp_path).process(database.next_queued())

    assert _final_state(database.get("job-2")) == FINAL
    assert [item["file"] for item in database.get("job-2")["outputs"]] == ["corte-01.mp4"]


def test_job_level_publish_function_is_gone():  # PUB-05 (youtube.publish)
    assert not hasattr(youtube, "publish")


ROOT = __import__("pathlib").Path(__file__).resolve().parent.parent


def test_review_queue_is_the_only_publish_file_caller():  # PUB-04
    import re
    calls = [
        (path.relative_to(ROOT).as_posix(), line)
        for path in (ROOT / "farmcortes").rglob("*.py")
        for line in path.read_text(encoding="utf-8").splitlines()
        if re.search(r"\bpublish_file\(", line) and not line.lstrip().startswith("def ")
    ]
    assert len(calls) == 1
    assert calls[0][0] == "farmcortes/review.py"


def test_backfill_module_is_gone():  # PUB-05 (backfill)
    assert not (ROOT / "farmcortes" / "backfill.py").exists()


@pytest.mark.parametrize("module", ["original", "compilations", "source_compilations"])
def test_publish_flag_is_rejected(module, tmp_path):  # PUB-06
    import subprocess
    import sys
    result = subprocess.run(
        [sys.executable, "-m", f"farmcortes.{module}", "--data-dir", str(tmp_path), "--publish"],
        cwd=ROOT, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 2
    assert "--publish" in result.stderr


def test_original_short_manifest_is_rendered_only(tmp_path, monkeypatch, youtube_enabled):  # PUB-07
    import json
    from datetime import date
    from farmcortes import original
    story = original.Story(1900, "Evento", "Título", "Resumo.", "https://a", "https://i", "https://p")
    monkeypatch.setattr(original, "research_on_this_day", lambda day: story)
    monkeypatch.setattr(original, "_download", lambda url, destination: None)
    monkeypatch.setattr(original, "synthesize", lambda script, output, model: None)
    monkeypatch.setattr(original, "render_original", lambda *args: None)
    (tmp_path / "models").mkdir()
    (tmp_path / "models" / "pt_BR-edresson-low.onnx").write_bytes(b"0")

    result = original.generate(tmp_path, date(2026, 11, 22))

    assert result["status"] == "rendered"
    assert result["publication"] == {}
    manifest = json.loads(next((tmp_path / "originals").glob("*/manifest.json")).read_text(encoding="utf-8"))
    assert manifest["status"] == "rendered"
    assert manifest["publication"] == {}


def test_deploy_units_do_not_pass_publish():  # PUB-08
    units = list((ROOT / "deploy").rglob("*.service"))
    assert units
    assert [unit.name for unit in units if "--publish" in unit.read_text(encoding="utf-8")] == []


def test_old_compilation_manifest_is_left_untouched(tmp_path, monkeypatch, youtube_enabled):  # PUB-10
    import json
    from farmcortes import compilations
    database = Database(tmp_path / "farmcortes.sqlite3")
    database.create("job-1", "https://drive.google.com/file/d/x", 1, 30, 60, status="done")
    database.update("job-1", outputs=[{"file": "corte-01.mp4", "duration": 60}], source_author="Canal")
    folder = tmp_path / "compilations" / "canal" / "abc"
    folder.mkdir(parents=True)
    old = {"subject": "Canal", "publication": {"video_id": "old-1", "publish_at": "2026-11-01T23:00:00Z"}}
    manifest_path = folder / "manifest.json"
    manifest_path.write_text(json.dumps(old), encoding="utf-8")
    monkeypatch.setattr(compilations, "select_clips", lambda jobs: [{"duration": compilations.MIN_SECONDS}])
    monkeypatch.setattr(
        compilations, "render_compilation",
        lambda *args: (folder / "compilacao.mp4", json.loads(manifest_path.read_text(encoding="utf-8"))),
    )

    results = compilations.run_once(tmp_path)

    assert results == [old]
    assert json.loads(manifest_path.read_text(encoding="utf-8")) == old
