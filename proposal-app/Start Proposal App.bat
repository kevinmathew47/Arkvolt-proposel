@echo off
rem Solar Proposal Generator - double-click to start. Keep the black "Solar Proposal App" window open while you work.
cd /d "%~dp0"
title Solar Proposal App
echo Starting the Solar Proposal App...
start "Solar Proposal App - keep this window open" py -3.12 run.py
rem wait until the app answers (up to about 60 s), then open the browser
set /a n=0
:wait
set /a n+=1
powershell -NoProfile -Command "try { Invoke-WebRequest http://localhost:8520/login -UseBasicParsing -TimeoutSec 2 | Out-Null; exit 0 } catch { exit 1 }"
if %errorlevel%==0 goto open
if %n% geq 30 goto fail
timeout /t 2 /nobreak >nul
goto wait
:open
start "" http://localhost:8520
exit /b
:fail
echo.
echo The app did not start. Look at the "Solar Proposal App" window for the error message.
pause
