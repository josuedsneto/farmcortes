"""Telegram previews and authenticated callback polling."""
import json
import logging
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path
import requests
from . import review

log = logging.getLogger(__name__)

def config(data_dir=None):
    path = Path(data_dir or review.DATA) / 'secrets' / 'telegram.env'
    values = {}
    if path.exists():
        if path.stat().st_mode & 0o077:
            raise ValueError('telegram.env deve ter permissão 600')
        for line in path.read_text().splitlines():
            key, sep, value = line.partition('=')
            if sep and key.strip() in ('FARMCORTES_TELEGRAM_BOT_TOKEN', 'FARMCORTES_TELEGRAM_CHAT_ID'):
                values[key.strip()] = value.strip().strip('"\'')
    token = os.getenv('FARMCORTES_TELEGRAM_BOT_TOKEN', values.get('FARMCORTES_TELEGRAM_BOT_TOKEN'))
    chat = os.getenv('FARMCORTES_TELEGRAM_CHAT_ID', values.get('FARMCORTES_TELEGRAM_CHAT_ID'))
    if not token or not chat:
        log.warning('Telegram não configurado; use /api/review')
        return None
    return token, chat

def send_preview(item, data_dir=None):
    cfg = config(data_dir)
    if not cfg:
        return False
    token, chat = cfg
    titles = json.loads(item['titles_json'])
    caption = (('[TESTE] ' if item['is_test'] else '') + ('[BÍBLIA] ' if item.get('kind') == 'biblia' else '') + f"{item['streamer']} — {item['category']}\n" + '\n'.join(f'{i}. {t}' for i,t in enumerate(titles, 1)))[:1024]
    keyboard = {'inline_keyboard': [[{'text': f'Título {i}', 'callback_data': f"fc:{item['id']}:{i}"} for i in range(1,4)] + [{'text': 'Descartar', 'callback_data': f"fc:{item['id']}:x"}]]}
    path = Path(item['file_path'])
    with tempfile.TemporaryDirectory() as tmp:
        method = 'sendVideo'
        if path.stat().st_size > 50 * 1024 * 1024:
            preview = Path(tmp) / 'preview.mp4'
            subprocess.run(['ffmpeg','-y','-i',str(path),'-vf','scale=-2:720','-c:v','libx264','-preset','veryfast','-crf','30','-c:a','aac','-b:a','64k',str(preview)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            path = preview
            method = 'sendDocument'
        with path.open('rb') as video:
            response = requests.post(f'https://api.telegram.org/bot{token}/{method}', data={'chat_id':chat,'caption':caption,'reply_markup':json.dumps(keyboard)}, files={'video' if method == 'sendVideo' else 'document': video}, timeout=120)
            response.raise_for_status()
            if not response.json().get('ok'): raise RuntimeError('Telegram recusou prévia')
    return True

def poll():
    cfg = config()
    if not cfg: return
    token, chat = cfg
    offset = None
    while True:
        try:
            response = requests.get(f'https://api.telegram.org/bot{token}/getUpdates', params={'offset':offset,'timeout':30,'allowed_updates':json.dumps(['callback_query'])}, timeout=40)
            response.raise_for_status()
            for update in response.json().get('result', []):
                offset = update['update_id'] + 1
                cb = update.get('callback_query', {})
                msg = cb.get('message', {})
                match = re.fullmatch(r'fc:(\d+):([123x])', cb.get('data',''))
                if not match or str(msg.get('chat', {}).get('id')) != str(chat) or str(cb.get('from', {}).get('id')) != str(chat): continue
                try:
                    title = review.decide(int(match[1]), 'discard' if match[2] == 'x' else int(match[2]))
                    text = 'Descartado' if title is None else f'Aprovado: {title}'
                except ValueError as exc:
                    text = str(exc)
                requests.post(f'https://api.telegram.org/bot{token}/editMessageCaption', json={'chat_id':chat,'message_id':msg['message_id'],'caption':text}, timeout=15).raise_for_status()
                requests.post(f'https://api.telegram.org/bot{token}/answerCallbackQuery', json={'callback_query_id':cb['id']}, timeout=15).raise_for_status()
        except Exception:
            log.warning('Falha no polling do Telegram; tentando novamente')
            time.sleep(5)

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(); parser.add_argument('--poll', action='store_true'); args = parser.parse_args()
    if args.poll: poll()
