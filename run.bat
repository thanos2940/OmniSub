@echo off
title Omnisub Launcher
cd /d "%~dp0"

echo ===================================================
echo             Starting Omnisub Services
echo ===================================================

:: Check if virtual environment python exists
if exist ".venv\Scripts\python.exe" (
    set "PYTHON_EXE=.venv\Scripts\python.exe"
) else (
    set "PYTHON_EXE=python"
)

:: Launch the Graphical Dashboard
start "" "%PYTHON_EXE%" scripts\run_gui.py

exit
