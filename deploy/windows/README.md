# OmbiSub on a Windows server

OmbiSub runs like Sonarr or Radarr: it starts at boot, nobody has to be logged in, it comes back after a
crash, and it updates itself (or with one click). Everything below is run from the repo folder.

## Install (one click)

Double-click **`deploy\windows\install.cmd`**.

It asks for administrator rights, then for the port (default **2945**), then registers the boot task, opens
the Windows Firewall for that port on Private networks and stops the machine from sleeping on AC power. The
PowerShell step asks for the Windows password of your account: Task Scheduler needs it to run the task when
nobody is logged on (it is handed to Task Scheduler only, never written to a file; leave it empty if the
account has no password and the task is registered with S4U logon instead).

Prerequisites it checks: Python 3.11+, Node.js (only to build the UI), ffmpeg/ffprobe on the **system** PATH,
and the `agy` / `claude` CLIs, which must be signed in **as the account the task runs as**.

Then open `http://localhost:2945` (give it about 20 seconds on the first start).

Without the clicks, from an elevated PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File deploy\windows\install.ps1 -Mode Service -Port 2945 -OpenFirewall -KeepAwake
powershell -ExecutionPolicy Bypass -File deploy\windows\install.ps1 -WhatIf     # dry run
```

Options: `-Port`, `-DataDir D:\OmbiSubData`, `-TaskName OmbiSub`, `-User DOMAIN\name`, `-SkipBuild`,
`-OpenFirewall`, `-KeepAwake`, `-Mode Service|Tray|Headless`.

### Service or Tray?

| Mode | Starts | What runs | Use it for |
| --- | --- | --- | --- |
| **Service** (default for `install.cmd`) | at boot, nobody logged on | `tray_app.py --headless`, a supervisor that runs the server | the always-on server |
| **Tray** | at logon | `tray_app.py` with a tray icon (open UI, pause/resume runner, restart, logs) | a desktop PC |
| **Headless** | at logon | `run_server.py` directly, no supervisor | debugging |

The supervisor (Service and Tray) starts the server again **at once** after an update or *Restart* (exit code
42) and after a crash with a growing wait of 5 s, 15 s, then 60 s; the wait starts over after the server has
stayed up for a minute. Each respawn, with the exit code, is in `logs\tray_app.log`.

## Update

* **In the app:** Settings > Updates & System > *Check for updates* > *Install & Restart*. The default
  channel follows `origin/main` (every pushed commit is an update, the commit subjects are the release notes);
  set the channel to *GitHub Releases* once you publish releases. *Automatically install updates when idle*
  does the same on a timer. The updater refuses when translations are running, the checkout has uncommitted
  changes, or it has commits that are not on `origin/main`. It reinstalls the locked Python packages when
  `backend\requirements*` changed, rebuilds the UI when `frontend\` changed (needs `npm`; the old UI stays until
  the new one is ready) and restarts the server.
* **From the server:** double-click **`deploy\windows\update.cmd`**: stops the task, `git pull --ff-only`,
  `pip install -r backend\requirements.lock.txt`, rebuilds the UI if needed, starts the task. Your data is not
  touched.

## Uninstall

Double-click **`deploy\windows\uninstall.cmd`**. It removes the scheduled task and stops the server; your data
(config, databases, projects, logs), `.venv` and the UI build stay.

## Status and logs

```powershell
powershell -ExecutionPolicy Bypass -File deploy\windows\status.ps1     # task state, server process, /api/health, autopilot
```

In the app (and through the API, behind the login when it is on): `GET /api/system/status` (version and commit,
uptime, port, URL base, data folder, supervisor, Python, ffmpeg, agent CLIs, runner state, free disk space)
and `GET /api/system/logs?name=ombisub&lines=500` (names: `ombisub`, `agent_runner`, `tray_app`,
`service_console`; `/api/system/logs/list` shows which exist).

Log files live in `<data folder>\logs` (`backend\logs` unless you used `-DataDir`):

| File | Written by |
| --- | --- |
| `ombisub.log` | the server (rotating, 10 MB x 5) |
| `agent_runner.log` | the agent runner |
| `tray_app.log` | the supervisor: starts, exits, respawns |
| `service_console.log` | console output of the boot task (tracebacks before logging starts) |

## Behind Caddy (reverse proxy)

OmbiSub speaks plain HTTP on its port; let Caddy add HTTPS. **Turn on OmbiSub's own login (Settings >
Security) before you expose it to the internet.**

**Own (sub)domain** - nothing to configure in OmbiSub:

```caddyfile
ombisub.example.com {
    reverse_proxy localhost:2945
}
```

**Under a path** - set *Settings > Updates & System > Reverse proxy > URL base* to `/ombisub` (setting
`url_base`) and restart OmbiSub. Both forms work, with the prefix stripped or kept:

```caddyfile
example.com {
    handle_path /ombisub/* {      # strips /ombisub before proxying
        reverse_proxy localhost:2945
    }
}
```

```caddyfile
example.com {
    handle /ombisub/* {           # keeps /ombisub; OmbiSub strips it itself
        reverse_proxy localhost:2945
    }
}
```

OmbiSub serves `index.html` with `<base href="/ombisub/">`, so the UI, the API and the live-update WebSocket
all live under the path; requests without the prefix keep working (direct access by `IP:2945`).

`X-Forwarded-For` / `X-Forwarded-Proto` from Caddy are trusted when Caddy runs on the same machine
(setting `trusted_proxies`, default `127.0.0.1`; use the proxy's address when it is on another host).
Sonarr/Radarr webhooks then use `https://example.com/ombisub/api/webhook/...`.

## Files in this folder

`install.cmd` / `update.cmd` / `uninstall.cmd` (the clicks, self-elevating), `install.ps1` / `update.ps1` /
`uninstall.ps1` (what they run), `status.ps1`, `start_tray.vbs` (windowless tray launcher).
