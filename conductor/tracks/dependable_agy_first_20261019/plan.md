# Plan: Dependable, agy-first OmbiSub (`dependable_agy_first_20261019`)

Implementation by Claude Sonnet 5.5 subagents (high effort) in isolated worktrees; every change set is
reviewed, tested and merged by the orchestrator (Opus). Spec: [spec.md](./spec.md).

Rules for every workstream
- Run tests from `backend/` with the repo venv: `"<repo>/.venv/Scripts/python.exe" -m pytest -q -p no:cacheprovider`.
  Full suite must stay green; every behaviour change gets a regression test (offline: fake agent CLI,
  mocked LLM seam — never a real CLI or API).
- Do **not** write config migrations; change defaults only and list needed migrations in the report
  (the orchestrator writes them in one place).
- Stay inside the files of your workstream; report anything outside it instead of editing it.
- Windows/PowerShell 5.1 quirks: no double quotes inside native-command arguments; wrap one-item arrays.

## Phase 1 — backend workstreams (parallel)

### WS-A: Operations — service, updates, logs, Caddy
- [x] A1 Service mode runs the headless supervisor (`tray_app.py --headless`): respawn with back-off on crash, at once on exit 42; `/api/system/restart` and the updater use exit 42 when supervised. Console output to `logs/service_console.log`.
- [x] A2 `deploy/windows/install.cmd` (self-elevating, asks port once, default 2945, Service mode + firewall + keep-awake), `update.cmd`, `uninstall.cmd`.
- [x] A3 Updater: `update_channel` `main` | `releases`; `main` follows `origin/main` (ahead/behind, changelog of commits), apply with guards, lock-file deps, UI build when `frontend/` changed, exit 42. Remove duplicate `/api/system/update` + `/api/system/check-update` (point the UI at `/api/system/updates/*`).
- [x] A4 `GET /api/system/status` and `GET /api/system/logs?name=&lines=` (whitelist: ombisub, agent_runner, tray_app, service_console).
- [x] A5 URL base (`url_base`): prefix-tolerant ASGI middleware, `<base href>` + `window.__OMBISUB_BASE__` injection into index.html, Vite `base: './'`, router basename, API/WS URLs under base; uvicorn `proxy_headers`/`forwarded_allow_ips`.
- [x] A6 Git-ignore lock/runtime files (`backend/*.lock`, `backend/logs/`, …) so the updater's dirty-tree guard is meaningful.
- [x] A7 `docs/DEPLOYMENT_GUIDE.md`: install/update/uninstall, Caddyfile (subdomain + `/ombisub` path), auth before exposing.

### WS-B: Engine & context — agy first, bootstrap, growth
- [x] B1 `services/agy_llm.py` `complete()` (agent CLI first; API rescue only on safety refusal).
- [x] B2 `retranslate-line` through agy with neighbours, glossary hits, guide, characters (synchronous, lock, timeout); API rescue on refusal; same response contract.
- [x] B3 In agent mode, context/glossary create & enhance, summary generation, term harvest, ScriptGuard repair use `agy_llm` (background jobs keep their contracts).
- [x] B4 Runner bootstrap before the first episode of a project without guide/glossary (per-project lock, `bootstrapped_at`, bounded retry, never blocks translation); `ensure_context` honoured by the runner.
- [x] B5 Guide growth every 6 translated episodes (≤5 additions, cap ~6000 chars); character genders from bootstrap feed `_characters()`.
- [x] B6 Defaults: `translation_engine` = `agent`; runner autostarts in watch mode when engine is agent; "pause translations" pauses runner + worker translation only.

### WS-C: Integrations & automation
- [x] C1 Autopilot is the only automation: webhooks always to the watcher; with Autopilot off only a library refresh of that title; remove legacy auto-translate execution path and "Bazarr wait delay".
- [x] C2 Scheduled sync never mass-enqueues translations.
- [x] C3 Plex refresh after every OmbiSub export (debounced per section), `POST` test endpoint for Plex; Sonarr/Radarr/Plex connection tests reachable from one router.
- [x] C4 Movie / episode with no imported source: "translate" path creates the episode from the media file and extracts (verify every entry point; fix gaps).

## Phase 2 — orchestrator
- [x] O1 Review + merge WS-A, WS-B, WS-C; resolve conflicts; full suite.
- [x] O2 Config migrations (one module, recorded): engine → agent; legacy auto-translate on → Autopilot on (backlog off); drop dead keys.

### Orchestrator follow-ups (done)
- [x] Library-wide "Translate all missing" also reaches subtitles inside videos (background job, single flight); job ids no longer collide.
- [x] Tests never start the real agent runner (conftest guard, `real_autostart` marker).
- [x] Sync stores year/genres/overview for the bootstrap; `agent_runner.recover()` restarts a runner that stopped on an error; "Translate missing lines" goes through the runner in agent mode.
- [x] Shared UI kit `frontend/src/components/ui/index.jsx`.

## Phase 3 — frontend (sequential, after Phase 2)
- [x] F1 Navigation (Library · Activity · Review · Settings · System), merged Library, Activity page (Queue + runner panel, History, Autopilot), System page (Status, Logs, Backup, Updates); remove Dashboard page.
- [x] F2 Project page (4 tabs, one Translate per episode, overflow menu, "Build with agy"), Episode view retranslate via agy, Settings reorganisation (Translation, Connections, Subtitles, Automation, Security, General); remove Sandbox Tester, Presets, Hyperparameters, Token Stats, Simple-pipeline wizard, legacy auto-translate UI.

## Phase 4 — verification
- [x] V1 Full pytest, eslint, vite build; preview the UI (light/dark, narrow width). *(2026-10-21: every page walked at 1440 and 390 px, light and dark, on a scratch data dir with the test suite's fake agy; Translate → runner → translated → Delete flow driven in the browser. Fixes it found are listed in HANDOFF.md.)*
- [x] V2 Update CLAUDE.md, handoff, memory; commit; deployment steps for the server. *(CLAUDE.md is git-ignored and absent from fresh clones; its Frontend notes live in HANDOFF.md.)*

## Phase 3 detail — UI spec (from the 2026-10-19 walkthrough of the running UI)

Observed: Home `/` is a bare project list (no progress); `/library` (MediaLibrary) is the useful list
(progress bars, status chips, priority, Translate missing / View / Disable). Project page: "Back to
Dashboard", "Guided Pipeline Wizard", "Sync Disk & Server", 8 tabs that overflow into a horizontal
scrollbar, a "Quick tools" strip (Agent Translate, Translate with runner, Batch Extract Subs, SubtitleEdit
Polish, Format Conversion) and episode cards with 2 translate icons + 4 tool icons each. Queue page: six
header buttons (Resume Worker, Sync Agent Tasks, Sync Disk Subtitles, Cancel All Pending, Clear Queue,
Refresh), a small Agent runner card, a Gemini per-model quota panel. Settings opens on "Gemini AI" with the
engine radio buried inside; its tab row also overflows. Top bar: Home, search, API Key, Unsecured,
Dashboard, Review, Queue, Autopilot, Library, Health, gear.

### Global
- Top bar: logo (= Library), nav **Library · Activity · Review (count) · Settings · System (health dot)**,
  search/command palette, lock icon (auth state → Settings > Security), runner pill ("agy · 2 translating"
  / "paused" / "idle" → Activity). Old routes redirect: `/library`→`/`, `/queue`,`/autopilot`→`/activity`,
  `/health`,`/dashboard`→`/system`.
- No horizontal tab scrollbars: tabs wrap or become a select on narrow screens. Light + dark, 390 px wide works.

### Library (`/`) — MediaLibrary becomes home
- Header: "Library", summary (titles, % translated, missing, in queue), actions: Sync now, Translate all
  missing (confirm with count). No worker buttons.
- Tabs: Series · Movies · Other (manual projects; "New project" lives here) · Ignored.
- Filters: search, status, priority, sort. Row: title, status chip, priority menu, progress bar, counts,
  Translate missing, open.

### Activity (`/activity`)
- Runner panel on top (engine + model, state, lanes with progress, Pause/Resume, last error, quota pause
  note). Tabs: Queue (actions: Run now, Postpone, Cancel, Logs; bulk), History (completed/failed; Retry),
  Autopilot (existing AutopilotPage pieces). Gemini quota panel only when the engine is `model`.
  Maintenance actions (sync disk subtitles, clear queue, cancel all) in one "More" menu.

### System (`/system`)
- Status (from `/api/system/status` + existing health checks + Library translation check card; Restart),
  Logs (pick log, lines, auto-refresh, filter, download), Backup (existing card), Updates (channel, check,
  apply, auto-update, commits list).

### Project page
- Breadcrumb Library › Title. Header: type, language, priority, primary **Translate missing**, Sync, ⚙
  (project settings incl. VisualGuard/formatting). Tabs: **Episodes · Glossary · Context · History**.
- Episodes: filter chips (All, Translated, Missing, In progress, No subtitle), search, one table grouped
  by season (no card grid). Row: status, progress, last translated, primary **Translate** (or
  **Retranslate**), overflow menu (Extract from video, Inspect tracks, SubtitleEdit fixes, Check missing
  lines, ScriptGuard, Upload subtitle, Delete translation). Bulk bar: Translate, Extract, Delete translation.
- Glossary: glossary table + character profiles section + "Build with agy" / "Enhance with agy".
- Context: guide editor + "Build with agy" / "Enhance with agy" + episode summaries (collapsible).
- History: TM & QC. Removed: Guided Pipeline Wizard, Quick tools strip, Token Stats tab, Agent-vs-model
  double buttons, model pickers when the engine is agent.

### Settings (`/settings`, side navigation)
- Translation (how it works: agy first, API for refused lines; runner: CLI, model, fallbacks, lanes, per
  show, chunk lines, fix rounds, autostart; API rescue: on/off, model, Gemini key set/test/remove, safety
  filter; Advanced (collapsed): model engine + its rosters/presets/hyperparameters/concurrency/batch).
- Connections (Sonarr, Radarr, Plex — URL/key/token + Test; path mappings + test; webhook URLs to paste
  into Sonarr/Radarr; media probe diagnostic collapsed).
- Subtitles (formats, ASS, lyrics, embedded extraction, SubtitleEdit path, formatting/VisualGuard defaults).
- Automation (Autopilot; notifications/digest). Security (login, API key for integrations). General
  (target language and defaults, URL base, trusted proxies; port shown read-only).
- Removed: Sandbox Tester, Quality Presets / Hyperparameters tabs (folded into Translation › Advanced),
  legacy Auto-Translate Mode + Bazarr delay.
