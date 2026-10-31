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

## Verification and fixes (2026-10-21, branch `claude/tender-pascal-go38dg`)
Every page was walked at 1440 and 390 px, light and dark (Playwright), on a scratch `OMNISUB_DATA_DIR`
with `agent_runner_autostart: false`, no `claude` CLI on PATH, no Gemini key, and agy replaced by the test
suite's `tests/fake_agent_cli.py` (`OMNISUB_ANTIGRAVITY_CMD`). The Translate → queued → runner → translated
→ Delete-translation flow was driven in the browser. No console errors, failed API calls or horizontal
overflow remain. Changed:
- `GET /integrations/webhooks` (behind the API key when login is on) returns the webhook paths with the
  stored token; Settings › Connections shows the real URLs.
- `GET /api/queue?project=` filters items and extractions to one project (cap 1000); `useEpisodeQueue` uses it.
- One `ConfirmDialog` (kit, with the `checkbox` option); `project/ConfirmDialog.jsx` removed.
- EpisodeView fills the viewport under the TopBar (`100dvh - 3.5rem - 1px`); EditorView uses `h-full`
  (its `h-screen` hid the last ~180 px of cues below the window) and its header wraps on phones.
- `useAdaptivePolling`: the in-flight guard is per effect run. The shared ref let a cancelled run block the
  next one, so polling died after the first request under React StrictMode (dev) or any effect re-run.
- `useTranslate.ensureRunnerStarted` starts a runner that is not `running` (it only started `stopped`
  ones; a runner started without watch ends as `done` and was never restarted).
- Agent runner: the "queue empty, stop" decision is taken under `claim_lock` and counts claimed episodes
  (`held`); idle lanes stopped the runner while another lane was bootstrapping a just-claimed episode,
  leaving it `running` with nobody on it (regression test in `test_agent_runner.py`).
- Endpoints whose work goes to agy (glossary/context create & enhance, summary generation, retranslate-line)
  no longer demand a Gemini key in agent mode (`validate_llm_access`); subtitle upload needs none at all.
- System › Status finds agent CLIs the way the runner does (`agent_cli.engine_command`), not `which`.
- JobContext fallback link → `/activity`; `SettingsRequest` drops `api_rescue`/`rescue_model`.
Tests: 1809 pass, 5 skip on Linux; 20 fail identically on the base commit `1590ae8` (Windows paths/UNC/drive
letters, root ignoring read-only files, no sentence-transformers) and are environment-only.

### Engine choice and Gemini API models (2026-10-23)
- Settings › Translation opens with "Translate with: Antigravity (agy) | Gemini API" (`translation_engine`
  agent | model); the page shows only the chosen engine's settings. Saving a change runs
  `services/engine_switch.py`: to the API, the runner finishes its episodes in hand and stops (and stays
  off at boot) and episodes queued for it go to the worker (`TranslationQueue.release_agent_items`,
  `mode: full` → `force`); to agy, the runner autostarts (when "Start with OmbiSub" is on).
- `utils/gemini_models.py` decides the thinking setting per model for all three Gemini paths
  (`cached_translator`, ADK agents, Batch API). `-latest` aliases now get the thinking budget (they did not,
  so every API translation thought at full length); a 400 about the thinking setting is retried once
  without it and remembered for that model.
- "Send a test line" (`POST /api/config/test-gemini-model`) translates one line with a chosen model through
  the real request path and shows which model answered (`model_version`, i.e. what an alias points to).

- Sampling follows Google's defaults: temperature / top-k / top-p are sent only when set in Settings
  (or a project); every hard-coded 0.1–0.3 is gone. Migration `2026-10-23-google-sampling-defaults` clears
  stored values equal to the old defaults (0.3 / 64 / 0.95) from the settings and every project and drops
  the unused `max_output_tokens`. New projects no longer copy sampling in; they inherit it.

### Still open
- Merge `claude/tender-pascal-go38dg` into `main` and push `main`; then the server steps below.
- ~~Command palette's light/dark toggle has no effect~~ (2026-10-30: `darkMode: 'class'`, see below).
- ~~GitHub push webhook accepts unsigned pushes when no secret is set~~ (2026-10-30: it now refuses every
  event without `github_webhook_secret`). Its settings card stays out; exempting it from the API key is only
  needed if it is ever re-added.
- `/api/system/status` `port` is null under plain uvicorn (set by `run_server.py`; fine on the server).

### Review 2026-10-30: cancellation, the runner's claims, cleanup (branch `claude/ecstatic-lovelace-efom9o`)
Found by reading the queue/runner/worker/autopilot paths end to end and driving the UI against the fake agy.
Every item has a regression test (`tests/test_cancel_and_cleanup.py` unless noted); full suite: same 20
environment-only failures on Linux as before, nothing new.
- **Cancel never reached the runner.** Cancel, Stop, Delete, "cancel all", "clear the queue", a job cancel,
  the pipeline cascade and the Autopilot (file deleted in Sonarr) only flipped the row; the lane kept
  translating, submitted, exported, and `update_status(completed)` brought the cancelled row back as
  completed (MorpiceHub then saw "completed" too). Now every path goes through
  `queue_service.cancel_item` (worker task, job flag, agent workspace, row); `cancel_agent_task` tells the
  runner (`AgentRunner.cancel_task`), which kills that lane's CLI request so the lane moves on at once;
  `_ask` treats a failure on a workspace that is no longer its own as a stop, not a model failure; and
  `submit_task_result` refuses when the row is cancelled or gone. `POST /api/queue/{id}/release` puts an
  orphaned running row back to pending (the Active Tasks "Release" used to cancel it).
- `/api/queue/cancel-all` ignored the UI's `?status=` (always "active"); it now honours it. The Activity
  menu has "Cancel waiting translations" and "Cancel all translations" instead of one mislabelled item.
- **The runner ignored `next_retry_at`**: an episode whose share was offline was re-claimed in a tight loop
  and spent its five extraction attempts in seconds. `_pending_items` now waits for the retry time.
- **Extraction inside the claim lock**: building a workspace may extract the source subtitle from the video
  (minutes); it ran under `claim_lock`, so no other lane could claim meanwhile. The claim
  (`claim_item`) stays under the lock, the build (`build_claimed_workspace`) runs outside it; the claim is
  held as `claim:<id>` so per-show limits stay exact, the lane shows "preparing the episode", the Activity
  snapshot shows "being prepared" instead of "orphaned", `release_item` refuses it, and a cancel during the
  build closes the new workspace.
- **Give-ups were invisible**: an episode that failed twice stayed `pending` for nobody (and a Retry did
  nothing until a restart). It is now marked failed with the reason (History shows it with Retry) and the
  count is forgotten so a Retry works; give-ups inside a run of crashes that stops the runner are put back
  to pending (the fault was not the episode's). `test_agent_runner.py::TestCrashes` updated.
- **Autopilot undid cancels**: a cancelled row counted as a failed attempt and was queued again 5 min later
  (three times). It now parks the item as skipped, reason `cancelled` (`test_autopilot_pipeline.py`).
- **A restart cancelled model-engine work**: `worker.stop()` cancels its tasks and the handler marked the
  rows cancelled, so they never resumed. During shutdown (`worker.stopping`) and without a user cancel they
  go back to pending. `worker.cancel_task`/`trigger` are thread-safe now (they are called from threads).
  The worker's idle wait woke on "new item AND a translation finished" (gather); it is FIRST_COMPLETED now.
- **Process cleanup**: one-off agy requests (`agy_llm`) were not killed at shutdown and their timeout timer
  was non-daemon (shutdown could wait 5 min); `stop_all` kills them and they end as `stopped` instead of
  trying the fallback model. On POSIX the CLI runs in its own process group and the whole group is killed
  (a launcher's child kept the output pipe open, so a killed CLI could still hold a lane).
- **One working folder for every CLI request** (`agent_cli.call_dir()`, in the system temp folder): lanes ran
  each request in the episode's workspace, inside the repo when `DATA_DIR` is `backend/`. Claude Code and the
  Gemini CLI put the working folder into the context they send (the Gemini CLI also its listing) and read
  CLAUDE.md / GEMINI.md from it and its parents, so every episode's prompt differed early on and could pull in
  the checkout's own files. What Antigravity sends about its folder is not documented; one stable, empty
  folder costs nothing either way and keeps the CLI's preamble plus the show's shared block identical from
  request to request, which is what provider prefix caching needs.
- **Closed workspaces piled up** (each with the episode and a copy of the glossary): the maintenance loop
  deletes completed/cancelled/expired/released ones older than 7 days (`prune_closed_workspaces`).
- `/api/queue` did its SQLite reads and one metadata file per finished item on the event loop; it runs in a
  thread now. Sonarr/Radarr webhook token compared in constant time.
- UI: the runner panel's "Last error" disappears once something succeeded after it; History offers Retry only
  for failed/cancelled rows (it did nothing for completed ones); the floating Active Tasks panel waits as its
  pill on the Activity page (it covered the running rows' Cancel buttons) and opens again elsewhere; the
  Active Tasks text says "Building the show's context…" during the bootstrap; dark mode is `darkMode:
  'class'` with a pre-paint script in index.html (`src/utils/theme.js`): the palette toggle works and is
  remembered, "Use the system theme" goes back to following the OS.
- Tests: `test_auto_update.py::test_apply_update_executes_when_idle` ran a real `npm ci && npm run build` and
  replaced `backend/static` (network, and a working UI on the PC); it builds nothing now. The fake agy also
  records each request's working folder (`call_NNN.cwd`).

Follow-up 2026-10-31 (same branch):
- **Top-bar indicator replaces the floating Active Tasks panel** (`JobProgressWidget.jsx` removed):
  `activity/ActivityIndicator.jsx` (runner/worker state, `+n` background jobs, a mark when one failed; click:
  episodes in progress with Cancel / Put back, background jobs with Log / Cancel, just-finished jobs), built
  from the activity snapshot (`activity/liveWork.js`, `useLiveWork.js`, `LiveWorkList.jsx`). Activity gains a
  "Background jobs" card; its running queue rows show the snapshot's live step or "Nothing is working on it".
- **Library**: `GET /api/library` (`storage.get_library`: every project grouped, only row fields, cached) and
  per-title queue counts as `by_project` in `GET /api/activity`; the page no longer polls `/api/queue` (its
  counts were wrong past 100 queued items) or loads every project's full metadata. Rows memoized, 200 rendered
  at a time.
- **On-demand (MorpiceHub)**: progress of agy-translated episodes (from the runner lane), `ahead` for queued
  episodes, `POST /api/on-demand/subtitles/{id}/cancel`, status reports `engine`/`paused`/`runner`; a cancelled
  extraction reads as cancelled and no longer chains a translation. The integration guide for MorpiceHub is
  `deploy/morpicehub/README.md`. MorpiceHub's own code is not in any repository this account reaches, so
  whether it calls these endpoints today could not be checked from here.

Not changed, worth knowing:
- **Session reuse per show** was considered and rejected: a resumed CLI conversation re-sends its whole
  history every request (tokens and quota grow with every episode, quality drops with context length, lanes
  could no longer run a show's episodes in parallel, and a failure poisons the session). The runner's
  stateless requests with the shared block first (guide, style rules), then a curated per-chunk context
  (three episode summaries, matching glossary terms, characters, the last 8 translated lines) are the
  better design; the stable working folder above is what lets prefix caching do its part.
- Two lanes on a new show: the second waits on the per-show bootstrap lock and then finds the context built
  (no second request), so it idles for the bootstrap's duration. `per_show` 1 avoids that if it matters.
- `/api/agent/runner/start` has no `bootstrap` field (only `PUT /api/agent/runner/config` sets it).

### Frontend map (CLAUDE.md is git-ignored, so here)
Routes: `/` Library · `/activity?tab=queue|history|autopilot` · `/review` · `/settings?section=translation|
connections|subtitles|automation|security|general` · `/system?tab=status|logs|backup|updates` ·
`/project/:name?tab=episodes|glossary|context|history` · `/project/:name/episode/:ep`. Old routes redirect.
Shared kit: `frontend/src/components/ui/index.jsx` (Page, PageHeader, Card, Button, Tabs, Modal,
ConfirmDialog, OverflowMenu, StatusChip, ProgressBar, …). Translate actions: `hooks/useTranslate.js`.

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
