---
description: Surefy AI Voice Sales Assistant project rules
alwaysApply: true
---

# Project
App name: Vaani. Repo: vaani-voice-sales-assistant (GitHub: visheshbpatel). Use "Vaani" in the UI title and README.
Independent internship prototype (Sankar Group assignment): a web app where a prospect
holds a real-time spoken conversation with an AI sales assistant for Surefy.
Never present it as an official Surefy product. Never claim integration with Surefy systems.
I am learning: keep code simple, one-line comments on non-obvious parts, no needless abstraction.

# Stack (do not add anything else)
Python 3.12, FastAPI, Uvicorn, pydantic-settings, SQLAlchemy 2.x + Alembic,
Google Gemini Live API via the official SDK (server-side only), vanilla HTML/CSS/JS,
MediaRecorder, Neon PostgreSQL (SQLite OK for local dev only), Cloudflare R2 (boto3), Render.
No React, LiveKit, LangGraph, telephony, auth, CRM, or extra infrastructure.

# Env var names (use exactly these everywhere)
GEMINI_API_KEY, DATABASE_URL, STORAGE_ENDPOINT_URL, STORAGE_REGION,
STORAGE_ACCESS_KEY_ID, STORAGE_SECRET_ACCESS_KEY, STORAGE_BUCKET_NAME

# Layout
app/{main,config,database}.py, app/models/call.py, app/schemas/call.py,
app/services/{gemini_live,recording_storage,call_summary,product_knowledge}.py,
app/routes/{calls,voice}.py, app/static/{index.html,styles.css,app.js},
alembic/, tests/, .env.example, README.md, pyproject.toml

# Hard rules
- Never guess SDK methods, model IDs, audio formats or config fields. Verify against official
  docs (https://ai.google.dev/gemini-api/docs/live-api) ONCE, record findings with URL and date
  in docs/VERIFIED.md, and reuse that file afterward instead of re-fetching.
- Product facts live only in app/services/product_knowledge.py (reviewed, local, no live scraping).
  Surefy modules: Reachout, Console, Insight, Pulse, Engage (descriptions come from
  surefy.ai, verified once and saved with the source URL).
  Never invent prices, customers, stats, integrations, guarantees, or capabilities.
  Unknown -> "needs confirmation from Surefy".
- Never say a demo is booked or a lead was sent unless a real integration confirmed it.
- Final status must be exactly one of: Converted, Interested, Follow-up Required,
  Not Interested, Demo Requested, No Response. Enforce with a backend enum/validator.
  Converted only with real evidence. No customer speech -> No Response. Uncertain -> conservative.
- Recording: explicit consent BEFORE recording; no consent = no audio saved. Real audio only,
  never placeholders. Audio goes to R2 via backend; only object key + metadata in the DB.
  Playback via time-limited presigned URL. No R2 or Gemini secrets in browser code.
- Transcript comes from the live API's real transcription; if unavailable, say so. Never fabricate.
- Don't log transcripts or secrets. No stack traces to users. Clean up mic tracks,
  WebSockets, playback queues and timers on end/failure. One call record per session.
- No fake success states. Never claim something works, is tested, or is deployed unless you ran it.

# Git workflow
- Default branch: main. After the initial commit, never commit directly to main.
- At the start of each phase, create a branch: phase-<N>-<short-name>
  (e.g. phase-2-voice-loop, phase-4-database-history).
- Do NOT commit or push until I say the phase is approved / "commit".
- Commit routine (when I ask):
  1. Run the phase's relevant tests; report real results.
  2. `git status` and `git diff --stat` only (no full diffs, to save tokens).
  3. Check nothing sensitive is staged: no .env, keys, DB files, recordings. Grep the staged
     diff for API_KEY, SECRET, postgres://, postgresql:// before committing.
  4. Make 1-3 logical commits using Conventional Commits (feat:, fix:, test:, docs:, chore:,
     refactor:), imperative mood, subject <= 72 chars, body only when the "why" isn't obvious.
  5. Push the phase branch with `git push -u origin <branch>`.
  6. Print the merge commands for me (below). Do not merge or tag yourself.
- Commit messages are mine only: NO "Co-authored-by", "Generated with/by", "Made with Cursor",
  tool/AI names, emojis or signature lines, in the subject or body. Author/committer must be my
  configured git identity (never override it with --author or a bot name).
  After committing, run `git log -n 3 --format=%an%n%B` (before pushing) and confirm none of the
  above appear; if they do, fix with `git commit --amend` (or `git rebase -i` for older unpushed
  commits) and re-check before `git push`.
- Never force-push, rewrite pushed history, or use `git add -A` before checking `git status`.
- If tests fail, say so and ask before committing.
- Merge commands I run after reviewing:
  git checkout main && git merge --no-ff <branch> && git push
  git tag -a v0.<N> -m "Phase <N>: <name>" && git push --tags

# Working style (token-efficient)
- Work ONLY on the current phase, then stop and wait for my approval.
- Don't re-read files already seen in this chat. Use grep/targeted reads, not whole-file dumps.
- Edit files directly; don't paste large code blocks into chat.
- End each phase with <= 15 lines: what changed, files touched, ACTUAL test results,
  limitations, and the exact next command/manual step for me.
- No tutorials unless I say "explain".
- If a choice has a sensible default, pick it and note it in one line. Ask only about
  real blockers (credentials, contradictory requirements).
- Tests use mocks for Gemini/R2. Never call paid APIs in tests. Run only relevant tests.