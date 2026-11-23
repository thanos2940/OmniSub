# Omnisub

[![CI](https://github.com/thanos2940/OmniSub/actions/workflows/ci.yml/badge.svg)](https://github.com/thanos2940/OmniSub/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Omnisub is a self-hosted, context-aware AI translator for **English subtitles** of TV series and films. It
plugs into Sonarr, Radarr and Plex the way Bazarr does, and translates whole episodes into your language
with the context a human translator would use: a guide to the show, a glossary of names and terms, who the
characters are (and their gender, which many languages must show), what happened in earlier episodes, and
the lines around each line. Names, terms and forms of address stay the same from the first episode to the
last.

It is built and tuned first for **Greek** (idioms, register, gender agreement, punctuation, the accent on
Japanese names in anime), and works for about 70 target languages.

## How it translates

Every episode — from a Translate button, the Autopilot, a Sonarr/Radarr webhook or an on-demand request —
goes into one priority queue. One of two engines works through it (Settings › Translation):

| Engine | What it uses | Good for |
| --- | --- | --- |
| **Agent CLI** (default when one is installed) | Antigravity (`agy`), Claude Code (`claude`) or the Gemini CLI, signed in on the server, with your own plan. Lines the CLI's safety filter refuses can go to the Gemini API. | A home server or PC that already has a subscription |
| **Gemini API** | A Gemini API key from [Google AI Studio](https://aistudio.google.com/apikey) (free tier with daily limits, or billed). Optional Batch API for cheap backlog runs. | Docker, any server without a CLI |

Before the first episode of a new show Omnisub builds its **context guide** and **glossary** from what
Sonarr/Radarr know of it (overview, genres, original language) and a sample of its dialogue, and grows both as
the show goes on. Each episode is then sent in scene-aware parts with that context; lines that come back
missing, still in English or on the wrong number get a fix round; the result is checked (alphabet mix-ups,
glossary use, alignment) and exported next to the video, and Plex is asked to rescan.

## Features

- **Consistency across a series**: context guide, glossary with grammatical gender and senses ("Pillar" the
  rank, not "the pillar collapsed"), character profiles, episode summaries, translation memory.
- **Subtitle formats**: SRT and ASS/SSA end to end. ASS styles, positioning, karaoke and vector drawings pass
  through untouched; signs and on-screen text are translated; long lines are re-balanced and split on export;
  reading-speed and line-length checks; font scaling for ASS.
- **Embedded subtitles**: a video with no subtitle file has its English track probed and extracted (ffmpeg).
- **Automation**: Sonarr/Radarr sync and webhooks, an Autopilot for new imports (and an optional backlog),
  priorities per show, an off-peak window, Plex refresh after every export, on-demand requests over an API
  ([MorpiceHub guide](deploy/morpicehub/README.md)).
- **Editor and review**: side-by-side editor with search, filters and single-line retranslation; a review
  queue for lines the checks flagged; glossary, context and character editors per project.
- **Operations**: Activity page with live progress, history and logs; backups; an in-app updater; runs as a
  Windows service with a supervisor, in Docker, or from source.

## Install

### Windows (service, like Sonarr)

Double-click **`deploy\windows\install.cmd`**, then open `http://localhost:2945`. Details, updates, Caddy
(reverse proxy) and uninstall: [deploy/windows/README.md](deploy/windows/README.md).

### Docker

```bash
docker compose up -d --build
```

Then open `http://localhost:8000`. All state (settings, queue, projects, translation memory) lives in
`./config`. Point the commented `/media` volume in `docker-compose.yml` at your media, using the paths
Sonarr/Radarr report (or map them in Settings › Connections). The image has no agent CLI, so choose
**Gemini API** in the setup wizard and add a key.

### From source

Python 3.11+ and Node.js 18+.

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r backend/requirements.txt
cd backend && python -m uvicorn main:app --host 0.0.0.0 --port 8000
```

```bash
cd frontend && npm ci && npm run dev    # http://localhost:5173, talks to the backend on :8000
```

For a single process, build the UI (`npm run build`) and copy `frontend/dist` to `backend/static`.

## First run

A setup wizard asks for the target language, who translates (agent CLI or Gemini API key), a username and
password, and Sonarr/Radarr. Everything can be changed later in Settings. For an agent CLI, run it once in a
terminal **as the account Omnisub runs as** and sign in; Omnisub cannot do that step for you.

Source subtitles must be **English**: the prompts, the checks and the glossary matching are written for an
English source.

## Security

- Set a username and password (the wizard asks; Settings › Security later) before the server is reachable from
  anywhere but your own machine. Sonarr/Radarr webhooks then need the webhook token shown in Settings ›
  Connections.
- Browsers may only call the API from the app itself (and the Vite dev server); requests that another website
  makes through your browser are refused. If you serve the UI from a different origin, list it in
  `cors_allow_origins` in `config.json`.
- Expose Omnisub through HTTPS (Caddy, Traefik, nginx); set the URL base and trusted proxies in Settings ›
  General.

## Development

```bash
cd backend && python -m pytest -q        # ~2000 offline tests: the model and the CLIs are faked
cd frontend && npm run lint && npm run build && npm test
```

The tests never call a model or a real agent CLI (`tests/fake_agent_cli.py` stands in for `agy`), never touch
your data folder and never reach Plex. CI runs both suites on every pull request.

## License

[MIT](LICENSE)
