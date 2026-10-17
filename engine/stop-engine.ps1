# Beendet eine laufende TCG-Card-Lens-Engine (nur Python-Prozesse, die server.py aus diesem Ordner ausführen).
$dir = $PSScriptRoot
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -and $_.CommandLine -like "*server.py*" -and $_.ExecutablePath -like "$dir*" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
