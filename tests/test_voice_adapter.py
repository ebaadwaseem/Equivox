from types import SimpleNamespace

from audio.moonshine_transcriber import MoonshineTranscriber


def test_partials_and_final_share_physical_id():
    adapter=MoonshineTranscriber()
    first=adapter.transcript(SimpleNamespace(line_id=99,text='open a new'))
    final=adapter.transcript(SimpleNamespace(line_id=99,text='open a new tab'),True)
    other=adapter.transcript(SimpleNamespace(line_id=100,text='scroll down'))
    assert first.id==final.id
    assert final.is_final
    assert first.id!=other.id


def test_native_session_restart_cannot_reuse_consumed_id():
    adapter=MoonshineTranscriber()
    before=adapter.transcript(SimpleNamespace(line_id=1,text='scroll down'))
    adapter._epoch+=1
    after=adapter.transcript(SimpleNamespace(line_id=1,text='scroll down'))
    assert before.id!=after.id


def test_vocabulary_hints_use_native_comma_separated_format(monkeypatch, tmp_path):
    import json
    import sys
    from audio import moonshine_transcriber as adapter
    config = tmp_path / 'config.json'
    config.write_text(json.dumps({'model_path':'test-model','model_arch':5}))
    received = {}
    def transcriber(path, arch, **kwargs):
        received.update(kwargs)
        return object()
    fake = SimpleNamespace(ModelArch=lambda value:value, Transcriber=transcriber)
    monkeypatch.setitem(sys.modules, 'moonshine_voice', fake)
    monkeypatch.setattr(adapter, 'CONFIG_PATH', config)
    adapter.load_model()
    hints = received['options']['keyterms']
    assert isinstance(hints, str)
    assert hints.split(',') == ['Vox','Equivox','Edmonton','physiotherapy','backspace','option one','option two']
