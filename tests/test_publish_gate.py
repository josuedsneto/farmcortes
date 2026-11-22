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
