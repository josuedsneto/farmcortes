import json
import pytest
from farmcortes import livecut

def test_whitelist_and_crop(tmp_path):
    (tmp_path/'streamers.json').write_text(json.dumps([{'slug':'ok','platform':'twitch','approved':True},{'slug':'no','platform':'kick','approved':False},{'slug':'youtube','platform':'youtube','approved':True}]))
    assert [x['slug'] for x in livecut.streamers(tmp_path)] == ['ok']
    assert 'crop=' in livecut.crop_expr({'x':.7,'y':0,'w':.3,'h':.3})
    with pytest.raises(ValueError): livecut.crop_expr({'x':.9,'y':0,'w':.3,'h':.3})
