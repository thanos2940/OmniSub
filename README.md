# Omnisub

<<<<<<< HEAD
[![CI](https://github.com/thanos2940/OmbiSub---Google-Seminar-Capstone-Project/actions/workflows/ci.yml/badge.svg)](https://github.com/thanos2940/OmbiSub---Google-Seminar-Capstone-Project/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

=======
>>>>>>> 0ed0551b5c166ee2dcf737224e5d0abb5fb1c3a4
Omnisub is a context-aware AI subtitle translator built to overcome the limitations of line-by-line machine translation. Before it translates a single line, it builds a **Spherical Context** for the whole project — a dynamic glossary, character profiles, episode summaries, a lore/context guide, and a translation memory — so terminology, character voice, and gender stay consistent across an entire series or movie. It solves the "amnesiac translation" problem where names, genders, and running jokes get mistranslated line-by-line by standard LLM prompts.

Omnisub is a monorepo:

- **Backend (Python / FastAPI):** subtitle parsing, state management, a priority translation queue, a background worker, and Google's Agent Development Kit (ADK) orchestrating a small team of specialized agents. Integrates directly with Sonarr/Radarr and the filesystem to discover media and missing subtitles.
- **Frontend (React 18 / Vite / Tailwind):** a side-by-side translation editor, a review queue for flagged lines, a live translation-queue dashboard, a media library view, and project/glossary management — all talking to the backend over a REST API.

## Key Features

**Context-aware translation quality**
- **Spherical Context** — a compiled "bible" per project: dynamic glossary (names, terms, honorifics), character profiles (voice/gender/relationships), episode summaries, and a lore/context guide, assembled *before* translation and reused across episodes.
- **Translation memory** (LanceDB, embeddings-based) reuses your own past edits and gold examples as few-shot context, and can carry over translations for unchanged lines when a source subtitle is re-synced.
- **Glossary enforcement, QC funnel, and repair pass** — deterministic zero/low-token passes that catch untranslated glossary terms, dropped lines, and other structural issues after translation, plus an anchor-based alignment audit that detects and flags cue-split/shift drift.
- **Multi-agent pipeline (ADK):** a Cartographer agent extracts terms/entities, a Translator agent does the actual localization, a Research agent digs up lore, and a Reviewer agent grades output — instead of one overloaded mega-prompt.

**Subtitle formats & output quality**
- **SRT** and **ASS/SSA** (Advanced SubStation Alpha) both supported end-to-end. ASS handling is style/karaoke-aware: karaoke and vector-drawing events (detected via the `Effect` field, style name, and `\k` tags) pass through untouched, while dialogue, signs, moving text, and on-screen written text are translated; script info, styles, and comments are preserved on export.
- **Auto-balance & auto-split on export** — SubtitleEdit-style: over-long or 3+ line cues are re-wrapped into balanced lines, and cues that still can't fit are split into two cues that share the original duration. Runs on every export path; the editor's underlying data is never touched.
- **Conformance checking** — reading-speed (CPS), line-length, and line-count limits, with optional semantic condensation of over-fast lines.
- **Font-size scaling** for ASS targets whose script renders visually larger/smaller than the source (e.g. Greek vs. Latin) at the same nominal size.
- Optional **SubtitleEdit** integration for one-click "Fix Common Errors" / "Split Long Lines" passes on SRT tracks.

**Automation & integrations**
- **Sonarr & Radarr** direct integration — scheduled and webhook-triggered sync discovers new/missing episodes and movies and enqueues them for translation automatically. Filesystem-based scanning finds existing subtitles without needing Bazarr.
- **Priority translation queue** (SQLite, WAL mode): `Webhook > Manual > Sync > Backlog`, drained by a background worker that runs items in parallel and can be restricted to an off-peak time window.
- **Gemini Batch API lane** for the backlog — a much cheaper, async bulk-draft pass using the same glossary/context as the live pipeline; anything flagged gets refined interactively.
- Auto-export of translated subtitles next to the media file, and/or to a configured export directory, on completion.

**Model flexibility**
- **Cloud, local, or hybrid** model routing — Gemini models via ADK, or any OpenAI-compatible local server (llama.cpp, etc.) via LiteLLM, selected per agent *role* (translation, glossary, review, summary, research, …), with per-project overrides.
- Adaptive concurrency and a rate limiter for the Gemini API (local models bypass both, since there's no external quota).
- Cost/usage telemetry per model and per role.

**Editor & review UI**
- Side-by-side original/translated editor with search, status filters, inline TM/glossary badges, permanent line deletion (with export re-sync), and manual realignment tools for fixing model split/shift drift.
- Dedicated **review queue** for lines flagged `needs_review` across all projects, a **translation queue panel** for live job/queue status, a **media library** view, and a health/diagnostics page.

## Architecture

Every translation request — UI button, Sonarr/Radarr sync, webhook, or manual batch — goes through the same priority queue; nothing translates inline in a request handler. A single background worker drains it, and all model calls funnel through one dispatch point (`adk_agents/llm_factory.py`) that decides cloud vs. local per call.

```
UI / Sonarr / Radarr / Webhook
        │
        ▼
  queue_service.enqueue_translation()
        │
        ▼
  translation_queue (SQLite, WAL)   Priorities: Webhook > Manual > Sync > Backlog
        │
        ▼
  BackgroundTranslationWorker  ──►  translation_service (live)  ──► llm_factory.generate()
        │                       ──►  batch_translator (Gemini Batch, backlog)
        ▼
  storage (per-project/episode JSON)  +  auto-export next to media
```

## Tech Stack

- **Backend:** FastAPI, Google ADK, google-genai, LiteLLM, SQLite (aiosqlite), LanceDB + sentence-transformers (translation memory), pysubs2 (ASS/SSA)
- **Frontend:** React 18, Vite, Tailwind CSS, React Router
- **Deployment:** Docker / Docker Compose (multi-stage build serves the built frontend from the backend)

## Setup Instructions

### Prerequisites

- Python 3.11+
- Node.js (v18+)
- Google Gemini API Key (for cloud models) — not required if running fully local

### Environment Setup

Create a `.env` file in the **project root**:

```env
GOOGLE_API_KEY=your_gemini_api_key_here
```

(`GOOGLE_API_KEY` can also be set via `config.json`, which is loaded into the environment at startup.)

### Running Locally

**1. Backend**

The virtual environment lives at the **repo root** (`.venv`); commands run **from `backend/`**, since imports are top-level (`from services...`, `from utils...`, not `from backend...`).

```bash
# From the project root
python -m venv .venv
# Activate the virtual environment (Windows: .venv\Scripts\activate | Mac/Linux: source .venv/bin/activate)

cd backend
pip install -r requirements.txt
../.venv/Scripts/python.exe -m uvicorn main:app --reload --host 0.0.0.0 --port 8000   # Windows
# or: ../.venv/bin/python -m uvicorn main:app --reload --host 0.0.0.0 --port 8000     # Mac/Linux
```

**2. Frontend**

```bash
cd frontend
npm install
npm run dev
```

The frontend will be available at `http://localhost:5173`.

### Running via Docker

```bash
docker compose up --build
```

This builds the frontend and serves it from the FastAPI backend in a single container at `http://localhost:8000`.

All mutable state — `config.json`, the queue/telemetry database, `projects/`,
and `translation_memory/` — lives under one directory (`OMNISUB_DATA_DIR`),
mounted as `./config:/config` in `docker-compose.yml`, so it survives image
rebuilds. Edit `docker-compose.yml` to uncomment and point the `/media` volume
at your actual media root before enabling Sonarr/Radarr sync or auto-export —
the container path must match what Sonarr/Radarr report (or be remapped via
**Settings → Sonarr/Radarr → path mappings**; the Health page has path
diagnostics if a mount doesn't resolve).

### Tests

The backend test suite is offline and network-free by design — it mocks/stubs the LLM seam, so no API key or live model calls are required to run it.

```bash
cd backend
../.venv/Scripts/python.exe -m pytest tests/            # full suite (~240 tests)
../.venv/Scripts/python.exe -m eval.run_eval             # offline translation-quality regression eval
```

### Securing your install

Omnisub ships with authentication **disabled by default** on existing installs so
upgrades never lock anyone out, but a fresh install is prompted to set credentials
immediately. If you expose this server beyond your own machine (LAN, VPS, reverse
proxy), set a username/password in **Settings → Security** (or via the setup
wizard) before doing anything else — this generates the API key the frontend uses
and a webhook secret that Sonarr/Radarr must include in their webhook URLs. Until
credentials are set, both Settings and the top bar show a persistent warning.

Other recommendations for a public-facing deployment:

- Put Omnisub behind HTTPS (a reverse proxy such as Caddy, Traefik, or nginx).
- Set `cors_allow_origins` in Settings to your actual frontend origin(s) instead of
  leaving CORS wide open.
- Don't expose the Sonarr/Radarr API keys stored in Omnisub's settings — they are
  masked in the API response but are only as safe as your login credentials.

## Current Status

**Status: Actively developed.** The core translation pipeline (context assembly, dynamic glossary, translation memory, QC funnel), the priority queue with Sonarr/Radarr sync and webhooks, SRT and ASS/SSA support, the side-by-side editor, and the review/queue UI are all functional and covered by an offline test suite. Local/hybrid model routing and export-time conformance (auto-balance/auto-split, font scaling) are implemented; Bazarr integration has been retired in favor of direct Sonarr/Radarr + filesystem sync.

## Contributing

Pull requests are welcome. CI runs the backend test suite and the frontend
lint/build on every PR — please make sure both pass locally first (`cd backend &&
../.venv/Scripts/python.exe -m pytest` and `cd frontend && npm run lint && npm run
build`). Keep backend tests offline/network-free; the LLM seam is meant to be
mocked, not called live.

## License

[MIT](LICENSE)
