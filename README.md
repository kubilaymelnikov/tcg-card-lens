# TCG Card Lens

Erkennt **Magic: The Gathering**- und **Pokémon**-Karten im Kamerabild, rahmt sie ein und blendet daneben den
aktuellen **Cardmarket-Preis** samt **Preisverlauf** ein – im Browser oder als **Overlay auf einer bestehenden
OBS-Quelle**.

Ablauf: Kamera/OBS-Quelle → Kartenumriss (OpenCV) → Name + Nummer (Tesseract) → Preis (Scryfall/TCGdex/MTGJSON) → Overlay

## Schnellstart

Voraussetzung: Node.js ≥ 22.13 (getestet mit 24).

```bash
npm install
npm start
```

- Kamera-Ansicht: <http://localhost:4711/>
- OBS-Overlay: <http://localhost:4711/overlay.html?source=Webcam>
- Testszene ohne Kamera: <http://localhost:4711/test.html>

Für den **Magic-Preisverlauf** einmalig (und danach gern regelmäßig, z. B. wöchentlich) ausführen:

```bash
npm run import:mtgjson
```

Lädt ~150 MB von MTGJSON, dauert ~7 min und legt eine ~60 MB große Datenbank unter `data/` an.
Ältere Tage bleiben bei jedem Import erhalten – der Verlauf wird also mit der Zeit länger als 90 Tage.

## Mit dem Handy testen

```bash
npm run start:lan
```

Der Server ist dann zusätzlich im Heimnetz erreichbar, per HTTPS auf Port 4712 (Handy-Browser geben die Kamera nur
über HTTPS frei). Die Adresse steht in der Konsole, z. B. `https://192.168.1.163:4712/`. Das Zertifikat ist selbst
signiert: Die Browser-Warnung einmal bestätigen („Erweitert → Trotzdem fortfahren“). Windows fragt beim ersten Start
ggf. nach Firewall-Zugriff für Node.js – für **private Netzwerke** erlauben.

## In OBS nutzen

Das Overlay arbeitet **auf jeder bestehenden Quelle** (Webcam, Capture-Karte, Handy-Kamera …), nicht nur auf einer
eigenen Kamera:

1. OBS → **Werkzeuge → WebSocket-Server-Einstellungen** → Server aktivieren, Port/Passwort notieren.
2. In der Kamera-Ansicht auf **„In OBS nutzen“** klicken, Quellenname + Passwort eintragen, URL kopieren.
3. In OBS eine **Browser**-Quelle mit dieser URL anlegen, Breite/Höhe = Auflösung der Quelle.
4. Die Browserquelle **über** die Kamera-Quelle legen und gleich groß ausrichten (am einfachsten beide in eine Gruppe).

Technisch holt sich das Overlay per obs-websocket (`GetSourceScreenshot`) ~5 Bilder/s der Quelle, erkennt darin
die Karten und zeichnet nur Rahmen und Infos auf transparentem Hintergrund. Ein echter Filter im OBS-Sinn ist es
damit nicht, wirkt aber genauso und braucht kein Plugin.

URL-Parameter: `source` (Pflicht), `host`, `port` (4455), `password`, `fps` (5), `game` (`auto`/`mtg`/`pokemon`),
`scale` (Panelgröße), `debug=1` (zeigt auch noch nicht erkannte Karten und Statusmeldungen).

## Wie es funktioniert

| Schritt | Technik |
|---|---|
| Karte finden | OpenCV.js: Kanten → Konturen → Vierecke mit Seitenverhältnis 63 × 88 mm |
| Verfolgen | Tracking über Frames, damit nicht jedes Bild neu gelesen wird |
| Lesen | Karte entzerren, Tesseract.js liest Namenszeile und Fußzeile (Set-Code, Sammlernummer); Ausrichtung (auf dem Kopf liegend) wird automatisch bestimmt |
| Zuordnen | Unscharfe Suche gegen alle Kartennamen (Scryfall, TCGdex) – OCR-Fehler werden toleriert. Pokémon werden bevorzugt über „Nummer/Setgröße“ zugeordnet |
| Preise Magic | Scryfall (`prices.eur` = Cardmarket) + Verlauf aus MTGJSON (Cardmarket, 90 Tage, wächst mit jedem Import) |
| Preise Pokémon | TCGdex (Cardmarket: Trend, ab-Preis, Ø 1/7/30 Tage) |
| Eigener Verlauf | Jede erkannte Karte wird gemerkt und ihr Preis täglich in `data/prices.sqlite` geschrieben |

Alles läuft lokal; nur die Preis-/Katalog-APIs werden abgefragt (mit Cache).

## Grenzen (ehrlich)

- **Druck/Edition**: Der Name wird zuverlässig erkannt, die exakte Edition nur, wenn die Fußzeile lesbar ist.
  Sonst steht im Panel „Druck geschätzt“ – dann ist der Preis der einer anderen Version derselben Karte.
  Besonders schwierig: Vollbild-Karten mit heller Schrift auf dem Artwork.
- **Pokémon-Verlauf**: TCGdex liefert keine Zeitreihe, nur Ø 1/7/30 Tage (als Balken dargestellt). Eine echte Kurve
  entsteht durch die tägliche Eigenaufzeichnung erst mit der Zeit.
- **Magic auf Deutsch**: Erkannt werden derzeit nur englische Magic-Namen. Pokémon funktioniert auf Englisch und Deutsch.
- **Bildqualität**: Gutes Licht, scharfes Bild und ein ruhiger Hintergrund mit Kontrast zur Karte helfen stark.
  Karten in Hüllen funktionieren, starke Spiegelungen nicht.
- Cardmarket selbst bietet keine öffentliche API mehr für neue Nutzer – deshalb der Weg über Scryfall/TCGdex/MTGJSON.

## Ideen für später

- Bildbasierte Erkennung (Perceptual Hash des Artworks) statt/zusätzlich zu OCR → exakte Edition auch ohne lesbare Fußzeile
- Deutsche Magic-Namen (Scryfall-Bulk-Daten `all_cards`)
- Steuer-Panel für den Stream (Karte anpinnen, Fehlerkennung korrigieren, Druck auswählen)
- Natives OBS-Filter-Plugin (C++) für höhere Bildrate

## Projektstruktur

```
server/            Node-Server (ohne Framework): API, Kataloge, Preise, SQLite (node:sqlite)
  identify.js      OCR-Text → Karte (Fußzeile parsen, Namen matchen)
  mtg.js           Scryfall + MTGJSON
  pokemon.js       TCGdex
  fuzzy.js         Trigramm-Index + Edit-Distanz
public/            Browser-Seiten
  js/detector.js   OpenCV: Karten finden und entzerren
  js/reader.js     Tesseract: Name und Fußzeile lesen
  js/lens.js       Tracking + Identifikation
  js/render.js     Rahmen, Panels, Preisverlauf
  js/obs.js        obs-websocket-v5-Client
scripts/
  import-mtgjson.js  Magic-Preisverlauf importieren
  mock-obs.js        OBS-WebSocket-Simulator zum Testen ohne OBS (Passwort „test“)
data/              Caches und Datenbank (wird automatisch angelegt)
```

`npm test` führt die Unit-Tests aus.
