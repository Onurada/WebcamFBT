@echo off
rem Removes the webcamfbt driver from SteamVR (close SteamVR first)
set VRPATHREG="C:\Program Files (x86)\Steam\steamapps\common\SteamVR\bin\win64\vrpathreg.exe"
%VRPATHREG% removedriver "%~dp0webcamfbt"
pause
