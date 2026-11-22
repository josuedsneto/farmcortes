from farmcortes.peaks import detect
from farmcortes.chat import append, prune
import json

def test_chat_and_audio_dedupe():
    messages = [(310+i*.1,'KEKW') for i in range(10)]
    audio = [(i,0.1) for i in range(320)] + [(321, 0.9)]
    peaks = detect(messages,audio)
    assert len(peaks)==1
    assert set(peaks[0]['reasons']) == {'chat','termos','volume'}

def test_retention(tmp_path):
    path=tmp_path/'chat'
    append(path,'old','a',100)
    append(path,'new','b',200000)
    prune(path,200001)
    assert [json.loads(x)['display_name'] for x in path.read_text().splitlines()] == ['new']
