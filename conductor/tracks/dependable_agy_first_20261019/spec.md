# Spec: Dependable, agy-first OmbiSub (`dependable_agy_first_20261019`)

Owner goal (2026-10-19): OmbiSub must run like Sonarr / Radarr / Bazarr — something you can count on and
reach: easy to install, update and manage, reachable through Caddy, connected to Sonarr / Radarr / Plex
the way Bazarr is, translating efficiently and cleanly. Translation **always tries agy (Antigravity CLI)
first**; the Gemini API is used only for lines agy's safety filter refuses. Every "Translate" anywhere
respects that. A title with no subtitle file gets its embedded track probed, extracted and translated.
Context guide and glossary are created when missing and enhanced as a show progresses. The UI and the
processes are simplified so they feel intentional, organised and functional.

---

## 1. Review findings (2026-10-19)

### 1.1 Running, updating, managing, reaching it
| # | Finding | Impact |
|---|---------|--------|
| R1 | `install.ps1 -Mode Service` runs `run_server.py` directly. Nothing respawns it after a crash or after an update restart (exit 42 is only handled by the tray supervisor). Task Scheduler's "restart on failure" does not reliably cover a process that exits on its own. | Server can stay down until someone notices. |
| R2 | Updates: `update_service` tracks GitHub Releases only; no release exists, so the in-app updater never offers anything. The real workflow is "push to `main`, `git pull` on the server". Two duplicate endpoint pairs exist (`/api/system/update` vs `/api/system/updates/apply`, `/api/system/check-update` vs `/api/system/updates/check`). | Updating needs a remote shell. |
| R3 | No log viewer in the app (Sonarr/Bazarr: System > Logs). Logs live in `backend/logs/` (`ombisub.log`, `agent_runner.log`, `tray_app.log`, `service_console.log`). | Diagnosing needs RDP/PowerShell. |
| R4 | No URL-base support: Vite builds absolute `/assets/...`, the router has no basename, the WebSocket URL is `${host}/api/ws`. Works behind Caddy only on its own (sub)domain, not under a path like `/ombisub`. uvicorn does not trust `X-Forwarded-*`. | Caddy sub-path setups break. |
| R5 | Install is a multi-step PowerShell session (elevation, parameters, password prompt). No one-click installer/updater script. | Hard to (re)install. |
| R6 | `backend/ombisub_tray.lock` (and the server lock) are not git-ignored. | Dirty tree blocks the in-app updater (it refuses dirty trees). |

### 1.2 Translation engine ("agy first")
| # | Finding | Impact |
|---|---------|--------|
| E1 | The global `translation_engine` defaults to `model` (Gemini worker). The migrated config on the server has `translation_engine: model`, `queue_worker_paused: true`. Queue items only reach agy while the runner happens to be running. | Translate buttons do not reliably mean "agy". |
| E2 | `POST .../retranslate-line` (editor "retranslate this line") calls Gemini through ADK with **no** glossary hits, no context guide text in the prompt and no neighbouring lines. | Not agy, and poor quality. |
| E3 | Context guide / glossary create & enhance, episode summary generation, term harvesting and ScriptGuard repair call Gemini (ADK / `llm_factory.generate`) directly. | API spend where agy should be used. |
| E4 | The UI shows two translate buttons per episode ("Agent Translate" and "Translate") plus a manual "Extract" button; project-level "Translate missing", "Batch translate", "Simple pipeline" wizard, unused `pipeline/start`, `pipeline/full-ingest`, `auto-translate`, `translate-missing` endpoints. | Confusing; several paths, different behaviour. |
| E5 | `agy -p /quota` is no longer a command in the installed Antigravity CLI; it ran a ~29k-token model turn every 2 minutes. *(Fixed 2026-10-18, commit 741dc73.)* | — |
| E6 | Antigravity dropped `claude-sonnet-4-6`; fallback model was invalid. *(Fixed 741dc73: default `claude-sonnet-5-5-low`, stored configs upgraded.)* | — |

### 1.3 Source subtitles, context and glossary
| # | Finding | Impact |
|---|---------|--------|
| S1 | Agent path failed episodes with no source cues instead of extracting the embedded track. *(Fixed 741dc73: `_extract_missing_source`.)* Model worker already extracts inline; autopilot extracts via `media_inspect`; sync enqueues `kind='extraction'`. | — |
| S2 | `ensure_context` (build context guide + glossary for a new show before its first translation) is honoured only by the Gemini worker (`translation_item_worker` → `project_bootstrap`, ADK). The agent runner never bootstraps: the first episode of a new show is translated with no guide, no glossary, no character genders. The runner writes a guide only after the first episode (`want_guide` on the last chunk when the project has none). | Inconsistent names / gender agreement early in a show. |
| S3 | Glossary growth: each runner reply may add `+ term | …` lines (merged on submit through `glossary_judgment`). The guide is never refined after it exists. | Guide goes stale as a show progresses. |

### 1.4 Sonarr / Radarr / Plex and automation
| # | Finding | Impact |
|---|---------|--------|
| A1 | Two automation systems: legacy webhook "Auto-Translate Mode" (`arr_auto_translate`: smart / true / false, with a "Bazarr wait delay" — Bazarr was retired) and the Autopilot (history watcher + reconcile + grace period). Webhooks go to Autopilot only while it is on. The server config has Autopilot **off** and legacy mode `smart`. | Two code paths, two sets of settings, unclear behaviour. |
| A2 | The scheduled arr sync enqueues every missing episode at `PRIORITY_SYNC` when Autopilot is off (`main.py` sync loop). | Surprise mass-enqueues; Bazarr only searches what is "wanted". |
| A3 | Plex refresh lives in the Autopilot (`plex.py`); a manual translation/export does not refresh Plex. No "Test" button for Plex. | Plex shows new subs late. |

### 1.5 UI
| # | Finding |
|---|---------|
| U1 | Nine top-level pages: `/` (ProjectList), `/library` (MediaLibrary), `/dashboard`, `/review`, `/queue`, `/autopilot`, `/health`, `/settings`, plus project/episode. Two library lists show the same shows. |
| U2 | Settings tabs are Gemini-centric: Gemini AI, Quality Presets, Hyperparameters, Media Servers, Subtitles & ASS, Autopilot, Security & Auth, Updates & System, Sandbox Tester. The agy runner — the actual engine — is configured on the Queue page. |
| U3 | Project page has 8 tabs (Episodes, Context Guide, Glossary, Character Profiles, Episode Summaries, VisualGuard & Formatting, Translation Memory & QC, Token Stats) and 12 header/toolbar buttons. |
| U4 | Refreshing `/queue` showed raw JSON (alias clash). *(Fixed 162b23e.)* |

---

## 2. Target design

### 2.1 Operations (Sonarr-like)
- **Service**: `install.ps1 -Mode Service` registers a boot task that runs the **headless supervisor**
  (`tray_app.py --headless`), which runs `run_server.py`, respawns it with back-off after a crash, and
  respawns it at once on exit code 42 (in-app restart / update). Console output → `logs/service_console.log`.
- **One-click scripts** in `deploy/windows/`: `install.cmd` (self-elevating; asks for the port once,
  default 2945; runs Service mode with firewall + keep-awake), `update.cmd` (pull, lock deps, UI build,
  restart task), `uninstall.cmd`.
- **Updates in-app**: `update_channel` = `main` (follow `origin/main`, default for git installs without
  releases) or `releases`. Check / apply / auto-update; apply = fetch, guard (dirty tree, local commits),
  reset, `pip install -r requirements.lock.txt` if changed, `npm ci && npm run build` → `backend/static`
  if `frontend/` changed, then exit 42 for the supervisor. One endpoint set: `/api/system/updates/*`.
- **System management**: `GET /api/system/status` (version/commit, uptime, port, url_base, data dir,
  supervisor mode, Python, ffmpeg, agy/claude CLI presence, runner state, disk free), `GET
  /api/system/logs?name=&lines=` (whitelisted log files, tail), restart, backup (exists), updates.
- **Reverse proxy (Caddy)**: config `url_base` (e.g. `/ombisub`, empty = root). Backend accepts requests
  with or without the prefix (so `handle_path` and `handle` both work), injects `<base href>` and
  `window.__OMBISUB_BASE__` into `index.html`; Vite `base: './'`; router `basename`; API and WebSocket URLs
  under the base. uvicorn honours `X-Forwarded-*`. Docs give Caddyfile examples for a subdomain and a path.

### 2.2 Translation engine — agy first
- `translation_engine` defaults to `agent`; the runner (Antigravity by default) is always on in watch mode
  when the engine is `agent`. "Pause translations" pauses the runner (and the model worker); extraction and
  export lanes keep running.
- Every translate action enqueues to the queue; the runner claims it. Single-line retranslate goes through
  agy with context (neighbouring lines, glossary hits, guide, characters) via a synchronous helper.
- One helper, `services/agy_llm.py`: `complete(prompt, purpose, project=None, timeout=…) -> str` uses the
  runner's engine/model/fallbacks through `agent_cli.call_model`; only when the CLI refuses on safety
  grounds does it fall back to `llm_factory.generate` with the rescue model and `gemini_safety_filter`.
  In agent mode, context/glossary create/enhance, summaries, term harvest and ScriptGuard repair use it.
- The model engine (Gemini worker, Batch API, ADK pipeline) stays available as an Advanced option.

### 2.3 Source, context, glossary
- Missing source → probe → extract → translate on every path (runner: done; check manual translate of
  movies/episodes with no imported source, on-demand, autopilot).
- **Bootstrap** before the first episode of a project without a context guide or with an empty glossary:
  one agy prompt (show title, Sonarr/Radarr overview/genres/year, target language, ~400 spread source
  lines from up to 3 episodes) → context guide (`G|` lines) + glossary entries (`+ term | rendering |
  type | gender | note`), merged through the existing submit merge / `glossary_judgment`; per-project lock;
  `bootstrapped_at` in project metadata; failure never blocks translation (retry later, bounded).
- **Growth**: `+ term` lines per episode (exists); every 6 translated episodes the prompt asks for at most
  5 short guide additions, appended to a "Story so far / additions" section, guide capped (~6000 chars).

### 2.4 Integrations
- Autopilot is the only automation. Webhooks always feed it; with Autopilot off a webhook only refreshes
  that title's library entry. The legacy auto-translate mode and "Bazarr wait delay" go away (config
  migration: legacy mode on → Autopilot on, backlog off).
- The scheduled arr sync keeps the library fresh and never mass-enqueues; missing-episode translation is
  Autopilot backlog (opt-in) or an explicit "Translate missing".
- Plex: refresh after **every** export OmbiSub writes (debounced per library section), "Test Plex" button.
- Connections test for Sonarr, Radarr, Plex in one place.

### 2.5 UI information architecture
Top bar: **Library · Activity · Review · Settings · System** (+ command palette).
- **Library**: one list (merges `/` and `/library`): Series · Movies · Other (manual projects); search,
  filters (status, priority), per-title Translate-missing, priority, open; header stats.
- **Activity**: Queue (runner panel on top: state, lanes, pause/resume) · History · Autopilot items.
- **Review**: QC-flagged lines (unchanged component).
- **Project page**: Episodes · Glossary & Characters · Context & Summaries · History (TM & QC). One
  "Translate" per episode (becomes "Retranslate" when done); Extract/inspect/fixes in an overflow menu;
  "Build context & glossary with agy" buttons replace the Simple-pipeline wizard.
- **Settings**: Translation (agy runner: CLI, model, fallbacks, lanes, per show; API rescue: model, Gemini
  key, safety filter; Advanced: model engine) · Connections (Sonarr, Radarr, Plex, path mappings, webhook
  URLs) · Subtitles (formats, ASS, lyrics, SubtitleEdit, VisualGuard) · Automation (Autopilot) · Security ·
  General (target language, URL base, port info).
- **System**: Status · Logs · Backup · Updates.
- Removed from the UI: Dashboard page (stats move to Library header and System status), Sandbox Tester,
  Quality Presets / Hyperparameters (model-engine knobs go to Translation > Advanced), Token Stats tab,
  Simple-pipeline wizard, legacy auto-translate settings.

## 3. Non-functional
- Tests stay offline (fake agent CLI, mocked LLM seam). Every fix carries a regression test.
- Never delete user data; config changes go through explicit, recorded migrations.
- Windows first (PowerShell 5.1 quirks: no double quotes in native args, one-item arrays unroll).
