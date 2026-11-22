"""Anonymous Twitch IRC and Kick public chat, with 48-hour retention."""
import json
import logging
import random
import re
import ssl
import time
from pathlib import Path
import requests
import websocket

log = logging.getLogger(__name__)

def prune(path, current=None):
    path = Path(path)
    if not path.exists(): return
    cutoff = (current or time.time()) - 48*3600
    kept = []
    for line in path.read_text(encoding='utf-8').splitlines():
        try:
            if json.loads(line)['timestamp'] >= cutoff: kept.append(line)
        except (ValueError, KeyError, TypeError): pass
    path.write_text(''.join(x+'\n' for x in kept), encoding='utf-8')

def append(path, name, text, timestamp=None):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a',encoding='utf-8') as f:
        f.write(json.dumps({'timestamp':timestamp or time.time(), 'display_name':name, 'text':text},ensure_ascii=False)+'\n')

def read(platform, slug, path, stop):
    prune(path)
    while not stop.is_set():
        ws = None
        try:
            if platform == 'twitch':
                ws = websocket.create_connection('wss://irc-ws.chat.twitch.tv:443', timeout=10, sslopt={'cert_reqs':ssl.CERT_REQUIRED})
                ws.send('CAP REQ :twitch.tv/tags'); ws.send(f'NICK justinfan{random.randint(10000,99999999)}'); ws.send(f'JOIN #{slug}')
            elif platform == 'kick':
                data = requests.get(f'https://kick.com/api/v2/channels/{slug}',timeout=15); data.raise_for_status()
                room = data.json()['chatroom']['id']
                ws = websocket.create_connection('wss://ws-us2.pusher.com/app/32cbd69e4b950bf97679?protocol=7&client=js&version=8.4.0', timeout=10)
                ws.send(json.dumps({'event':'pusher:subscribe','data':{'channel':f'chatrooms.{room}.v2'}}))
            else: raise ValueError('Plataforma inválida')
            last_prune = time.time()
            while not stop.is_set():
                try: raw = ws.recv()
                except websocket.WebSocketTimeoutException: continue
                if platform == 'twitch':
                    for line in raw.splitlines():
                        if line.startswith('PING'): ws.send(line.replace('PING','PONG',1)); continue
                        match = re.search(r'^(?:@([^ ]+) )?:([^! ]+)![^ ]+ PRIVMSG #[^ ]+ :(.*)$',line)
                        if match:
                            tags = dict(x.split('=',1) for x in (match[1] or '').split(';') if '=' in x)
                            append(path,tags.get('display-name') or match[2],match[3])
                else:
                    event = json.loads(raw)
                    if event.get('event') == 'pusher:ping': ws.send(json.dumps({'event':'pusher:pong','data':{}})); continue
                    if event.get('event','').endswith('ChatMessageEvent'):
                        msg = event.get('data',{}); msg = json.loads(msg) if isinstance(msg,str) else msg
                        append(path,msg.get('sender',{}).get('username',''),msg.get('content',''))
                if time.time()-last_prune > 3600: prune(path); last_prune=time.time()
        except Exception:
            log.warning('Chat %s/%s desconectado; tentando novamente',platform,slug)
            stop.wait(5)
        finally:
            if ws: ws.close()
