from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path
import json, sqlite3, uuid, secrets, os

SCHEMA='''
CREATE TABLE IF NOT EXISTS profile(id TEXT PRIMARY KEY, data TEXT NOT NULL, ingest_token TEXT NOT NULL, deleted INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS metric(id TEXT PRIMARY KEY,timestamp TEXT NOT NULL,source TEXT NOT NULL,type TEXT NOT NULL,value REAL NOT NULL,unit TEXT NOT NULL,origin TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_metric_source_type_time ON metric(source,type,timestamp);
CREATE TABLE IF NOT EXISTS body_report(id TEXT PRIMARY KEY,test_date TEXT NOT NULL,raw_json TEXT NOT NULL,analysis_md TEXT NOT NULL,confirmed_by_user INTEGER NOT NULL,origin TEXT NOT NULL,file_id TEXT);
CREATE TABLE IF NOT EXISTS food_log(id TEXT PRIMARY KEY,timestamp TEXT NOT NULL,data TEXT NOT NULL,origin TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS baseline(type TEXT NOT NULL,source TEXT NOT NULL,avg_14d REAL NOT NULL,std_14d REAL NOT NULL,updated_at TEXT NOT NULL,PRIMARY KEY(type,source));
CREATE TABLE IF NOT EXISTS nudge(id TEXT PRIMARY KEY,rule_id TEXT NOT NULL,message TEXT NOT NULL,cited_metrics TEXT NOT NULL,status TEXT NOT NULL,created_at TEXT NOT NULL,day_key TEXT NOT NULL,UNIQUE(rule_id,day_key));
CREATE TABLE IF NOT EXISTS connection(provider TEXT PRIMARY KEY,status TEXT NOT NULL,last_synced TEXT,allowed INTEGER NOT NULL DEFAULT 1,mode TEXT NOT NULL DEFAULT 'demo');
CREATE TABLE IF NOT EXISTS message(id TEXT PRIMARY KEY,role TEXT NOT NULL,content TEXT NOT NULL,cited_sources TEXT NOT NULL,created_at TEXT NOT NULL,mode TEXT NOT NULL DEFAULT 'demo');
CREATE INDEX IF NOT EXISTS idx_message_time ON message(created_at);
CREATE TABLE IF NOT EXISTS app_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
'''
def now(): return datetime.now(timezone.utc).isoformat()
def encode(v): return json.dumps(v,ensure_ascii=False)
class Database:
    def __init__(self,path:Path): self.path=Path(path)
    @contextmanager
    def connect(self):
        con=sqlite3.connect(self.path,timeout=15)
        con.row_factory=sqlite3.Row
        con.execute('PRAGMA foreign_keys=ON')
        try:
            yield con
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally: con.close()
    def initialize(self,seed_demo=True):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.connect() as c:
            c.execute('PRAGMA journal_mode=WAL')
            c.executescript(SCHEMA)
            c.execute('BEGIN IMMEDIATE')
            if c.execute("SELECT 1 FROM app_meta WHERE key='initialized'").fetchone(): return
            seed=json.loads((Path(__file__).with_name('seed.json')).read_text())
            p=seed['profile']|{'timezone':'America/Los_Angeles'}
            c.execute('INSERT INTO profile VALUES (?,?,?,0)',('demo',encode(p),secrets.token_urlsafe(48)))
            for co in seed['connections']:
                c.execute('INSERT INTO connection VALUES (?,?,?,?,?)',(co['provider'],'connected' if seed_demo else 'disconnected',now() if seed_demo else None,1,'demo'))
            if seed_demo:
                today=datetime.now(timezone.utc).date()
                delta=today-datetime.fromisoformat('2026-09-26').date()
                for m in seed['metrics']:
                    ts=datetime.fromisoformat(m['timestamp'].replace('Z','+00:00'))+delta
                    c.execute('INSERT INTO metric VALUES (?,?,?,?,?,?,?)',(m['id'],ts.isoformat(),m['source'],m['type'],m['value'],m['unit'],'demo'))
                for m in seed['meals']:
                    m['timestamp']=(datetime.fromisoformat(m['timestamp'].replace('Z','+00:00'))+delta).isoformat()
                    c.execute('INSERT INTO food_log VALUES (?,?,?,?)',(m['id'],m['timestamp'],encode(m),'demo'))
                for report in seed['reports']:
                    report['test_date']=(datetime.fromisoformat(report['test_date'])+delta).date().isoformat()
                    c.execute('INSERT INTO body_report VALUES (?,?,?,?,?,?,?)',(report['id'],report['test_date'],encode(report),'Sample device report; not a diagnosis.',1,'demo',None))
            c.execute("INSERT INTO app_meta VALUES ('initialized','1')")
        os.chmod(self.path,0o600)
    def rows(self,sql,args=()):
        with self.connect() as c:return [dict(r) for r in c.execute(sql,args).fetchall()]
    def one(self,sql,args=()):
        rows=self.rows(sql,args)
        return rows[0] if rows else None
    def execute(self,sql,args=()):
        with self.connect() as c:c.execute(sql,args)
    def profile(self):return json.loads(self.one("SELECT data FROM profile WHERE id='demo'")['data'])
    def add_message(self,role,content,sources=(),mode='demo'):
        m={'id':str(uuid.uuid4()),'role':role,'content':content,'cited_sources':list(sources),'created_at':now(),'mode':mode}
        self.execute('INSERT INTO message VALUES (?,?,?,?,?,?)',(m['id'],role,content,encode(sources),m['created_at'],mode))
        return m
    def snapshot(self):
        connections=self.rows('SELECT * FROM connection ORDER BY provider')
        # A live provider never silently falls back to demo metrics.
        metrics=self.rows("SELECT m.* FROM metric m JOIN connection c ON c.provider=m.source WHERE c.status='connected' AND (m.origin=c.mode OR m.origin='manual') ORDER BY m.timestamp,m.rowid")
        food_connection=next(c for c in connections if c['provider']=='renpho_food')
        day=datetime.now(timezone.utc).date().isoformat()
        meals=self.rows('SELECT data FROM food_log WHERE substr(timestamp,1,10)=? AND (origin=? OR origin=?) ORDER BY timestamp',(day,food_connection['mode'],'manual')) if food_connection['status']=='connected' else []
        reports=self.rows('SELECT * FROM body_report ORDER BY test_date DESC,rowid DESC')
        return {'profile':self.profile(),'metrics':metrics,'meals':[json.loads(m['data']) for m in meals],'connections':connections,
            'messages':[m|{'cited_sources':json.loads(m['cited_sources'])} for m in self.rows('SELECT * FROM (SELECT * FROM message ORDER BY created_at DESC,rowid DESC LIMIT 200) ORDER BY created_at')],
            'nudges':[n|{'cited_metrics':json.loads(n['cited_metrics'])} for n in self.rows('SELECT * FROM nudge ORDER BY created_at DESC LIMIT 20')],
            'reports':[json.loads(r['raw_json'])|{'id':r['id'],'analysis_md':r['analysis_md'],'report_mode':r['origin']} for r in reports],
            'deleted':bool(self.one("SELECT deleted FROM profile WHERE id='demo'")['deleted'])}
