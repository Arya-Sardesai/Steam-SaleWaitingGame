@echo off
REM Task Scheduler runs this. Calls the venv's python directly (no activate step needed).
REM -u = unbuffered, so backfill.log updates live instead of in big chunks.
cd /d C:\Users\aryas\Desktop\Poject\Steam-SaleWaitingGame
if not exist data\state mkdir data\state
echo ===== %date% %time% start ===== >> data\state\backfill.log
.venv\Scripts\python.exe -u -m steamwait.fetch_details >> data\state\backfill.log 2>&1
echo ===== %date% %time% end (exit %errorlevel%) ===== >> data\state\backfill.log
