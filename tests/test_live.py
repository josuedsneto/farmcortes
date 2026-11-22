from collections import deque
from pathlib import Path
import threading
import time
from unittest.mock import patch

import pytest

from farmcortes.db import Database
from farmcortes.live import (
    LiveManager, LiveSession, LiveTranscriber, MIN_FREE, PipelineError,
    Segment, producer_command, rotate_window, segment_command, validate_live_url,
)
from farmcortes.pipeline import Caption, probe, render, run


@pytest.fixture
def manager(tmp_path):
    return LiveManager(Database(tmp_path / 'jobs.sqlite3'), tmp_path)


@pytest.fixture
def synthetic(tmp_path):
    source = tmp_path / 'synthetic.ts'
    run(['ffmpeg', '-nostdin', '-y', '-v', 'error', '-f', 'lavfi', '-i',
         'testsrc=size=160x90:rate=10', '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000',
         '-t', '64', '-c:v', 'libx264', '-threads', '1', '-preset', 'ultrafast',
         '-pix_fmt', 'yuv420p', '-g', '10', '-c:a', 'aac', '-f', 'mpegts', str(source)])
    return source


def job(manager, ident='live-test', minimum=15, maximum=20):
    return manager.db.create(ident, 'https://twitch.tv/example', 1, minimum, maximum,
                             status='live_recording', auto_publish=False)


def wait_finished(session, timeout=120):
    assert session.finished.wait(timeout), 'Live did not terminate'


def fake_speech(source, subtitle):
    duration = float(probe(source)['format']['duration'])
    return [Caption(i, min(i + 1.8, duration),
                    f'A verdade mudou o resultado depois do problema número {i}.')
            for i in range(0, max(0, min(20, int(duration)) - 2), 2)]


def test_live_validation_and_producer_safety():
    for url in ['https://twitch.tv/channel', 'https://kick.com/channel', 'https://youtu.be/live']:
        assert validate_live_url(url) == url
        command = producer_command(url)
        assert '--cookies' not in command and '--cookies-from-browser' not in command
        assert '--extractor-args' not in command
    youtube = producer_command('https://youtu.be/live')
    assert '--ignore-config' in youtube and '--live-from-start' in youtube
    assert youtube[youtube.index('--proxy') + 1] == ''
    for url in ['file:///etc/passwd', 'http://twitch.tv/channel', 'https://twitch.tv.evil/x',
                'https://user:secret@twitch.tv/x', 'https://kick.com:444/x', 'https://localhost/x']:
        with pytest.raises(ValueError):
            validate_live_url(url)


def test_rights_and_duration_required(manager):
    with pytest.raises(ValueError, match='autorização'):
        manager.start('https://twitch.tv/example', False)
    with pytest.raises(ValueError, match='duração'):
        manager.start('https://twitch.tv/example', True, 70, 20)
    assert manager.db.list() == []


def test_capture_copy_segments_and_real_vertical_cut(manager, synthetic):
    session = LiveSession(manager, job(manager), local_source=synthetic, transcriber=fake_speech)
    thread = threading.Thread(target=session.run)
    thread.start()
    wait_finished(session)
    thread.join()
    result = manager.db.get('live-test')
    assert result['status'] == 'live_done', result['error']
    segments = list(session.folder.glob('capture-*/*.ts'))
    assert len(segments) == 2
    assert 59 <= float(probe(sorted(segments)[0])['format']['duration']) <= 62
    assert result['outputs']
    output = result['outputs'][0]
    path = session.folder / output['file']
    info = probe(path)
    video = next(s for s in info['streams'] if s['codec_type'] == 'video')
    assert (video['width'], video['height']) == (1080, 1920)
    assert path.with_suffix('.subscribe-v2').exists()
    assert 'INSCREVA-SE NO CANAL' in path.with_suffix('.burn.ass').read_text()
    assert path.with_suffix('.srt').stat().st_size
    assert output['quality']['editorial_ready'] and 'rms_boost' in output['quality']
    assert result['publication'] == {} and result['auto_publish'] == 0
    assert manager.db.next_queued() is None


def test_window_rotation_preserves_pending_and_outputs(tmp_path):
    segments = deque()
    for i in range(5):
        path = tmp_path / f'segment-{i}.ts'
        path.write_bytes(b'video')
        path.with_suffix('.srt').write_text('caption')
        segments.append(Segment(path, i * 60, 60))
    output = tmp_path / 'corte-01.mp4'
    output.write_bytes(b'keep')
    protected = {segments[0].path}
    rotate_window(segments, 120, protected)
    assert len(segments) == 5
    rotate_window(segments, 120)
    assert len(segments) == 2
    assert len(list(tmp_path.glob('*.ts'))) == 2
    assert len(list(tmp_path.glob('*.srt'))) == 2
    assert output.exists()


def test_concurrency_across_managers_and_stop(manager):
    entered = threading.Event()
    def capture(session, epoch):
        entered.set()
        session.stop.wait(5)
        return 0
    with patch.object(LiveSession, 'capture', capture):
        first = manager.start('https://twitch.tv/one', True)
        assert entered.wait(2)
        second = manager.start('https://kick.com/two', True)
        other = LiveManager(manager.db, manager.data)
        with pytest.raises(PipelineError, match='Limite de 2'):
            other.start('https://twitch.tv/three', True)
        sessions = list(manager.sessions.values())
        assert manager.stop_job(first['id'])['status'] == 'live_stopping'
        manager.stop_job(second['id'])
        for session in sessions:
            wait_finished(session, 5)
        assert manager.db.get(first['id'])['status'] == 'live_done'
        # Slot is released after the worker's finalization.
        deadline = time.monotonic() + 3
        while manager.sessions and time.monotonic() < deadline:
            time.sleep(.01)
        third = other.start('https://kick.com/three', True)
        other.stop_job(third['id'])
        other.shutdown()


def test_disk_space_fails_before_start_and_during_capture(manager):
    low = type('Usage', (), {'free': MIN_FREE - 1})()
    with patch('farmcortes.live.shutil.disk_usage', return_value=low):
        with pytest.raises(PipelineError, match='5 GB'):
            manager.start('https://twitch.tv/example', True)
        session = LiveSession(manager, job(manager))
        session.run()
    assert manager.db.get('live-test')['status'] == 'live_failed'
    assert '5 GB' in manager.db.get('live-test')['error']


def test_network_backoff_and_context_reset(manager):
    session = LiveSession(manager, job(manager))
    with patch.object(session, 'capture', side_effect=[1, 1, 0]) as capture, \
         patch.object(session.stop, 'wait', return_value=False) as delay:
        session.run()
    assert capture.call_count == 3
    assert [call.args[0] for call in delay.call_args_list] == [2, 4]
    assert manager.db.get('live-test')['status'] == 'live_done'


def test_youtube_block_does_not_retry_or_bypass(manager):
    data = job(manager)
    data['url'] = 'https://youtu.be/blocked'
    session = LiveSession(manager, data)
    with patch.object(session, 'capture', return_value=1) as capture:
        session.run()
    assert capture.call_count == 1
    assert 'sem cookies, proxy ou PO token' in manager.db.get(data['id'])['error']


def test_stop_real_capture(manager, synthetic):
    # Pace the local file as a live, and verify subprocess termination.
    from farmcortes.live import segment_command as original
    def paced(*args):
        command = original(*args)
        command[command.index('-i'):command.index('-i')] = ['-re']
        return command
    session = LiveSession(manager, job(manager), local_source=synthetic, transcriber=lambda *args: [])
    with patch('farmcortes.live.segment_command', paced):
        thread = threading.Thread(target=session.run)
        thread.start()
        time.sleep(.7)
        session.stop.set()
        wait_finished(session, 10)
        thread.join()
    assert manager.db.get('live-test')['status'] == 'live_done'


def test_restart_marks_orphan_and_preserves_cuts(manager):
    job(manager)
    manager.db.update('live-test', outputs=[{'file': 'corte-0001.mp4'}])
    manager.recover_interrupted()
    stored = manager.db.get('live-test')
    assert stored['status'] == 'live_failed'
    assert stored['outputs'] == [{'file': 'corte-0001.mp4'}]


def test_transcriber_uses_existing_config_offline(monkeypatch, tmp_path):
    monkeypatch.setenv('FARMCORTES_WHISPER_MODEL', '/local/model')
    with patch('faster_whisper.WhisperModel') as model:
        model.return_value.transcribe.return_value = ([], type('Info', (), {'language_probability': 1})())
        transcriber = LiveTranscriber()
        transcriber(tmp_path / 'source.ts', tmp_path / 'out.srt')
        transcriber(tmp_path / 'source.ts', tmp_path / 'out.srt')
        model.assert_called_once_with('/local/model', device='cpu', compute_type='int8',
                                      cpu_threads=4, local_files_only=True)


def test_capture_rotates_real_media(manager, synthetic):
    manager.window = 2  # Exercise eviction without two hours of synthetic media.
    session = LiveSession(manager, job(manager), local_source=synthetic,
                          transcriber=lambda *args: [])
    session.run()
    assert manager.db.get('live-test')['status'] == 'live_done'
    assert len(session.retained) == 1
    assert len(list(session.folder.glob('capture-*/*.ts'))) == 1
    assert session.retained[0].start >= 59


def test_context_media_survives_rotation_and_resets_after_reconnect(manager):
    manager.window = 60
    session = LiveSession(manager, job(manager), transcriber=lambda *args: [])
    segments = []
    for i in range(3):
        path = session.folder / f'input-{i}.ts'
        path.write_bytes(b'media')
        segments.append(Segment(path, i * 60, 60, int(i == 2)))
    session.retained.extend(segments)
    session.protected.update(s.path for s in segments)
    for segment in segments:
        session.pending.put(segment)
    # A separate producer supplies the sentinel after consumption makes space.
    consumer = threading.Thread(target=session._consume)
    with patch('farmcortes.live.rms_levels', return_value=[]), patch.object(session, '_cut'):
        consumer.start()
        session.pending.put(None, timeout=3)
        consumer.join(3)
    assert not consumer.is_alive() and session.error is None
    assert [s.epoch for s, _, _ in session.context] == [1]
    assert not segments[0].path.exists() and not segments[1].path.exists()
    assert segments[2].path.exists()


def test_render_failure_preserves_existing_outputs(manager, synthetic):
    data = job(manager)
    previous = {'file': 'corte-0001.mp4', 'subtitle': 'corte-0001.srt'}
    manager.db.update(data['id'], outputs=[previous])
    def fail(*args):
        raise PipelineError('Falha de renderização local')
    session = LiveSession(manager, data, local_source=synthetic,
                          transcriber=fake_speech, render_fn=fail)
    session.run()
    stored = manager.db.get(data['id'])
    assert stored['status'] == 'live_failed'
    assert 'renderização' in stored['error']
    assert stored['outputs'] == [previous]


def test_live_routes_and_lifecycle(manager, monkeypatch):
    import importlib
    from fastapi.testclient import TestClient
    monkeypatch.setenv('FARMCORTES_DATA', str(manager.data))
    module = importlib.import_module('farmcortes.app')
    monkeypatch.setattr(module, 'DB', manager.db)
    monkeypatch.setattr(module, 'LIVE', manager)
    monkeypatch.setattr(module, 'stop_event', threading.Event())
    def capture(session, epoch):
        session.stop.wait(5)
        return 0
    with patch.object(LiveSession, 'capture', capture), TestClient(module.app) as client:
        assert client.get('/live').status_code == 200
        assert client.post('/api/live', data={'url': 'https://kick.com/test'}).status_code == 400
        response = client.post('/api/live', data={'url': 'https://kick.com/test', 'rights_confirmed': 'true'})
        assert response.status_code == 202
        ident = response.json()['id']
        assert ident in {j['id'] for j in client.get('/api/jobs').json()}
        assert client.get(f'/api/live/{ident}/preview').status_code == 404
        assert client.post('/api/live/missing/stop').status_code == 404
        assert client.post(f'/api/live/{ident}/stop').status_code == 200
        session = manager.sessions.get(ident)
        if session:
            wait_finished(session, 5)
        assert manager.db.get(ident)['status'] == 'live_done'


def test_preview_cache_keeps_context_protection(manager, synthetic, monkeypatch):
    import importlib
    monkeypatch.setenv('FARMCORTES_DATA', str(manager.data))
    module = importlib.import_module('farmcortes.app')
    monkeypatch.setattr(module, 'LIVE', manager)
    data = job(manager)
    session = LiveSession(manager, data)
    session.retained.append(Segment(synthetic, 0, 64))
    session.protected.add(synthetic)
    manager.sessions[data['id']] = session
    try:
        from farmcortes.pipeline import run as actual_run
        with patch('farmcortes.pipeline.run', wraps=actual_run) as conversion:
            assert Path(module.live_preview(data['id']).path).is_file()
            assert Path(module.live_preview(data['id']).path).is_file()
            assert conversion.call_count == 1
        assert synthetic in session.protected
        assert not session.preview_protected
    finally:
        manager.sessions.clear()


def test_stop_is_idempotent_after_finalization(manager):
    data = job(manager)
    session = LiveSession(manager, data)
    manager.sessions[data['id']] = session
    try:
        with patch.object(session, 'capture', return_value=0):
            session.run()
        assert manager.stop_job(data['id'])['status'] == 'live_done'
    finally:
        manager.sessions.clear()
