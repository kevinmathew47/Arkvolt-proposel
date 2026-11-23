@echo off
rem ARKVOLT Proposals - double-click to install or update the app on this laptop.
if not exist "%~dp0setup.ps1" goto notextracted
start "" powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%~dp0setup.ps1"
exit /b

:notextracted
powershell -NoProfile -Command "Add-Type -AssemblyName System.Windows.Forms; [void][System.Windows.Forms.MessageBox]::Show('Please unzip the folder first.' + [char]10 + [char]10 + 'Right-click the ZIP file  >  Extract All  >  Extract,' + [char]10 + 'then open the extracted folder and double-click Setup.', 'ARKVOLT Proposals - Setup', 'OK', 'Information')"
exit /b
