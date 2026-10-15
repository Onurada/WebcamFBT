@echo off
rem Registers the webcamfbt driver with SteamVR and sets tracker roles (close SteamVR first)
cd /d "%~dp0"
tasklist /fi "imagename eq vrserver.exe" | find /i "vrserver.exe" >nul && (
    echo SteamVR acik. Once SteamVR'i kapat, sonra tekrar calistir.
    pause
    exit /b 1
)
set VRPATHREG="C:\Program Files (x86)\Steam\steamapps\common\SteamVR\bin\win64\vrpathreg.exe"
%VRPATHREG% adddriver "%~dp0webcamfbt"
".venv\Scripts\python.exe" tracker\set_roles.py
%VRPATHREG% show
pause
