# CureWise — agent development guide

Practical notes for working in this repo. For *what the product is*, read
`PRODUCT.md`; for the visual system, `DESIGN.md`; for setup, `README.md`.

---

## The one thing to know

CureWise used to be a hospital-management platform. It isn't any more. There is
**one `user` role** and no doctors, admins, hospitals, departments,
availability or appointments — those tables and routes were deliberately
deleted. Don't reintroduce them. Every feature is a self-serve tool a patient
uses on their own, and every AI result carries the disclaimer that CureWise
informs and does not diagnose.

## Stack

- **Backend** — FastAPI, Python 3.11, PostgreSQL via raw `psycopg2` over a
  threaded connection pool (no ORM, no migrations; the schema is created on
  startup). JWT auth (`python-jose` + `passlib`/bcrypt).
- **Frontend** — Next.js 16 App Router, React 19, TypeScript, Tailwind v4,
  shadcn/radix. No axios, no react-query, no framer-motion, no react-router.
- **AI** — OpenAI `gpt-4o-mini` (chat, embeddings, report structuring), Groq
  `llama-3.3-70b` / `llama-4-scout` (per-disease chat, skin vision), Pinecone
  (RAG), LlamaParse (blood-report PDFs), Keras/TensorFlow `.h5` image models.

## Commands

```bash
# Backend (from backend/)
uvicorn src.main:app --reload --port 8000
python scripts/ingest_rag.py --dry-run     # seed the RAG index (see below)

# Frontend (from frontend/)
npm run dev
npm run build
npm run lint

# Everything
docker compose up --build
```

`backend/tests/smoke_test.py` drives the real API over HTTP (needs the server
up; needs **no** provider keys — it asserts the AI routes degrade cleanly).
Run it with `python tests/smoke_test.py`; it exits non-zero on failure, so it
works as a CI gate. There are **no unit tests** beyond that — if you add
behaviour worth protecting, add the harness with it.

## Backend layout

Feature-first. Each feature under `src/features/<name>/` owns its own
`router.py` (HTTP), `service.py` (logic + data access) and `schemas.py`
(pydantic). Shared concerns live in `src/core/`.

```
src/
  main.py            # app factory, router mounting, lifespan
  core/              # config, pooled database, security (JWT), deps, logging
  db/schema.py       # users, medical_history, general_chat_history — that's all
  auth/              # signup / login / me
  features/
    profile/  medical_history/  chat/  medical_report/  skin/  emergency/
    disease_detection/
scripts/ingest_rag.py
ml_training/         # training + eval scripts for the image models
```

### Conventions that matter

- **Database access goes through `src/core/database.py`.** Use `get_conn()` /
  `get_cursor()`; they borrow from the pool and commit/roll back for you. Never
  open a bare `psycopg2.connect`.
- **Always parameterise SQL** (`%s`). The only interpolation allowed into a
  query string is a column name drawn from a hardcoded whitelist — see
  `features/profile/service.py`.
- **Never return `str(exc)` to the client** on a 5xx. Log the exception with
  `exc_info=True` and return a calm, generic message. Exception: `AuthError`
  messages in `auth/router.py` are ours and intentionally user-facing.
- **Uploads go through `core/uploads.py`** (`require_image` / `read_upload`),
  which enforces content type and a size cap. Call them *before* the handler's
  `try`, so a 400/413 isn't caught and remapped to a 500.
- Routers stay thin: validate, delegate to the service, map errors to HTTP.
- Every authed route takes `current_user: dict = Depends(get_current_user)`.

### Adding an image-screening model

This is a **data change, not new endpoint code**. Add a `ClassifierSpec` to
`features/disease_detection/specs.py` (key, weights path, input size, labels,
`kind`, optional system prompt) and a matching `*_MODEL_PATH` setting in
`core/config.py`. The three existing routes then serve it, and the frontend
picks it up automatically from `GET /api/disease-detection`.

Weights live in `backend/weights/<key>/`, are ~1.5 GB in total, and are
gitignored — provision them out of band or regenerate them with
`ml_training/`. `backend/data/` holds only datasets and sample images.

### RAG

`src/features/chat/rag.py` builds the retrieval chain at startup and will
create an **empty** Pinecone index if none exists — retrieval then silently
returns nothing. Seed it with `scripts/ingest_rag.py`, which reads
`data/rag/data.csv`. The index name, embedding model and dimension are defined
once in `rag.py` and imported by the script; keep it that way. Chunk metadata
must keep its `text` key — that's what LangChain's retriever reads.

## Frontend layout

See `frontend/README.md` for the tree. The rules that bite:

- **No component hardcodes a color.** Every token is in
  `src/app/globals.css`. Note that `--muted` is a *background* colour — for
  secondary text you want `text-muted-foreground`, not `text-muted`.
- `src/lib/api.ts` is the single place that knows about backend routes. Add
  new endpoints there with their types rather than calling `fetch` in a page.
- `src/components/nav-config.tsx` is the single source of truth for both the
  sidebar and the dashboard tiles.
- Pages are client components (`"use client"`) because everything is behind
  token auth held in `localStorage`.
- Model text renders through `components/ai-response.tsx`, which formats a
  deliberate subset (bold, paragraphs, bullets) and **never injects raw HTML**.

## Known gaps

Worth knowing before you trip over them:

- Only the HTTP smoke test — no unit tests, and the frontend has none at all.
- Rate limiting (`core/ratelimit.py`) is a per-worker in-memory counter on the
  auth routes. It blunts credential stuffing; it is not a hard control. Put a
  real limiter in front for production.
- The JWT is stored in `localStorage`; there's no refresh token and no
  server-side revocation.
- `blood-marker`'s 5th label is an inferred guess, flagged in `specs.py`.
- `ml_training/README.md` is honest that these are demo-scale models, not
  clinical-grade ones. Keep it that way.

## Security posture

Patient-adjacent data. Don't log request bodies, uploaded file contents, or
model inputs. `backend/data/blood_reports/` is gitignored because it holds PII;
keep it that way. Secrets come from `backend/.env` (never committed) via
`src/core/config.py` — don't read `os.environ` directly in feature code.
