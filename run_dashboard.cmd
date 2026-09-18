@echo off
REM Starts the sales analytics dashboard and opens it.
REM
REM   run_dashboard.cmd          http://localhost:8001
REM   run_dashboard.cmd 8123     a different port
REM
REM Port 8001, so this and the StoreHub recon UI (8000) can run at the same
REM time. Binds to 127.0.0.1 -- reachable from this PC only.
REM Close this window (or Ctrl+C) to stop it.

setlocal
cd /d "%~dp0"

set PORT=8001
if not "%~1"=="" set PORT=%~1

echo Starting the sales analytics dashboard on port %PORT% ...
echo Leave this window open while you use it.
echo.

python analytics_web.py --port %PORT%
set RC=%ERRORLEVEL%

if not %RC%==0 (
  echo.
  echo The server exited with code %RC%.
  pause
)
exit /b %RC%
