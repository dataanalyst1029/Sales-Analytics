@echo off
REM Registers the dashboards and the daily update with Task Scheduler, so they
REM survive a reboot and the data keeps itself current.
REM
REM   Sales Analytics Dashboard  -- starts at logon, stays running
REM   Sales Analytics Update     -- runs at 06:30 daily
REM
REM Both run as you, in your own session; no admin rights are needed and nothing
REM system-wide is touched. Undo with uninstall_autostart.cmd.

setlocal
cd /d "%~dp0"

set LAUNCH=run_dashboard_quiet.cmd
if /i "%~1"=="network" set LAUNCH=run_dashboard_network_quiet.cmd

echo Registering the dashboard to start at logon (%LAUNCH%) ...
schtasks /create /tn "Sales Analytics Dashboard" /tr "\"%~dp0%LAUNCH%\"" /sc onlogon /f
if errorlevel 1 goto failed

echo.
echo Registering the daily update for 06:30 ...
schtasks /create /tn "Sales Analytics Update" /tr "\"%~dp0run_daily_update.cmd\"" /sc daily /st 06:30 /f
if errorlevel 1 goto failed

echo.
echo Done. Both tasks are registered:
schtasks /query /tn "Sales Analytics Dashboard" /fo list | findstr /i "TaskName Status"
schtasks /query /tn "Sales Analytics Update" /fo list | findstr /i "TaskName Status"
echo.
echo The dashboard will be on http://localhost:8001 after the next logon.
echo Starting it now as well ...
start "" "%~dp0%LAUNCH%"
exit /b 0

:failed
echo.
echo Could not register the tasks. Run this from an account that can create
echo scheduled tasks, or start run_dashboard.cmd by hand each time.
pause
exit /b 1
