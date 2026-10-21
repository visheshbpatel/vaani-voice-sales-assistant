# Vaani

Vaani is an unofficial internship prototype for the Sankar Group: a real-time
AI voice sales assistant for Surefy. It is not an official Surefy product and
does not integrate with Surefy systems.

## Local setup

Requirements: Python 3.12 and a PostgreSQL database (SQLite is suitable for
local-only development).

```powershell
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
alembic upgrade head
uvicorn app.main:app --reload
```

Open `http://127.0.0.1:8000/`. The browser needs microphone permission and the
server needs `GEMINI_API_KEY` for a real voice conversation.

Run the relevant tests with:

```powershell
pytest
```

Tests mock Gemini and recording storage; they do not call paid APIs.

## Services and environment

Copy [.env.example](.env.example) and set:

- **Google AI Studio**: `GEMINI_API_KEY` is required for the Gemini Live API.
- **Neon**: `DATABASE_URL` points to the PostgreSQL connection string. SQLite is
  the default only when no URL is configured.
- **Supabase Storage (S3-compatible)**: recordings use Supabase Storage because Cloudflare R2 required a payment card.
  Vaani stores only the storage object key and metadata in the database, then
  generates five-minute presigned playback URLs. Without configured storage locally, recordings
  are stored in the ignored `recordings/` directory.

Keep all secrets server-side. Do not put them in browser code. If the app is
served from a separate frontend origin, configure that origin at the hosting
layer and allow it in the API's CORS policy; the included Render deployment
serves the frontend and API from one origin and does not need cross-origin
browser requests.

## Render

`render.yaml` runs migrations and starts the web service with:

```text
alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

Create or connect a Neon database, create a private R2 bucket, and provide the
environment variables listed in `render.yaml` in Render. Deployment status is
not claimed here; verify the Render service and `/health` endpoint after
deploying.

## History and recording behavior

The history page lists saved calls and provides a detail view with ID, start
time, duration, status, summary, transcript, and recording playback. A missing
or failed recording is shown as unavailable without preventing other records
from rendering. Recording requires explicit consent before audio is saved.

Surefy product facts are maintained locally in
[`app/services/product_knowledge.py`](app/services/product_knowledge.py).
Unknown facts are not invented and are reported as needing confirmation from
Surefy.
