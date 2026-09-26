import json
from datetime import datetime,timezone,timedelta
import pytest
from fastapi.testclient import TestClient
from app.main import create_app
from app.config import Settings
from app.models import MetricInput
from app.services import monitor,authorized
from app.integrations.oura import OuraProvider

@pytest.fixture
def env(tmp_path):
    settings=Settings(database_path=tmp_path/'journal.sqlite3',seed_demo=True,ai_enabled=False,anthropic_api_key='',provider_module='',oura_access_token='')
    app=create_app(settings)
    with TestClient(app) as client:yield client,app,settings

def events(response):return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]
def auth(client):return {'Authorization':'Bearer '+client.get('/api/bridge').json()['token']}
def reading(kind='steps',value=9021,unit='steps',external='reading-1'):
    return {'type':kind,'value':value,'unit':unit,'timestamp':datetime.now(timezone.utc).isoformat(),'external_id':external}

def test_seed_and_persistent_profile(env):
    c,app,settings=env
    s=c.get('/api/state').json()
    assert len(s['metrics'])==480 and len(s['reports'])==2
    assert len({m['timestamp'][:10] for m in s['metrics']})==30
    p=s['profile']|{'name':'Robin'}
    assert c.put('/api/profile',json=p).status_code==200
    with TestClient(create_app(settings)) as restarted:assert restarted.get('/api/profile').json()['name']=='Robin'
    assert 'ingest_token' not in json.dumps(s)
    assert 'ingest_token' not in c.get('/api/export').text

def test_profile_validation_and_bmr_floor(env):
    c,_,_=env;p=c.get('/api/profile').json()
    assert c.put('/api/profile',json=p|{'calorie_goal':1000}).status_code==422
    assert c.put('/api/profile',json=p|{'timezone':'not-a-timezone'}).status_code==422
    assert c.put('/api/profile',json=p|{'name':'   '}).status_code==422
    assert c.get('/api/profile').json()==p

def test_food_log_persists_and_changes_nutrition(env):
    c,app,settings=env
    m={'food_name':'Tofu bowl','meal':'dinner','grams':320,'kcal':500,'protein_g':32,'carbs_g':50,'fat_g':15}
    response=c.post('/api/food-log',json=m)
    assert response.status_code==201
    assert sum(x['protein_g'] for x in c.get('/api/food-log').json())==108
    assert '108g' in c.get('/api/state').json()['messages'][-1]['content']
    with TestClient(create_app(settings)) as restarted:assert len(restarted.get('/api/food-log').json())==4

def test_privacy_applies_to_demo_chat_and_insights(env):
    c,_,_=env
    assert c.put('/api/privacy',json={'provider':'oura','allowed':False}).status_code==200
    data=events(c.post('/api/chat',json={'message':'How did I sleep?'}))
    reply=''.join(e.get('delta','') for e in data)
    assert 'don’t have access' in reply and '7.7' not in reply
    assert not any('Oura' in i['sources'] for i in c.get('/api/insights').json())
    assert data[-1]['done']

@pytest.mark.parametrize('text',['I have chest pain','I can’t breathe','I want to end my life','I am thinking of self-harm'])
def test_urgent_messages_bypass_ai(env,text):
    c,app,_=env;app.state.settings.ai_enabled=True;app.state.settings.anthropic_api_key='not-a-real-key'
    chunks=events(c.post('/api/chat',json={'message':text}))
    content=''.join(x.get('delta','') for x in chunks)
    assert chunks[0]['urgent'] and chunks[0]['mode']=='demo'
    assert '911' in content and '988' in content

def test_streamed_reply_has_grounded_numbers_and_history(env):
    c,_,_=env
    chunks=events(c.post('/api/chat',json={'message':'How did I sleep this week?'}))
    assert len([x for x in chunks if 'delta' in x])>2
    assert '7.7 hours' in ''.join(x.get('delta','') for x in chunks)
    assert chunks[0]['sources']
    messages=c.get('/api/state').json()['messages'];assert messages[-2]['role']=='user' and messages[-1]['role']=='twin'

def test_ingestion_auth_validation_idempotency_and_live_separation(env):
    c,app,_=env;payload={'metrics':[reading()]}
    assert c.post('/api/ingest/apple_health',json=payload).status_code==401
    headers=auth(c)
    assert c.post('/api/ingest/apple_health',json=payload,headers=headers).status_code==200
    assert c.post('/api/ingest/apple_health',json=payload,headers=headers).status_code==200
    s=c.get('/api/state').json();watch=[m for m in s['metrics'] if m['source']=='apple_watch']
    assert len(watch)==1 and watch[0]['value']==9021 and watch[0]['origin']=='live'
    assert next(co for co in s['connections'] if co['provider']=='apple_watch')['mode']=='live'
    assert c.post('/api/ingest/apple_health',json={'metrics':[reading(value=-5)]},headers=headers).status_code==422
    assert c.post('/api/ingest/apple_health',json={'metrics':[reading(unit='kg')]},headers=headers).status_code==422
    assert c.post('/api/ingest/oura',json=payload,headers=headers).status_code==422
    assert len(app.state.db.rows("SELECT * FROM metric WHERE origin='live'"))==1

def test_missing_live_data_is_not_guessed(env):
    c,_,_=env
    c.post('/api/ingest/oura',headers=auth(c),json={'metrics':[reading('sleep_score',82,'score')]})
    reply=''.join(x.get('delta','') for x in events(c.post('/api/chat',json={'message':'How did I sleep?'})))
    assert 'No sleep-duration readings' in reply

def test_python_provider_adapter_sync(env):
    c,app,_=env
    class Adapter:
        async def fetch_metrics(self,since):return [MetricInput(**reading('sleep_hours',8.25,'h'))]
    app.state.registry.overrides['oura']=Adapter()
    assert c.get('/api/integrations').json()['oura']['configured']
    assert c.post('/api/connections/oura/sync').json()['readings']==1
    assert c.post('/api/connections/oura/sync').json()['readings']==1
    values=[m['value'] for m in c.get('/api/state').json()['metrics'] if m['source']=='oura']
    assert values==[8.25]
    assert c.post('/api/ingest/simulate').status_code==409

def test_adapter_failure_is_atomic(env):
    c,app,_=env
    class Bad:
        async def fetch_metrics(self,since):return [reading('sleep_hours',8,'h'),reading('sleep_hours',200,'h')]
    app.state.registry.overrides['oura']=Bad()
    before=c.get('/api/state').json()['metrics']
    assert c.post('/api/connections/oura/sync').status_code==502
    assert c.get('/api/state').json()['metrics']==before

def test_disconnect_hides_source_and_reconnects(env):
    c,_,_=env
    c.post('/api/connections/oura',json={'status':'disconnected','mode':'demo'})
    assert not any(m['source']=='oura' for m in c.get('/api/state').json()['metrics'])
    c.post('/api/connections/oura',json={'status':'connected','mode':'demo'})
    assert any(m['source']=='oura' for m in c.get('/api/state').json()['metrics'])

def test_simulation_nudges_dedupe_and_quiet_hours(env):
    c,app,_=env
    p=c.get('/api/profile').json();p.update(quiet_start=0,quiet_end=0)
    c.put('/api/profile',json=p)
    assert c.post('/api/ingest/simulate').status_code==200
    assert c.post('/api/ingest/simulate').status_code==200
    nudges=c.get('/api/nudges').json();assert any(n['rule_id']=='sleep-fuel' for n in nudges)
    for _ in range(6):monitor(app.state.db)
    nudges=c.get('/api/nudges').json();assert len(nudges)<=3 and len({n['rule_id'] for n in nudges})==len(nudges)
    app.state.db.execute('DELETE FROM nudge');p.update(quiet_start=22,quiet_end=8)
    c.put('/api/profile',json=p)
    monitor(app.state.db,datetime(2026,9,27,6,tzinfo=timezone.utc)) # 11 p.m. Pacific
    assert c.get('/api/nudges').json()==[]

def test_report_upload_manual_review_and_saved_analysis(env):
    c,app,settings=env
    data=b'%PDF-1.4\n% minimal test fixture'
    r=c.post('/api/reports/renpho/extract',files={'file':('body.pdf',data,'application/pdf')})
    assert r.status_code==200 and r.json()['mode']=='manual' and r.json()['fields']=={}
    uid=r.json()['upload_id']
    assert c.get('/api/reports/files/'+uid).content==data
    fields=c.get('/api/reports/sample').json()['fields'];fields.update(weight_kg=83.4,muscle_kg=64)
    assert c.post('/api/reports/renpho/confirm',json={'fields':fields,'mode':'manual','reviewed':False,'upload_id':uid}).status_code==422
    r=c.post('/api/reports/renpho/confirm',json={'fields':fields,'mode':'manual','reviewed':True,'upload_id':uid})
    assert r.status_code==201 and '83.4 kg' in r.json()['analysis']
    s=c.get('/api/state').json();assert s['reports'][0]['weight_kg']==83.4
    assert next(m['value'] for m in reversed(s['metrics']) if m['type']=='weight_kg')==83.4
    assert '83.4 kg' in s['messages'][-1]['content']

def test_invalid_report_file_and_values(env):
    c,_,_=env
    assert c.post('/api/reports/renpho/extract',files={'file':('fake.png',b'hello','image/png')}).status_code==422
    fields=c.get('/api/reports/sample').json()['fields'];fields['body_fat_pct']=1000
    assert c.post('/api/reports/renpho/confirm',json={'fields':fields,'mode':'sample','reviewed':True}).status_code==422

def test_demo_reports_excluded_from_live_context(env):
    c,app,_=env
    c.post('/api/ingest/renpho_scale',headers=auth(c),json={'metrics':[reading('weight_kg',72,'kg')]})
    context=authorized(app.state.db.snapshot())
    assert context['reports']==[]
    assert [m['value'] for m in context['metrics'] if m['source']=='renpho_scale']==[72]

def test_delete_persists_no_reseed_and_rotates_token(env):
    c,app,settings=env;old=auth(c)
    assert c.request('DELETE','/api/privacy',json={'confirm':'wrong'}).status_code==422
    assert c.request('DELETE','/api/privacy',json={'confirm':'DELETE'}).status_code==200
    with TestClient(create_app(settings)) as restarted:
        s=restarted.get('/api/state').json()
        assert s['deleted'] and s['metrics']==[] and s['messages']==[] and s['reports']==[]
        assert s['profile']['name']=='Friend'
        assert restarted.post('/api/ingest/apple_health',headers=old,json={'metrics':[reading()]}).status_code==401

def test_origin_and_host_protection(env):
    c,_,_=env
    assert c.post('/api/ingest/simulate',headers={'Origin':'https://untrusted.example'}).status_code==403
    assert c.get('/api/state',headers={'Host':'evil.example'}).status_code==400
