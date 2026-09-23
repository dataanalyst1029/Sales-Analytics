@echo off
REM Removes what install_autostart.cmd registered. The dashboard and the loaders
REM still work by hand afterwards.
setlocal
schtasks /delete /tn "Sales Analytics Dashboard" /f
schtasks /delete /tn "Sales Analytics Update" /f
echo.
echo Removed. Run run_dashboard.cmd by hand when you need the dashboard.
exit /b 0
