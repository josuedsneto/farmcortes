import json
import sqlite3

import pytest

from farmcortes import ai_client, biblia, biblia_ai, review, youtube
from farmcortes.db import Database

LONG = " ".join(f"palavra{i}," if i % 20 == 19 else f"palavra{i}" for i in range(300)) + "."


@pytest.fixture(autouse=True)
def no_ai_keys(monkeypatch):
    for name in ("FARMCORTES_GROQ_API_KEY", "FARMCORTES_GEMINI_API_KEY", "FARMCORTES_BIBLIA_AUTO_APPROVE",
                 "FARMCORTES_META_ACCESS_TOKEN", "FARMCORTES_FACEBOOK_PAGE_ID", "FARMCORTES_INSTAGRAM_USER_ID"):
        monkeypatch.delenv(name, raising=False)


def write_csv(path, rows):
    lines = ["ordem,livro,capitulo,verso,publicado_youtube,publicado_tiktok,publicado_facebook,publicado_instagram"]
    lines += [",".join(map(str, row)) for row in rows]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def fake_fetch(texts):
    calls = []

    def fetch(livro, capitulo):
        calls.append((livro, capitulo))
        return "Gênesis", texts[capitulo]
    fetch.calls = calls
    return fetch


# ── verse queue and split ────────────────────────────────────────────────────

def test_split_keeps_every_word_and_each_part_fits_60_seconds():
    parts = biblia.split_parts(LONG)
    assert len(parts) == 3
    assert " ".join(parts).split() == LONG.split()
    assert all(biblia.estimate_seconds(part) <= 60 for part in parts)
    assert parts[0].endswith(",")  # cut at punctuation, not mid-phrase
    assert biblia.split_parts("No princípio criou Deus os céus e a terra.") == ["No princípio criou Deus os céus e a terra."]


def test_import_runs_once_and_keeps_published_flags(tmp_path):
    db = Database(tmp_path / "db.sqlite3")
    csv = write_csv(tmp_path / "v.csv", [(1, "gn", 1, 1, "true", "false", "true", "false"), (2, "GN", 1, 2, "false", "false", "false", "false")])
    assert biblia.import_csv(db, csv) == 2
    assert biblia.import_csv(db, csv) == 0
    with db.connect() as conn:
        rows = [dict(r) for r in conn.execute("SELECT * FROM biblia_verses ORDER BY ordem")]
    assert rows[0]["published_youtube"] == 1 and rows[0]["published_facebook"] == 1 and rows[0]["published_tiktok"] == 0
    assert rows[1]["livro"] == "gn" and rows[1]["published_youtube"] == 0


def test_next_video_batches_consecutive_pending_verses_and_advances(tmp_path):
    db = Database(tmp_path / "db.sqlite3")
    biblia.import_csv(db, write_csv(tmp_path / "v.csv", [
        (1, "gn", 1, 1, "true", "false", "false", "false"),
        (2, "gn", 1, 2, "false", "false", "false", "false"),
        (3, "gn", 1, 3, "false", "false", "false", "false"),
        (4, "gn", 1, 4, "false", "false", "false", "false"),
    ]))
    fetch = fake_fetch({1: {1: "Um.", 2: "palavra " * 60, 3: "palavra " * 40, 4: "palavra " * 40}})
    plan = biblia.next_video(db, fetch=fetch)
    assert [v["ordem"] for v in plan["verses"]] == [2, 3]  # 4 would pass 57 s
    assert plan["verses"][0]["referencia"] == "Gênesis 1:2"
    assert fetch.calls == [("gn", 1)]  # one API call fills the whole chapter
    biblia.record_video(db, plan, review_id=7)
    plan = biblia.next_video(db, fetch=fetch)
    assert [v["ordem"] for v in plan["verses"]] == [4]
    assert fetch.calls == [("gn", 1)]
    with db.connect() as conn:
        assert [r[0] for r in conn.execute("SELECT ordem FROM biblia_videos WHERE review_id=7")] == [2, 3]


def test_long_verse_becomes_one_part_per_run(tmp_path):
    db = Database(tmp_path / "db.sqlite3")
    biblia.import_csv(db, write_csv(tmp_path / "v.csv", [(1, "et", 8, 9, "false", "false", "false", "false"), (2, "et", 8, 10, "false", "false", "false", "false")]))
    fetch = fake_fetch({8: {9: LONG, 10: "Curto."}})
    seen = []
    for review_id in (1, 2, 3):
        plan = biblia.next_video(db, fetch=fetch)
        seen.append((plan["part"], plan["parts"], plan["complete"]))
        biblia.record_video(db, plan, review_id)
        meta = biblia_ai.verse_metadata(plan["verses"], db, plan["part"], plan["parts"])
        assert all(t.endswith(f"(Parte {plan['part']}/3)") and len(t) <= 100 for t in meta["titulos"])
    assert seen == [(1, 3, []), (2, 3, []), (3, 3, [1])]
    assert [v["ordem"] for v in biblia.next_video(db, fetch=fetch)["verses"]] == [2]


# ── metadata fallback ────────────────────────────────────────────────────────

VERSES = [{"ordem": 1, "livro": "gn", "capitulo": 1, "verso": 1, "texto": "No princípio criou Deus os céus e a terra.", "referencia": "Gênesis 1:1"},
          {"ordem": 2, "livro": "gn", "capitulo": 1, "verso": 2, "texto": "E a terra era sem forma e vazia.", "referencia": "Gênesis 1:2"}]


def test_metadata_without_keys_uses_template(tmp_path, monkeypatch):
    monkeypatch.setattr(ai_client, "call_ai_json", lambda prompt: pytest.fail("no key, no call"))
    meta = biblia_ai.verse_metadata(VERSES, Database(tmp_path / "db"))
    assert meta["source"] == "template"
    assert len(set(meta["titulos"])) == 3
    assert meta["titulos"][0] == "Gênesis 1:1-2 | Leia a Bíblia Toda em Um Ano"
    assert "No princípio" in meta["descricao"]


def test_metadata_falls_back_when_ai_fails_and_caches_success(tmp_path, monkeypatch):
    db = Database(tmp_path / "db")
    monkeypatch.setenv("FARMCORTES_GROQ_API_KEY", "x")

    def broken(url, **kwargs):
        raise OSError("rede caiu")
    monkeypatch.setattr(ai_client.requests, "post", broken)
    assert biblia_ai.verse_metadata(VERSES, db)["source"] == "template"

    calls = []
    monkeypatch.setattr(ai_client, "call_ai_json", lambda prompt: calls.append(prompt) or {"titulos": ["A CRIAÇÃO: Gênesis 1:1-2"], "descricao": "Descrição"})
    first = biblia_ai.verse_metadata(VERSES, db)
    second = biblia_ai.verse_metadata(VERSES, db)
    assert first == second and len(calls) == 1
    assert first["titulos"][0] == "A CRIAÇÃO: Gênesis 1:1-2" and len(first["titulos"]) == 3


def test_ai_client_uses_gemini_when_groq_fails(monkeypatch):
    monkeypatch.setenv("FARMCORTES_GROQ_API_KEY", "g")
    monkeypatch.setenv("FARMCORTES_GEMINI_API_KEY", "m")
    monkeypatch.setattr(ai_client, "_call_groq", lambda prompt, key: (_ for _ in ()).throw(RuntimeError("429")))
    monkeypatch.setattr(ai_client, "_call_gemini", lambda prompt, key: '```json\n{"titulo": "linha\nquebrada"}\n```')
    assert ai_client.call_ai_json("p") == {"titulo": "linha\nquebrada"}
    monkeypatch.delenv("FARMCORTES_GEMINI_API_KEY")
    with pytest.raises(ai_client.AIUnavailable):
        ai_client.call_ai_json("p")


def test_theological_metadata_falls_back_without_ai(tmp_path):
    meta = biblia_ai.theological_metadata("Salvação", "A Obra Completa de Cristo", [{"nome": "Introdução", "texto": "..."}], Database(tmp_path / "db"))
    assert meta["source"] == "template" and meta["titulo"].startswith("Salvação: A Obra Completa de Cristo")


# ── auto-approve gate and shared publisher ───────────────────────────────────

def add_biblia(db, tmp_path, ordens=(1,)):
    video = tmp_path / "biblia" / "00001-p1" / "short.mp4"
    video.parent.mkdir(parents=True, exist_ok=True)
    video.write_bytes(b"0")
    item = review.enqueue(kind="biblia", streamer="biblia", platform="biblia", source_url="https://pexels.com/p",
                          file_path=video, duration=40, category="Bíblia", titles=["t1", "t2", "t3"],
                          description="d", hashtags="#Bíblia", credit_text="ACF", db=db)
    with db.connect() as conn:
        conn.execute("INSERT OR IGNORE INTO biblia_verses (ordem,livro,capitulo,verso,parts_total,parts_done) VALUES (1,'gn',1,1,1,1)")
        conn.executemany("INSERT INTO biblia_videos (review_id,ordem) VALUES (?,?)", [(item, o) for o in ordens])
    return item


@pytest.fixture
def publisher(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(youtube, "settings", lambda _: {"enabled": True, "token": tmp_path / "token", "privacy": "private",
                                                       "schedule_times": ("12:00",), "timezone": "America/Sao_Paulo"})
    monkeypatch.setattr(youtube, "publish_file", lambda video, metadata, **kw: calls.append((metadata, kw)) or {"video_id": f"yt{len(calls)}"})
    monkeypatch.setenv("FARMCORTES_DAILY_UPLOAD_LIMIT", "5")
    return calls


def test_auto_approve_is_off_by_default_and_nothing_posts(tmp_path, publisher):
    db = Database(tmp_path / "db")
    item = add_biblia(db, tmp_path)
    assert review.auto_approve(item, db) is None
    assert review.publish_approved(db, tmp_path) == []
    assert review.list_items(db)[0]["status"] == "pending" and publisher == []


def test_auto_approve_only_touches_pending_biblia_items(tmp_path, monkeypatch):
    db = Database(tmp_path / "db")
    monkeypatch.setenv("FARMCORTES_BIBLIA_AUTO_APPROVE", "true")
    clip = review.enqueue(kind="short", streamer="s", platform="twitch", source_url="u", file_path="v", duration=1, titles=["a", "b", "c"], db=db)
    assert review.auto_approve(clip, db) is None
    item = add_biblia(db, tmp_path)
    assert review.auto_approve(item, db) == "t1"
    assert review.auto_approve(item, db) is None
    row = review.list_items(db, kind="biblia")[0]
    assert (row["status"], row["chosen_title"], row["approved_by"]) == ("approved", "t1", "auto")
    assert review.list_items(db, kind="short")[0]["status"] == "pending"


def test_approved_biblia_publishes_without_whitelist_and_marks_verses(tmp_path, monkeypatch, publisher):
    db = Database(tmp_path / "db")
    monkeypatch.setenv("FARMCORTES_BIBLIA_AUTO_APPROVE", "1")
    clip = review.enqueue(kind="short", streamer="s", platform="twitch", source_url="u", file_path="v", duration=1, titles=["a", "b", "c"], db=db)
    review.decide(clip, 1, db)
    item = add_biblia(db, tmp_path)
    review.auto_approve(item, db)
    review.publish_approved(db, tmp_path, kinds=("biblia",))
    assert len(publisher) == 1
    metadata, kwargs = publisher[0]
    assert metadata["category_id"] == "22" and "bíblia" in metadata["tags"] and "ACF" in metadata["description"]
    assert kwargs["_approval"] is youtube._REVIEW_AUTHORIZATION and kwargs["publish_at"] is None and kwargs["short_form"]
    statuses = {row["kind"]: row["status"] for row in review.list_items(db)}
    assert statuses == {"biblia": "published", "short": "approved"}  # clip not in kinds and not whitelisted
    with db.connect() as conn:
        assert conn.execute("SELECT published_youtube FROM biblia_verses WHERE ordem=1").fetchone()[0] == 1


def test_biblia_shares_the_daily_limit_and_public_uses_schedule_times(tmp_path, monkeypatch, publisher):
    db = Database(tmp_path / "db")
    monkeypatch.setenv("FARMCORTES_DAILY_UPLOAD_LIMIT", "1")
    monkeypatch.setattr(youtube, "settings", lambda _: {"enabled": True, "token": tmp_path / "t", "privacy": "public",
                                                       "schedule_times": ("12:00", "18:00"), "timezone": "America/Sao_Paulo"})
    first, second = add_biblia(db, tmp_path), add_biblia(db, tmp_path)
    review.decide(first, 1, db)
    review.decide(second, 2, db)
    review.publish_approved(db, tmp_path)
    review.publish_approved(db, tmp_path)
    assert len(publisher) == 1
    assert publisher[0][1]["publish_at"] is not None
    assert [row["status"] for row in review.list_items(db)] == ["approved", "published"]


def test_meta_reels_only_post_approved_biblia_items(tmp_path, monkeypatch):
    from farmcortes.social import meta
    db = Database(tmp_path / "db")
    pending = add_biblia(db, tmp_path)
    approved = add_biblia(db, tmp_path)
    review.decide(approved, 3, db)
    assert review.publish_social(db) == []  # not configured -> off
    monkeypatch.setenv("FARMCORTES_META_ACCESS_TOKEN", "tok")
    monkeypatch.setenv("FARMCORTES_INSTAGRAM_USER_ID", "ig")
    posted = []
    monkeypatch.setattr(meta, "publish", lambda platform, video, title, description: posted.append((platform, title)) or {"platform": platform, "id": "ig1"})
    review.publish_social(db)
    review.publish_social(db)
    assert posted == [("instagram", "t3")]
    rows = {row["id"]: row for row in review.list_items(db)}
    assert json.loads(rows[approved]["social_json"])["instagram"]["id"] == "ig1"
    assert json.loads(rows[pending]["social_json"]) == {}
    with db.connect() as conn:
        assert conn.execute("SELECT published_instagram FROM biblia_verses WHERE ordem=1").fetchone()[0] == 1


def test_run_once_enqueues_for_review_and_cleans_temp_files(tmp_path, monkeypatch):
    db = Database(tmp_path / "farmcortes.sqlite3")
    monkeypatch.setenv("FARMCORTES_BIBLIA_CSV", str(write_csv(tmp_path / "v.csv", [(1, "gn", 1, 1, "false", "false", "false", "false")])))
    monkeypatch.setattr(biblia, "fetch_chapter", lambda livro, cap: ("Gênesis", {1: "No princípio criou Deus os céus e a terra."}))
    monkeypatch.setattr(youtube, "publish_file", lambda *a, **k: pytest.fail("pending item must not publish"))
    monkeypatch.setattr(youtube, "settings", lambda _: {"enabled": True, "token": tmp_path / "t", "privacy": "private", "schedule_times": ("12:00",), "timezone": "America/Sao_Paulo"})
    (tmp_path / "models").mkdir()
    (tmp_path / "models" / "pt_BR-edresson-low.onnx").write_bytes(b"0")
    monkeypatch.setattr(biblia, "synthesize", lambda text, output, model: output.write_bytes(b"wav"))
    def fake_image(dest):
        dest.write_bytes(b"jpg")
        return {"credit": "Foto: Ana no Pexels", "url": "https://pexels.com/p/1"}
    monkeypatch.setattr(biblia, "background_image", fake_image)
    monkeypatch.setattr(biblia, "background_music", lambda data_dir: (None, ""))

    def fake_render(image, narration, music, text, reference, label, output):
        output.with_suffix(".ass").write_text("x")
        output.write_bytes(b"mp4")
        return 12.0
    monkeypatch.setattr(biblia, "render", fake_render)
    monkeypatch.setattr(biblia.telegram, "send_preview", lambda item, data_dir: None)

    result = biblia.run_once(tmp_path, db)

    assert result["status"] == "pending" and result["published"] == []
    item = review.list_items(db)[0]
    assert (item["kind"], item["platform"], item["category"]) == ("biblia", "biblia", "Bíblia")
    assert "Foto: Ana no Pexels" in item["credit_text"] and item["source_url"] == "https://pexels.com/p/1"
    assert sorted(p.name for p in (tmp_path / "biblia").rglob("*") if p.is_file()) == ["short.mp4"]
    assert biblia.run_once(tmp_path, db)["status"] == "idle"


# ── migration ────────────────────────────────────────────────────────────────

def test_migration_rebuilds_check_constraints_and_keeps_rows(tmp_path):
    path = tmp_path / "farmcortes.sqlite3"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE review_queue (id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL CHECK(kind IN ('short','long','compilation')), "
        "streamer TEXT NOT NULL, platform TEXT NOT NULL CHECK(platform IN ('twitch','kick')), source_url TEXT NOT NULL, file_path TEXT NOT NULL, "
        "duration REAL NOT NULL, peak_at REAL, category TEXT NOT NULL, titles_json TEXT NOT NULL, description TEXT NOT NULL, hashtags TEXT NOT NULL, "
        "credit_text TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','approved','discarded','published','failed')), "
        "chosen_title TEXT, created_at TEXT NOT NULL, decided_at TEXT, youtube_id TEXT, error TEXT, is_test INTEGER NOT NULL DEFAULT 0, published_at TEXT)"
    )
    conn.execute(
        "INSERT INTO review_queue (id,kind,streamer,platform,source_url,file_path,duration,category,titles_json,description,hashtags,credit_text,status,chosen_title,created_at,youtube_id,published_at) "
        "VALUES (41,'short','alice','kick','u','v',10,'GTA','[\"a\",\"b\",\"c\"]','d','#h','c','published','a','2026-11-01','yt41','2026-11-02')"
    )
    conn.commit()
    conn.close()

    db = Database(path)
    Database(path)  # second open is a no-op

    rows = review.list_items(db)
    assert len(rows) == 1
    assert {k: rows[0][k] for k in ("id", "streamer", "status", "youtube_id", "published_at", "social_json")} == {
        "id": 41, "streamer": "alice", "status": "published", "youtube_id": "yt41", "published_at": "2026-11-02", "social_json": "{}"}
    new = add_biblia(db, tmp_path)
    assert new == 42
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='review_queue_pre_biblia'").fetchone()[0] == 0
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE review_queue SET kind='other' WHERE id=41")
