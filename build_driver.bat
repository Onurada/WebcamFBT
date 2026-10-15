@echo off
rem Builds webcamfbt\bin\win64\driver_webcamfbt.dll with the newest Visual Studio that has the C++ tools.
rem Close SteamVR first: it keeps the DLL locked.
setlocal
set "VSWHERE=%ProgramFiles(x86)%\Microsoft Visual Studio\Installer"
if not exist "%VSWHERE%\vswhere.exe" (
    echo Visual Studio not found. Install it with the "Desktop development with C++" workload.
    exit /b 1
)
set "PATH=%VSWHERE%;%PATH%"
for /f "usebackq delims=" %%i in (`vswhere.exe -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath`) do set "VSDIR=%%i"
if not defined VSDIR (
    echo Visual Studio C++ tools not found. Add the "Desktop development with C++" workload.
    exit /b 1
)
call "%VSDIR%\VC\Auxiliary\Build\vcvars64.bat" >nul || exit /b 1
cd /d "%~dp0"
if not exist build mkdir build
if not exist webcamfbt\bin\win64 mkdir webcamfbt\bin\win64
cl /nologo /O2 /EHsc /std:c++17 /MT /LD driver_src\driver_webcamfbt.cpp /Fobuild\ /Fewebcamfbt\bin\win64\driver_webcamfbt.dll /link /IMPLIB:build\driver_webcamfbt.lib ws2_32.lib || exit /b 1
echo Build OK: webcamfbt\bin\win64\driver_webcamfbt.dll
