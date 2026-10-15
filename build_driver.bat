@echo off
rem Builds webcamfbt\bin\win64\driver_webcamfbt.dll with Visual Studio 2022 (MSVC x64)
setlocal
set VCVARS="C:\Program Files\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvars64.bat"
if not exist %VCVARS% set VCVARS="C:\Program Files\Microsoft Visual Studio\18\Community\VC\Auxiliary\Build\vcvars64.bat"
call %VCVARS% >nul || exit /b 1
cd /d "%~dp0"
if not exist build mkdir build
cl /nologo /O2 /EHsc /std:c++17 /MT /LD driver_src\driver_webcamfbt.cpp /Fobuild\ /Fewebcamfbt\bin\win64\driver_webcamfbt.dll /link /IMPLIB:build\driver_webcamfbt.lib ws2_32.lib || exit /b 1
echo Build OK: webcamfbt\bin\win64\driver_webcamfbt.dll
