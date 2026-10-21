# Handoff — track `dependable_agy_first_20261019` (2026-10-21)

Read this first, then `spec.md` (review findings + target design) and `plan.md` (task checklist) in this
folder. `CLAUDE.md` at the repo root is **git-ignored** (all `*.md` are ignored except force-added ones),
so a fresh clone does not have it — the architecture notes that matter for this track are summarised below.

## The owner's goal (verbatim intent)
OmbiSub must run like Sonarr/Radarr/Bazarr: dependable, easily installed, updated, managed, reachable
through **Caddy**; connected to Sonarr/Radarr/Plex like Bazarr; translations efficient and clean.
Translation **always tries agy (Antigravity CLI) first**, the Gemini API only for lines agy's safety filter
refuses; every "Translate" button respects that. No subtitle file → probe the container, extract the
embedded track, translate. Context guide + glossary created when missing and enhanced as a show
progresses. UI and processes simplified, intentional, organised. The owner asked that code be written by
**Claude Sonnet 5.5 subagents (medium/high effort)** and reviewed by the orchestrator.

## Environment facts
- Owner: Windows 11. Repo on the PC: `P:\Tools\OmbiSub - Google Seminar Capstone Project`. GitHub:
  `thanos2940/OmniSub` (old name `OmbiSub---Google-Seminar-Capstone-Project` redirects).
- Live install: the **server** `Morpiceserver`, `C:\ServerTools\OmniSub`, port **2945**, Service mode
  (boot task running `tray_app.py --headless` after the next reinstall; before that it runs
  `run_server.py` directly). Media on the server's local drives `D: E: F: M: S:`.
- Python 3.11 venv at repo-root `.venv`; tests from `backend/`:
  `../.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider` (≈1820 tests, ~3.5 min, all passing at
  commit `2ef95e7`; frontend-only commits since). In a Linux cloud box use `python -m pytest` from
  `backend/` after `pip install -r backend/requirements.lock.txt` (Windows-only deps may need skipping;
  a few tests are Windows-specific).
- Frontend: `cd frontend && npm ci && npm run lint && npm run build` (lint now covers `.jsx`; 0 errors,
  19 harmless fast-refresh warnings).
- PowerShell 5.1 quirks matter for `deploy/windows/*.ps1`: native-command args lose embedded double
  quotes; a one-item `@(...)` unrolls. Parse-check every .ps1 you touch.
- Never run tests or a dev backend that can reach the real `agy` CLI or real media: `tests/conftest.py`
  stubs runner autostart + Plex. For a manual UI preview, use a scratch `OMNISUB_DATA_DIR` with blanked
  `arr_*` paths, `agent_runner_autostart: false`, and agy removed from PATH/LOCALAPPDATA.

## What is DONE (on `main`, local commits ahead of origin until pushed)
Backend (all reviewed + merged, tests green):
- **WS-A ops**: supervised Service mode (respawn on crash 5/15/60 s, at once on exit 42),
  `deploy/windows/install.cmd|update.cmd|uninstall.cmd` (+ `update.ps1`), updater channel
  `auto|main|releases` (follows `origin/main`, cached status, lock-file deps, UI rebuild, exit 42),
  `GET /api/system/status`, `/api/system/logs[/list]`, `url_base` for Caddy (`utils/url_base.py`,
  `utils/spa.py`), `trusted_proxies`, docs in `deploy/windows/README.md`.
- **WS-B engine**: `services/agy_llm.py` (agy first, API only on refusal); retranslate-line, context/
  glossary create+enhance, summaries, term harvest, ScriptGuard via agy in agent mode; runner show
  **bootstrap** before the first episode (`project_bootstrap.bootstrap_project`); **guide growth** every 6
  episodes; `translation_engine` default `agent`; pause semantics.
- **WS-C integrations**: Autopilot is the only automation (legacy `arr_auto_translate` removed), sync
  never mass-enqueues, Plex refresh after every export (`services/plex_refresh.py`) + `POST
  /integrations/plex/test`, "Translate missing" reaches never-imported episodes (`on_demand.translate_missing`).
- Orchestrator: config migrations (`utils/config_migrations.py`), library-wide translate-missing
  background job, job-id collision fix, test guard against real runner autostart, sync stores
  `arr_year/genres/overview`, `agent_runner.recover()` after errors, "translate missing lines" via runner,
  agent-path missing-source extraction, Sonnet 5.5 fallback, `/quota` prompt-spend fix, SPA routes.
Frontend (merged; **built and linted, NOT yet visually verified together**):
- Shared kit `frontend/src/components/ui/index.jsx`.
- **F1**: top bar (Library · Activity · Review · Settings · System, runner pill), Library at `/`,
  Activity (`/activity`: runner panel, Queue, History, Autopilot), System (`/system`: Status, Logs,
  Backup, Updates), redirects from old routes.
- **F2a**: project page (4 tabs, one Translate/Retranslate per episode, overflow menu, Build/Enhance
  with agy), episode view, `hooks/useTranslate.js`; fixed an editor crash (missing `useMemo` import —
  this crash is LIVE on the server until the update).
- **F2b**: Settings (`?section=translation|connections|subtitles|automation|security|general`, one
  sticky save bar), agy-first setup wizard, `PUT /api/agent/runner/config`.

## What REMAINS (do in this order)
1. **Verify the merged UI end to end** (plan V1): run backend on a scratch data dir + `npm run dev`
   (or the built `backend/static`), walk every page at 1440 px and 390 px, light and dark. Known
   integration risks to check:
   - EpisodeView uses `h-[calc(100vh-3.75rem)]` for the top-bar height — check against the new TopBar.
   - Settings › Connections webhook card shows `?token=YOUR_TOKEN` because `webhook_secret` is masked;
     add a small authenticated endpoint that returns the full webhook URLs (and use it in the card).
   - `/api/queue` caps at 100 rows; the project page shows "Queued" only for those → add a
     `project` filter to the queue list endpoint (`routers/queue.py`) and use it in `useEpisodeQueue`.
   - `JobContext.jsx` job links still point at `/library`, `/queue` (redirect works; update them).
   - `/api/system/status` returns `port: null` unless `OMBISUB_PORT` is set (it is set by `run_server.py`;
     fine on the server, null under plain uvicorn).
   - Duplicate confirm dialogs: kit `ConfirmDialog` (F1) and `project/ConfirmDialog.jsx` (F2a) — consolidate.
   - The GitHub push-webhook settings card was dropped; decide whether to re-add it under Connections.
2. Drop unused `api_rescue`/`rescue_model` from `routers/schemas.py` `SettingsRequest` (nothing reads them;
   the runner config is the source).
3. Full pytest + lint + build; update `plan.md` checkboxes (V1, V2), `CLAUDE.md` Frontend section
   (new routes, kit), commit.
4. **Push** `main` and give the owner server steps (below). Pushing is outward-facing: the owner asked
   for it, but say what is being pushed.

## Server update steps for the owner (after push)
1. On the PC: **exit the old OmbiSub instance** — tray app (`pythonw tray_app.py`, stuck in a respawn
   loop) and the `uvicorn --reload --port 8000` dev server. Both run from the repo with the real
   library; on 2026-10-19 the startup migrations ran there (engine→agent, Autopilot on) — reverted by
   hand to engine `model`, Autopilot off, translations paused, runner stopped. Nothing was translated or
   written (audited: no queue items, no autopilot items, no new owned files).
2. On the server, elevated PowerShell in `C:\ServerTools\OmniSub`: `git pull`, then
   `powershell -ExecutionPolicy Bypass -File deploy\windows\install.ps1 -Mode Service -Port 2945 -OpenFirewall -KeepAwake`
   (re-registers the task with the supervisor; installs `requirements.lock.txt`; builds the UI).
   Empty Windows password → S4U logon (prompt: leave empty).
3. First start runs the config migrations on the server: engine → agent (agy installed), Autopilot on
   for new imports only, update channel auto. Check System › Status and Activity.
4. Settings › General: URL base if Caddy serves a path; Settings › Connections: Test Sonarr/Radarr/Plex,
   copy webhook URLs into Sonarr/Radarr.

## Incidents / cautions
- Background agents shared the session scratchpad and once let a scratch backend start the real runner
  (one fake 14-line episode translated with real agy). Keep previews isolated as described above.
- The startup migration enabling Autopilot affects whichever instance boots the new code first — make
  sure only the server runs it.
