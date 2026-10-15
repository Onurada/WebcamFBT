# Builds the installable WebcamFBT: a folder laid out exactly like the installation (build\dist)
# and, with Inno Setup installed, the installer build\installer\WebcamFBT-Setup-<version>.exe.
#
#   powershell -ExecutionPolicy Bypass -File installer\package.ps1 [-SkipInstaller]
#
# Layout of build\dist (= install folder):
#   WebcamFBT.exe            self-contained app (.NET included)
#   python\                  embeddable Python 3.12 + the engine's packages
#   tracker\                 engine (.py) and pose models
#   webcamfbt\               SteamVR driver
#   VERSION, README.md, KULLANIM.md, THIRD_PARTY_NOTICES.md
param([switch]$SkipInstaller)
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$root = Split-Path $PSScriptRoot -Parent
$version = (Get-Content "$root\VERSION" -Raw).Trim()
$dist = "$root\build\dist"
$cache = "$root\build\cache"
$pyVersion = "3.12.10"
Write-Host "WebcamFBT $version"

if (Test-Path $dist) { Remove-Item $dist -Recurse -Force }
New-Item -ItemType Directory -Force $dist, $cache | Out-Null

# 1. Embeddable Python. Its ._pth file fixes sys.path: add site-packages and the engine folder.
$zip = "$cache\python-$pyVersion-embed-amd64.zip"
if (-not (Test-Path $zip)) {
    Write-Host "Downloading Python $pyVersion (embeddable)..."
    Invoke-WebRequest "https://www.python.org/ftp/python/$pyVersion/python-$pyVersion-embed-amd64.zip" -OutFile $zip
}
Expand-Archive $zip "$dist\python"
$pth = Get-ChildItem "$dist\python" -Filter "python*._pth" | Select-Object -First 1
Set-Content $pth.FullName -Encoding ascii -Value @(
    ($pth.BaseName + ".zip"), ".", "Lib\site-packages", "..\tracker", "import site")

# 2. Engine packages (no extras, see runtime-requirements.txt). Any Python 3.12 can install them.
$hostPython = if (Test-Path "$root\.venv\Scripts\python.exe") { "$root\.venv\Scripts\python.exe" } else { "python" }
Write-Host "Installing engine packages..."
& $hostPython -m pip install --disable-pip-version-check --no-deps --no-compile --target "$dist\python\Lib\site-packages" `
    -r "$PSScriptRoot\runtime-requirements.txt" | Out-Null
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }
Get-ChildItem "$dist\python\Lib\site-packages" -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force
# MediaPipe imports matplotlib only for a debug plot we never use: ship a placeholder (saves ~70 MB).
Copy-Item "$PSScriptRoot\stubs\matplotlib" "$dist\python\Lib\site-packages" -Recurse
# OpenCV's FFmpeg plugin only reads video files/streams; cameras use DirectShow (saves ~30 MB).
Remove-Item "$dist\python\Lib\site-packages\cv2\opencv_videoio_ffmpeg*.dll" -ErrorAction SilentlyContinue

# 3. C++ runtime DLLs the OpenCV / MediaPipe wheels need (embeddable Python ships only vcruntime140.dll).
$vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
$vsDir = & $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
$crt = Get-ChildItem "$vsDir\VC\Redist\MSVC" -Recurse -Directory -Filter "Microsoft.VC14*.CRT" |
    Where-Object { $_.FullName -match "\\x64\\" -and $_.FullName -notmatch "onecore|debug_nonredist" } | Select-Object -First 1
if (-not $crt) { throw "Visual C++ redistributable DLLs not found under $vsDir" }
Copy-Item "$($crt.FullName)\*.dll" "$dist\python" -Force

# 4. Engine and models (no user settings, calibration or logs).
New-Item -ItemType Directory "$dist\tracker" | Out-Null
Copy-Item "$root\tracker\*.py", "$root\tracker\*.task", "$root\tracker\requirements.txt" "$dist\tracker"

# Precompile everything: otherwise the first engine start after installing takes ~15 s instead of ~3 s.
& "$dist\python\python.exe" -m compileall -q -j 0 "$dist\python\Lib\site-packages" "$dist\tracker" | Out-Null

# 5. SteamVR driver (build_driver.bat builds it; SteamVR must be closed while building).
$dll = "$root\webcamfbt\bin\win64\driver_webcamfbt.dll"
if (-not (Test-Path $dll)) { & cmd /c "$root\build_driver.bat"; if ($LASTEXITCODE -ne 0) { throw "driver build failed" } }
New-Item -ItemType Directory "$dist\webcamfbt\bin\win64" | Out-Null
Copy-Item "$root\webcamfbt\driver.vrdrivermanifest" "$dist\webcamfbt"
Copy-Item $dll "$dist\webcamfbt\bin\win64"

# 6. App: self-contained single file, so no .NET install is needed.
Write-Host "Publishing the app..."
dotnet publish "$root\ui\WebcamFBT.csproj" -c Release -r win-x64 --self-contained true `
    -p:PublishSingleFile=true -p:IncludeNativeLibrariesForSelfExtract=true -p:EnableCompressionInSingleFile=true `
    -p:OutputPath="$root\build\ui-dist\" -o "$dist" --nologo | Out-Null
if ($LASTEXITCODE -ne 0) { throw "dotnet publish failed" }
Remove-Item "$dist\*.pdb" -ErrorAction SilentlyContinue

# 7. Docs and notices.
Copy-Item "$root\VERSION", "$root\README.md", "$root\KULLANIM.md", "$root\THIRD_PARTY_NOTICES.md" $dist

$size = (Get-ChildItem $dist -Recurse -File | Measure-Object Length -Sum).Sum / 1MB
Write-Host ("build\dist ready: {0:N0} MB" -f $size)

if ($SkipInstaller) { return }
$iscc = @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
          "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) { throw "Inno Setup 6 not found (winget install JRSoftware.InnoSetup)" }
& $iscc /Q "/DAppVersion=$version" "/DSourceDir=$dist" "/DOutputDir=$root\build\installer" "$PSScriptRoot\WebcamFBT.iss"
if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed" }
Write-Host "Installer: build\installer\WebcamFBT-Setup-$version.exe"
