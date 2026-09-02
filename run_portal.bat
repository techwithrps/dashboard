@echo off
title SyncTally Cloud Portal - Local Server
echo ============================================================
echo   SyncTally Cloud Analytics Portal - Local Host Runner
echo ============================================================
echo.
echo Starting server on http://127.0.0.1:9000 ...
echo Master Portal Password : SyncTally@2026
echo.
python "%~dp0run_local.py"
pause
