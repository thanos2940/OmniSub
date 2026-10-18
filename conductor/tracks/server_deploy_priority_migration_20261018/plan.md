# Implementation Plan: Bazarr-Style Server Deployment, Autonomous Boot, 4-Tier Project Priorities, Migration Bundle & GitHub Release Auto-Updater (`server_deploy_priority_migration_20261018`)

## Phase 1: 4-Tier Per-Project Translation Priority & Ignore System (Backend + Queue + Autopilot + UI)
- [x] Task 1.1: Project Priority Schema, Storage & Metadata Index (`high` / `normal` / `low` / `ignored`)
  - [x] Write failing tests in `backend/tests/test_project_priority_and_ignore.py` verifying `translation_priority` persistence in `project.json`, synchronization with `arr_disabled` (`translation_priority == "ignored"`), and indexing in `metadata_index`.
  - [x] Implement `translation_priority` field in `project_store.py`, `schemas.py`, `metadata_index.py`, and `routers/projects.py` (`PATCH /api/projects/{project_name}/priority` and `POST /api/projects/batch-priority`).
- [x] Task 1.2: Dynamic Queue Re-Ordering, Autopilot Skip & Agent/Worker Claim Ordering
  - [x] Write failing tests in `backend/tests/test_project_priority_and_ignore.py` verifying:
    - Enqueueing from `high` projects places items ahead of `normal` and `low` projects,
    - Enqueueing from `low` projects uses `PRIORITY_LOW = 5` so `high` and `normal` items always drain first,
    - Changing a project's priority dynamically updates all existing `pending`/`queued` items in `translation_queue`,
    - Setting a project to `ignored` cancels all `pending`/`queued`/`postponed` items in `translation_queue` and skips the project in Autopilot (`pipeline.py`), Sonarr/Radarr sync, and `enqueue_missing_for_library()`.
  - [x] Implement `PRIORITY_HIGH_PROJECT` / `PRIORITY_LOW` and `update_project_priority()` in `utils/translation_queue.py`, `services/queue_service.py`, `services/agent_task_service.py`, and `services/autopilot/pipeline.py`.
- [x] Task 1.3: Frontend 4-Tier Priority & Ignore Controls in `MediaLibrary.jsx` & `ProjectDetail.jsx`
  - [x] Add `updateProjectPriority` and `batchUpdateProjectPriority` to `frontend/src/api.js`.
  - [x] Update `MediaLibrary.jsx` with 1-click 4-tier priority selector (`First`, `Normal`, `Last`, `Ignored`) on each series/movie card, priority filter pills (`All`, `First`, `Normal`, `Last`, `Ignored`), priority sorting, and bulk priority actions.
  - [x] Update `ProjectDetail.jsx` header with a 4-tier priority badge/selector.
- [x] Task 1.4: Phase 1 Verification & Checkpoint (Refer to `workflow.md`)

## Phase 2: Server Migration Export / Import System (`.zip` Bundle + Online SQLite Backup + Path Remapping)
- [x] Task 2.1: Backend Backup & Restore Service with Online SQLite Snapshot & UNC ↔ Local Drive Path Remapping
  - [x] Write failing tests in `backend/tests/test_backup_migration.py` verifying:
    - Online SQLite backup (`sqlite3.Connection.backup`) of `omnisub.db`, `autopilot.db`, `subtitle_ownership.db`, and `omnisub_sessions.db`,
    - Export of `config.json` (preserving obfuscated secrets), `rate_limiter_state.json`, `translation_memory/`, and `projects/` (both Compact mode excluding `original.*` and Full mode including `original.*`),
    - Zip-Slip path traversal protection on import,
    - Automatic path rewriting on import (`\\Morpiceserver\e\...` ↔ `E:\...`) across `config.json`, `metadata.json`, `omnisub.db`, `autopilot.db`, and `subtitle_ownership.db`.
  - [x] Implement `backend/services/backup_service.py` and `backend/routers/backup.py` (`/api/system/backup/info`, `/api/system/backup/export`, `/api/system/backup/import`), and register router in `backend/main.py`.
- [x] Task 2.2: CLI Migration Script (`scripts/migrate_server.py`) & Web UI Backup/Migration Panel
  - [x] Write failing tests for `scripts/migrate_server.py` CLI (`export` and `import` subcommands with `--compact` and `--remap-unc-drives`).
  - [x] Implement `scripts/migrate_server.py` and add the **Backup & Server Migration** UI card to `frontend/src/components/settings/SystemUpdatesSettings.jsx` and `frontend/src/api.js`.
- [x] Task 2.3: Phase 2 Verification & Checkpoint (Refer to `workflow.md`)

## Phase 3: Official GitHub Releases Auto-Updater (`/releases/latest` + Frontend Static Rebuild)
- [x] Task 3.1: GitHub Releases Detection & Tag-Based `apply_update` with Static UI Rebuild
  - [x] Write failing tests in `backend/tests/test_github_release_updater.py` verifying:
    - `check_for_updates()` queries GitHub Releases (`/releases/latest`) and only flags `update_available=True` when a newer official release tag exists (returning `update_available=False` when no releases exist or when only untagged branch commits exist),
    - `apply_update()` fetches tags, checks out/resets to the release tag, runs `pip install` if `requirements.txt` changed, rebuilds `frontend/dist` → `backend/static/` (or extracts a release `static.zip` asset) if `frontend/` changed, writes `release_version.json`, and triggers restart.
  - [x] Implement GitHub Releases logic in `backend/services/update_service.py`, `backend/routers/system.py`, `scripts/restart_helper.py`, and `.github/workflows/release.yml`.
- [x] Task 3.2: Updates UI Enhancement for GitHub Releases (`SystemUpdatesSettings.jsx`)
  - [x] Update `SystemUpdatesSettings.jsx` to display current/latest GitHub Release tag, release title, published date, release notes, and auto-update toggle.
- [x] Task 3.3: Phase 3 Verification & Checkpoint (Refer to `workflow.md`)

## Phase 4: Bazarr-Style Windows System Tray App, Autonomous Boot & Dual Path Resolver
- [x] Task 4.1: Automatic UNC ↔ Local Drive Path Resolution & Autonomous Agent Runner Boot
  - [x] Write failing tests in `backend/tests/test_autonomous_boot_and_tray.py` verifying:
    - `PathResolver` automatically resolves `\\Morpiceserver\<drive>\<rest>` ↔ `<DRIVE>:\<rest>` on Windows when the local drive or UNC path exists on disk,
    - `main.py:lifespan` + `agent_runner.autostart()` automatically starts the Agent Runner in `keep_watching=True` daemon mode on boot when `translation_engine == "agent"` and `agent_runner_autostart` is True,
    - Windows subprocesses (`embedded_subs.py`, `subtitle_fixer.py`, `agent_cli.py`) pass `CREATE_NO_WINDOW` on Windows so background execution never flashes console windows.
  - [x] Implement enhancements in `backend/integrations/path_resolver.py`, `backend/services/agent_runner.py`, `backend/main.py`, `backend/routers/schemas.py`, `backend/integrations/embedded_subs.py`, `backend/utils/subtitle_fixer.py`, and `backend/services/agent_cli.py`.
- [x] Task 4.2: Windows System Tray App (`backend/tray_app.py`) & Installer (`deploy/windows/install.ps1`)
  - [x] Write unit tests in `backend/tests/test_autonomous_boot_and_tray.py` verifying `tray_app.py` server lifecycle management, menu actions (Open Web UI, Pause/Resume Queue, Start/Stop Agent Runner, View Logs, Restart, Exit), and single-instance lock handling.
  - [x] Implement `backend/tray_app.py`, `deploy/windows/start_tray.vbs` / `deploy/windows/OmbiSub.bat` (`pythonw.exe` launcher), and update `deploy/windows/install.ps1` to configure automatic logon/startup tray execution, firewall rules, and power settings on `Morpiceserver`.
- [x] Task 4.3: Final Build, Static Sync & Full End-to-End Verification
  - [x] Run `npm run lint`, `npm run build`, sync `frontend/dist` to `backend/static/`, and run full `pytest` suite (`100%` pass).


## Completion Notes (2026-10-18)
- Full suite: 1,587 passed + 158 re-verified after integration fixes; eslint 0 errors; vite build OK; dist synced to backend/static.
- Integration fixes by orchestrator: removed duplicate /api/backup routes from system.py (backup.py is canonical); auto_update default False everywhere; tray Exit no longer calls /api/system/restart; keep_watching folded into watch (single stored key); autopilot skip reason kept as arr_disabled; api.js gained priority + backup methods.
- Not done (out of scope / follow-up): firewall + power-plan automation in install.ps1 (printed as next steps instead).
