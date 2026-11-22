import pytest
from farmcortes import review, youtube
from farmcortes.db import Database

def test_queue_decision_and_limit(tmp_path, monkeypatch):
    db = Database(tmp_path/'db')
    def add(test=False):
        return review.enqueue(kind='short',streamer='s',platform='twitch',source_url='https://twitch.tv/s',file_path='v',duration=50,category='GTA',titles=['a','b','c'],is_test=test,db=db)
    ids = [add(), add(), add(True)]
    for i in ids: review.decide(i, 2, db)
    with pytest.raises(ValueError): review.decide(ids[0], 1, db)
    (tmp_path/'streamers.json').write_text('[{"slug":"s","platform":"twitch","approved":true}]')
    monkeypatch.setenv('FARMCORTES_DAILY_UPLOAD_LIMIT','1')
    monkeypatch.setattr(youtube,'settings',lambda _: {'enabled': True,'token':tmp_path/'token','privacy':'private'})
    calls=[]
    monkeypatch.setattr(youtube,'publish_file',lambda *a,**k: calls.append(k) or {'video_id':'abc'})
    review.publish_approved(db,tmp_path)
    review.publish_approved(db,tmp_path)
    assert len(calls)==1
    assert [x['status'] for x in reversed(review.list_items(db))] == ['published','approved','approved']
    assert calls[0]['_approval'] is youtube._REVIEW_AUTHORIZATION

def test_legacy_upload_blocked(tmp_path):
    with pytest.raises(RuntimeError, match='aprovação'):
        youtube.publish_file(tmp_path/'v', {}, token=tmp_path/'t')

def test_revoked_whitelist_stays_queued(tmp_path, monkeypatch):
    db=Database(tmp_path/'db')
    item=review.enqueue(kind='short',streamer='revoked',platform='kick',source_url='https://kick.com/revoked',file_path='v',duration=1,titles=['a','b','c'],db=db)
    review.decide(item,1,db)
    (tmp_path/'streamers.json').write_text('[]')
    monkeypatch.setattr(youtube,'settings',lambda _: {'enabled':True})
    monkeypatch.setattr(youtube,'publish_file',lambda *a,**kw: pytest.fail('must not upload'))
    assert review.publish_approved(db,tmp_path)==[]
    assert review.list_items(db)[0]['status']=='approved'

def test_telegram_preview_payload(tmp_path, monkeypatch):
    from farmcortes import telegram
    video=tmp_path/'v.mp4'; video.write_bytes(b'video')
    monkeypatch.setenv('FARMCORTES_TELEGRAM_BOT_TOKEN','test')
    monkeypatch.setenv('FARMCORTES_TELEGRAM_CHAT_ID','123')
    called={}
    class Response:
        def raise_for_status(self): pass
        def json(self): return {'ok':True}
    def post(url,**kwargs):
        called.update(kwargs); return Response()
    monkeypatch.setattr(telegram.requests,'post',post)
    assert telegram.send_preview({'id':5,'is_test':1,'streamer':'alice','category':'GTA','titles_json':'["a","b","c"]','file_path':str(video)},tmp_path)
    assert '[TESTE]' in called['data']['caption']
    assert 'fc:5:x' in called['data']['reply_markup']
