@echo off
rem Startet die TCG-Card-Lens-Engine.
rem   start-engine.cmd            sichtbar in diesem Konsolenfenster
rem   start-engine.cmd --hidden   vom OBS-Plugin genutzt: ohne Fenster, Ausgabe in data\engine.log,
rem                               beendet sich nach 15 min ohne OBS-Verbindung
cd /d "%~dp0"
if not exist data mkdir data
set PYTHONUNBUFFERED=1
if /I "%~1"=="--hidden" (
  if exist data\engine.log move /y data\engine.log data\engine.prev.log >nul
  ".venv\Scripts\python.exe" server.py --idle-exit 900 > data\engine.log 2>&1
) else (
  title TCG Card Lens Engine
  ".venv\Scripts\python.exe" server.py %*
)
