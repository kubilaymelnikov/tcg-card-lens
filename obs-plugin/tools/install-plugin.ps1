# Kopiert das gebaute Plugin in den OBS-Pluginordner für alle Benutzer (ProgramData, OBS ab 28).
param([string]$Config = "RelWithDebInfo")
$root = Split-Path -Parent $PSScriptRoot
$dll = Join-Path $root "build_x64\$Config\tcg-card-lens.dll"
if (-not (Test-Path $dll)) { throw "Plugin nicht gebaut: $dll" }
$target = Join-Path $env:ProgramData "obs-studio\plugins\tcg-card-lens"
New-Item -ItemType Directory -Force "$target\bin\64bit", "$target\data\locale" | Out-Null
Copy-Item $dll "$target\bin\64bit\" -Force
$pdb = [IO.Path]::ChangeExtension($dll, ".pdb")
if (Test-Path $pdb) { Copy-Item $pdb "$target\bin\64bit\" -Force }
Copy-Item (Join-Path $root "data\locale\*.ini") "$target\data\locale\" -Force
Write-Host "Installiert nach $target"
