from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
import json, re, uuid, math
from .database import now, encode
from .models import MetricInput, SOURCES

URGENT=re.compile(r"chest\s*(pain|pressure|tightness)|trouble\s*breathing|can[’']?t\s*breathe|cannot\s*breathe|shortness of breath|suicid|self[- ]?harm|kill\s*myself|end\s*my\s*life|hurt\s*myself|want\s*to\s*die",re.I)
EMERGENCY='Please get immediate support. For chest pain, trouble breathing, or immediate danger, call 911 in the U.S. or your local emergency number. For suicidal thoughts or self-harm in the U.S., call or text 988. Twin cannot assess an emergency or provide crisis care.'
LABELS={'oura':'Oura','renpho_scale':'RENPHO Body','renpho_food':'RENPHO Food','apple_watch':'Apple Watch','lumen':'Lumen'}
def latest(s,kind):return next((m['value'] for m in reversed(s['metrics']) if m['type']==kind),None)
def allowed(s,source):return any(c['provider']==source and c['status']=='connected' and c['allowed'] for c in s['connections'])
def authorized(s):
    permitted=[c['provider'] for c in s['connections'] if c['status']=='connected' and c['allowed']]
    return s|{'metrics':[m for m in s['metrics'] if m['source'] in permitted], 'meals':s['meals'] if 'renpho_food' in permitted else [],'reports':[r for r in s['reports'] if r.get('report_mode') in (next(c['mode'] for c in s['connections'] if c['provider']=='renpho_scale'),'manual')] if 'renpho_scale' in permitted else [],'messages':[],'nudges':[]}
def sources_for(s,ids):
    return [f'{LABELS[k]} · latest recorded readings' for k in ids if allowed(s,k)]
def insights(s):
    s=authorized(s);out=[]
    sleep=latest(s,'sleep_hours');level=latest(s,'lumen_level')
    if sleep is not None and level is not None:
        out.append({'id':'sleep-fuel','title':'Rest well. Fuel well.' if sleep>=6 else 'A little rest goes a long way','text':f'{sleep:g} hours of sleep and a Lumen level of {level:g}. These observations are worth comparing; they do not establish what caused a change.','sources':['Oura','Lumen'],'prompt':'How are my sleep and metabolism connected?','color':'blue'})
    muscle=latest(s,'muscle_kg')
    if muscle is not None and allowed(s,'apple_watch'):
        out.append({'id':'muscle','title':'Small steps, steady progress','text':f'Your latest muscle reading is {muscle:g} kg. Keep an eye on the longer trend alongside your movement.','sources':['RENPHO','Apple Watch'],'prompt':'How is my body composition changing?','color':'peach'})
    if allowed(s,'renpho_food'):
        p=sum(m['protein_g'] for m in s['meals']);goal=s['profile']['protein_goal']
        out.append({'id':'protein','title':'Room for a protein boost' if p<goal else 'A little win for today','text':f'Today’s log has {p:g}g of protein against your saved {goal:g}g goal. Consider your whole day, one meal at a time.','sources':['RENPHO Food'],'prompt':'What should I focus on for dinner?','color':'yellow'})
    return out

def ingest(db,source,readings,origin='live'):
    normalized=[r if isinstance(r,MetricInput) else MetricInput.model_validate(r) for r in readings]
    if any(r.type not in SOURCES[source] for r in normalized):raise ValueError('This metric does not belong to the selected source')
    with db.connect() as c:
        for r in normalized:
            identity=r.external_id or f'{r.timestamp.isoformat()}:{r.type}'
            mid=str(uuid.uuid5(uuid.NAMESPACE_URL,f'{source}:{origin}:{identity}'))
            c.execute('INSERT INTO metric VALUES (?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET timestamp=excluded.timestamp,value=excluded.value,unit=excluded.unit',
                (mid,r.timestamp.isoformat(),source,r.type,r.value,r.unit,origin))
        if normalized:
            c.execute("UPDATE connection SET status='connected',mode=?,last_synced=? WHERE provider=?",('live' if origin=='live' else 'demo',now(),source))
    update_baselines(db)
    monitor(db)
    return len(normalized)

def update_baselines(db):
    s=db.snapshot();groups={}
    # Compare against calendar-day aggregates in the previous fourteen days.
    for m in s['metrics']:
        key=(m['source'],m['type']);groups.setdefault(key,{}).setdefault(m['timestamp'][:10],[]).append(m['value'])
    with db.connect() as c:
        c.execute('DELETE FROM baseline')
        for (source,kind),days in groups.items():
            latest_day=max(days);cutoff=(datetime.fromisoformat(latest_day)-timedelta(days=14)).date().isoformat()
            means=[sum(v)/len(v) for d,v in days.items() if cutoff<=d<latest_day]
            if not means:continue
            avg=sum(means)/len(means);std=math.sqrt(sum((v-avg)**2 for v in means)/len(means))
            c.execute('INSERT INTO baseline VALUES (?,?,?,?,?)',(kind,source,avg,std,now()))

def monitor(db,at=None):
    s=authorized(db.snapshot());p=s['profile'];at=at or datetime.now(timezone.utc);local=at.astimezone(ZoneInfo(p.get('timezone','America/Los_Angeles')))
    start,end=p['quiet_start'],p['quiet_end'];quiet=(local.hour>=start or local.hour<end) if start>end else start<=local.hour<end
    if quiet or p['nudge_freq']=='off':return
    sleep=latest(s,'sleep_hours');level=latest(s,'lumen_level');candidates=[]
    if sleep is not None and sleep<6 and level is not None and level>=4:
        candidates.append(('sleep-fuel',f'Your latest sleep was {sleep:g}h and Lumen level {level:g}. These readings occurred together; they do not establish a cause. Consider a protein source with breakfast and time to wind down tonight.',['Oura','Lumen']))
    nights={m['timestamp'][:10]:m['value'] for m in s['metrics'] if m['type']=='sleep_hours'}
    if len(nights)>=2 and all(nights[d]<6 for d in sorted(nights)[-2:]):
        candidates.append(('short-sleep','Two recent recorded nights were below six hours. Make a little more room for rest tonight.',['Oura']))
    if local.hour>=18 and allowed(s,'renpho_food'):
        protein=sum(m['protein_g'] for m in s['meals'])
        if protein<p['protein_goal']:candidates.append(('protein',f'Today’s log has {protein:g}g of your {p["protein_goal"]:g}g protein goal. Consider a protein source with your next meal.',['RENPHO Food']))
    activity=latest(s,'active_kcal')
    if local.hour>=16 and activity is not None and activity<p['move_goal']/2:candidates.append(('move',f'Your latest activity is {activity:g} of {p["move_goal"]:g} active calories. If you feel well, a comfortable walk could be a next step.',['Apple Watch']))
    # The write lock makes deduplication and the daily cap atomic across requests.
    with db.connect() as c:
        c.execute('BEGIN IMMEDIATE');day=local.date().isoformat();count=c.execute('SELECT COUNT(*) FROM nudge WHERE day_key=?',(day,)).fetchone()[0]
        for rule,text,sources in candidates:
            if count>=3:break
            cursor=c.execute('INSERT OR IGNORE INTO nudge VALUES (?,?,?,?,?,?,?)',(str(uuid.uuid4()),rule,text,encode(sources),'sent',at.isoformat(),day))
            if cursor.rowcount:
                c.execute('INSERT INTO message VALUES (?,?,?,?,?,?)',(str(uuid.uuid4()),'twin',text,encode(sources),at.isoformat(),'check-in'));count+=1

def demo_reply(text,s):
    s=authorized(s);p=s['profile']
    if URGENT.search(text):return EMERGENCY,['NIMH · Immediate help']
    if re.search('med|dose|prescri|diagnos|disease',text,re.I):
        return f'Your medication notes say “{p["meds"] or "None added"}”. I don’t have a dose-adherence log, so I can’t tell whether you missed a dose. I can’t diagnose or change medication. Ask your clinician or pharmacist about treatment or dosing.',['Your profile']
    groups=[(r'sleep|rest|tired|metabol|fuel','oura'),(r'body|weight|muscle|fat|report','renpho_scale'),(r'food|dinner|protein|breakfast|eat|meal|nutrition|calori','renpho_food'),(r'move|walk|step|workout|exercise','apple_watch')]
    source=next((source for pattern,source in groups if re.search(pattern,text,re.I)),None)
    if source and not allowed(s,source):return f'I don’t have access to your {LABELS[source]} readings. Connect that source and enable it in Privacy to explore it together.',[]
    if source=='oura':
        rows=[m for m in s['metrics'] if m['type']=='sleep_hours'][-7:]
        if not rows:return 'No sleep-duration readings are available yet. I won’t guess them.',[]
        avg=sum(m['value'] for m in rows)/len(rows)
        answer=f'Your latest recorded sleep was {rows[-1]["value"]:g} hours. Your last {len(rows)} recorded nights average {avg:.1f} hours.'
        if allowed(s,'lumen') and latest(s,'lumen_level') is not None:answer+=f' Your latest Lumen reading is level {latest(s,"lumen_level"):g}. These readings don’t establish what caused a change.'
        return answer+'\n\nOne little focus: make time for a comfortable wind-down routine tonight. How rested do you feel?',sources_for(s,['oura','lumen'])
    if source=='renpho_scale':
        pieces=[f'{label}: {latest(s,k):g}{u}' for k,label,u in [('weight_kg','Weight',' kg'),('body_fat_pct','Body fat','%'),('muscle_kg','Muscle',' kg')] if latest(s,k) is not None]
        return ('Your latest readings are '+', '.join(pieces)+'.' if pieces else 'No body readings are available yet.')+'\n\nConsistent weigh-in conditions make trends easier to compare. Device estimates are not diagnoses.',sources_for(s,[source])
    if source=='renpho_food':
        protein=sum(m['protein_g'] for m in s['meals']);kcal=sum(m['kcal'] for m in s['meals'])
        return f'You’ve logged {kcal:g} kcal and {protein:g}g protein today, against your saved {p["protein_goal"]:g}g protein goal.\n\nFor a nourishing meal, you could pair a protein source such as tofu, chicken, or beans with vegetables and a grain you enjoy. Your saved goal is a preference, not a clinical recommendation.',sources_for(s,[source])+['Your saved goals']
    if source=='apple_watch':
        steps=latest(s,'steps');cal=latest(s,'active_kcal')
        return f'Your recorded steps: {steps if steps is not None else "not available"}. Active calories: {cal if cal is not None else "not available"}. Your move goal is {p["move_goal"]:g} kcal.\n\nIf you feel well, a comfortable walk can be a nice next step.',sources_for(s,[source])
    return f'Hi {p["name"]}, I’m {p["twin_name"]}. In demo mode, I can explain recorded sleep, body, nutrition, and activity data. For that particular question, I don’t have enough information to give a grounded answer.',['Your profile']

def report_analysis(r,previous):
    def delta(k):return f'{r[k]-previous[k]:+.2f}' if previous and isinstance(previous.get(k),(float,int)) else 'not available'
    flags=[f'{k.replace("_"," ")}: {v.get("label")}' for k,v in (r.get('evaluations') or {}).items() if isinstance(v,dict) and re.search(r'high|low|over',str(v.get('label','')),re.I)]
    return (f'Your reviewed report from {r["test_date"]} records {r["weight_kg"]:g} kg, {r["body_fat_pct"]:g}% body fat, and {r["muscle_kg"]:g} kg of muscle. Device estimates are not diagnoses.\n\n'
        f'Compared with the earlier report: weight {delta("weight_kg")} kg; body fat {delta("body_fat_pct")} percentage points; muscle {delta("muscle_kg")} kg; visceral fat {delta("visceral_fat")}.\n\n'
        f'Device flags: {"; ".join(flags) if flags else "No out-of-range evaluations were entered. This does not establish that all values are within range."}\n\n'
        f'Your recorded BMR is {r["bmr_kcal"]:g} kcal, which is not your total daily energy need. This report does not set a calorie deficit or a weight-loss prescription.\n\n'
        'Three next steps: keep weigh-in conditions consistent; discuss personal goals with a qualified professional; compare a new report in about two weeks.')
