@echo off
REM Task Scheduler runs this for the IsThereAnyDeal history backfill.
REM -u = unbuffered, so itad.log updates live.
cd /d C:\Users\aryas\Desktop\Poject\Steam-SaleWaitingGame
if not exist data\state mkdir data\state
echo ===== %date% %time% start ===== >> data\state\itad.log
.venv\Scripts\python.exe -u -m steamwait.itad_history history >> data\state\itad.log 2>&1
echo ===== %date% %time% end (exit %errorlevel%) ===== >> data\state\itad.log
