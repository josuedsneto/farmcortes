"""Whitelisted live capture, incremental peak detection and review rendering."""
import argparse
import fcntl
import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from . import chat, peaks, review, telegram, titles

log = logging.getLogger(__name__)
DATA = review.DATA

def streamers(data=DATA):
    path = Path(data)/'streamers.json'
    entries = json.loads(path.read_text(encoding='utf-8')) if path.exists() else []
    if not isinstance(entries,list): raise ValueError('streamers.json deve conter uma lista')
    return [x for x in entries if x.get('approved') is True and x.get('platform') in ('twitch','kick') and re.fullmatch(r'[A-Za-z0-9_-]+', x.get('slug',''))]

def cleanup(data=DATA):
    root = Path(data)/'live'
    if not root.exists(): return
    cutoff=time.time()-48*3600
    for path in root.rglob('*'):
        if path.is_file() and path.stat().st_mtime < cutoff: path.unlink()
    for path in sorted(root.rglob('*'),key=lambda x:len(x.parts),reverse=True):
        if path.is_dir():
            try: path.rmdir()
            except OSError: pass

def probe(path):
    result = subprocess.run(['ffprobe','-v','error','-show_entries','format=duration','-of','default=nw=1:nk=1',str(path)],capture_output=True,text=True,check=True)
    return float(result.stdout.strip())

def audio_levels(path, offset):
    cmd=['ffmpeg','-hide_banner','-nostats','-i',str(path),'-af','asetpts=PTS-STARTPTS,aresample=48000,asetnsamples=n=48000:p=0,astats=metadata=1:reset=1,ametadata=print:key=lavfi.astats.Overall.RMS_level','-f','null','-']
    result=subprocess.run(cmd,capture_output=True,text=True,check=True)
    times=re.findall(r'pts_time:([\d.]+).*?lavfi\.astats\.Overall\.RMS_level=([-\d.inf]+)',result.stderr,re.S)
    return [(offset+float(t),10**(float(v)/20)) for t,v in times if v != '-inf']

def silence_intervals(path, duration):
    result=subprocess.run(['ffmpeg','-hide_banner','-i',str(path),'-af','silencedetect=noise=-35dB:d=0.7','-f','null','-'],capture_output=True,text=True,check=True)
    starts=[float(x) for x in re.findall(r'silence_start: ([\d.]+)',result.stderr)]
    ends=[float(x) for x in re.findall(r'silence_end: ([\d.]+)',result.stderr)]
    keep=[]; cursor=0.0
    for i,start in enumerate(starts):
        if start-cursor > .05: keep.append((cursor,start))
        cursor = ends[i] if i<len(ends) else duration
    if duration-cursor>.05: keep.append((cursor,duration))
    return keep or [(0,duration)]

def ass_time(value):
    value=max(0,value); hours=int(value//3600); mins=int(value//60)%60; seconds=value%60
    return f'{hours}:{mins:02d}:{seconds:05.2f}'

def ass_escape(text):
    return str(text).replace('\\','').replace('{','').replace('}','').replace('\n',' ')

def make_subtitles(video, output, streamer, platform, slug):
    from faster_whisper import WhisperModel
    model=WhisperModel('small',device='cpu',compute_type='int8',cpu_threads=2)
    segments,_=model.transcribe(str(video),language='pt',word_timestamps=True,beam_size=1)
    words=[w for segment in segments for w in (segment.words or [])]
    header='''[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\n[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\nStyle: Default,DejaVu Sans,65,&H00FFFFFF,&H0000FFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,4,1,2,35,35,240,1\nStyle: Credit,DejaVu Sans,32,&H00FFFFFF,&H0000FFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,2,1,2,30,30,45,1\nStyle: Hook,DejaVu Sans,55,&H00FFFFFF,&H0000FFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,3,1,8,30,30,60,1\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n'''
    lines=[header, f'Dialogue: 0,0:00:00.00,{ass_time(probe(video))},Credit,,0,0,0,,{ass_escape(f"Live de {streamer} — {platform}/{slug}")}\n', 'Dialogue: 0,0:00:00.00,0:00:01.00,Hook,,0,0,0,,VEJA ESSE MOMENTO!\n']
    for i in range(0,len(words),3):
        chunk=words[i:i+3]
        text=' '.join((r'{\c&H00FFFF&}'+ass_escape(w.word.strip())+r'{\c&HFFFFFF&}' if j==0 else ass_escape(w.word.strip())) for j,w in enumerate(chunk))
        lines.append(f'Dialogue: 0,{ass_time(chunk[0].start)},{ass_time(chunk[-1].end)},Default,,0,0,0,,{text}\n')
    output.write_text(''.join(lines),encoding='utf-8')
    return ' '.join(w.word.strip() for w in words)

def crop_expr(rect):
    x,y,w,h=(float(rect[k]) for k in ('x','y','w','h'))
    if min(x,y,w,h)<0 or w<=0 or h<=0 or x+w>1 or y+h>1: raise ValueError('Crop inválido')
    return f'crop=trunc(iw*{w}/2)*2:trunc(ih*{h}/2)*2:trunc(iw*{x}/2)*2:trunc(ih*{y}/2)*2'

def render(files, start, end, entry, folder):
    # Concat demuxer uses paths generated locally, never user-supplied text.
    manifest=folder/'segments.txt'
    manifest.write_text(''.join("file '"+str(p.resolve()).replace("'", "'\\''")+"'\n" for p in files))
    raw=folder/'excerpt.mp4'; cleaned=folder/'clean.mp4'; ass=folder/'captions.ass'; final=folder/'short.mp4'
    subprocess.run(['ffmpeg','-y','-f','concat','-safe','0','-i',str(manifest),'-ss',str(start),'-t',str(end-start),'-c:v','libx264','-preset','veryfast','-c:a','aac',str(raw)],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    intervals=silence_intervals(raw,probe(raw))
    filters=[]
    for i,(a,b) in enumerate(intervals): filters += [f'[0:v]trim=start={a}:end={b},setpts=PTS-STARTPTS[v{i}]',f'[0:a]atrim=start={a}:end={b},asetpts=PTS-STARTPTS[a{i}]']
    filters.append(''.join(f'[v{i}][a{i}]' for i in range(len(intervals)))+f'concat=n={len(intervals)}:v=1:a=1[v][a]')
    subprocess.run(['ffmpeg','-y','-i',str(raw),'-filter_complex',';'.join(filters),'-map','[v]','-map','[a]','-c:v','libx264','-preset','veryfast','-c:a','aac',str(cleaned)],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    transcript=make_subtitles(cleaned,ass,entry.get('name',entry['slug']),entry['platform'],entry['slug'])
    face=crop_expr(entry.get('face_crop',{'x':.7,'y':0,'w':.3,'h':.3})); game=crop_expr(entry.get('game_crop',{'x':.2,'y':0,'w':.6,'h':1}))
    # Paths for the ASS filter are escaped for ffmpeg's filter parser.
    subpath=str(ass.resolve()).replace('\\','\\\\').replace(':','\\:').replace("'", "\\'")
    filt=f"[0:v]split=2[f][g];[f]{face},scale=1080:960:force_original_aspect_ratio=increase,crop=1080:960[f1];[g]{game},scale=1080:960:force_original_aspect_ratio=increase,crop=1080:960[g1];[f1][g1]vstack,ass='{subpath}'[v]"
    subprocess.run(['ffmpeg','-y','-i',str(cleaned),'-filter_complex',filt,'-map','[v]','-map','0:a','-c:v','libx264','-preset','veryfast','-c:a','aac',str(final)],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    return final, transcript

def run(entry, *, test=False, data=DATA):
    data=Path(data); cleanup(data)
    limit=max(0,int(os.getenv('FARMCORTES_LIVE_LIMIT','2')))
    locks=data/'live-locks'; locks.mkdir(parents=True,exist_ok=True)
    slot=None
    for i in range(limit):
        handle=(locks/f'{i}.lock').open('w')
        try: fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB); slot=handle; break
        except BlockingIOError: handle.close()
    if slot is None: raise RuntimeError('Limite de lives simultâneas atingido')
    slug=entry['slug']; platform=entry['platform']
    session=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    folder=data/'live'/slug/session; folder.mkdir(parents=True)
    url=f'https://{platform}.tv/{slug}' if platform=='twitch' else f'https://kick.com/{slug}'
    stop=threading.Event(); worker=threading.Thread(target=chat.read,args=(platform,slug,folder/'chat.jsonl',stop),daemon=True)
    stream=None; recorder=None
    try:
        worker.start(); started=time.time()
        stream=subprocess.Popen(['streamlink','--stdout',url,'720p60,720p,480p,360p,240p,160p'],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
        recorder=subprocess.Popen(['ffmpeg','-hide_banner','-loglevel','error','-i','pipe:0','-c','copy','-f','segment','-segment_time','10','-reset_timestamps','1',str(folder/'segment-%06d.ts')],stdin=stream.stdout,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        stream.stdout.close()
        files=[]; offsets=[]; levels=[]; processed=[]; last_cleanup=time.time()
        while recorder.poll() is None or len(files)<len(list(folder.glob('segment-*.ts'))):
            found=sorted(folder.glob('segment-*.ts'))
            ready=found if recorder.poll() is not None else found[:-1]
            for path in ready[len(files):]:
                offset=sum(offsets); duration=probe(path)
                levels.extend(audio_levels(path,offset)); files.append(path); offsets.append(duration)
            total=sum(offsets)
            messages=[]
            chat_path=folder/'chat.jsonl'
            if chat_path.exists():
                for line in chat_path.read_text(encoding='utf-8').splitlines():
                    try:
                        msg=json.loads(line); messages.append((msg['timestamp']-started,msg['text']))
                    except (ValueError,KeyError): continue
            for peak in peaks.detect(messages,levels, min_messages=int(os.getenv('FARMCORTES_CHAT_MIN_MESSAGES','8'))):
                t=peak['timestamp']
                if any(abs(t-old)<45 for old in processed) or t+15>total: continue
                processed.append(t)
                work=folder/f'clip-{int(t)}'; work.mkdir(exist_ok=True)
                try:
                    video, transcript=render(files,max(0,t-40),min(total,t+15),entry,work)
                    output=data/'review'/f'{slug}-{session}-{int(t)}.mp4'; output.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(video,output)
                    identifier=review.enqueue(kind='short',streamer=slug,platform=platform,source_url=url,file_path=output,duration=probe(output),peak_at=t,category='GTA',titles=titles.generate({'streamer':slug,'transcript':transcript}),description='',hashtags='#GTA #Shorts',credit_text=entry.get('credit') or f'Live de {slug} — {url}',is_test=test,db=review.Database(data/'farmcortes.sqlite3'))
                    item=next(x for x in review.list_items(review.Database(data/'farmcortes.sqlite3')) if x['id']==identifier)
                    try: telegram.send_preview(item,data)
                    except Exception: log.warning('Falha ao enviar prévia; item %s permanece na fila',identifier)
                except Exception: log.exception('Falha ao gerar corte do pico %.1f',t)
                finally: shutil.rmtree(work,ignore_errors=True)
            if time.time()-last_cleanup>3600: cleanup(data); last_cleanup=time.time()
            if recorder.poll() is None: time.sleep(3)
        if recorder.returncode or stream.wait() != 0: raise RuntimeError('Captura da live falhou')
    finally:
        stop.set()
        if recorder and recorder.poll() is None: recorder.terminate(); recorder.wait(timeout=10)
        if stream and stream.poll() is None: stream.terminate(); stream.wait(timeout=10)
        worker.join(timeout=12); slot.close()

def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--streamer'); parser.add_argument('--platform',choices=['twitch','kick']); parser.add_argument('--test',action='store_true'); parser.add_argument('--manager',action='store_true'); parser.add_argument('--cleanup',action='store_true'); args=parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    if args.cleanup:
        cleanup()
        return
    entries=streamers()
    if args.manager:
        pending=list(entries); children=[]; failed=False
        limit=max(1,int(os.getenv('FARMCORTES_LIVE_LIMIT','2')))
        while pending or children:
            while pending and len(children)<limit:
                entry=pending.pop(0)
                cmd=[os.sys.executable,'-m','farmcortes.livecut','--streamer',entry['slug'],'--platform',entry['platform']]
                if args.test: cmd.append('--test')
                children.append(subprocess.Popen(cmd))
            for child in children[:]:
                if child.poll() is not None:
                    failed |= child.returncode != 0
                    children.remove(child)
            if children: time.sleep(1)
        if failed: raise SystemExit(1)
    else:
        if not args.streamer or not args.platform: parser.error('--streamer e --platform são obrigatórios')
        entry=next((x for x in entries if x['slug']==args.streamer and x['platform']==args.platform),None)
        if not entry: parser.error('Streamer não está aprovado na whitelist')
        run(entry,test=args.test)

if __name__ == '__main__': main()
