# Yu-Gi-Oh! – Katalog, Identifikation, Preise

Stand: 2026-10-17. Spielkürzel intern `ygo`, Schlüssel `ygo|<id>`.

## Aufbau der Daten

```powershell
.\.venv\Scripts\python.exe -m cardengine.config --games pokemon,mtg,ygo   # Yu-Gi-Oh mit auswählen
.\.venv\Scripts\python.exe -m cardengine.maintenance                       # Datenpaket oder lokaler Aufbau (~1 h Bilder)
.\.venv\Scripts\python.exe -m cardengine.card_database.ygo --no-images   # nur Katalog/Zuordnung
```

Danach hält die Hintergrund-Wartung den Yu-Gi-Oh-Katalog wöchentlich aktuell (nur wenn er einmal aufgebaut wurde –
Pokémon-/Magic-Nutzer laden nichts zusätzlich). Umfang (17.10.2026): 14 618 Karten, **44 591 Drucke**,
684 reine Artwork-Einträge, **14 790 Artworks** (Bilder 1,6 GB bei 680 px Höhe), Index-Aufbau ~6 min (RTX, Batch 32).

| Datei | Inhalt |
|---|---|
| `cardengine/card_database/ygo.py` | YGOPRODeck-Abruf, Katalogeinträge, Bild-Download, Cardmarket-Zuordnung |
| `cardengine/recognition/footer.py` | `ygo_code_key`, `parse_ygo_codes`, `ygo_match_codes` (Set-Code-Parser) |
| `cardengine/recognition/ocr.py` | `FooterOcr.read_set_code` (Zeilenstreifen unter dem Artwork / Pendel unten links) |
| `cardengine/recognition/identifier.py` | `_identify_ygo` (Name über Artwork, Druck über Set-Code) |
| `cardengine/games.py` | Einstellung `games` (Kombinationen, rückwärtskompatibel) |
| `tools/eval_ygo.py` | Messung (echte Snapshots, synthetisch) |

### Katalogmodell

- **Ein Eintrag je Druck** (`card_sets` der API): `id = "<passcode>-<set-code>-<seltenheit>"`, z. B.
  `76792184-LCYW-EN078-C`; `set_code = "LCYW"`, `number = "EN078"`, `printed_number = "LCYW-EN078"`,
  `finishes` = Seltenheit („Common“), `released` = TCG-Datum des Sets. Doppelte API-Zeilen werden entfernt; gleicher
  Code in einem zweiten Set (z. B. Neuauflage) bekommt `-2`.
- **Bilder je Artwork**: `catalog.image_path("ygo", id)` bildet auf `<passcode>.jpg` bzw. `<bild-id>.jpg` ab
  (Signatur unverändert, Magic/Pokémon unberührt). `build_index` bettet jede Bilddatei nur einmal ein.
- **Reine Artwork-Einträge** `"<passcode>-art<bild-id>"` (ohne Set-Code) für Alt-Arts (YGOPRODeck ordnet sie keinem
  Druck zu) und Karten ganz ohne TCG-Druck (OCG-only, Duel Links …). Sie liefern den Namen, nie einen sicheren Druck.
- Namen: `CardNames` lädt bei aufgebautem Katalog `cardinfo.php?language=de|fr|it|pt` (je ~19 MB, 7 Tage Cache),
  Schlüssel Passcode; es/ja gibt es bei YGOPRODeck nicht → Englisch.

## Identifikation

Yu-Gi-Oh-Karten sind 59 × 86 mm (Seitenverhältnis 0,686 statt 0,716). **Geometrie/Detektor verkraften das:** der
Detektor fand beide Testkarten mit Score 0,95; `rectify` streckt auf 488 × 680 (≈ 4,6 % horizontal) – für das
Embedding (224 × 224) und die OCR-Streifen (Kartenanteile) unerheblich. Der klassische Detektor (nur Fallback)
akzeptiert ±14 % Abweichung, bewertet Yu-Gi-Oh-Karten aber ~30 % niedriger.

Ablauf (`_identify_ygo`):
1. Embedding-Suche → Name. Ist der Abstand zur besten anderen Karte < `name_margin`, kommen bis zu drei knappe
   Namen in Frage – der Set-Code entscheidet dann.
2. OCR des Set-Codes: reine Zeilenerkennung (RapidOCR ohne Textdetektor) auf 4 schmalen Streifen rechts unter dem
   Artwork, bei Misserfolg 2 Streifen unten links (Pendel). Der Textdetektor fand die winzige Zeile im Kamerabild fast
   nie (2/30), die Streifen meist (19/27, gleiche Bilder). Abbruch, sobald ein Code eines Kandidaten passt.
3. Abgleich tolerant gegen Region und OCR-Verwechslungen (`ygo_match_codes`): `LOB-DE001`, `LOB-G001`, `LOB-E001`,
   `LOB-001` → Katalog `LOB-EN001`; O/0/D, I/L/1, B/8, Z/2, S/5, G/6 gleichgesetzt, Bindestrich optional. Vertretbar,
   weil nur die (wenigen) Drucke der erkannten Karte verglichen werden.
4. Entscheidung:
   - Code passt zu **genau einem** Druck → sicher (`embedding+ocr`) → Preis.
   - Code passt zu mehreren Drucken (gleicher Code, **mehrere Seltenheiten**, oder alte Regionsdrucke mit gleichem
     Schlüssel) → Name sicher, Druck unsicher, kein Preis. Seltenheit wird bewusst nicht optisch geraten.
   - Kein Code lesbar → nur Karten mit genau einem TCG-Druck gelten als sicher (wie Magic/Pokémon), sonst unsicher.
   - Gelesener Code gehört zu keinem Druck der Karte → unsicher; Regionskennung JP/KR/AE (OCG/Asien) → unsicher.
   Bei unsicherem Druck zeigt Overlay/Dock trotzdem den (übersetzten) Namen.

## Preise (Cardmarket)

`price_guide_3.json` (Yu-Gi-Oh) wird wie Magic/Pokémon täglich geladen (`pricing/cardmarket.py`, `GAME_IDS["ygo"] = 3`);
nur der Normalpreis (`trend`/`avg`…) wird genutzt – die Datei enthält auch `-foil`-Felder, deren Bedeutung für
Yu-Gi-Oh nicht dokumentiert ist.

**Zuordnung Druck → idProduct** (`ygo.cardmarket_products`): `products_singles_3.json` enthält je Produkt nur Name,
idExpansion, idMetacard, dateAdded – keinen Set-Code und keine Seltenheit; YGOPRODeck kennt keine Cardmarket-IDs.
Expansionsnamen gibt es nur indirekt über `products_nonsingles_3.json` (Booster/Displays derselben idExpansion).
Verfahren:
1. Set ↔ Expansion über gemeinsame Kartennamen (Anteil am größeren Namensbestand ≥ 0,6). Liegen Kandidaten näher als
   0,1 beieinander (z. B. „Metal Raiders“ vs. „Metal Raiders (PMT)“ vs. 25th-Anniversary-Neuauflage), entscheidet der
   Expansionsname (difflib ≥ 0,5 und ≥ 0,15 Vorsprung), sonst keine Zuordnung. Sets < 5 Karten nur mit passendem
   Namen; jede Expansion höchstens einem Set.
2. Plausibilität: Produkt von Cardmarket mehr als 6 Monate *vor* Erscheinen des Sets angelegt → Set verworfen.
3. Druck: genau ein Druck dieses Namens im Set **und** genau ein Produkt dieses Namens in der Expansion. Mehrere
   Seltenheiten im Set bzw. mehrere Produkt-Versionen (Cardmarket „V.1/V.2“) → keine idProduct.

Die idProduct steht danach in `cards.cardmarket_id`; `CardmarketMapping` braucht für Yu-Gi-Oh kein Netz.
Stichprobe von 25 zufälligen Zuordnungen manuell geprüft (Set ↔ Expansionsname, Datum): alle plausibel.

**Abdeckung (17.10.2026):**

| | Drucke | mit idProduct | mit Preis (trend) |
|---|---:|---:|---:|
| alle Drucke | 44 591 | 27 385 (61,4 %) | 27 371 |
| per Set-Code unterscheidbar¹ | 31 601 | 27 379 (**86,6 %**) | 27 365 |
| davon Sets vor 2010 | 5 879 | 74,8 % | |
| 2010–2019 | 14 914 | 87,0 % | |
| ab 2020 | 10 805 | 92,6 % | |

¹ Nur diese Drucke kann die Identifikation überhaupt sicher bestimmen (eindeutiger regionsunabhängiger Code unter den
Drucken der Karte); die übrigen sind Seltenheits-Varianten im selben Set und bleiben ohnehin „Druck unsicher“.
Sets gesamt: 450 von 1 031 zugeordnet (ohne Zuordnung v. a. alte Sets mit mehreren Regions-/Neuauflage-Expansionen,
Duel-Terminal-Sets ohne Sealed-Produkt, Promos).

## Messwerte

Embedder `occ-dinov3s` (auf Magic/Pokémon trainiert, für Yu-Gi-Oh unverändert), Index nur Yu-Gi-Oh (14 790 Artworks).
OCR lief hier auf der CPU (ONNX-Runtime-CUDA fehlt `cublasLt64_13.dll`), daher ~350–400 ms je Identifikation.

**Echte Snapshots** (`data/ygo-test`, 1920 × 1080, Karten ~360 × 520 px, leicht unscharf):

| Karte | Name | Ähnlichkeit (nächste andere Karte) | Set-Code | Ergebnis |
|---|---|---|---|---|
| deutsche „Schwarzer-Glanz-Ritual“ (= Black Luster Ritual, nicht Black Magic Ritual!) | ✔ alle 4 Ecken-Varianten | 0,765–0,786 (0,38–0,40) | nicht lesbar | Name sicher, Druck unsicher (7 Drucke) |
| englische „Black Magic Ritual“ | ✔ beide | 0,815–0,820 (0,40) | nicht lesbar | Name sicher, Druck unsicher (5 Drucke) |

Der Set-Code ist bei dieser Auflösung/Schärfe auch für Menschen nicht lesbar (~9 px Zeilenhöhe, Bewegungsunschärfe).
Der Name wird mit sehr großem Abstand erkannt; die Track-Ecken des ersten Snapshots (Seitenverhältnis 0,731, oben
abgeschnitten) schaden kaum. Laut Overlay-Logik: Name (deutsch) ohne Preis.

**Synthetisch** (`tools/eval_ygo.py`, 200 zufällige Artworks, perspektivisch verzerrt, ungenaue Ecken, Unschärfe,
Rauschen, Glanz, dunkles Licht, JPEG; Seed 11):

| Szenario | Name top-1 | sicher + richtig | **sicher + falsch** | unsicher |
|---|---:|---:|---:|---:|
| ohne Set-Code (Referenzbilder sind Nachbauten ohne Code), Karte 400 px | 99,5 % | 28,0 % | **0** | 72,0 % |
| Set-Code aufgedruckt (EN/DE/G zufällig), 400 px | 99,5 % | 34,5 % | **0** | 65,5 % |
| Set-Code aufgedruckt, 600 px | 100 % | 57,5 % | **0** | 42,5 % |

Set-Code gelesen: 19 % (400 px) bzw. 62 % (600 px); bei 18,5 % der Stichprobe ist der Druck prinzipiell nicht
eindeutig (gleicher Code in mehreren Seltenheiten). Unsicher-Gründe bei 600 px: Code nicht lesbar 26, falsch gelesen 19,
mehrere Seltenheiten 24, kein TCG-Druck 12, Name knapp 4. Hinweis: Synthetische Abfragen stammen aus den
Referenzbildern selbst → Namenswerte optimistisch; die aufgedruckte Schrift (Hershey) ähnelt der Originalschrift nur.

## Lizenz- und Nutzungshinweise

- **YGOPRODeck API** (db.ygoprodeck.com/api-guide): kostenlos, höchstens 20 Anfragen/s (sonst 1 h Sperre) – wir
  stellen je Aktualisierung 2 Anfragen (+ je Sprache 1). Bilder **nicht hotlinken**, sondern einmal laden und lokal
  speichern; Download gedrosselt auf 10 Bilder/s. Daten/Bilder nicht weitergeben.
- **Kartenbilder/-texte © Konami / Studio Dice.** Bilder liegen nur lokal (`engine/data/refimg/ygo`), werden nicht
  verteilt (wie bei Magic/Pokémon).
- **Cardmarket**: öffentliche Produkt-/Preisdateien wie bei Magic/Pokémon; Anzeige mit Quellenangabe, Rechtslage für
  die Stream-Anzeige siehe ARCHITEKTUR.md (Risiko 8).

## Offene Punkte

1. **Set-Code-OCR an echten Karten** messen (Tischkamera/4K, Karte ≥ 600 px hoch); Streifenlage für Link-/Pendel-
   Karten und Speed-Duel-Karten (anderes Layout) mit echten Bildern prüfen. Bei ~520 px Kartenhöhe (Snapshots) unlesbar.
2. ONNX-Runtime-CUDA für die OCR einrichten (fehlende CUDA-13-DLLs) – OCR läuft sonst auf der CPU (~300 ms).
3. Alt-Arts: YGOPRODeck sagt nicht, welcher Druck welches Artwork trägt → Alt-Art liefert nur den Namen; der Druck
   kommt allein über den Set-Code (der Abgleich prüft das Artwork nicht).
4. Alte Sets (vor 2010) mit NA/EU-Druckvarianten (`LOB-001`/`LOB-E001`/`LOB-EN001`) haben denselben
   regionsunabhängigen Schlüssel → Druck unsicher. Ggf. Regionsbuchstaben (E/G/…) auswerten und mit Cardmarket-
   Expansionsvarianten („(PMT)“ …) abgleichen, sobald deren Bedeutung geklärt ist.
5. Seltenheit (Common/Rare/Super/Ultra/Secret) optisch: Namensfolie/Glanz ließe sich teils erkennen – bewusst nicht
   umgesetzt (Grundsatz: lieber kein Preis als ein falscher).
6. Erste/Unlimited Edition und Sprachversion beeinflussen den Cardmarket-Preis; der Price Guide mittelt darüber.
7. Embedder ist nicht auf Yu-Gi-Oh trainiert; Namen funktionieren trotzdem sehr gut (Abstand ~0,4). Gelernte
   Referenzen aus dem Dock wirken auch für Yu-Gi-Oh-Drucke.
8. OCG-/Asien-Englisch-Drucke: Wird die Regionskennung JP/KR/AE gelesen, bleibt der Druck unsicher (kein TCG-Preis).
   Ist sie nicht lesbar, aber die Nummer, könnte ein OCG-Druck auf den gleichnamigen TCG-Code fallen (selten).
