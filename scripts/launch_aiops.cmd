@echo off
setlocal
title AI Ops Platform

rem ---------------------------------------------------------------------------
rem Desktop launcher: start the API in mock mode (offline, no API keys) and
rem open the operator dashboard in the default browser.
rem
rem Serves on port 8200 on purpose: the README/uvicorn default is 8000, but
rem other local apps commonly claim that port. 8200 keeps this shortcut
rem deterministic. If 8200 is already answering, this is a no-op that just
rem re-opens the dashboard (single-instance guard — two API processes writing
rem the same JSON store would race).
rem ---------------------------------------------------------------------------

cd /d "%~dp0.."
set PORT=8200

rem /c: keeps the pattern in one piece — findstr otherwise splits it on the
rem space and ":8200" alone would match TIME_WAIT rows, faking "already running".
netstat -ano | findstr /r /c:":%PORT% .*LISTENING" >nul
if not errorlevel 1 (
  echo AI Ops Platform is already running - opening the dashboard.
  start "" http://localhost:%PORT%
  exit /b 0
)

where python >nul 2>nul
if errorlevel 1 (
  echo Python was not found on PATH. Install Python 3.11+ and retry.
  pause
  exit /b 1
)

echo Starting the AI Operations ^& Workflow Automation Platform (mock mode)...
start "AI Ops Platform API" /min cmd /c "python -m uvicorn backend.main:app --port %PORT% > data\launcher.log 2>&1"

set /a tries=0
:wait
curl -s -o NUL http://localhost:%PORT%/health && goto ready
set /a tries+=1
if %tries% GEQ 20 (
  echo The API did not answer within 20 seconds. See data\launcher.log
  pause
  exit /b 1
)
timeout /t 1 /nobreak >nul
goto wait

:ready
start "" http://localhost:%PORT%
echo Dashboard: http://localhost:%PORT%   (API window is minimised; close it to stop)
timeout /t 4 /nobreak >nul
exit /b 0
