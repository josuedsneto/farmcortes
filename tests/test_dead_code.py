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
