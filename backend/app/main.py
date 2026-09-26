from contextlib import asynccontextmanager
from datetime import datetime,timezone,timedelta,date
from pathlib import Path
import asyncio, json, logging, re, secrets, sqlite3, uuid
from fastapi import FastAPI, HTTPException, Request, UploadFile, File, Header, Query
from fastapi.responses import StreamingResponse, FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from fastapi.staticfiles import StaticFiles
from .config import Settings
from .database import Database,now,encode
from .models import (Profile,FoodInput,ConnectionChange,PrivacyChange,ChatInput,ConfirmReport,IngestRequest,MetricInput,PROVIDERS,METRICS,SOURCES)
from .integrations.registry import ProviderRegistry
from .services import (latest,allowed,authorized,insights,ingest,update_baselines,monitor,demo_reply,report_analysis,URGENT)
from .ai import stream_reply,extract_report

logger=logging.getLogger(__name__)

def create_app(settings:Settings|None=None):
    settings=settings or Settings();db=Database(settings.database_path);registry=ProviderRegistry(settings)
    uploads=settings.database_path.parent/'uploads'
    @asynccontextmanager
    async def lifespan(app):
        db.initialize(settings.seed_demo);update_baselines(db)
        yield
    app=FastAPI(title='Health Twin API',version='1.0.0',description='Local single-user React + Python health journal. Integration endpoints accept normalized readings with a bearer token.',lifespan=lifespan)
    app.state.db=db;app.state.registry=registry;app.state.settings=settings
    origins=list({settings.app_origin,'http://localhost:5174','http://127.0.0.1:5174','http://localhost:8000','http://127.0.0.1:8000'})
    _vercel_origin=re.compile(r'^https://[^/]+\.vercel\.app$')
    app.add_middleware(CORSMiddleware,allow_origins=origins,allow_origin_regex=r'https://[^/]+\.vercel\.app',allow_methods=['GET','POST','PUT','DELETE'],allow_headers=['Content-Type','Authorization'])
    app.add_middleware(TrustedHostMiddleware,allowed_hosts=['localhost','127.0.0.1','testserver','*.vercel.app'])
    @app.middleware('http')
    async def same_origin(request:Request,call_next):
        # Protect local mutations against browser requests from unrelated websites.
        if request.method not in ('GET','HEAD','OPTIONS') and request.headers.get('origin') and request.headers['origin'] not in origins and not _vercel_origin.match(request.headers['origin']):
            return JSONResponse({'detail':'This origin is not permitted.'},status_code=403)
        response=await call_next(request)
        if request.url.path.startswith('/api'):response.headers['Cache-Control']='no-store'
        return response
    @app.exception_handler(sqlite3.Error)
    async def storage_error(request,exc):
        logger.error('Database operation failed: %s',type(exc).__name__)
        return JSONResponse({'detail':'Your journal could not be saved. Please try again.'},status_code=503)
    def valid_provider(provider):
        if provider not in PROVIDERS:raise HTTPException(404,'Unknown provider.')
    def authorize_ingest(authorization):
        expected=db.one("SELECT ingest_token FROM profile WHERE id='demo'")['ingest_token']
        if not authorization or not secrets.compare_digest(authorization,f'Bearer {expected}'):raise HTTPException(401,'A valid ingestion bearer token is required.')
    def snapshot():
        s=db.snapshot();return s|{'aiEnabled':settings.live_ai,'integrationStatus':registry.status(),'insights':insights(s),'serverDate':datetime.now(timezone.utc).date().isoformat()}

    @app.get('/api/health')
    def health():return {'status':'ok','storage':'sqlite','version':'1.0.0'}
    @app.get('/api/state')
    def get_state():return snapshot()
    @app.get('/api/profile')
    def get_profile():return db.profile()
    @app.put('/api/profile')
    def put_profile(profile:Profile):
        bmr=latest(db.snapshot(),'bmr_kcal') or 1842
        if profile.calorie_goal<bmr:raise HTTPException(422,f'The saved calorie goal must not be below recorded BMR ({bmr:g} kcal).')
        db.execute("UPDATE profile SET data=? WHERE id='demo'",(profile.model_dump_json(),))
        return profile
    @app.get('/api/metrics')
    def get_metrics(range: str=Query('7d',pattern='^(7d|30d)$')):
        rows=db.snapshot()['metrics'];days=int(range[:-1]);cutoff=(datetime.now(timezone.utc)-timedelta(days=days)).isoformat()
        return [m for m in rows if m['timestamp']>=cutoff]
    @app.get('/api/food-log')
    def get_food(day:date|None=Query(None,alias='date')):
        if day:return [json.loads(r['data']) for r in db.rows('SELECT data FROM food_log WHERE substr(timestamp,1,10)=? ORDER BY timestamp',(day.isoformat(),))]
        return db.snapshot()['meals']
    @app.post('/api/food-log',status_code=201)
    def post_food(food:FoodInput):
        m=food.model_dump(mode='json');m.update(id=str(uuid.uuid4()),timestamp=(food.timestamp or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat())
        if not m['food_name'].strip():raise HTTPException(422,'Enter a meal name.')
        with db.connect() as c:
            c.execute('INSERT INTO food_log VALUES (?,?,?,?)',(m['id'],m['timestamp'],encode(m),'manual'))
            c.execute("UPDATE connection SET status='connected',last_synced=? WHERE provider='renpho_food'",(now(),))
        s=db.snapshot()
        if allowed(s,'renpho_food'):
            protein=sum(m['protein_g'] for m in s['meals'])
            db.add_message('twin',f'{food.food_name} is saved: {food.kcal:g} kcal and {food.protein_g:g}g protein. Today’s log now has {protein:g}g of your {s["profile"]["protein_goal"]:g}g protein goal.',['Meal log · '+m['timestamp'][:10]],'check-in')
        monitor(db);return m
    @app.get('/api/insights')
    def get_insights():return insights(db.snapshot())
    @app.get('/api/nudges')
    def get_nudges():return db.snapshot()['nudges']
    @app.post('/api/connections/{provider}')
    def connection(provider:str,change:ConnectionChange):
        valid_provider(provider)
        if change.status=='connected' and change.mode=='live' and not registry.get(provider):
            raise HTTPException(409,'No Python pull adapter is configured. Configure an adapter, or send readings to the authenticated ingestion endpoint.')
        with db.connect() as c:
            c.execute('UPDATE connection SET status=?,mode=? WHERE provider=?',(change.status,change.mode,provider))
            if change.status=='connected' and change.mode=='demo':
                seed=json.loads(Path(__file__).with_name('seed.json').read_text());delta=datetime.now(timezone.utc).date()-date(2026,9,26)
                for m in seed['metrics']:
                    if m['source']==provider:
                        ts=(datetime.fromisoformat(m['timestamp'].replace('Z','+00:00'))+delta).isoformat()
                        c.execute('INSERT OR IGNORE INTO metric VALUES (?,?,?,?,?,?,?)',(m['id'],ts,provider,m['type'],m['value'],m['unit'],'demo'))
                if provider=='renpho_food':
                    for m in seed['meals']:
                        m['timestamp']=(datetime.fromisoformat(m['timestamp'].replace('Z','+00:00'))+delta).isoformat()
                        c.execute('INSERT OR IGNORE INTO food_log VALUES (?,?,?,?)',(m['id'],m['timestamp'],encode(m),'demo'))
                c.execute('UPDATE connection SET last_synced=? WHERE provider=?',(now(),provider))
        update_baselines(db);return {'ok':True}
    @app.post('/api/connections/{provider}/sync')
    async def sync(provider:str):
        valid_provider(provider);adapter=registry.get(provider)
        if not adapter:raise HTTPException(409,'Configure the server-side Python provider before syncing live readings.')
        try:
            # Validate everything before committing, so an invalid reading cannot partially update the journal.
            readings=await adapter.fetch_metrics(datetime.now(timezone.utc)-timedelta(days=30))
            count=ingest(db,provider,readings)
        except Exception as exc:
            logger.warning('Provider sync failed for %s (%s)',provider,type(exc).__name__)
            raise HTTPException(502,'The provider could not sync. Check the Python provider configuration and authorization.') from exc
        return {'ok':True,'readings':count,'message':f'{count} live readings saved.'}
    @app.get('/api/integrations')
    def integrations():return registry.status()
    @app.get('/api/bridge')
    def bridge():
        return {'token':db.one("SELECT ingest_token FROM profile WHERE id='demo'")['ingest_token'],'endpoint':'http://127.0.0.1:8000/api/ingest/apple_health','example':{'metrics':[{'type':'steps','value':7248,'unit':'steps','timestamp':now(),'external_id':'apple-steps-'+datetime.now(timezone.utc).date().isoformat()}]}}
    @app.post('/api/ingest/simulate')
    def simulate():
        # Demo injection is deliberately prohibited when it could replace a live source's display mode.
        row=db.one("SELECT value FROM app_meta WHERE key='simulation_step'");step=int(row['value']) if row else 0
        source='lumen' if step%2 else 'oura'
        connection=db.one('SELECT * FROM connection WHERE provider=?',(source,))
        if connection['mode']=='live':raise HTTPException(409,'This source is live. Switch it to demo mode before simulating data.')
        r=MetricInput(type='lumen_level' if step%2 else 'sleep_hours',unit='level' if step%2 else 'h',value=5 if step%2 else 5.2,timestamp=datetime.now(timezone.utc),external_id='simulation-'+str(step))
        ingest(db,source,[r],origin='demo')
        db.execute("INSERT INTO app_meta VALUES ('simulation_step',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(str(step+1),))
        return {'ok':True,'message':'Lumen level 5 added. Check the cross-source insight.' if step%2 else 'Oura logged 5.2 hours. Simulate again to add a Lumen reading.'}
    @app.post('/api/ingest/{source}')
    def push(source:str,body:IngestRequest,authorization:str|None=Header(None)):
        authorize_ingest(authorization)
        if source=='apple_health':
            allowed_types={'steps','active_kcal','exercise_min','stand_hours','resting_hr','weight_kg','body_fat_pct'}
            if any(m.type not in allowed_types for m in body.metrics):raise HTTPException(422,'Unsupported Apple Health metric.')
            groups={'apple_watch':[],'renpho_scale':[]}
            for m in body.metrics:groups['renpho_scale' if m.type in ('weight_kg','body_fat_pct') else 'apple_watch'].append(m)
        else:
            valid_provider(source);groups={source:body.metrics}
        count=0
        try:
            for provider,readings in groups.items():
                if readings:count+=ingest(db,provider,readings)
        except ValueError as exc:raise HTTPException(422,str(exc)) from exc
        return {'ok':True,'readings':count}
    @app.put('/api/privacy')
    def privacy(change:PrivacyChange):
        db.execute('UPDATE connection SET allowed=? WHERE provider=?',(int(change.allowed),change.provider));return {'ok':True}
    @app.get('/api/export')
    def export():
        # Exclude ingestion token, secrets, and file bytes.
        return JSONResponse(snapshot(),headers={'Content-Disposition':'attachment; filename="health-twin-journal.json"'})
    @app.delete('/api/privacy')
    def delete_data(body:dict):
        if body.get('confirm')!='DELETE':raise HTTPException(422,'Confirm deletion with DELETE.')
        profile=json.loads(Path(__file__).with_name('seed.json').read_text())['profile'];profile.update(name='Friend',conditions='',meds='',timezone='America/Los_Angeles')
        with db.connect() as c:
            for table in ('metric','body_report','food_log','baseline','nudge','message'):c.execute(f'DELETE FROM {table}')
            c.execute("DELETE FROM app_meta WHERE key='simulation_step'")
            c.execute("UPDATE connection SET status='disconnected',allowed=0,last_synced=NULL")
            c.execute("UPDATE profile SET data=?,ingest_token=?,deleted=1 WHERE id='demo'",(encode(profile),secrets.token_urlsafe(48)))
        if uploads.exists():
            for file in uploads.iterdir():
                if file.is_file():file.unlink()
        return {'ok':True}
    @app.post('/api/chat')
    async def chat(body:ChatInput):
        text=body.message.strip()
        if not text:raise HTTPException(422,'Write a message first.')
        s=db.snapshot();urgent=bool(URGENT.search(text));live=settings.live_ai and not urgent
        demo,sources=demo_reply(text,s)
        if live:sources=[c['provider']+' · authorized readings' for c in s['connections'] if c['allowed'] and c['status']=='connected']
        db.add_message('user',text,[],mode='live' if live else 'demo')
        def event(data):return 'data: '+json.dumps(data,ensure_ascii=False)+'\n\n'
        async def stream():
            content='';yield event({'sources':sources,'mode':'live' if live else 'demo','urgent':urgent})
            try:
                if live:
                    async for chunk in stream_reply(settings,s,text):content+=chunk;yield event({'delta':chunk})
                else:
                    for i in range(0,len(demo),35):
                        chunk=demo[i:i+35];content+=chunk;yield event({'delta':chunk});await asyncio.sleep(.012)
                m=db.add_message('twin',content,sources,'live' if live else 'demo');yield event({'done':True,'message_id':m['id']})
            except asyncio.CancelledError:
                if content:db.add_message('twin',content+'\n\n[Reply interrupted]',sources,'live' if live else 'demo')
                raise
            except Exception:
                yield event({'error':'The AI reply could not complete. Your message is saved; please try again.'})
        return StreamingResponse(stream(),media_type='text/event-stream',headers={'X-Accel-Buffering':'no','Cache-Control':'no-cache'})
    @app.post('/api/reports/renpho/extract')
    async def report_extract(file:UploadFile=File(...)):
        mime=file.content_type
        if mime not in ('image/jpeg','image/png','application/pdf'):raise HTTPException(422,'Choose a JPG, PNG, or PDF.')
        content=await file.read(10*1024*1024+1)
        if len(content)>10*1024*1024:raise HTTPException(413,'Choose a file smaller than 10 MB.')
        magic={'image/jpeg':b'\xff\xd8\xff','image/png':b'\x89PNG\r\n\x1a\n','application/pdf':b'%PDF-'}
        if not content.startswith(magic[mime]):raise HTTPException(422,'The file does not match its reported type.')
        uid=str(uuid.uuid4());uploads.mkdir(parents=True,exist_ok=True)
        (uploads/uid).write_bytes(content);(uploads/uid).chmod(0o600)
        (uploads/(uid+'.json')).write_text(encode({'mime':mime,'filename':file.filename}));(uploads/(uid+'.json')).chmod(0o600)
        result={'upload_id':uid,'filename':file.filename,'mode':'manual','fields':{},'confidence':{},'message':'Automatic extraction is not enabled. Enter and verify the values from your report.'}
        if settings.live_ai:
            try:result.update(await extract_report(settings,content,mime));result.update(mode='extracted',message='Review extracted values. Low-confidence fields are highlighted.')
            except Exception:result['message']='Automatic extraction could not read this file reliably. Please enter the values manually.'
        return result
    @app.get('/api/reports/files/{upload_id}')
    def report_file(upload_id:str):
        try:uuid.UUID(upload_id)
        except ValueError:raise HTTPException(404,'Report not found.')
        info=uploads/(upload_id+'.json');file=uploads/upload_id
        if not file.exists() or not info.exists():raise HTTPException(404,'Report not found.')
        mime=json.loads(info.read_text())['mime']
        return FileResponse(file,media_type=mime,headers={'X-Content-Type-Options':'nosniff'})
    @app.post('/api/reports/renpho/confirm',status_code=201)
    def report_confirm(body:ConfirmReport):
        if body.upload_id and not (uploads/body.upload_id).is_file():raise HTTPException(422,'Upload the report again before confirming.')
        fields=body.fields.model_dump(mode='json');s=db.snapshot()
        previous=next((r for r in s['reports'] if r['test_date']<fields['test_date']),None)
        text=report_analysis(fields,previous);rid=str(uuid.uuid4());origin='demo' if body.mode=='sample' else 'manual'
        readings=[MetricInput(type=k,unit=METRICS[k][0],value=v,timestamp=fields['test_date']+'T23:59:00+00:00',external_id=f'{rid}:{k}') for k,v in fields.items() if k in SOURCES['renpho_scale'] and v is not None]
        # Validate all fields before writing the report. Raw segmental data stays in raw_json.
        with db.connect() as c:
            c.execute('INSERT INTO body_report VALUES (?,?,?,?,?,?,?)',(rid,fields['test_date'],encode(fields),text,1,origin,body.upload_id))
            for m in readings:
                c.execute('INSERT INTO metric VALUES (?,?,?,?,?,?,?)',(str(uuid.uuid4()),m.timestamp.isoformat(),'renpho_scale',m.type,m.value,m.unit,origin))
            c.execute("UPDATE connection SET status='connected',last_synced=? WHERE provider='renpho_scale'",(now(),))
        if allowed(db.snapshot(),'renpho_scale'):db.add_message('twin',text,['RENPHO · '+fields['test_date']],'report')
        update_baselines(db);return {'id':rid,'analysis':text,'ok':True}
    @app.get('/api/reports/sample')
    def sample_report():
        report=json.loads(Path(__file__).with_name('seed.json').read_text())['reports'][0]
        report['test_date']=datetime.now(timezone.utc).date().isoformat()
        return {'fields':report,'confidence':{k:1 for k in report},'mode':'sample'}
    # A production frontend build can be served by this same Python process.
    dist=Path(__file__).resolve().parents[2]/'frontend'/'dist'
    if dist.is_dir():
        if (dist/'assets').is_dir():app.mount('/assets',StaticFiles(directory=dist/'assets'),name='assets')
        app.mount('/art',StaticFiles(directory=dist/'art'),name='art')
        app.mount('/fonts',StaticFiles(directory=dist/'fonts'),name='fonts')
        @app.get('/favicon.svg',include_in_schema=False)
        def favicon():return FileResponse(dist/'favicon.svg')
        for path in ('/','/twin','/settings'):
            app.add_api_route(path,lambda:FileResponse(dist/'index.html'),methods=['GET'],include_in_schema=False)
    return app

app=create_app()
