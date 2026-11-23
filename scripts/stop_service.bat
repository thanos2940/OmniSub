@echo off
net session >nul 2>&1
if %errorLevel% neq 0 (
    echo [!] Requesting administrative privileges...
    powershell -Command "Start-Process '%~dpnx0' -Verb runAs"
    exit /b
)

echo [*] Stopping Omnisub Service...
net stop Omnisub
echo.
pause
