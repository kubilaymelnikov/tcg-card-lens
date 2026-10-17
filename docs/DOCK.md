# Bestätigungs-Dock und Lernen aus Bestätigungen

## Überblick

```
OBS ──(Filter, TCP 47820)──► Engine ◄──(HTTP 47830, JSON)── Dock (OBS-Browser-Dock, dock.html)
                                │
                                └── data/learned/  (gelernte Referenzen + Trainingsdatensatz)
```

- Die Engine startet neben dem TCP-Server für das Plugin einen kleinen HTTP-Server (`--dock-port`, Standard
  `47830`, nur 127.0.0.1). Er liefert die Dock-Seite (`engine/dock/dock.html`) und eine JSON-API.
- In OBS: *Docks → Benutzerdefinierte Browser-Docks…* → Name „Karten“, URL `http://127.0.0.1:47830/`.
- Jede Karte bekommt im Video ein kurzes **Kennzeichen** (A, B, C …, je Quelle stabil, frei gewordene Buchstaben
  werden wiederverwendet). Das Kennzeichen steht als Badge an der linken oberen Ecke des Rahmens und im Dock.
- Wann die Kennzeichen im Video stehen, regelt die Einstellung `show_labels`: `dock` (Standard – nur solange das
  Dock offen ist, also in den letzten 5 s abgefragt hat), `always` oder `never`.
- Fährt man im Dock über eine Karte, wird sie im Video **hervorgehoben** (pulsierender, dickerer Rahmen).

## HTTP-API (alles JSON, UTF-8)

### `GET /api/state`
Polling alle ~500 ms. Antwort:

```json
{
  "engine": "0.2.0",
  "status": "bereit",
  "learned": {"references": 12, "negatives": 3, "dataset": 15},
  "dock_rev": "1792233686",             // ändert sich mit dock.html → Dock lädt sich selbst neu
  "sessions": [
    {
      "id": "s1",                       // Sitzungs-ID (eine je OBS-Filterinstanz)
      "source": "Kamera Tisch",
      "highlight": "B",                 // aktuell hervorgehobenes Kennzeichen oder null
      "cards": [
        {
          "label": "B",                 // Kennzeichen im Video
          "track": 17,                  // interne Track-ID (für POSTs)
          "state": "identified",        // new | identifying | identified | uncertain | failed | rejected
          "needs_confirmation": true,   // unsicher erkannt → Frage „Ist das die Karte?“ zeigen
          "confirmed": false,           // vom Nutzer bestätigt
          "game": "pokemon",
          "key": "pokemon|sv3pt5-25",   // null, wenn der Druck nicht feststeht
          "name": "Pikachu",            // Anzeigename (Overlay-Sprache)
          "set_name": "151", "set_code": "MEW", "number": "025/165",
          "similarity": 0.81,
          "method": "embedding",        // embedding | embedding+ocr | gelernt | bestätigt
          "reason": "geringe Ähnlichkeit",
          "image": "/api/card-image/pokemon/sv3pt5-25",   // Referenzbild aus den lokalen Daten
          "crop": "/api/crop/s1/17?v=3",                  // aktueller Kamera-Ausschnitt (entzerrt, JPEG)
          "cardmarket_url": "https://www.cardmarket.com/de/Pokemon/Products?idProduct=123456",
          "price": {"value": 0.42, "currency": "EUR", "variant": "normal", "change_30d": -3.1,
                    "updated": "2026-10-17T02:44:00Z"},   // null, wenn kein (sicherer) Preis
          "sticky": true,               // Engine hält die Frage offen (bis Antwort, 20 s nicht im Bild
                                        // oder 10 s sicher erkannt)
          "recheck": false,             // Karte wird gerade neu geprüft – letzter Inhalt wird gezeigt
          "gone": false,                // Karte gerade nicht im Bild (Frage bleibt noch stehen)
          "candidates": [               // Alternativen, beste zuerst (max. 8, ohne die gewählte Karte)
            {"key": "pokemon|sv3pt5-173", "name": "Pikachu", "set_name": "151", "number": "173/165",
             "similarity": 0.79, "image": "/api/card-image/pokemon/sv3pt5-173"}
          ]
        }
      ]
    }
  ]
}
```

### `POST` (Body JSON, Antwort `{"ok": true}` oder `{"ok": false, "error": "…"}`)

| Pfad | Body | Wirkung |
|---|---|---|
| `/api/confirm` | `{"session","track","key"}` | Karte bestätigen (`key` = vorgeschlagene oder Alternative). Track wird fixiert, Preis sicher angezeigt, Referenz gelernt. |
| `/api/reject` | `{"session","track"}` | „Keine Karte“: Track verwerfen, Stelle als Hintergrund merken, Negativbeispiel lernen. |
| `/api/dismiss` | `{"session","track"}` | Karte ohne Angabe von Gründen ausblenden (✕): weg aus Dock und Video, nichts gelernt. Liegt dort später eine andere Karte, wird sie normal erkannt. |
| `/api/refresh` | `{"session","track"}` | Karte neu erkennen und Preis neu laden. |
| `/api/highlight` | `{"session","label"}` | Karte im Video hervorheben; `label: null` hebt auf. Läuft nach 3 s ohne Wiederholung automatisch ab. |
| `/api/forget` | `{"key"}` | Gelernte Referenzen einer Karte löschen. |

### `GET /api/search?q=pika&game=pokemon&limit=20`
Kartensuche für den Fall, dass die richtige Karte nicht unter den Alternativen ist:
`{"results": [{"key","name","set_name","set_code","number","image"}]}`.

### Bilder
- `GET /api/card-image/<game>/<id>` → Referenzbild (JPEG/PNG aus `data/images`, 404 wenn nicht vorhanden)
- `GET /api/crop/<session>/<track>` → entzerrter Kamera-Ausschnitt (JPEG, 244×340)

## Ergebnis-Protokoll Engine → Plugin (Erweiterung)

Jeder Track in `result.tracks[]` bekommt zusätzlich:

```json
{"label": "B", "highlight": true}
```

und ein weiteres Panel mit `"anchor": "corner"`: ein kleines RGBA-Badge mit dem Kennzeichen, das das Plugin mit
seinem Mittelpunkt auf die erste Ecke (`corners[0]`, oben links) setzt. Ist `highlight` gesetzt, zeichnet das Plugin
den Rahmen pulsierend (Dicke × 1,5…2,5, ~2 Hz) mit hellem Außenschein.

## Lernen aus Bestätigungen

**Stufe 1 – sofort, zur Laufzeit (lokal, ohne Training):**
- Bestätigung/Korrektur speichert das Embedding des Kamera-Ausschnitts als *gelernte Referenz*
  (`data/learned/references.npz`, Schlüssel = Kartenschlüssel). Die Identifikation durchsucht diese Referenzen
  zusätzlich zum Katalog-Index. Ein Treffer ≥ 0,90 Ähnlichkeit mit klarem Abstand zur nächsten anderen gelernten
  Karte gilt als sicher (`method = "gelernt"`).
- „Keine Karte“ speichert ein *Negativbeispiel*. Ein Ausschnitt mit ≥ 0,93 Ähnlichkeit zu einem Negativbeispiel
  wird nicht mehr als Karte gemeldet.
- Höchstens 20 Referenzen je Karte (älteste fallen raus), Negativbeispiele max. 500.

**Stufe 2 – gesammelt, später (Nachtraining):**
- Jede Bestätigung legt zusätzlich ein Trainingsbeispiel ab: `data/learned/dataset/<datum>/<uuid>.jpg` (entzerrt,
  488×680) + Zeile in `data/learned/dataset/labels.jsonl` (`key`, `label`: `card`/`no_card`, vorgeschlagene Karte,
  Ähnlichkeit, Quelle, Zeit). Grenze über die Snapshot-Größenbegrenzung.
- `tools/export_training_set.py` packt den Datensatz für das Nachtraining (Embedder-Feintuning mit echten
  Kamerabildern, Kalibrierung der Schwellwerte). Neue Modelle werden gegen eine feste Testmenge geprüft und nur
  ausgeliefert, wenn sie nicht schlechter sind.
