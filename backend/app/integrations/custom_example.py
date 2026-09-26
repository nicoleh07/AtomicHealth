"""Example only. Replace the import and field mapping with your actual client.

Set HEALTH_TWIN_PROVIDER_MODULE=app.integrations.custom_adapter after
creating custom_adapter.py. Keep OAuth and credentials inside Python.
"""
from datetime import datetime
from app.models import MetricInput

class YourPythonAdapter:
    def __init__(self,client):self.client=client
    async def fetch_metrics(self,since:datetime):
        # For a synchronous client use: await asyncio.to_thread(client.readings, since)
        readings=await self.client.readings(since=since)
        return [MetricInput(type=r['metric_type'],value=r['value'],unit=r['unit'],
                    timestamp=r['timestamp'],external_id=str(r['id'])) for r in readings]

def get_provider(provider_id):
    # Return your configured adapter for the selected provider, or None.
    return None
