# Vaani

## What Vaani is

Vaani is an unofficial Sankar Group internship prototype where a prospect has a
spoken conversation with an AI sales assistant for Surefy. It is not an
official Surefy product and does not integrate with Surefy systems.

## Features mapped to the assignment

- **Voice call:** real-time microphone input and spoken Gemini replies.
- **Live chat text:** customer and assistant transcript text during the call.
- **Call controls:** start/end, elapsed timer, and connection status.
- **Recording with consent:** audio is saved only after explicit consent.
- **AI summary:** Gemini summarizes the conversation after the call.
- **Sales status:** the summary assigns a controlled sales outcome.
- **Call history:** timestamps, duration, transcript, summary, lead details,
  status, and recording playback when available.

## How it works

The browser sends microphone audio to a FastAPI WebSocket. The server forwards
it to Gemini Live and sends text and response audio back. With consent,
recordings go to Supabase Storage. After the call, Gemini generates the summary
and sales status; call records and metadata go to PostgreSQL.

## Tech stack

Python 3.12, FastAPI, Uvicorn, the official Google Gemini Python SDK, vanilla
HTML/CSS/JavaScript with MediaRecorder, SQLAlchemy 2.x, Alembic, PostgreSQL
(SQLite for local development), Supabase Storage, and Render.

## Run locally

Requirements: Python 3.12 and `uv`.

```powershell
uv sync
Copy-Item .env.example .env
uv run alembic upgrade head
uv run uvicorn app.main:app --reload
```

Open `http://127.0.0.1:8000/` and allow microphone access. Set
`GEMINI_API_KEY` for a real conversation. Tests use mocks:

```powershell
uv run pytest
```

## Deploy on Render

Create a Render web service from this repository. [`render.yaml`](render.yaml)
installs locked dependencies, runs migrations, and starts Uvicorn on `$PORT`.
Add variables marked `sync: false` in the Render dashboard and use PostgreSQL.

## Environment variables

| Name | Meaning |
| --- | --- |
| `GEMINI_API_KEY` | Gemini key for conversations and summaries. |
| `DATABASE_URL` | PostgreSQL or local SQLite URL. |
| `APP_SECRET` | Secret used to sign call tokens. |
| `HISTORY_PASSWORD` | Password protecting call history. |
| `ALLOWED_ORIGINS` | Additional allowed WebSocket origins. |
| `MAX_CALL_SECONDS` | Maximum call duration. |
| `ALLOW_LOCAL_RECORDINGS` | Whether local recording storage is allowed. |
| `SUMMARY_MODEL` | Gemini model used for summaries. |
| `STORAGE_ENDPOINT_URL` | S3-compatible storage endpoint. |
| `STORAGE_REGION` | Storage region. |
| `STORAGE_ACCESS_KEY_ID` | Storage access key. |
| `STORAGE_SECRET_ACCESS_KEY` | Storage secret key. |
| `STORAGE_BUCKET_NAME` | Storage bucket name. |
| `RECORDING_MAX_BYTES` | Maximum recording upload size. |

For Render/PostgreSQL deployments, `HISTORY_PASSWORD` is required. If it is
missing, protected history endpoints fail closed with a configuration error.
Local SQLite development may omit it and uses the local-development exception.
The PostgreSQL RLS migration enables the database feature but does not provide
per-user isolation: Vaani has no user ownership model, so application-level
call tokens and the history password remain the authorization controls.

## Limitations

Free hosting and database tiers sleep when idle, so the first request may be
slow. The voice flow is English only.
