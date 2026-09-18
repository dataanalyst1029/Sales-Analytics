@echo off
REM Pulls the last 3 days from the API into the warehouse.
REM
REM Safe to run as often as you like: rows are upserted on the API's UUID, so a
REM day already loaded is corrected rather than duplicated. Three days rather
REM than one, so a late-posted or amended receipt is still picked up.
REM
REM Point Task Scheduler at this file to keep the warehouse current.

setlocal
cd /d "%~dp0"
echo Updating the warehouse from the API ...
python ingest.py --dataset both --recent 3
set RC=%ERRORLEVEL%
if not %RC%==0 (
  echo.
  echo Update failed with code %RC%.
  pause
)
exit /b %RC%
