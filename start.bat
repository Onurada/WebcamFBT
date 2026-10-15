@echo off
cd /d "%~dp0"
".venv\Scripts\python.exe" tracker\webcam_fbt.py %*
if errorlevel 1 pause
