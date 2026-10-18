@echo off
rem One-click OmbiSub install: runs as a boot-time task (like Sonarr), firewall opened, machine kept awake.
rem Double-click it. It asks for administrator rights, the port (once) and, in the PowerShell step, your
rem Windows password (Task Scheduler needs it to run the task when nobody is logged on; leave it empty if
rem your account has none).
setlocal

fltmc >nul 2>&1
if errorlevel 1 (
    echo Asking for administrator rights...
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

set "PORT="
set /p "PORT=Port OmbiSub should listen on [2945]: "
if not defined PORT set "PORT=2945"
echo %PORT%| findstr /r "^[0-9][0-9]*$" >nul
if errorlevel 1 (
    echo "%PORT%" is not a port number.
    goto :done
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" -Mode Service -Port %PORT% -OpenFirewall -KeepAwake

:done
echo.
pause
