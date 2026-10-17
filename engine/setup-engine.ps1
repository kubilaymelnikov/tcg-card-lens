# Richtet die TCG-Card-Lens-Engine ein (wird vom Installer aufgerufen, kann auch erneut ausgeführt werden).
#   1. Python 3.12 finden oder für den aktuellen Benutzer installieren
#   2. Virtuelle Umgebung + Pakete (PyTorch mit CUDA, wenn eine NVIDIA-GPU vorhanden ist)
#   3. Modelle laden
#   4. Auswahl speichern (data\config.json: Spiele, Namenssprachen, Paket-Adresse)
#   5. Kartendaten: je Spiel ein fertiges Datenpaket (Katalog + Erkennungs-Index) installieren;
#      ohne Paket (offline, noch nicht veröffentlicht) lokal aufbauen (Bilder laden + einbetten)
#
#   setup-engine.ps1 -Games pokemon,mtg -Languages de,en [-PackDir D:\packs] [-PackUrl https://…] [-SkipData]
#   Ohne -Games/-Languages bleibt die bisherige Auswahl (bzw. Standard: Pokémon + Magic, Deutsch + Englisch).
param(
    [switch]$SkipData,
    [string[]]$Games = @(),     # Spielkürzel, z. B. pokemon,mtg
    [string[]]$Languages = @(), # Sprachcodes, z. B. de,en
    [string]$PackUrl = "",    # Basis-URL der Datenpakete (leer = Standard/bisherige Einstellung)
    [string]$PackDir = ""     # lokaler Ordner mit Datenpaketen (Offline-Installation)
)
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
# GPU über die Windows-Geräteliste erkennen (nvidia-smi-Ausgabe auf stderr bricht unter "Stop" ab)
$gpu = [bool](Get-CimInstance Win32_VideoController -ErrorAction SilentlyContinue | Where-Object { $_.Name -match "NVIDIA" })
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

Step "Auswahl speichern (Spiele, Namenssprachen)"
$cfgArgs = @("-m", "cardengine.config")
# Listen zusammenfügen: "-Games pokemon,mtg" kommt aus PowerShell als Array, über -File als ein Text
if ($Games) { $cfgArgs += @("--games", ($Games -join ",")) }
if ($Languages) { $cfgArgs += @("--languages", ($Languages -join ",")) }
if ($PackUrl) { $cfgArgs += @("--pack-url", $PackUrl) }
& $venvPy @cfgArgs
if ($LASTEXITCODE -ne 0) { throw "Einstellungen konnten nicht gespeichert werden" }

if (-not $SkipData) {
    Step "Kartendaten installieren"
    Write-Host "Je Spiel wird zuerst ein fertiges Datenpaket (Kartenkatalog + Erkennungs-Index, ohne Bilder) installiert."
    Write-Host "Ist keines erreichbar, werden die Daten lokal aufgebaut (Kartenbilder laden + einbetten, dauert länger;"
    Write-Host "Magic-Bilder folgen dann im Hintergrund, während die Engine läuft)."
    $dataArgs = @("-m", "cardengine.maintenance", "--initial")
    if ($PackDir) {
        if (Test-Path $PackDir) {
            Write-Host "Offline-Pakete: $PackDir"
            $dataArgs += @("--pack-dir", $PackDir)
        } else {
            Write-Warning "Paket-Ordner $PackDir nicht gefunden – es wird die Paket-Adresse bzw. der lokale Aufbau genutzt."
        }
    }
    & $venvPy @dataArgs
    if ($LASTEXITCODE -ne 0) { Write-Warning "Kartendaten unvollständig – die Engine ergänzt sie beim nächsten Start." }
}

Step "Fertig"
Write-Host "Die Engine startet automatisch, sobald der Filter 'TCG Card Lens' in OBS aktiv ist."
Stop-Transcript | Out-Null
