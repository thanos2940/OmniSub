# OmbiSub on a Windows server

Run from an elevated PowerShell in the repo root:

```powershell
powershell -ExecutionPolicy Bypass -File deploy\windows\install.ps1            # install / update
powershell -ExecutionPolicy Bypass -File deploy\windows\install.ps1 -WhatIf    # dry run
powershell -ExecutionPolicy Bypass -File deploy\windows\status.ps1             # state + health
powershell -ExecutionPolicy Bypass -File deploy\windows\uninstall.ps1          # remove the task (keeps data)
```

`install.ps1` options: `-Port 8000`, `-DataDir D:\OmbiSubData`, `-TaskName OmbiSub`, `-User DOMAIN\name`,
`-SkipBuild`, `-AlsoAtLogon`. It asks for the user's Windows password (Task Scheduler needs it for
"run whether logged on or not"; it is not saved anywhere by the script).

The task runs `.venv\Scripts\python.exe backend\run_server.py --port <port>` in the repo root at startup,
restarts every minute on failure (up to 999 times), has no time limit and ignores battery state.
Sign in to `agy` / `claude` as the same user. Full guide: `docs/DEPLOYMENT_GUIDE.md`, "Server (24/7, Windows)".
