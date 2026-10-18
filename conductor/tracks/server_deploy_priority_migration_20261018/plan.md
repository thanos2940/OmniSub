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

## Phase 5: Post-Implementation Review Fixes (2026-10-18)
- [x] Task 5.1: Priority tier vs `arr_disabled` consistency
  - [x] `save_project_metadata` diffs against the stored copy: a path that only flips `arr_disabled` (Sonarr/Radarr sync, on-demand re-enable, legacy enable/disable routes) is no longer silently reverted; un-ignoring restores `priority_before_ignore`; explicit Ignore sets `ignored_by_user`, which sync and on-demand re-enables respect.
  - [x] Ignored blocks automatic work only (`queue_service.skips_ignored`); a Translate click, Run Now or MorpiceHub request still queues instead of returning an empty item id. Agent claim paths use the same rule and `list_pending_tasks` no longer mutates the queue.
  - [x] Re-enqueueing a finished row takes the new priority (was `MIN` with a stale tier priority).
- [x] Task 5.2: Agent runner claims Run Now / Continue items (`queued` status) and orders priority 10 first (`_pending_items`, `_pending_count`, `_claim_specific`, `list_pending_tasks`, `peek`).
- [x] Task 5.3: `PathResolver` no longer maps a not-yet-written UNC target onto an unrelated local drive that merely has the same letter.
- [x] Task 5.4: Migration import stages and verifies before touching live data, restores DBs through the SQLite backup API (no stale `-wal`), snapshots every replaced DB, swaps `translation_memory/` whole, only extracts manifest-listed entries; read-only snapshot URI fixed. `full` is now the default bundle (compact drops `original.*`, which ASS export needs); UI downloads through a one-time link instead of an in-memory blob; CLI refuses to import over a running server.
- [x] Task 5.5: Updater refuses to reset a checkout onto a release tag that lacks its local commits (unless forced).
- [x] Task 5.6: `install.ps1 -OpenFirewall` (inbound TCP on Private networks) and `-KeepAwake` (no sleep/hibernate on AC) — the Phase 4 follow-up.
- [x] Task 5.7: Real-data dry run (731 projects, 3,086 episodes; full bundle 2.67 GB) found two more export/import gaps, now fixed: the bundle skipped project files other than `project.json`/`glossary.json` (episode summaries, character profiles, suggestions) and episode subfolders (`disk_backup/`), plus `agent_runner.json`; remapped ownership keys were not re-normalised (`ownership.norm_path`), so the server would have treated its own subtitles as foreign.
- [x] Task 5.8: Regression tests for each fix (each fails on the pre-fix code); full suite, eslint and vite build green.

## Remaining (operational, needs the owner)
- [ ] Move to `Morpiceserver`: export a **full** bundle, install with `install.ps1 -Mode Tray -OpenFirewall -KeepAwake`, stop OmbiSub, import with `--remap-unc-to-local`, start it.
- [ ] Publish the first GitHub Release tag so the updater has something to track.

