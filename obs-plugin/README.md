# TCG Card Lens – OBS-Plugin

Videofilter für OBS Studio (Windows, ab 31.x), der Pokémon- und Magic-Karten in jeder Videoquelle erkennt und
Rahmen, Kartennamen und Cardmarket-Preise **direkt ins Bild** zeichnet – ohne Browserquelle.
Architektur und Protokoll: [../docs/PLUGIN.md](../docs/PLUGIN.md).

## Bauen (Windows)

Voraussetzungen: Visual Studio 2022 (Build Tools mit „Desktopentwicklung mit C++“), CMake ≥ 3.28.

```powershell
cd obs-plugin
cmake --preset windows-x64          # lädt OBS-Quellen 31.1.1 + obs-deps (einmalig, ~1 GB)
cmake --build --preset windows-x64 --config RelWithDebInfo
```

Ergebnis: `build_x64/RelWithDebInfo/tcg-card-lens.dll` und `data/locale/*.ini`.

## Installieren

Nach `C:\ProgramData\obs-studio\plugins\tcg-card-lens\` kopieren:

```
tcg-card-lens\bin\64bit\tcg-card-lens.dll
tcg-card-lens\data\locale\de-DE.ini
tcg-card-lens\data\locale\en-US.ini
```

`tools\install-plugin.ps1` erledigt das nach dem Build.

## Benutzen

1. Engine starten: `engine\start-engine.cmd` (oder im Filter „Engine automatisch starten“ + Startskript wählen).
2. In OBS: Quelle (z. B. Webcam) → Filter → **+** → „TCG Card Lens (Kartenerkennung)“.
3. Einstellungen im Filter: Kartenspiele, Kameramodus, Sprachen, Anzeigesprache, Preis/Verlauf, Farben,
   Debug-Anzeige, Snapshots.

Mehrere Quellen können den Filter gleichzeitig nutzen; die Engine lädt die Modelle nur einmal.
