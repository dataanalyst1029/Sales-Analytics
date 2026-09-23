@echo off
REM Starts the dashboard so OTHER MACHINES on this network can reach it.
REM
REM   run_dashboard_network.cmd          port 8001
REM   run_dashboard_network.cmd 8123     a different port
REM
REM This refuses to start unless Google sign-in is configured, because it would
REM otherwise publish every branch's sales, costs and margins to anyone who can
REM reach the port. Run setup_google.py first.
REM
REM Windows Firewall may still block the port. If colleagues cannot connect, run
REM this once from an ADMINISTRATOR prompt:
REM
REM   netsh advfirewall firewall add rule name="Sales Analytics 8001" dir=in action=allow protocol=TCP localport=8001

setlocal
cd /d "%~dp0"
set PORT=8001
if not "%~1"=="" set PORT=%~1

echo Starting the dashboard on the network, port %PORT% ...
echo Leave this window open while people are using it.
echo.
python analytics_web.py --host 0.0.0.0 --port %PORT% --no-browser
set RC=%ERRORLEVEL%
if not %RC%==0 (
  echo.
  echo It did not start. Read the message above -- the usual cause is that
  echo sign-in is not configured yet.
  pause
)
exit /b %RC%
