from farmcortes.db import Database
from farmcortes.pipeline import is_youtube_url


def test_youtube_jobs_can_be_claimed_by_local_worker(tmp_path):
    database = Database(tmp_path / "farmcortes.sqlite3")
    created = database.create(
        "job-1",
        "https://youtu.be/example",
        3,
        30,
        75,
        status="awaiting_download",
        stage="Aguardando o PC local",
    )

    assert created["status"] == "awaiting_download"
    claimed = database.claim_download("LUCAS")
    assert claimed is not None
    assert claimed["id"] == "job-1"
    assert claimed["status"] == "downloading"
    assert database.worker_status()["online"] is True
    assert database.claim_download("LUCAS") is None


def test_youtube_host_detection():
    assert is_youtube_url("https://www.youtube.com/watch?v=abc")
    assert is_youtube_url("https://youtu.be/abc")
    assert not is_youtube_url("https://drive.google.com/file/d/abc")


def test_strategy_snapshots_are_persisted(tmp_path):
    database = Database(tmp_path / "farmcortes.sqlite3")
    stored = database.record_strategy_snapshot({"period_days": 28, "views": 4833})
    latest = database.latest_strategy_snapshot()
    assert latest is not None
    assert latest["id"] == stored["id"]
    assert latest["views"] == 4833
