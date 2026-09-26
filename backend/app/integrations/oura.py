from datetime import datetime, timezone
import httpx
from app.models import MetricInput

class OuraProvider:
    """Uses a server-side OAuth access token. Never asks the browser for secrets."""
    def __init__(self,access_token): self.access_token=access_token
    async def fetch_metrics(self,since:datetime):
        rows=[]
        async with httpx.AsyncClient(base_url='https://api.ouraring.com',timeout=30,headers={'Authorization':f'Bearer {self.access_token}'}) as client:
            for endpoint in ('sleep','daily_sleep','daily_readiness'):
                params={'start_date':since.date().isoformat(),'end_date':datetime.now(timezone.utc).date().isoformat()}
                for page in range(100):
                    response=await client.get(f'/v2/usercollection/{endpoint}',params=params)
                    if response.status_code in (401,403):raise ValueError('Oura authorization expired or lacks the required scope. Refresh it in your Python integration.')
                    response.raise_for_status()
                    payload=response.json()
                    for item in payload.get('data',[]):
                        ts=item.get('bedtime_end') or item['day']+'T12:00:00+00:00'
                        if endpoint=='sleep':
                            if item.get('type') not in (None,'long_sleep'):continue
                            fields=[('sleep_hours','h',item.get('total_sleep_duration'),1/3600),('hrv','ms',item.get('average_hrv'),1),('resting_hr','bpm',item.get('lowest_heart_rate'),1)]
                        elif endpoint=='daily_sleep':fields=[('sleep_score','score',item.get('score'),1)]
                        else:fields=[('readiness','score',item.get('score'),1)]
                        for kind,unit,value,multiplier in fields:
                            if value is not None:rows.append(MetricInput(type=kind,unit=unit,value=round(value*multiplier,2),timestamp=ts,external_id=f'{endpoint}:{item["id"]}:{kind}'))
                    cursor=payload.get('next_token')
                    if not cursor:break
                    params['next_token']=cursor
                else:raise ValueError('Oura returned too many pages; shorten the sync interval.')
        return rows
