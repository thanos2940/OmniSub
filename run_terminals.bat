@echo off
title OmniSub (Dual Terminal)
cd /d "%~dp0"

echo Starting Backend in new window...
start "OmniSub - Backend (8000)" cmd /k "cd backend && ..\.venv\Scripts\python.exe -m uvicorn main:app --reload --host 0.0.0.0 --port 8000"

echo Starting Frontend in new window...
start "OmniSub - Frontend (5173)" cmd /k "cd frontend && npm run dev"

echo All services launched!
timeout /t 3 >nul
exit
