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


# worker-binary-pinning: static checks; the VPS has no PowerShell to run the script.
import re
from pathlib import Path

WORKER = (Path(__file__).resolve().parent.parent / "farmcortes" / "worker.ps1").read_text(encoding="utf-8")
YTDLP_SHA = "66674953fe251b89f4d08c5f0e35e0728679bd67ab3d7d05c0562af101dd3e7a"
DENO_SHA = "cba84b26e0df5ae7f74fc66b86a95b866872a86292ebd84fa1e959927edd93bf"


def _value(name):
    return re.search(rf'^\${name} = "([^"]*)"', WORKER, re.M).group(1)


def test_worker_downloads_fixed_releases():  # BIN-01
    assert "latest" not in WORKER
    requirements = (Path(__file__).resolve().parent.parent / "requirements.txt").read_text(encoding="utf-8")
    pinned = re.search(r"^yt-dlp==(\S+)", requirements, re.M).group(1)
    assert [int(part) for part in _value("YtDlpVersion").split(".")] == [int(part) for part in pinned.split(".")]
    assert _value("YtDlpUrl") == "https://github.com/yt-dlp/yt-dlp/releases/download/$YtDlpVersion/yt-dlp.exe"
    assert _value("DenoUrl") == "https://github.com/denoland/deno/releases/download/v$DenoVersion/deno-x86_64-pc-windows-msvc.zip"
    assert "-o $temporary $YtDlpUrl" in WORKER and "-o $archive $DenoUrl" in WORKER


def test_worker_pins_official_sha256():  # BIN-02
    assert _value("YtDlpSha256") == YTDLP_SHA
    assert _value("DenoSha256") == DENO_SHA
    assert ASSERT_SHA256 in WORKER.replace("\r\n", "\n")


# The whole guard: mismatch (-ne) deletes the file and aborts with the given message.
ASSERT_SHA256 = """function Assert-Sha256($Path, $Expected, $Message) {
    if ((Get-FileHash -Algorithm SHA256 -Path $Path).Hash -ne $Expected) {
        Remove-Item -Force $Path
        throw $Message
    }
}"""


def test_ytdlp_hash_is_checked_before_install():  # BIN-03
    check = WORKER.index('Assert-Sha256 $temporary $YtDlpSha256 "Hash do yt-dlp nao confere"')
    assert check < WORKER.index("Move-Item -Force $temporary $YtDlp")
    assert WORKER.replace("\r\n", "\n").index(ASSERT_SHA256) < WORKER.replace("\r\n", "\n").index("if (-not (Test-YtDlp))")


def test_deno_hash_is_checked_before_extraction():  # BIN-04
    check = WORKER.index('Assert-Sha256 $archive $DenoSha256 "Hash do Deno nao confere"')
    assert check < WORKER.index("Expand-Archive -Path $archive")


def test_installed_binaries_must_report_pinned_version():  # BIN-05, BIN-06, BIN-07
    lines = [line.strip() for line in WORKER.splitlines()]
    assert 'return $LASTEXITCODE -eq 0 -and "$version".Trim() -eq $YtDlpVersion' in lines
    assert 'return $LASTEXITCODE -eq 0 -and "$version" -match "^deno $([regex]::Escape($DenoVersion)) "' in lines
    assert "if (-not (Test-YtDlp)) {\n    Remove-Item -Force -ErrorAction SilentlyContinue $YtDlp" in WORKER.replace("\r\n", "\n")
    assert "if (-not (Test-Deno)) {\n    Remove-Item -Force -ErrorAction SilentlyContinue $Deno" in WORKER.replace("\r\n", "\n")


def test_failed_download_removes_partial_file():  # BIN-08
    text = WORKER.replace("\r\n", "\n")
    assert 'Remove-Item -Force -ErrorAction SilentlyContinue $temporary\n        throw "Falha ao baixar o yt-dlp oficial"' in text
    assert 'Remove-Item -Force -ErrorAction SilentlyContinue $archive\n        throw "Falha ao baixar o Deno oficial"' in text
