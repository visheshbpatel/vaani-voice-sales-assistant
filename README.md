# Vaani

## What it does

Vaani lets a prospect hold a real-time spoken conversation with an AI sales
assistant for Surefy. It is an unofficial internship prototype and does not
integrate with Surefy systems.

## Run locally

Requirements: Python 3.12 and a PostgreSQL database (SQLite is suitable for
local-only development).

```powershell
uv sync
Copy-Item .env.example .env
uv run alembic upgrade head
uv run uvicorn app.main:app --reload
```

Open `http://127.0.0.1:8000/`. The browser needs microphone permission and the
server needs `GEMINI_API_KEY` for a real voice conversation.

Run the relevant tests with:

```powershell
pytest
```

Tests mock Gemini and recording storage; they do not call paid APIs.

## Deploy on Render

Create a Render web service from this repository. `render.yaml` installs the
locked dependencies, runs Alembic migrations, and starts the app. Add the
database, Gemini, history, and storage environment variables marked
`sync: false` in Render; recordings are disabled locally on the deployed
service. Free tiers sleep when idle, so the first request after inactivity may
be slow.
