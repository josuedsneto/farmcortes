"""Approval queue and serialized publisher."""
import fcntl
import json
import os
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .db import Database, now
from . import youtube

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

def list_items(db=None):
    with (db or database()).connect() as conn:
        return [dict(row) for row in conn.execute('SELECT * FROM review_queue ORDER BY id DESC')]

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
        conn.execute('UPDATE review_queue SET status=?,chosen_title=?,decided_at=? WHERE id=?', ('discarded' if title is None else 'approved', title, now(), item_id))
        return title

def publish_approved(db=None, data_dir=None):
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
            return results
        limit = max(0, int(os.getenv('FARMCORTES_DAILY_UPLOAD_LIMIT', '5')))
        zone = ZoneInfo('America/Sao_Paulo')
        today = datetime.now(zone).date()
        start = datetime.combine(today, time.min, zone).astimezone(timezone.utc).isoformat()
        end = datetime.combine(today + timedelta(days=1), time.min, zone).astimezone(timezone.utc).isoformat()
        with db.connect() as conn:
            count = conn.execute("SELECT COUNT(*) FROM review_queue WHERE status='published' AND published_at>=? AND published_at<?", (start, end)).fetchone()[0]
            rows = conn.execute("SELECT * FROM review_queue WHERE status='approved' AND is_test=0 ORDER BY id").fetchall()
        for row in rows:
            if (row['platform'], row['streamer']) not in allowed:
                continue
            if count >= limit:
                break
            description = '\n\n'.join(filter(None, [row['description'], row['credit_text'], row['source_url'], row['hashtags']]))
            metadata = {'title': row['chosen_title'], 'description': description, 'tags': [], 'category_id': '20', 'language': 'pt-BR', 'made_for_kids': False, 'contains_synthetic_media': False}
            try:
                result = youtube.publish_file(Path(row['file_path']), metadata, token=config['token'], privacy=config['privacy'], short_form=row['kind']=='short', _approval=youtube._REVIEW_AUTHORIZATION)
            except Exception as exc:
                with db.connect() as conn:
                    conn.execute("UPDATE review_queue SET status='failed',error=? WHERE id=?", (str(exc)[:500], row['id']))
                continue
            with db.connect() as conn:
                conn.execute("UPDATE review_queue SET status='published',youtube_id=?,published_at=?,error=NULL WHERE id=?", (result['video_id'], now(), row['id']))
            count += 1
            results.append(result)
    return results

if __name__ == '__main__':
    publish_approved()
