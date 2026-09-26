# Twin — React + Python

A working local health companion with a React/TypeScript frontend, FastAPI backend, and SQLite persistence. The illustrated paper-and-avocado design is carried over from the original prototype.

## Start the app

Requirements: Node.js 22.12+ (or 24 LTS) and [uv](https://docs.astral.sh/uv/). Setup installs Python 3.12 when needed.

```sh
./scripts/setup.sh
npm run dev
```

- App: http://127.0.0.1:5174
- Python API: http://127.0.0.1:8000
- Interactive API documentation: http://127.0.0.1:8000/docs
- Stop both development servers with Ctrl+C.

The dependencies are already installed in the delivered local checkout. There, `npm run dev` is enough when ports 5174 and 8000 are free.

To build and serve React directly from Python:

```sh
./scripts/start-production.sh
```

Then open http://127.0.0.1:8000. This remains a local, single-user app; the script name describes the compiled frontend, not an Internet-ready deployment.

## Where the code and database live

| Path | Purpose |
|---|---|
| `frontend/src/App.tsx` | React shell, navigation, API loading and refresh |
| `frontend/src/Dashboard.tsx` | Dashboard cards, range controls, charts and insights |
| `frontend/src/Twin.tsx` | Streaming chat and memory panel |
| `frontend/src/Settings.tsx` | Connections, profile, Twin preferences and privacy |
| `frontend/src/dialogs.tsx` | Meal logging and report upload/review |
| `frontend/src/styles.css` | Shared visual style and responsive layouts |
| `frontend/src/api.ts` | React-to-Python API client |
| `backend/app/main.py` | FastAPI routes and application factory |
| `backend/app/database.py` | SQLite schema, transactions and initial demo seeding |
| `backend/app/models.py` | Validated inputs and canonical metric units |
| `backend/app/services.py` | Ingestion, baselines, insights, safety and nudges |
| `backend/app/ai.py` | Optional Claude streaming and document extraction |
| `backend/app/integrations/` | Python provider interface, Oura adapter and extension example |
| `backend/data/health-twin.sqlite3` | Actual database, created at first startup |
| `backend/data/uploads/` | Locally saved report files |
| `backend/tests/test_api.py` | Isolated API/integration tests |

The database uses SQLite directly through Python's `sqlite3`; no browser local storage is used for authoritative data. Open the database with a SQLite viewer, or inspect it through the API. Do not edit it while an import or write is running. `backend/data/` and secret environment files are excluded from version control and the source archive.

## What works now

- Three React views: Dashboard, Twin, and Settings, with the original visual style.
- Thirty days of clearly labeled sample metrics, three meals and two body reports on first startup.
- Durable profile settings, meal logs, source permissions, reports and message history.
- Server-streamed, data-grounded demo chat; optional live Claude streaming.
- Source privacy filtering applied server-side to new replies, insights, and AI context.
- Upload a JPG, PNG or PDF, review values, and confirm a structured body report. Without AI credentials, the app explicitly asks for manual entry; it never pretends to extract values.
- Monitoring runs after ingestion. Nudges have cited sources, quiet hours, deduplication, and a maximum of three per local day.
- Authenticated JSON ingestion for real source data, including an Apple Health bridge.
- A live Oura pull adapter that uses an OAuth access token configured only on the Python server.
- Source mode separation: a live provider does not silently fill gaps with demo readings.
- Export and deletion, including deletion of uploaded reports and rotation of the ingestion token.
- Urgent-symptom banners and server guardrails for chest pain, breathing difficulty and self-harm language.

The illustrative balance score is calculated from recorded sleep and activity. It is not a validated health assessment. Device body-composition labels are treated as estimates, not diagnoses. The app does not prescribe medication or a calorie deficit.

## Connect real Python integrations

No pre-existing user Python project is required. Two supported paths are ready.

### 1. Push normalized readings

In Settings → Connections → Apple Watch → Apple Health bridge, copy the ingestion token. This token stays on the Python server and is shown only through the local setup flow. Send it in an `Authorization: Bearer ...` header.

```python
import os
from datetime import datetime, timezone
import httpx

response = httpx.post(
    'http://127.0.0.1:8000/api/ingest/apple_health',
    headers={'Authorization': f'Bearer {os.environ["TWIN_INGEST_TOKEN"]}'},
    json={'metrics': [{
        'external_id': 'steps-2026-09-26',
        'type': 'steps',
        'value': 7248,
        'unit': 'steps',
        'timestamp': datetime.now(timezone.utc).isoformat(),
    }]},
)
response.raise_for_status()
```

Generic endpoints: `POST /api/ingest/oura`, `/renpho_scale`, `/apple_watch`, `/lumen`. Apple Health accepts steps, active calories, exercise minutes, stand hours, resting heart rate, weight and body-fat percentage. Food entries use `POST /api/food-log` from the local application.

Use stable `external_id` values to update a reading idempotently. Timestamps, source/type compatibility, numeric bounds and canonical units are validated before ingestion. Examples: weight must be `weight_kg` in `kg`; sleep duration is `sleep_hours` in `h`; body fat is `body_fat_pct` in `%`.

A Shortcut running on an iPhone cannot reach the computer through `127.0.0.1`. A reachable HTTPS deployment with access control is required before using it from another device. This local app deliberately binds to loopback and rejects unrecognized Host and Origin headers. It is not an unauthenticated LAN or public health-data server.

### 2. Pull through a Python provider adapter

Implement the protocol in `backend/app/integrations/base.py`:

```python
from datetime import datetime
from app.models import MetricInput

class MyProvider:
    async def fetch_metrics(self, since: datetime) -> list[MetricInput]:
        # Call your Python integration, then normalize its response.
        return []

def get_provider(provider_id: str):
    if provider_id == 'lumen':
        return MyProvider()
    return None
```

Save the module as `backend/app/integrations/custom_adapter.py` and set this in `backend/.env`:

```dotenv
HEALTH_TWIN_PROVIDER_MODULE=app.integrations.custom_adapter
```

Restart Python. The provider card's **Sync live** action will become available. `POST /api/connections/{provider}/sync` calls the adapter and saves its readings. For a synchronous client, wrap blocking calls with `await asyncio.to_thread(...)`.

The included Oura adapter can use an OAuth access token from your Oura authorization flow:

```dotenv
OURA_ACCESS_TOKEN=your_oauth_access_token
```

It retrieves sleep duration, HRV, resting heart rate, sleep score and readiness; follows pagination; validates the result; and stores readings idempotently. OAuth authorization and automatic token refresh are not implemented in this local app. Expired credentials fail visibly instead of switching to sample data. See [Oura's official authentication documentation](https://cloud.ouraring.com/docs/authentication).

## Enable live AI and report extraction

Create `backend/.env` from `.env.example`, then set:

```dotenv
ANTHROPIC_API_KEY=your_key
ANTHROPIC_MODEL=claude-sonnet-4-5-20250929
AI_ENABLED=true
```

Restart the Python server. Keys are never included in the React bundle. Enabling AI sends the currently permitted health context to Claude, and submitted report files to Claude for extraction. The Privacy screen and upload flow disclose this behavior. If the model ID is unavailable on your account, change `ANTHROPIC_MODEL` to a supported model. See the [Claude Messages API](https://platform.claude.com/docs/en/api/http/messages).

No live provider credentials or AI key are supplied. The live HTTP boundaries are tested with mocks; they have not been verified against a personal device account.

## Development and validation

```sh
npm run build
npm test
```

Backend tests use separate temporary databases and never modify the running journal. They cover persistence, validation, source permission filtering, urgent symptoms, streaming, ingestion authentication/idempotency, adapter failure, live/demo separation, uploads, report confirmation, nudge limits and deletion.

The earlier single HTML file is retained separately as a prototype. This React + Python project is the current implementation. It does not automatically import browser-local data from the old file; keep an export from that prototype if needed.

## Scope

This is a local single-user development app. It does not implement production authentication, multi-user isolation, HIPAA controls, medication adherence, a signed iOS Shortcut, or provider OAuth for every device. Live provider sync is explicit; there is no unattended cloud scheduler. Add deployment authentication and access controls before exposing it beyond the local computer.
