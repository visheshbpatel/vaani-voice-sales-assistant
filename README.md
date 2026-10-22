# Vaani — AI Voice Sales Assistant

Vaani is an unofficial Sankar Group internship prototype where a prospect has a spoken conversation with an AI sales assistant for Surefy. It is not an official Surefy product and does not integrate with Surefy systems.

## Features

- **Real-time voice conversation:** Microphone input and spoken responses from Gemini Live.
- **Live transcript:** Customer and assistant text during the call.
- **Call controls:** Start and end calls, elapsed timer, and connection status.
- **Recording with consent:** Audio is saved only after explicit consent.
- **AI-generated summary:** Gemini summarizes the conversation after the call.
- **Sales outcome:** The summary assigns a controlled sales status.
- **Call history:** Timestamps, duration, transcript, summary, lead details, sales status, and recording playback when available.

## How It Works

1. The browser captures microphone audio and sends it to a FastAPI WebSocket.
2. The server forwards the audio to Gemini Live.
3. Text and response audio are sent back to the browser.
4. With consent, recordings are saved to Supabase Storage.
5. After the call, Gemini generates a summary and sales status.
6. Call records and metadata are stored in PostgreSQL.

## Tech Stack

- **Language:** Python 3.12
- **Backend:** FastAPI, Uvicorn
- **AI:** Official Google Gemini Python SDK
- **Frontend:** HTML, CSS, JavaScript, MediaRecorder
- **Database:** PostgreSQL, SQLAlchemy 2.x, Alembic
- **Local development:** SQLite
- **Recording storage:** Supabase Storage
- **Deployment:** Render
- **Testing:** pytest

## Run Locally

### Requirements

- Python 3.12
- `uv`

### Setup

Clone the repository and open its directory:

```powershell
git clone https://github.com/visheshbpatel/vaani-voice-sales-assistant.git
cd vaani-voice-sales-assistant
```

Install dependencies and configure the environment:

```powershell
uv sync
Copy-Item .env.example .env
```

Configure the required environment variables in `.env`, then run database migrations and start the application:

```powershell
uv run alembic upgrade head
uv run uvicorn app.main:app --reload
```

Open http://127.0.0.1:8000/ in your browser and allow microphone access. Set `GEMINI_API_KEY` to enable real Gemini conversations.

### Run Tests

```powershell
uv run pytest
```

Tests use mocks.

## Deployment on Render

Create a Render web service from the GitHub repository. The `render.yaml` configuration installs the locked dependencies, runs database migrations, and starts Uvicorn using `$PORT`.

Configure the environment variables marked `sync: false` in the Render dashboard and use PostgreSQL for the deployed application.

## Environment Variables

| Variable | Purpose |
|---|---|
| `GEMINI_API_KEY` | Gemini conversations and summaries |
| `DATABASE_URL` | PostgreSQL or local SQLite connection URL |
| `APP_SECRET` | Secret used to sign call tokens |
| `HISTORY_PASSWORD` | Protects call history |
| `ALLOWED_ORIGINS` | Additional allowed WebSocket origins |
| `MAX_CALL_SECONDS` | Maximum call duration |
| `ALLOW_LOCAL_RECORDINGS` | Controls whether local recording storage is allowed |
| `SUMMARY_MODEL` | Gemini model used for summaries |
| `STORAGE_ENDPOINT_URL` | S3-compatible storage endpoint |
| `STORAGE_REGION` | Storage region |
| `STORAGE_ACCESS_KEY_ID` | Storage access key ID |
| `STORAGE_SECRET_ACCESS_KEY` | Storage secret access key |
| `STORAGE_BUCKET_NAME` | Storage bucket name |
| `RECORDING_MAX_BYTES` | Maximum recording upload size |

For Render/PostgreSQL deployments, `HISTORY_PASSWORD` is required. If it is missing, protected history endpoints fail closed with a configuration error. Local SQLite development may omit it and uses the local-development exception.

The PostgreSQL RLS migration enables the database feature but does not provide per-user isolation. Vaani does not have a user ownership model; application-level call tokens and the history password remain the authorization controls.

## Limitations

- Free hosting and database tiers may sleep when idle, making the first request slow.
- The voice flow supports English only.
- Vaani is an internship prototype, not an official Surefy product, and does not integrate with Surefy systems.