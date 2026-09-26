from dataclasses import dataclass
from pathlib import Path
import os
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / '.env')

_data_root = Path('/tmp/atomic-health') if os.getenv('VERCEL') else ROOT / 'data'

@dataclass
class Settings:
    database_path: Path = _data_root / os.getenv('DATABASE_PATH', 'health-twin.sqlite3')
    seed_demo: bool = os.getenv('SEED_DEMO', 'true').lower() == 'true'
    app_origin: str = os.getenv('APP_ORIGIN', 'http://127.0.0.1:5174')
    anthropic_api_key: str = os.getenv('ANTHROPIC_API_KEY', '')
    anthropic_model: str = os.getenv('ANTHROPIC_MODEL', 'claude-sonnet-4-5-20250929')
    ai_enabled: bool = os.getenv('AI_ENABLED', 'false').lower() == 'true'
    oura_access_token: str = os.getenv('OURA_ACCESS_TOKEN', '')
    provider_module: str = os.getenv('HEALTH_TWIN_PROVIDER_MODULE', '')

    @property
    def live_ai(self):
        return bool(self.anthropic_api_key)
