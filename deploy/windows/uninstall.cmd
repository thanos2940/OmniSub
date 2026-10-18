@echo off
rem Removes the OmbiSub scheduled task and stops the server. Your data, .venv and the UI build are kept.
setlocal

fltmc >nul 2>&1
if errorlevel 1 (
    echo Asking for administrator rights...
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0uninstall.ps1"

echo.
pause
