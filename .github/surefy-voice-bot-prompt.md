# Vaani (AI Voice Sales Assistant for Surefy): Cursor Setup (token-efficient)

## How to use this (saves your free-tier requests)

0. Do **Part 0** (repo setup) once, before anything else.
1. Create `.cursor/rules/surefy.mdc` and paste **Part A**. It is always-on, so you never re-paste the spec.
2. Save **Part B** as `docs/ACCEPTANCE.md` (the checklist). Attach it with `@docs/ACCEPTANCE.md` only when a phase needs it.
3. Start a **new chat for each phase** and paste only that phase's prompt from **Part C**. Long chats get re-sent every turn and burn your quota.
4. Attach only the files a phase needs (`@file`), not the whole repo.
5. When a phase works and you've checked it, paste **Part D** (commit prompt) in the same chat to commit and push the phase branch.

---

## Part 0: Repo setup (do once, before Phase 1)

**Names**
- App name: **Vaani** (Hindi for "voice")
- Repo: `vaani-voice-sales-assistant`
- GitHub description: `Unofficial prototype: real-time AI voice sales assistant for Surefy (Sankar Group intern assignment)`

I avoided putting "Surefy" in the app name so it doesn't read as an official Surefy product, which your assignment says not to imply. Surefy still appears in the description and README. If you'd rather use `surefy-voice-sales-assistant`, change it everywhere below.

**Step 1: local repo (terminal)**
```
mkdir vaani-voice-sales-assistant
cd vaani-voice-sales-assistant
git init -b main
# only if git isn't configured yet:
# git config --global user.name "Vishesh Patel"
# git config --global user.email "you@example.com"
```
Open this folder in Cursor.

**Step 2: starter files (paste into Cursor, one short request)**
```
Create ONLY these files, nothing else:
1. .gitignore for Python/FastAPI: .env, .env.*, !.env.example, .venv/, __pycache__/, *.pyc,
   .pytest_cache/, *.db, *.sqlite3, recordings/, tmp/, .DS_Store, .idea/, .vscode/
2. README.md: title "Vaani", one-line description "Unofficial prototype: real-time AI voice
   sales assistant for Surefy (Sankar Group intern assignment). Not an official Surefy product.",
   and "Status: in development".
3. .env.example with exactly: GEMINI_API_KEY, DATABASE_URL, R2_ACCOUNT_ID, R2_ACCESS_KEY_ID,
   R2_SECRET_ACCESS_KEY, R2_BUCKET_NAME, R2_ENDPOINT_URL (empty values).
Reply in 2 lines.
```
Then add `.cursor/rules/surefy.mdc` (Part A) and `docs/ACCEPTANCE.md` (Part B) yourself.

**Step 3: first commit (terminal)**
```
git add .
git status        # confirm no .env or secrets are listed
git commit -m "chore: initialize repository with gitignore, readme and env template"
```

**Step 4: remote**
- With GitHub CLI (`gh auth login` once):
```
gh repo create vaani-voice-sales-assistant --private --source=. --remote=origin --push \
  --description "Unofficial prototype: real-time AI voice sales assistant for Surefy (Sankar Group intern assignment)"
```
- Without it: create an empty repo named `vaani-voice-sales-assistant` on github.com (no README, license or .gitignore), then:
```
git remote add origin https://github.com/visheshbpatel/vaani-voice-sales-assistant.git
git push -u origin main
```
Start **private**. Make it public at submission, after a final check that no secrets were ever committed.

---

## Part A: `.cursor/rules/surefy.mdc`

```
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
GEMINI_API_KEY, DATABASE_URL, R2_ACCOUNT_ID, R2_ACCESS_KEY_ID,
R2_SECRET_ACCESS_KEY, R2_BUCKET_NAME, R2_ENDPOINT_URL

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
```

---

## Part B: `docs/ACCEPTANCE.md`

```
Manual acceptance (mark each PASS / FAIL / NOT TESTED honestly; never simulate success)
1. Public deployed URL loads
2. Mic permission granted, call starts
3. Assistant produces generated speech in response to real speech
4. Response is audible
5. Transcript shows real customer + assistant messages (partial vs final handled, no duplicates)
6. End call shows real duration
7. Consent requested before recording
8. Consented recording uploads to R2 and plays back
9. Summary + exactly one valid status generated from the real transcript
10. Call, transcript, summary, status, recording metadata persisted
11. History survives reload and server restart
12. Errors shown clearly, no fake success
```

---

## Part C: Phase prompts (paste ONE per new chat)

### Phase 1: Feasibility (no app code)
```
Phase 1 only. Do NOT write application code.
1. Inspect the repo briefly (file tree, dependency file, README). Note anything reusable.
2. Read the official Gemini Live API docs. Record in docs/VERIFIED.md (with URLs and today's date):
   current model ID, SDK package + connect/send/receive interfaces, required input and output
   audio formats and sample rates, how to enable input/output transcription, how interruption
   is signaled, session time limits, and whether the free API tier supports Live API.
3. Read https://surefy.ai/ (max 3 pages). Add to docs/VERIFIED.md: verified module facts
   with source URLs, plus a list of claims that need Surefy confirmation (pricing, integrations, etc.).
4. Report in <= 60 lines: repo assessment, architecture, file structure, key technical decisions,
   accounts/credentials I need (and free-tier limits for Gemini, Neon, R2, Render, incl. Render
   free cold starts and WebSocket behavior), blockers (mic -> PCM conversion, gapless playback,
   recording both sides, interruption), and a 6-8 step implementation plan.
Then stop and wait for approval.
```

### Phase 2: Smallest end-to-end voice loop
```
Phase 2 only. Read docs/VERIFIED.md first. Build the smallest working voice call:
- config.py (pydantic-settings), main.py, product_knowledge.py (verified facts + system
  instruction for a transparent AI sales rep: introduces itself as AI, asks about the prospect's
  business, concise, no invented facts, offers demo/follow-up).
- routes/voice.py WebSocket + services/gemini_live.py bridging browser <-> Gemini Live.
- Frontend: Start/End Call, mic capture converted to the required PCM format (AudioWorklet),
  queued playback of streamed audio, clear the playback queue when Gemini signals interruption,
  status text (connecting / listening / speaking / ended), mic-permission and device error messages.
- Full cleanup of tracks, sockets, queues on end/failure; block duplicate sessions.
- Tests with a mocked Gemini session (session lifecycle, cleanup, duplicate-session rejection).
Done when I can speak and hear a reply locally. Give me the run command.
```

### Phase 3: Live transcript + real duration
```
Phase 3 only. Add input/output transcription from the Live API (per docs/VERIFIED.md).
Show partial text separately from finalized messages, correct speaker labels, no duplicates,
auto-scroll, transcript kept after call ends, honest "transcription unavailable" state.
Add real call duration (server-measured start/end, shown live in UI). Add mocked tests for
transcript assembly and duplicate prevention.
```

### Phase 4: Database + history API
```
Phase 4 only. SQLAlchemy 2 Call model (fields per spec: id, started_at, ended_at, duration_seconds,
status, transcript, summary, customer_requirements, objections, next_steps,
recording_object_key, recording_content_type, recording_consent, summary_generation_status,
created_at), Alembic initial migration, status Enum validated in backend.
Create the call once at session start; finalize in a finally block so a disconnect never makes
duplicates. Endpoints: list calls, get call. Tests on SQLite for creation, completion, history,
status validation, missing recording.
```

### Phase 5: Consent + recording + R2
```
Phase 5 only. Show a plain-language consent notice (what is recorded, where stored, why) before
recording; decline = no audio saved (call may continue unrecorded, clearly stated).
Record BOTH sides by mixing mic + assistant playback through Web Audio into one MediaStream for
MediaRecorder; pick a supported MIME type; only claim both sides if verified. Upload via backend
to R2 with a size limit; store key + content type in DB; presigned playback URL endpoint;
handle unsupported browsers and upload failures. Mock R2 in tests (consent rules, size limit,
missing recording). Give me the R2 setup + CORS steps.
```

### Phase 6: Summary + final status
```
Phase 6 only. After the call, generate a structured summary from the real transcript using a
Gemini text model with a JSON schema (business/use case, needs, modules discussed, questions,
objections, interest + evidence, next steps, unknowns). Validate with Pydantic; status must be one
of the six values. Empty transcript or no customer speech -> No Response without calling the model.
Handle model failure and malformed output; add an idempotent retry endpoint that updates the same
record. Mocked tests for parsing, failures, retry, status validation.
```

### Phase 7: History UI, polish, deployment
```
Phase 7 only. History page: list + detail (ID, start, duration, status, summary, transcript,
recording player or "unavailable/error" state); one failed record must not break the page.
Responsive, accessible, clean CSS. Then: render.yaml, start command
(uvicorn app.main:app --host 0.0.0.0 --port $PORT), migrations via `alembic upgrade head`,
.env.example, README (local setup, tests, Neon, R2 + CORS, Render, which services need accounts).
Then run docs/ACCEPTANCE.md honestly and report PASS/FAIL/NOT TESTED. Do not claim deployment
until it actually succeeded.
```

---

## Part D: Commit prompt (paste in the same chat once you've tested the phase)

```
Phase <N> is approved. Run the commit routine from the git rules: tests, status + diff --stat,
secret check, Conventional Commits, push the phase branch. Reply in <= 8 lines: commits made
(subjects only), test result, anything left out of the commit, and the merge + tag commands.
```

Suggested branches, commits and tags (the agent should follow this pattern):

| Phase | Branch | Example commit | Tag |
|---|---|---|---|
| 1 Feasibility | `phase-1-feasibility` | `docs: add verified Gemini Live and Surefy facts` | `v0.1` |
| 2 Voice loop | `phase-2-voice-loop` | `feat: add real-time voice call over Gemini Live` | `v0.2` |
| 3 Transcript | `phase-3-transcript` | `feat: stream live transcript and call duration` | `v0.3` |
| 4 Database | `phase-4-database-history` | `feat: persist calls with Alembic and history API` | `v0.4` |
| 5 Recording | `phase-5-recording-r2` | `feat: add consent flow and R2 recording upload` | `v0.5` |
| 6 Summary | `phase-6-summary-status` | `feat: generate summary and validated sales status` | `v0.6` |
| 7 UI + deploy | `phase-7-ui-deploy` | `feat: add history UI and Render deployment config` | `v1.0` |
