@echo off
REM One command (or one scheduled task) to refresh the live website with the newest data:
REM   pull today's prices -> re-parse backfills -> rebuild + test warehouse -> predict (WSL) -> push.
REM If any step fails (including a failed dbt test), it stops and nothing is published.
REM Log: data\state\refresh.log
cd /d C:\Users\aryas\Desktop\Poject\Steam-SaleWaitingGame
if not exist data\state mkdir data\state
set LOG=data\state\refresh.log
echo ===== %date% %time% start ===== >> %LOG%

echo [1/6] downloading daily price snapshots from Hugging Face >> %LOG%
.venv\Scripts\python.exe -u -m steamwait.hf_sync download >> %LOG% 2>&1 || goto fail

echo [2/6] parsing store-data backfill >> %LOG%
.venv\Scripts\python.exe -u -m steamwait.parse_details >> %LOG% 2>&1 || goto fail

echo [3/6] parsing IsThereAnyDeal history >> %LOG%
.venv\Scripts\python.exe -u -m steamwait.parse_itad >> %LOG% 2>&1 || goto fail

echo [4/6] dbt build (models + tests) >> %LOG%
.venv\Scripts\python.exe -u scripts\run_dbt.py build >> %LOG% 2>&1 || goto fail

echo [5/6] predictions (WSL) >> %LOG%
wsl -d Ubuntu -- bash -lc "cd /mnt/c/Users/aryas/Desktop/Poject/Steam-SaleWaitingGame && ~/venvs/steamwait/bin/python -m steamwait.predict" >> %LOG% 2>&1 || goto fail

echo [6/6] publishing >> %LOG%
git add site/data >> %LOG% 2>&1
git diff --cached --quiet && (echo no changes to publish >> %LOG%) && goto done
git commit -m "Site data refresh %date%" >> %LOG% 2>&1 || goto fail
git push >> %LOG% 2>&1 || goto fail

:done
echo ===== %date% %time% done ===== >> %LOG%
exit /b 0

:fail
echo ===== %date% %time% FAILED, nothing published. See the lines above. ===== >> %LOG%
exit /b 1
