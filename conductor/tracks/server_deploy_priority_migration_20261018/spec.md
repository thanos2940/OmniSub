# Specification: Bazarr-Style Server Deployment, Autonomous Boot, 4-Tier Project Priorities, Migration Bundle & GitHub Release Auto-Updater (`server_deploy_priority_migration_20261018`)

## 1. Overview
Transform **OmbiSub** from a console-launched LAN workstation tool into a self-contained, Bazarr-style 24/7 server application running directly on the Plex Server PC (`Morpiceserver`). This track delivers four cohesive pillars:
1. **Bazarr-Style Windows System Tray & Logon Server App + Full Autonomous Boot**:
   - A windowless background tray application (`backend/tray_app.py` launched via `pythonw.exe` and registered at Windows logon/startup via `deploy/windows/install.ps1`) so Agent CLIs (`agy`, `claude`, `gemini`) share the interactive desktop session and auth tokens with zero console windows.
   - Full autonomous startup on boot: FastAPI server, Autopilot watcher, Background Worker, and Agent Runner (in `'Keep Watching'` daemon mode when `translation_engine == 'agent'`) start automatically on boot.
   - Automatic dual path resolution so both UNC network paths (`\\Morpiceserver\e\Shows\...`) and local server drive paths (`E:\Shows\...`, `M:\Movies\...`) resolve seamlessly on `Morpiceserver`.
2. **4-Tier Per-Series / Per-Movie Translation Priority & Ignore System**:
   - Every project (series or movie) supports a 4-tier `translation_priority`:
     - **`high` ("Translate First")**: Boosted ahead of all Normal and Low items across Autopilot, scheduled Sonarr/Radarr sync, and backlog enqueueing.
     - **`normal` ("Normal" — default)**: Standard priority ordering.
     - **`low` ("Translate Last / Deprioritized")**: Enqueued at lowest priority (`PRIORITY_LOW = 5`) so all `high` and `normal` series/movies finish before any `low` series/movie is translated.
     - **`ignored` ("Ignored / Do Not Translate")**: Skipped by Autopilot, scheduled sync, and bulk backlog enqueueing (`arr_disabled = True`).
   - Dynamic queue synchronization: changing a project's priority tier immediately re-prioritizes all of its currently `pending`/`queued` items in `translation_queue` (or auto-cancels its `pending`/`queued`/`postponed` items when set to `ignored`).
   - 1-click priority selector on `MediaLibrary.jsx` cards, bulk priority/ignore toolbar actions, filter/sort by priority tier, and priority badge/selector in `ProjectDetail.jsx`.
3. **Full Server Migration Export / Import System (`.zip` Bundle + Path Remapping)**:
   - Backend API (`routers/backup.py`, `services/backup_service.py`), CLI script (`scripts/migrate_server.py`), and Web UI (`Settings -> Backup & Migration` tab).
   - Exports a consistent `.zip` migration bundle using SQLite's online `sqlite3.Connection.backup()` API (zero lock contention on `omnisub.db`, `autopilot.db`, `subtitle_ownership.db`, `omnisub_sessions.db`), `config.json` (with preserved obfuscated API keys/secrets), `rate_limiter_state.json`, `translation_memory/` (LanceDB), and `projects/` (`project.json`, `metadata.json`, `data.json`, with selectable toggle to include or exclude raw `original.*` source subtitle files for a compact vs full bundle).
   - Imports a `.zip` bundle onto the target server with atomic replacement, automatic UNC ↔ local drive path normalization/remapping (e.g., `\\Morpiceserver\e\...` ↔ `E:\...`), and automatic index/cache refresh.
4. **Official GitHub Releases Auto-Updater (`/releases/latest`)**:
   - Re-architect `UpdateService` (`backend/services/update_service.py`) to track official **GitHub Releases** (`/repos/{GITHUB_REPO}/releases/latest` and git release tags `v*`) instead of triggering on raw commits pushed to `main`.
   - When a new GitHub Release is published and `auto_update_enabled=True` (and `has_active_jobs() == False`):
     - Fetch release tags (`git fetch --tags origin --force`), verify clean working tree, checkout/reset to the release tag (`<tag_name>`),
     - Install updated Python requirements (`pip install -r backend/requirements.txt`) if changed,
     - Automatically rebuild the frontend (`npm run build` → atomic swap into `backend/static/`) or extract prebuilt `static.zip` release asset if attached,
     - Record the installed release tag/version and trigger a graceful restart compatible with the Tray app, Scheduled Task, and standalone server.

---

## 2. Functional Requirements

### 2.1 Pillar 1: Bazarr-Style Tray/Background Deployment & Autonomous Boot
- **FR-1.1 (`backend/tray_app.py` & `deploy/windows/install.ps1`):**
  - Provide a lightweight Windows System Tray controller (`backend/tray_app.py`, using `pystray` + `Pillow` or native Win32/Tk fallback if headless) that launches `run_server.py` in-process or as a managed child without a console window (`pythonw.exe`).
  - System Tray right-click menu actions:
    - *Open OmbiSub Web UI* (`http://localhost:8000`)
    - *Server Status* (live status indicator: Running / Queue count / Agent Runner status)
    - *Toggle Pause Queue Worker*
    - *Toggle Agent Runner (Keep Watching)*
    - *Open Log File* (`logs/ombisub.log`)
    - *Restart Server*
    - *Exit OmbiSub* (graceful shutdown)
  - Update `deploy/windows/install.ps1` to support `-TrayMode` (default on interactive Windows server setups, registering a logon/startup Scheduled Task running `pythonw.exe backend\tray_app.py` with `RunLevel Highest` / `AtLogOn` + `AtStartup` fallback, plus optional Windows Startup folder shortcut).
- **FR-1.2 (Full Autonomous Boot in `backend/main.py` & `services/agent_runner.py`):**
  - Add config setting `agent_runner_autostart: bool = True` (default `True`).
  - On server startup (`main.py:lifespan`), when `translation_engine == "agent"` and `agent_runner_autostart` is enabled (or `agent_runner` state was previously running), automatically start `agent_runner` in `keep_watching=True` daemon mode so queued episodes translate hands-free after a reboot.
- **FR-1.3 (Automatic UNC ↔ Local Drive Path Resolution in `integrations/path_resolver.py`):**
  - Enhance `PathResolver` on Windows so if a stored path is a UNC path matching the local machine hostname or configured server share (e.g. `\\Morpiceserver\e\Shows\...` where single-letter share `\e\` corresponds to local drive `E:\Shows\...`, or vice versa), it automatically resolves to whichever path (`E:\Shows\...` or `\\Morpiceserver\e\Shows\...`) exists on disk even before manual path mappings are configured.

### 2.2 Pillar 2: 4-Tier Per-Project Priority & Ignore System
- **FR-2.1 (Project Schema & Storage):**
  - Add `translation_priority: str = "normal"` (`"high"` | `"normal"` | `"low"` | `"ignored"`) to project metadata (`project.json`, `schemas.py`, `metadata_index.py`).
  - Keep `arr_disabled: bool` synchronized with `translation_priority == "ignored"` (`arr_disabled = (translation_priority == "ignored")`) for full backward compatibility across all existing checks.
- **FR-2.2 (Queue Priority Mapping & Dynamic Re-Ordering):**
  - Define queue priority constants in `utils/translation_queue.py`:
    - `PRIORITY_RUN_NOW = 10` (boosted manual single-item override, ordered first via `CASE WHEN priority = 10 THEN -1`)
    - `PRIORITY_HIGH_PROJECT = -1` (or project priority tier modifier: items from `"high"` priority projects sort ahead of `"normal"` projects, which sort ahead of `"low"` projects at `PRIORITY_LOW = 5`).
  - Specifically, in `translation_queue.py` and `queue_service.py`:
    - Add `TranslationQueue.update_project_priority(project_name: str, priority_tier: str) -> Dict[str, int]`:
      - When `priority_tier == "ignored"`: cancels all `pending`, `queued`, and `postponed` translation items for `project_name`.
      - When `priority_tier == "high"`: updates existing `pending`/`queued` items for `project_name` to priority `0` (or `-1` high-priority tier) so they are claimed before `normal` and `low` items.
      - When `priority_tier == "normal"`: restores existing `pending`/`queued` items for `project_name` to standard priority (`PRIORITY_MANUAL = 1` / `PRIORITY_SYNC = 2` / `PRIORITY_BACKLOG = 3`).
      - When `priority_tier == "low"`: demotes existing `pending`/`queued` items for `project_name` to `PRIORITY_LOW = 5` (`Translate Last`).
    - Ensure `claim_next()` (worker) and `agent_task_service.claim_next_task()` (agent runner) both respect the exact same priority ordering (`priority = 10` first, then `priority ASC`, `created_at ASC`).
  - Update `enqueue_missing_for_library()` to iterate projects ordered by tier (`high` first, `normal` second, `low` last, skipping `ignored`).
- **FR-2.3 (API & Frontend UI Controls):**
  - Add `PATCH /api/projects/{project_name}/priority` and `POST /api/projects/batch-priority` (`{ "projects": [...], "priority": "high"|"normal"|"low"|"ignored" }`).
  - In `MediaLibrary.jsx`:
    - Add a compact 4-tier priority dropdown/pill on every series/movie card (`First`, `Normal`, `Last`, `Ignored`),
    - Add priority filter pills (`All`, `First`, `Normal`, `Last`, `Ignored`) and sort option (`Sort by Priority`),
    - Add batch priority actions when multiple shows/movies are selected.
  - In `ProjectDetail.jsx`:
    - Add the 4-tier priority selector badge in the project header next to Project Settings.

### 2.3 Pillar 3: Easy Server Migration (Export / Import Setup & Database Bundle)
- **FR-3.1 (`backend/services/backup_service.py` & `backend/routers/backup.py`):**
  - `POST /api/system/backup/export` (or `GET /api/system/backup/download?include_originals=false&include_tm=true`):
    - Creates a consistent `.zip` archive streaming or saved to temp dir containing:
      - `manifest.json` (version, timestamp, hostname, counts of projects/episodes, included components, source `DATA_DIR`),
      - `config.json` (complete global config including obfuscated secrets),
      - `rate_limiter_state.json`,
      - Consistent snapshots of `omnisub.db`, `autopilot.db`, `subtitle_ownership.db`, and `omnisub_sessions.db` created via `sqlite3.Connection.backup()`,
      - `translation_memory/` (LanceDB directory, optional via `include_tm: bool = True`),
      - `projects/` (`project.json`, `episodes/*/metadata.json`, `episodes/*/data.json`, and optionally `episodes/*/original.*` via `include_originals: bool = True`).
  - `POST /api/system/backup/import` (multipart `.zip` upload or local file path on server + options `remap_unc_to_local: bool = True`, `path_replacements: List[Dict]`):
    - Validates `manifest.json` and zip member paths (strictly preventing zip-slip path traversal),
    - Pauses background worker / autopilot during restore,
    - Atomically restores `config.json`, SQLite DBs, `translation_memory/`, and `projects/`,
    - Optionally rewrites media/subtitle paths across `config.json`, `projects/*/episodes/*/metadata.json`, `omnisub.db`, `autopilot.db`, and `subtitle_ownership.db` (e.g. `\\Morpiceserver\e\` → `E:\`, `\\Morpiceserver\m\` → `M:\`),
    - Reloads `GLOBAL_CONFIG`, rebuilds `metadata_index`, and resumes workers.
- **FR-3.2 (CLI Migration Tool `scripts/migrate_server.py` & Web UI):**
  - Provide `scripts/migrate_server.py` (`export --output ombisub_backup.zip [--compact]` and `import --input ombisub_backup.zip [--remap-unc-drives]`) for fast command-line or LAN transfer.
  - Add **Backup & Migration** section in `SystemUpdatesSettings.jsx` (or dedicated tab in `SettingsPage.jsx`) with 1-click **Export Migration Bundle** (Compact vs Full toggle) and **Import Migration Bundle** (with file picker / local server path input and UNC → Local Drive path auto-remap toggle).

### 2.4 Pillar 4: Official GitHub Releases Auto-Updater
- **FR-4.1 (`backend/services/update_service.py`):**
  - Query `https://api.github.com/repos/{GITHUB_REPO}/releases/latest` (and `git fetch --tags origin`) to check for published GitHub Releases.
  - Compare the latest release's `tag_name` (and target commit SHA) against the currently installed release tag (`DATA_DIR / "release_version.json"` or `git describe --tags --exact-match HEAD`).
  - Only report `update_available = True` when a newer **GitHub Release** exists (never on untagged commits to `main`). If the repository has no published releases yet, return `update_available = False` with `release_status = "no_releases_published"`.
  - Include `latest_release_tag`, `latest_release_name`, `latest_release_notes` (markdown body), `published_at`, and `current_release_tag` in `/api/system/updates/status`.
- **FR-4.2 (Release Installation & Frontend Static Rebuild):**
  - In `apply_update(force: bool = False)`:
    - Fetch tags (`git fetch --tags origin --force`),
    - Guard against active jobs (`not force and self.has_active_jobs()`) and uncommitted tracked file changes,
    - Check if `backend/requirements.txt` or `frontend/` changed between `HEAD` and the target release tag,
    - Checkout/reset to the release tag (`git reset --hard <release_tag>`),
    - Run `pip install -r requirements.txt` if `backend/requirements.txt` changed,
    - If a prebuilt `static.zip` asset is attached to the GitHub Release, download and extract it into `backend/static/`; otherwise if `frontend/` changed and `npm` is available, run `npm run build` in `frontend/` and atomically swap `frontend/dist` into `backend/static/`,
    - Persist installed release metadata to `DATA_DIR / "release_version.json"` and trigger graceful restart (`scripts/restart_helper.py` updated to support Tray app, Scheduled Task `OmbiSub`, NSSM service, and standalone `run_server.py`).

---

## 3. Non-Functional Requirements
- **Zero Data Loss on Migration or Update:** SQLite databases must be backed up using `sqlite3.Connection.backup()` (never raw file copy while WAL is open) and zip extraction must guard against path traversal (Zip-Slip).
- **Zero Console Window on Server:** Running via `tray_app.py` / `pythonw.exe` must not spawn visible cmd/PowerShell windows during normal operation, ffprobe/ffmpeg extraction, SubtitleEdit fixes, or agent CLI execution (`CREATE_NO_WINDOW` flag on Windows subprocesses).
- **Test Coverage:** Comprehensive unit and integration tests for all 4 pillars (`pytest` + `npm run lint` + `npm run build`).
