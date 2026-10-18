# Richtet die TCG-Card-Lens-Engine ein (wird vom Installer aufgerufen, kann auch erneut ausgeführt werden).
#   1. Python 3.12 finden oder für den aktuellen Benutzer installieren
#   2. Virtuelle Umgebung + Pakete (PyTorch mit CUDA, wenn eine NVIDIA-GPU vorhanden ist)
#   3. Modelle laden
#   4. Auswahl speichern (data\config.json: Spiele, Namenssprachen, Paket-Adresse)
#   5. Kartendaten: je Spiel ein fertiges Datenpaket (Katalog + Erkennungs-Index) installieren;
#      ohne Paket (offline, noch nicht veröffentlicht) lokal aufbauen (Bilder laden + einbetten)
#
#   setup-engine.ps1 -Games pokemon,mtg -Languages de,en [-PackDir D:\packs] [-PackUrl https://…] [-SkipData] [-UiLang de|en]
#   Ohne -Games/-Languages bleibt die bisherige Auswahl (bzw. Standard: Pokémon + Magic, Deutsch + Englisch).
param(
    [switch]$SkipData,
    [string[]]$Games = @(),     # Spielkürzel, z. B. pokemon,mtg
    [string[]]$Languages = @(), # Sprachcodes, z. B. de,en
    [string]$PackUrl = "",    # Basis-URL der Datenpakete (leer = Standard/bisherige Einstellung)
    [string]$PackDir = "",    # lokaler Ordner mit Datenpaketen (Offline-Installation)
    [string]$UiLang = ""      # Sprache der Meldungen: de | en (leer = Windows-Sprache; der Installer übergibt seine)
)
$ErrorActionPreference = "Stop"
$here = $PSScriptRoot
Set-Location $here
$log = Join-Path $here "data\setup.log"
New-Item -ItemType Directory -Force (Split-Path $log) | Out-Null
Start-Transcript -Path $log -Append | Out-Null

if (-not $UiLang) { $UiLang = (Get-UICulture).TwoLetterISOLanguageName }
$script:De = $UiLang -like "de*"
function L($de, $en) { if ($script:De) { $de } else { $en } }  # Meldung in der Setup-Sprache
function Step($text) { Write-Host ""; Write-Host "==> $text" -ForegroundColor Cyan }

Step (L "Python 3.12 suchen" "Looking for Python 3.12")
$py = $null
foreach ($c in @("$env:LOCALAPPDATA\Programs\Python\Python312\python.exe", "$env:ProgramFiles\Python312\python.exe")) {
    if (Test-Path $c) { $py = $c; break }
}
if (-not $py) {
    try { $py = (& py -3.12 -c "import sys; print(sys.executable)") 2>$null } catch {}
}
if (-not $py) {
    Step (L "Python 3.12 installieren (nur für diesen Benutzer)" "Installing Python 3.12 (current user only)")
    winget install --id Python.Python.3.12 --scope user --silent --accept-package-agreements --accept-source-agreements
    $py = "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe"
    if (-not (Test-Path $py)) { throw (L "Python-Installation fehlgeschlagen. Bitte Python 3.12 manuell installieren und Setup erneut starten." "Python installation failed. Please install Python 3.12 manually and run setup again.") }
}
Write-Host "Python: $py"

Step (L "Virtuelle Umgebung" "Virtual environment")
$venvPy = Join-Path $here ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPy)) { & $py -m venv (Join-Path $here ".venv") }
& $venvPy -m pip install --upgrade pip --quiet

Step (L "PyTorch installieren" "Installing PyTorch")
# GPU über die Windows-Geräteliste erkennen (nvidia-smi-Ausgabe auf stderr bricht unter "Stop" ab)
$gpu = [bool](Get-CimInstance Win32_VideoController -ErrorAction SilentlyContinue | Where-Object { $_.Name -match "NVIDIA" })
if ($gpu) {
    Write-Host (L "NVIDIA-GPU gefunden – PyTorch mit CUDA 13.0 (ca. 2 GB Download)" "NVIDIA GPU found – PyTorch with CUDA 13.0 (about 2 GB download)")
    & $venvPy -m pip install torch==2.14.1 torchvision==0.29.1 --index-url https://download.pytorch.org/whl/cu130
} else {
    Write-Host (L "Keine NVIDIA-GPU gefunden – PyTorch für CPU (deutlich langsamer)" "No NVIDIA GPU found – PyTorch for CPU (much slower)")
    & $venvPy -m pip install torch==2.14.1 torchvision==0.29.1 --index-url https://download.pytorch.org/whl/cpu
}
if ($LASTEXITCODE -ne 0) { throw (L "PyTorch-Installation fehlgeschlagen" "PyTorch installation failed") }

Step (L "Weitere Pakete" "Further packages")
& $venvPy -m pip install -r (Join-Path $here "requirements.txt")
if ($LASTEXITCODE -ne 0) { throw (L "Paketinstallation fehlgeschlagen" "Package installation failed") }

Step (L "Modelle laden" "Downloading models")
& $venvPy -m cardengine.models_fetch
if ($LASTEXITCODE -ne 0) { throw (L "Modelle konnten nicht geladen werden" "Models could not be downloaded") }

Step (L "Auswahl speichern (Spiele, Namenssprachen)" "Saving selection (games, card-name languages)")
$cfgArgs = @("-m", "cardengine.config")
# Listen zusammenfügen: "-Games pokemon,mtg" kommt aus PowerShell als Array, über -File als ein Text
if ($Games) { $cfgArgs += @("--games", ($Games -join ",")) }
if ($Languages) { $cfgArgs += @("--languages", ($Languages -join ",")) }
if ($PackUrl) { $cfgArgs += @("--pack-url", $PackUrl) }
& $venvPy @cfgArgs
if ($LASTEXITCODE -ne 0) { throw (L "Einstellungen konnten nicht gespeichert werden" "Settings could not be saved") }

if (-not $SkipData) {
    Step (L "Kartendaten installieren" "Installing card data")
    Write-Host (L "Je Spiel wird zuerst ein fertiges Datenpaket (Kartenkatalog + Erkennungs-Index, ohne Bilder) installiert." "For each game a ready-made data pack (card catalogue + recognition index, no images) is installed first.")
    Write-Host (L "Ist keines erreichbar, werden die Daten lokal aufgebaut (Kartenbilder laden + einbetten, dauert länger;" "If none is reachable, the data is built locally (downloading and embedding card images takes longer;")
    Write-Host (L "Magic-Bilder folgen dann im Hintergrund, während die Engine läuft)." "Magic images then follow in the background while the engine runs).")
    $dataArgs = @("-m", "cardengine.maintenance", "--initial")
    if ($PackDir) {
        if (Test-Path $PackDir) {
            Write-Host ((L "Offline-Pakete: " "Offline packs: ") + $PackDir)
            $dataArgs += @("--pack-dir", $PackDir)
        } else {
            Write-Warning ((L "Paket-Ordner nicht gefunden: " "Pack folder not found: ") + $PackDir + (L " – es wird die Paket-Adresse bzw. der lokale Aufbau genutzt." " – using the pack URL or local build instead."))
        }
    }
    & $venvPy @dataArgs
    if ($LASTEXITCODE -ne 0) { Write-Warning (L "Kartendaten unvollständig – die Engine ergänzt sie beim nächsten Start." "Card data incomplete – the engine completes it on next start.") }
}

Step (L "Fertig" "Done")
Write-Host (L "Die Engine startet automatisch, sobald der Filter 'TCG Card Lens' in OBS aktiv ist." "The engine starts automatically as soon as the 'TCG Card Lens' filter is active in OBS.")
Stop-Transcript | Out-Null
