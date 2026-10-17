# Datenpakete

Statt nach der Installation alle Referenzbilder zu laden und einzubetten (Magic: >100 000 Bilder, Stunden), holt
die Engine je Spiel ein fertiges **Datenpaket**: Kartenkatalog + Erkennungs-Index. Der lokale Aufbau bleibt als
Rückfall erhalten (kein Netz, Paket-Server noch nicht eingerichtet).

## Inhalt eines Pakets

`tcglens-<spiel>-<indexname>-<JJJJMMTT>.zip`, z. B. `tcglens-mtg-occ-dinov3s-norm-20261017.zip`

| Datei | Inhalt |
|---|---|
| `manifest.json` | `format`, `game`, `index_name`, `cards` (Katalogzeilen), `vectors`, `dim`, `created`, `version`, `engine`, SHA256 + Größe jeder Datei |
| `catalog.jsonl` | Zeilen der Tabelle `cards` für dieses Spiel (Name, Set, Nummer, Cardmarket-ID, Bild-URL …) |
| `index.npz` | Embeddings (float16) mit Schlüsseln `spiel\|id`, nur für das Modell `index_name` |

**Keine Referenzbilder** (Bildrechte von TPCi/WotC u. a.). Die Engine lädt Bilder nur bei Bedarf: für neue Karten,
die noch keinen Index-Eintrag haben, und für die Vorschau im Dock.

`index_name` kommt aus `Embedder.default_index_name()` (Modell + Normierung, derzeit `occ-dinov3s-norm`).
Vektoren verschiedener Modelle sind nicht vergleichbar. Wechselt das Standardmodell, braucht es neue Pakete.
Bis dahin baut die Engine lokal auf.

Neben den Paketen liegt `packs.json`:

```json
{"format": 1, "updated": "…", "packs": [
  {"game": "mtg", "index_name": "occ-dinov3s-norm", "file": "tcglens-mtg-occ-dinov3s-norm-20261017.zip",
   "size": 86452654, "sha256": "…", "cards": 103611, "vectors": 103611, "version": "20261017", "created": "…"}]}
```

Größen (Stand 2026-10-17): Pokémon 16,0 MB (21 990 Karten), Magic 86,5 MB (103 611 Drucke).

## Index je Spiel

Der Index liegt pro Spiel unter `engine/data/index/cards-<indexname>-<spiel>.npz`.
`EmbeddingIndex.load(name, games=[…])` führt die gewählten Spiele zusammen.
Die frühere gemeinsame Datei `cards-<indexname>.npz` wird beim ersten Start der neuen Engine einmal aufgeteilt.
Die Spieldateien werden atomar geschrieben, erst danach wird das Original in `….npz.vor-aufteilung` umbenannt
(kann nach erfolgreichem Start gelöscht werden). `build_index` ergänzt jede Spieldatei inkrementell. Vektoren aus
Paketen bleiben dabei erhalten, auch ohne lokales Bild.

## Bauen

```
engine\.venv\Scripts\python.exe engine\tools\build_data_pack.py --games pokemon,mtg --out D:\release
```

Das Werkzeug liest Katalog und Index nur (die Datenbank wird schreibgeschützt geöffnet, eine alte gemeinsame
Indexdatei wird nicht aufgeteilt). Ins Paket kommen nur Vektoren, zu denen es eine Katalogzeile gibt.
`packs.json` im Zielordner wird ergänzt: Je Spiel und Modell gilt der neueste Eintrag.

## Installieren

Die Quellen werden in dieser Reihenfolge versucht:

1. **Lokaler Ordner oder ZIP** (`--pack-dir`, Installer: Ordner `packs` neben der Setup-exe → Offline-Installation).
   Die Pakete werden über `packs.json` gefunden (dann mit SHA256-Prüfung) oder über den Dateinamen.
2. **Basis-URL**: `TCGLENS_PACK_URL` > `engine/data/config.json: pack_url` > `data_packs.DEFAULT_PACK_URL`.
   Geladen wird `<url>/packs.json`, danach die Paketdatei. Die Paketdatei wird gegen die SHA256 aus `packs.json` geprüft.

Bei jedem Paket werden außerdem die Prüfsummen der enthaltenen Dateien geprüft, ebenso Modell und Spiel.
Erst danach werden die Katalogzeilen per `upsert` eingespielt und die Spieldatei des Index geschrieben. Lokal schon
eingebettete Karten, die das Paket nicht kennt, bleiben dabei erhalten.

```
python -m cardengine.data_packs                              # Spiele aus config.json
python -m cardengine.data_packs --games pokemon --dir D:\packs
python -m cardengine.maintenance --initial --pack-dir D:\packs  # wie im Setup: Pakete, sonst lokaler Aufbau
```

**Ablauf in der Engine** (`cardengine/maintenance.py`): Fehlt einem gewählten Spiel der Index, wird zuerst das
Paket installiert. Das passiert beim Setup, beim ersten Start und stündlich im Hintergrund, etwa wenn ein Spiel
nachträglich gewählt wird. Danach wird der Index ohne Neustart getauscht. Schlägt das Paket fehl, folgt der
bisherige lokale Aufbau: Katalog laden, Bilder laden (Magic in Blöcken im Hintergrund), einbetten.

## Voreinstellungen (`engine/data/config.json`)

```json
{"games": ["pokemon", "mtg"], "languages": ["de", "en"], "pack_url": ""}
```

Der Installer schreibt die Datei über `setup-engine.ps1 -Games … -Languages … [-PackUrl …]`, das intern
`python -m cardengine.config` aufruft. Ohne Parameter bleibt die bisherige Auswahl. Die Engine lädt nur den Index der
gewählten Spiele und lädt die Namenslisten der gewählten Sprachen vorab. Bekannte Spiele stehen in
`cardengine/config.py` (`GAMES`), den Katalog-Aufbau je Spiel regelt `maintenance.CATALOG_BUILDERS`.

## Hosting (später: GitHub-Releases)

`DEFAULT_PACK_URL` ist ein **Platzhalter** (`…/PLATZHALTER/…`), weil das Repository noch nicht veröffentlicht ist.
Solange er gilt, greift die Engine nicht aufs Netz zu und baut lokal auf. Für die Veröffentlichung:

1. Pakete bauen (siehe oben).
2. Einen Release anlegen und alle ZIPs sowie `packs.json` als Assets hochladen, z. B. mit
   `gh release create daten-20261017 D:\release\*`.
3. `DEFAULT_PACK_URL` auf `https://github.com/<besitzer>/tcg-card-lens/releases/latest/download` setzen. GitHub
   leitet dort auf die Assets des neuesten Releases weiter, deshalb reicht ein fester Dateiname `packs.json`.
   Die Paketdateien tragen das Datum im Namen.

Assets bis 2 GB sind möglich, die Pakete liegen weit darunter.
Vor der Veröffentlichung klären: Lizenzhinweise zu Katalogdaten (Scryfall, TCGdex) und zu den Embeddings
(abgeleitete Merkmalsvektoren, keine Bilder; Modell unter DINOv3-Lizenz) in `NOTICE.md` ergänzen.

## Update-Strategie

- **Neue Sets/Karten**: Die Engine prüft wöchentlich die Kataloge. Für neue Karten ohne Index-Eintrag lädt sie
  nur deren Bilder und bettet sie lokal ein. Ein volles Paket muss dafür nicht erneut geladen werden.
- Der Katalog-Stempel wird auf das Erstellungsdatum des Pakets gesetzt. Ist das Paket älter als eine Woche, wird
  der Katalog sofort nach der Installation nachgeprüft.
- **Neue Pakete** (z. B. monatlich) helfen vor allem Neuinstallationen. Bestehende Installationen bleiben bei der
  inkrementellen Pflege. Ein Paket neu einspielen: Spieldatei `cards-<indexname>-<spiel>.npz` löschen, dann lädt
  die Engine beim nächsten Wartungslauf das aktuelle Paket.
- **Neues Embedding-Modell**: neue Pakete mit neuem `index_name` bauen. Alte Installationen nutzen weiter ihre
  Spieldateien.
- **Neues Spiel**: Kürzel in `config.GAMES`, Katalog-Aufbau in `maintenance.CATALOG_BUILDERS`, Eintrag in
  `GameList` von `installer/tcg-card-lens.iss`, dann ein Paket bauen.
