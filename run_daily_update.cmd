@echo off
REM Pulls the last few days from the API into the warehouse -- all four datasets.
REM
REM Safe to run as often as you like: every loader upserts on the API's own UUID,
REM so a day already loaded is corrected rather than duplicated. Six days rather
REM than one, so a late-posted or amended figure is still picked up, and a couple
REM of missed days catch themselves up.
REM
REM Point Task Scheduler at this file to keep the warehouse current.

setlocal
cd /d "%~dp0"

echo [1/2] transactions and receipts ...
python ingest.py --dataset both --recent 6
if errorlevel 1 goto failed

echo.
echo [2/2] sales summary and product movement ...
python ingest_reports.py --dataset both --recent 6
if errorlevel 1 goto failed

echo.
echo Done. Current state:
python ingest_reports.py --status
exit /b 0

:failed
echo.
echo Update failed. Nothing is corrupted -- the loaders are idempotent, so
echo simply run this again once the cause is fixed.
pause
exit /b 1
