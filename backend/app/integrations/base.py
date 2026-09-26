"""Implement this protocol around an existing Python client."""
from typing import Protocol
from datetime import datetime
from app.models import MetricInput

class Provider(Protocol):
    async def fetch_metrics(self, since: datetime) -> list[MetricInput]:
        """Return canonical readings. external_id makes repeated syncs idempotent."""
        ...
