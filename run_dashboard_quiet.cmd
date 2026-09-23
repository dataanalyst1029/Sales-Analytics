@echo off
REM The dashboard with no console window, for the scheduled logon task.
REM Use run_dashboard.cmd instead when you want to watch it start.
setlocal
cd /d "%~dp0"
start "" /min pythonw analytics_web.py --port 8001 --no-browser
exit /b 0
