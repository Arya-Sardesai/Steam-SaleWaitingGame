@echo off
REM Task Scheduler runs this. Edit the path on the next line to where your repo lives.
cd /d C:\path\to\Steam-SaleWaitingGame
if not exist data\state mkdir data\state
call .venv\Scripts\activate.bat
python -m steamwait.fetch_details >> data\state\backfill.log 2>&1
