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
- [ ] A1 Service mode runs the headless supervisor (`tray_app.py --headless`): respawn with back-off on crash, at once on exit 42; `/api/system/restart` and the updater use exit 42 when supervised. Console output to `logs/service_console.log`.
- [ ] A2 `deploy/windows/install.cmd` (self-elevating, asks port once, default 2945, Service mode + firewall + keep-awake), `update.cmd`, `uninstall.cmd`.
- [ ] A3 Updater: `update_channel` `main` | `releases`; `main` follows `origin/main` (ahead/behind, changelog of commits), apply with guards, lock-file deps, UI build when `frontend/` changed, exit 42. Remove duplicate `/api/system/update` + `/api/system/check-update` (point the UI at `/api/system/updates/*`).
- [ ] A4 `GET /api/system/status` and `GET /api/system/logs?name=&lines=` (whitelist: ombisub, agent_runner, tray_app, service_console).
- [ ] A5 URL base (`url_base`): prefix-tolerant ASGI middleware, `<base href>` + `window.__OMBISUB_BASE__` injection into index.html, Vite `base: './'`, router basename, API/WS URLs under base; uvicorn `proxy_headers`/`forwarded_allow_ips`.
- [ ] A6 Git-ignore lock/runtime files (`backend/*.lock`, `backend/logs/`, …) so the updater's dirty-tree guard is meaningful.
- [ ] A7 `docs/DEPLOYMENT_GUIDE.md`: install/update/uninstall, Caddyfile (subdomain + `/ombisub` path), auth before exposing.

### WS-B: Engine & context — agy first, bootstrap, growth
- [ ] B1 `services/agy_llm.py` `complete()` (agent CLI first; API rescue only on safety refusal).
- [ ] B2 `retranslate-line` through agy with neighbours, glossary hits, guide, characters (synchronous, lock, timeout); API rescue on refusal; same response contract.
- [ ] B3 In agent mode, context/glossary create & enhance, summary generation, term harvest, ScriptGuard repair use `agy_llm` (background jobs keep their contracts).
- [ ] B4 Runner bootstrap before the first episode of a project without guide/glossary (per-project lock, `bootstrapped_at`, bounded retry, never blocks translation); `ensure_context` honoured by the runner.
- [ ] B5 Guide growth every 6 translated episodes (≤5 additions, cap ~6000 chars); character genders from bootstrap feed `_characters()`.
- [ ] B6 Defaults: `translation_engine` = `agent`; runner autostarts in watch mode when engine is agent; "pause translations" pauses runner + worker translation only.

### WS-C: Integrations & automation
- [ ] C1 Autopilot is the only automation: webhooks always to the watcher; with Autopilot off only a library refresh of that title; remove legacy auto-translate execution path and "Bazarr wait delay".
- [ ] C2 Scheduled sync never mass-enqueues translations.
- [ ] C3 Plex refresh after every OmbiSub export (debounced per section), `POST` test endpoint for Plex; Sonarr/Radarr/Plex connection tests reachable from one router.
- [ ] C4 Movie / episode with no imported source: "translate" path creates the episode from the media file and extracts (verify every entry point; fix gaps).

## Phase 2 — orchestrator
- [ ] O1 Review + merge WS-A, WS-B, WS-C; resolve conflicts; full suite.
- [ ] O2 Config migrations (one module, recorded): engine → agent; legacy auto-translate on → Autopilot on (backlog off); drop dead keys.

## Phase 3 — frontend (sequential, after Phase 2)
- [ ] F1 Navigation (Library · Activity · Review · Settings · System), merged Library, Activity page (Queue + runner panel, History, Autopilot), System page (Status, Logs, Backup, Updates); remove Dashboard page.
- [ ] F2 Project page (4 tabs, one Translate per episode, overflow menu, "Build with agy"), Episode view retranslate via agy, Settings reorganisation (Translation, Connections, Subtitles, Automation, Security, General); remove Sandbox Tester, Presets, Hyperparameters, Token Stats, Simple-pipeline wizard, legacy auto-translate UI.

## Phase 4 — verification
- [ ] V1 Full pytest, eslint, vite build; preview the UI (light/dark, narrow width).
- [ ] V2 Update CLAUDE.md, handoff, memory; commit; deployment steps for the server.
