"""Approval queue and serialized publisher."""
import fcntl
import json
import os
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .db import Database, now
from . import youtube

BIBLIA_TAGS = ['bíblia', 'versículo do dia', 'shorts', 'fé', 'cristão', 'evangelho', 'palavra de deus', 'leia a bíblia', 'bíblia em um ano']
DATA = Path(os.getenv('FARMCORTES_DATA', Path(__file__).resolve().parent.parent / 'data'))

def database():
    return Database(DATA / 'farmcortes.sqlite3')

def enqueue(*, kind, streamer, platform, source_url, file_path, duration, peak_at=None,
            category='', titles, description='', hashtags='', credit_text='', is_test=False, db=None):
    if len(titles) != 3 or not all(isinstance(t, str) and t.strip() for t in titles):
        raise ValueError('São necessários três títulos')
    db = db or database()
    with db.connect() as conn:
        cur = conn.execute('INSERT INTO review_queue (kind,streamer,platform,source_url,file_path,duration,peak_at,category,titles_json,description,hashtags,credit_text,created_at,is_test) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (kind, streamer, platform, source_url, str(file_path), duration, peak_at, category, json.dumps(titles, ensure_ascii=False), description, hashtags, credit_text, now(), int(is_test)))
        return cur.lastrowid

def list_items(db=None, kind=None):
    with (db or database()).connect() as conn:
        if kind:
            return [dict(row) for row in conn.execute('SELECT * FROM review_queue WHERE kind=? ORDER BY id DESC', (kind,))]
        return [dict(row) for row in conn.execute('SELECT * FROM review_queue ORDER BY id DESC')]

def biblia_auto_approve_enabled():
    return os.getenv('FARMCORTES_BIBLIA_AUTO_APPROVE', 'false').strip().casefold() in {'1', 'true', 'yes'}

def auto_approve(item_id, db=None):
    """Approve a pending Biblia item with its first title, only when FARMCORTES_BIBLIA_AUTO_APPROVE is on."""
    if not biblia_auto_approve_enabled():
        return None
    with (db or database()).connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        row = conn.execute("SELECT * FROM review_queue WHERE id=? AND kind='biblia' AND status='pending'", (item_id,)).fetchone()
        if not row:
            return None
        title = json.loads(row['titles_json'])[0]
        conn.execute("UPDATE review_queue SET status='approved',chosen_title=?,decided_at=?,approved_by='auto' WHERE id=?", (title, now(), item_id))
        return title

def decide(item_id, choice, db=None):
    if choice not in (1, 2, 3, 'discard') or isinstance(choice, bool):
        raise ValueError('Escolha inválida')
    with (db or database()).connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        row = conn.execute('SELECT * FROM review_queue WHERE id=?', (item_id,)).fetchone()
        if not row:
            raise ValueError('Item não encontrado')
        if row['status'] != 'pending':
            raise ValueError('Item já decidido')
        title = None if choice == 'discard' else json.loads(row['titles_json'])[choice - 1]
        conn.execute('UPDATE review_queue SET status=?,chosen_title=?,decided_at=?,approved_by=? WHERE id=?', ('discarded' if title is None else 'approved', title, now(), 'human', item_id))
        return title

def publish_approved(db=None, data_dir=None, kinds=None):
    db = db or database()
    data_dir = Path(data_dir or DATA)
    data_dir.mkdir(parents=True, exist_ok=True)
    results = []
    with (data_dir / '.review-publisher.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        config = youtube.settings(data_dir)
        if not config['enabled']:
            return results
        whitelist_path = data_dir / 'streamers.json'
        try:
            entries = json.loads(whitelist_path.read_text(encoding='utf-8'))
            allowed = {(e['platform'], e['slug']) for e in entries if e.get('approved') is True and e.get('platform') in ('twitch', 'kick')}
        except (OSError, ValueError, TypeError, KeyError):
            allowed = set()  # no streamer passes; own Biblia content does not need the whitelist
        limit = max(0, int(os.getenv('FARMCORTES_DAILY_UPLOAD_LIMIT', '5')))
        zone = ZoneInfo('America/Sao_Paulo')
        today = datetime.now(zone).date()
        start = datetime.combine(today, time.min, zone).astimezone(timezone.utc).isoformat()
        end = datetime.combine(today + timedelta(days=1), time.min, zone).astimezone(timezone.utc).isoformat()
        with db.connect() as conn:
            count = conn.execute("SELECT COUNT(*) FROM review_queue WHERE status='published' AND published_at>=? AND published_at<?", (start, end)).fetchone()[0]
            rows = conn.execute("SELECT * FROM review_queue WHERE status='approved' AND is_test=0 ORDER BY id").fetchall()
            reserved = {r[0] for r in conn.execute('SELECT scheduled_for FROM review_queue WHERE scheduled_for IS NOT NULL')}
        for row in rows:
            if kinds and row['kind'] not in kinds:
                continue
            biblia = row['kind'] == 'biblia'
            if not biblia and (row['platform'], row['streamer']) not in allowed:
                continue
            if count >= limit:
                break
            description = '\n\n'.join(filter(None, [row['description'], row['credit_text'], row['source_url'], row['hashtags']]))
            metadata = {'title': row['chosen_title'], 'description': description, 'tags': BIBLIA_TAGS if biblia else [], 'category_id': '22' if biblia else '20', 'language': 'pt-BR', 'made_for_kids': False, 'contains_synthetic_media': False}
            publish_at = None
            if biblia and config['privacy'] == 'public':
                # Own content uses the shared schedule times; private/unlisted uploads stay immediate
                # because publishAt would turn a private upload public.
                publish_at = youtube.schedule_slots(1, config['schedule_times'], config['timezone'], reserved=reserved)[0]
            try:
                result = youtube.publish_file(Path(row['file_path']), metadata, token=config['token'], privacy=config['privacy'], publish_at=publish_at, short_form=row['kind'] in ('short', 'biblia'), _approval=youtube._REVIEW_AUTHORIZATION)
            except Exception as exc:
                with db.connect() as conn:
                    conn.execute("UPDATE review_queue SET status='failed',error=? WHERE id=?", (str(exc)[:500], row['id']))
                continue
            scheduled = result.get('publish_at')
            if scheduled:
                reserved.add(scheduled)
            with db.connect() as conn:
                conn.execute("UPDATE review_queue SET status='published',youtube_id=?,published_at=?,scheduled_for=?,error=NULL WHERE id=?", (result['video_id'], now(), scheduled, row['id']))
                if biblia:
                    _mark_verses(conn, row['id'], 'youtube')
            count += 1
            results.append(result)
    return results

def _mark_verses(conn, item_id, platform):
    column = {'youtube': 'published_youtube', 'tiktok': 'published_tiktok', 'facebook': 'published_facebook', 'instagram': 'published_instagram'}[platform]
    conn.execute(f'UPDATE biblia_verses SET {column}=1 WHERE ordem IN (SELECT ordem FROM biblia_videos WHERE review_id=?)', (item_id,))

TIKTOK_MODES = {'draft': (False, 'SELF_ONLY'), 'private': (True, 'SELF_ONLY'), 'public': (True, 'PUBLIC_TO_EVERYONE')}

def social_platforms():
    """Enabled extra platforms for Biblia: Meta Reels when their env is set, TikTok when its env and token exist."""
    from .social import meta, tiktok
    return meta.enabled_platforms() + (['tiktok'] if tiktok.is_configured() else [])

def _publish_tiktok(video, caption):
    """FARMCORTES_TIKTOK_MODE: draft (default, inbox; unaudited apps), private (direct post SELF_ONLY,
    allowed before the audit) or public (direct post, only after TikTok's audit)."""
    from .social import tiktok
    mode = os.getenv('FARMCORTES_TIKTOK_MODE', 'draft').strip().casefold()
    direct, privacy = TIKTOK_MODES.get(mode, TIKTOK_MODES['draft'])
    result = tiktok.upload_draft(video, caption, direct_post=direct, privacy_level=privacy)
    return {'platform': 'tiktok', 'id': result['publish_id'], 'mode': result['mode']}

def publish_social(db=None, max_attempts=3):
    """Post approved Biblia items to the enabled extra platforms (Facebook/Instagram Reels, TikTok)."""
    from .social import meta
    platforms = social_platforms()
    db = db or database()
    results = []
    if not platforms:
        return results
    with db.connect() as conn:
        rows = conn.execute("SELECT * FROM review_queue WHERE kind='biblia' AND status IN ('approved','published') AND is_test=0 ORDER BY id").fetchall()
    for row in rows:
        state = json.loads(row['social_json'] or '{}')
        video = Path(row['file_path'])
        for platform in platforms:
            entry = state.get(platform, {})
            if entry.get('id') or entry.get('attempts', 0) >= max_attempts or not video.is_file():
                continue
            try:
                if platform == 'tiktok':
                    result = _publish_tiktok(video, f"{row['chosen_title']} {row['hashtags']}")
                else:
                    result = meta.publish(platform, video, row['chosen_title'], '\n\n'.join(filter(None, [row['description'], row['credit_text'], row['hashtags']])))
                state[platform] = {k: v for k, v in result.items() if k != 'platform'} | {'published_at': now()}
                results.append(result)
            except Exception as exc:
                state[platform] = {'attempts': entry.get('attempts', 0) + 1, 'error': str(exc)[:300]}
            with db.connect() as conn:
                conn.execute('UPDATE review_queue SET social_json=? WHERE id=?', (json.dumps(state, ensure_ascii=False), row['id']))
                if state[platform].get('id'):
                    _mark_verses(conn, row['id'], platform)
    return results

if __name__ == '__main__':
    publish_approved()
