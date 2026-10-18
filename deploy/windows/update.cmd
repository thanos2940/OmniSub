@echo off
rem Updates OmbiSub in place: stops the task, git pull --ff-only, installs the locked Python packages,
rem rebuilds the web UI when it changed, starts the task again. Your data is not touched.
rem Registered tasks that run with the highest privileges can only be stopped/started elevated, so this
rem asks for administrator rights.
setlocal

fltmc >nul 2>&1
if errorlevel 1 (
    echo Asking for administrator rights...
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0update.ps1"

echo.
pause
