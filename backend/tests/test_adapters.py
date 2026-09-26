import asyncio
from datetime import datetime,timezone
import httpx
from app.integrations.oura import OuraProvider
from app.ai import extract_report,stream_reply
from app.config import Settings

class FakeResponse:
    def __init__(self,data,status=200):self.data=data;self.status_code=status
    def json(self):return self.data
    def raise_for_status(self):
        if self.status_code>=400:raise RuntimeError('http error')

class FakeStream:
    status_code=200
    async def __aenter__(self):return self
    async def __aexit__(self,*args):pass
    async def aiter_lines(self):
        yield 'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"Your sleep was 7.7 hours."}}'
        yield 'data: {"type":"message_stop"}'

class FakeClient:
    calls=[]
    def __init__(self,*args,**kwargs):self.kwargs=kwargs
    async def __aenter__(self):return self
    async def __aexit__(self,*args):pass
    async def get(self,path,params):
        self.calls.append(path)
        if path.endswith('/sleep'):return FakeResponse({'data':[{'id':'sleep-1','day':'2026-09-26','bedtime_end':'2026-09-26T08:00:00Z','type':'long_sleep','total_sleep_duration':27720,'average_hrv':48,'lowest_heart_rate':54}]})
        return FakeResponse({'data':[{'id':path.rsplit('/',1)[-1],'day':'2026-09-26','score':86}]})
    async def post(self,url,**kwargs):
        self.calls.append(kwargs)
        return FakeResponse({'content':[{'type':'text','text':'{"fields":{"weight_kg":85.85},"confidence":{"weight_kg":0.96}}'}]})
    def stream(self,*args,**kwargs):self.calls.append(kwargs);return FakeStream()

def test_oura_adapter_normalizes_units_and_ids(monkeypatch):
    monkeypatch.setattr(httpx,'AsyncClient',FakeClient)
    readings=asyncio.run(OuraProvider('test-token').fetch_metrics(datetime.now(timezone.utc)))
    assert len(readings)==5
    assert next(r for r in readings if r.type=='sleep_hours').value==7.7
    assert all(r.external_id for r in readings)
    assert len({r.external_id for r in readings})==5

def test_vision_and_streaming_transport(monkeypatch):
    monkeypatch.setattr(httpx,'AsyncClient',FakeClient)
    settings=Settings(anthropic_api_key='test-key',ai_enabled=True)
    result=asyncio.run(extract_report(settings,b'%PDF-1.4','application/pdf'))
    assert result['fields']['weight_kg']==85.85 and result['confidence']['weight_kg']==.96
    s={'profile':{'twin_name':'Pip','tone':'warm'},'metrics':[],'meals':[],'reports':[],'connections':[],'messages':[],'nudges':[]}
    async def read():return ''.join([chunk async for chunk in stream_reply(settings,s,'How did I sleep?')])
    assert asyncio.run(read())=='Your sleep was 7.7 hours.'
