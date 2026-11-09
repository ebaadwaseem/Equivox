import asyncio
from types import SimpleNamespace

from backend.choices import Choice, ChoiceSession, ranked_targets
from backend.vision import parse_observation, scene_revision
from shared.protocol import Decision, Element


def test_visual_targets_are_bounded_and_unknown_ids_are_not_executed():
    result=parse_observation({'description':'A map', 'plan':['Choose a clinic'], 'targets':[
        {'label':'Known', 'target_id':'e01'},
        {'label':'Visible clinic', 'x':.25,'y':.5,'confidence':.95},
        {'label':'Outside','x':2,'y':.5,'confidence':1},
        {'label':'NaN','x':float('nan'),'y':.5,'confidence':1},
        {'label':'Uncertain','x':.5,'y':.5,'confidence':.5},
        {'label':'Invented id','target_id':'e999'},
    ]},[Element('e01','button','Known')],(1000,800))
    assert len(result['choices'])==2
    assert result['choices'][0].decision.target_id=='e01'
    assert result['choices'][1].point==(250,400)
    assert result['choices'][1].decision.target_id is None


def test_choices_expire_on_page_or_scene_changes():
    page=SimpleNamespace(url='https://maps.example')
    session=ChoiceSession(page,page.url,[Choice('Clinic',Decision(True,'click'))],revision=[1])
    assert session.valid(page,[1])
    assert not session.valid(page,[2])
    assert not session.valid(SimpleNamespace(url=page.url),[1])
    page.url='https://other.example'
    assert not session.valid(page,[1])


def test_disambiguation_uses_model_ranking_and_control_roles():
    candidates=[Element('e1','link','Home'),Element('e2','button','Clinic'),Element('e3','textbox','Search')]
    d=Decision(True,'click',target_probabilities={'e2':.6,'e1':.3})
    assert ranked_targets(d,candidates)[0].id=='e2'
    assert [e.id for e in ranked_targets(Decision(True,'type'),candidates)]==['e3']


def test_vlm_description_uses_configured_model_and_screenshot(monkeypatch):
    from backend import vision
    from shared.protocol import PageState
    monkeypatch.setenv('VB_VLM_MODEL','vision-test')
    requests=[]
    class Response:
        def raise_for_status(self):
            pass
        def json(self):
            return {'choices':[{'message':{'content':'{"description":"Two buttons","plan":[],"targets":[{"label":"First button","target_id":"e1"}]}'}}]}
    class Client:
        def __init__(self,**kwargs):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self,*args):
            pass
        async def post(self,url,**kwargs):
            requests.append(kwargs['json'])
            return Response()
    monkeypatch.setattr(vision.httpx,'AsyncClient',Client)
    state=PageState('https://example.test','Buttons',[Element('e1','button','First')],screenshot_b64='AA==')
    result=asyncio.run(vision.observe(state,'tell me what you can see',(800,600)))
    assert result['description']=='Two buttons'
    assert result['choices'][0].decision.target_id=='e1'
    assert requests[0]['model']=='vision-test'
    assert requests[0]['messages'][0]['content'][1]['image_url']['url']=='data:image/jpeg;base64,AA=='
