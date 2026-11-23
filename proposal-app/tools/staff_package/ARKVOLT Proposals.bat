@echo off
rem ARKVOLT Proposals - starts the app on this laptop (if not already running) and opens it in the browser.
cd /d "%~dp0"
powershell -NoProfile -Command "try { Invoke-WebRequest http://localhost:8520/login -UseBasicParsing -TimeoutSec 2 | Out-Null; exit 0 } catch { exit 1 }"
if %errorlevel%==0 goto open
start "ARKVOLT Proposals - keep open while you work" /min ".venv\Scripts\python.exe" run.py
set /a n=0
:wait
set /a n+=1
powershell -NoProfile -Command "try { Invoke-WebRequest http://localhost:8520/login -UseBasicParsing -TimeoutSec 2 | Out-Null; exit 0 } catch { exit 1 }"
if %errorlevel%==0 goto open
if %n% geq 40 goto fail
timeout /t 2 /nobreak >nul
goto wait
:open
start "" http://localhost:8520
exit /b
:fail
echo The app did not start. Look at the minimised "ARKVOLT Proposals" window for the error, or run Setup again.
pause
