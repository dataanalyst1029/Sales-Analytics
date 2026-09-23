@echo off
REM Network mode with no console window, for the scheduled logon task.
setlocal
cd /d "%~dp0"
start "" /min pythonw analytics_web.py --host 0.0.0.0 --port 8001 --no-browser
exit /b 0
