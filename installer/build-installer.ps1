# Baut Plugin (Release mit Debug-Infos) und den Installer.
#   powershell -ExecutionPolicy Bypass -File installer\build-installer.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$cmake = Get-ChildItem "$env:LOCALAPPDATA\Microsoft\WinGet\Packages\Kitware.CMake*" -Recurse -Filter cmake.exe -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty FullName
if (-not $cmake) { $cmake = (Get-Command cmake -ErrorAction Stop).Source }
$iscc = @("$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe", "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) { throw "Inno Setup 6 nicht gefunden (winget install JRSoftware.InnoSetup)" }

Push-Location (Join-Path $root "obs-plugin")
if (-not (Test-Path "build_x64")) { & $cmake --preset windows-x64; if ($LASTEXITCODE) { throw "CMake-Konfiguration fehlgeschlagen" } }
& $cmake --build --preset windows-x64 --config RelWithDebInfo
if ($LASTEXITCODE) { throw "Plugin-Build fehlgeschlagen" }
Pop-Location

& $iscc (Join-Path $PSScriptRoot "tcg-card-lens.iss")
if ($LASTEXITCODE) { throw "Installer-Build fehlgeschlagen" }
Get-ChildItem (Join-Path $PSScriptRoot "Output") | Select-Object Name, @{n='MB';e={[math]::Round($_.Length/1MB,1)}}
