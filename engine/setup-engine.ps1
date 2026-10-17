# Richtet die TCG-Card-Lens-Engine ein (wird vom Installer aufgerufen, kann auch erneut ausgeführt werden).
#   1. Python 3.12 finden oder für den aktuellen Benutzer installieren
#   2. Virtuelle Umgebung + Pakete (PyTorch mit CUDA, wenn eine NVIDIA-GPU vorhanden ist)
#   3. Modelle laden
#   4. Grunddaten aufbauen (Pokémon-Katalog + Referenzbilder + Index, Preise); Magic folgt im Hintergrund
param([switch]$SkipData)
$ErrorActionPreference = "Stop"
$here = $PSScriptRoot
Set-Location $here
$log = Join-Path $here "data\setup.log"
New-Item -ItemType Directory -Force (Split-Path $log) | Out-Null
Start-Transcript -Path $log -Append | Out-Null

function Step($text) { Write-Host ""; Write-Host "==> $text" -ForegroundColor Cyan }

Step "Python 3.12 suchen"
$py = $null
foreach ($c in @("$env:LOCALAPPDATA\Programs\Python\Python312\python.exe", "$env:ProgramFiles\Python312\python.exe")) {
    if (Test-Path $c) { $py = $c; break }
}
if (-not $py) {
    try { $py = (& py -3.12 -c "import sys; print(sys.executable)") 2>$null } catch {}
}
if (-not $py) {
    Step "Python 3.12 installieren (nur für diesen Benutzer)"
    winget install --id Python.Python.3.12 --scope user --silent --accept-package-agreements --accept-source-agreements
    $py = "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe"
    if (-not (Test-Path $py)) { throw "Python-Installation fehlgeschlagen. Bitte Python 3.12 manuell installieren und Setup erneut starten." }
}
Write-Host "Python: $py"

Step "Virtuelle Umgebung"
$venvPy = Join-Path $here ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPy)) { & $py -m venv (Join-Path $here ".venv") }
& $venvPy -m pip install --upgrade pip --quiet

Step "PyTorch installieren"
$gpu = $false
try { $null = & nvidia-smi -L 2>$null; $gpu = ($LASTEXITCODE -eq 0) } catch {}
if ($gpu) {
    Write-Host "NVIDIA-GPU gefunden – PyTorch mit CUDA 13.0 (ca. 2 GB Download)"
    & $venvPy -m pip install torch==2.14.1 torchvision==0.29.1 --index-url https://download.pytorch.org/whl/cu130
} else {
    Write-Host "Keine NVIDIA-GPU gefunden – PyTorch für CPU (deutlich langsamer)"
    & $venvPy -m pip install torch==2.14.1 torchvision==0.29.1 --index-url https://download.pytorch.org/whl/cpu
}
if ($LASTEXITCODE -ne 0) { throw "PyTorch-Installation fehlgeschlagen" }

Step "Weitere Pakete"
& $venvPy -m pip install -r (Join-Path $here "requirements.txt")
if ($LASTEXITCODE -ne 0) { throw "Paketinstallation fehlgeschlagen" }

Step "Modelle laden"
& $venvPy -m cardengine.models_fetch
if ($LASTEXITCODE -ne 0) { throw "Modelle konnten nicht geladen werden" }

if (-not $SkipData) {
    Step "Grunddaten aufbauen (Pokémon, Preise) – Magic folgt automatisch im Hintergrund"
    & $venvPy -m cardengine.maintenance --initial
    if ($LASTEXITCODE -ne 0) { Write-Warning "Grunddaten unvollständig – die Engine ergänzt sie beim nächsten Start." }
}

Step "Fertig"
Write-Host "Die Engine startet automatisch, sobald der Filter 'TCG Card Lens' in OBS aktiv ist."
Stop-Transcript | Out-Null
