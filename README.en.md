# TCG Card Lens

[Deutsch](README.md) · **English**

**OBS plugin that recognizes trading cards in your camera image and overlays their Cardmarket price** – for
**Pokémon**, **Magic: The Gathering** and **Yu-Gi-Oh!**. Runs locally on your graphics card (NVIDIA via CUDA, AMD/Intel via DirectML), no cloud service.

- Several cards at once, also tilted, rotated or in a toploader
- A frame around each card, the name above it (optionally in German or another card language), price, trend and
  price history below
- **No wrong prices:** if the exact print is not certain, the overlay says “Print uncertain – no price”
- **Card dock in OBS:** confirm or correct uncertain cards, highlight a card in the video, Cardmarket link
- **Learns from confirmations:** once confirmed, the same card is recognized reliably under your lighting
- **Pack opening mode:** valuable cards (from an adjustable threshold, default €10) trigger a hit effect – gold
  frame, banner “HIT! €168.73”, optionally a scene switch and a sound; plus a session list with total value and
  CSV export

> Status: version 0.2 – usable day to day, but young. Feedback and bug reports are welcome.

## Language

The whole interface follows the **OBS language**: German if OBS is set to German, English otherwise. This covers
the filter properties, the overlay texts in the video (“Print uncertain – no price”, “Session value”, “Recent hits”),
status messages, the card dock and the session display. Prices and numbers use the matching format
(German: `0,15 €`, English: `€0.15`). The dock and the session display can be forced to a language with
`?lang=de` or `?lang=en` in their URL. The language of the *card names* is a separate filter setting
(*Overlay display language*).

## Installation (Windows)

Requirements: Windows 10/11, OBS Studio 32 or newer (tested with 32.2), NVIDIA graphics card RTX 20xx (Turing) or
newer with a current driver – AMD and Intel graphics cards run via DirectML (not yet tested), without a graphics card
it runs more slowly on the CPU –, about 3 GB of free disk space.

1. Download `TCGCardLens-Setup-<version>.exe` from the
   [latest release](https://github.com/kubilaymelnikov/tcg-card-lens/releases/latest) and run it (no administrator
   rights needed). Windows SmartScreen warns about unsigned programs: “More info → Run anyway”.
   The installer itself is in German.
2. On the **games and languages** page (*Spiele und Sprachen*), choose which games to recognize and in which
   languages card names should be available.
3. The installer sets up Python, the AI models and the card data (a few minutes, downloads about 1 GB with NVIDIA, otherwise about 0.2 GB).
   Recognition data comes as ready-made packs from the release; card images are not distributed.
4. In OBS: right-click your camera source → **Filters** → **+** → **TCG Card Lens**.
   The engine starts automatically in the background.
5. Card dock: **Docks → Custom Browser Docks…** → name `Cards`, URL `http://127.0.0.1:47830/`.

Offline installation: put the `packs` folder (release files `tcglens-*.zip` and `packs.json`) next to the setup
file – the installer then installs the packs from there.

## Usage

- **Filter properties:** games, card languages, display language, show/hide name/price/history, size, frame colors,
  table or facecam mode, hold time, smoothing, debug and snapshots.
- **Dock:** every card gets a label (A, B, C …) that is also shown in the video while the dock is open. Hovering a
  card in the dock highlights it in the video.
  - *Is this the card?* – **Yes**, pick an **alternative** (preview on hover), **search**
    (e.g. “Charizard 4/102”, “Sol Ring 409”) or **No card**.
  - **↻ Rotate** if a card was recognized sideways, **⟳** recognize again, **✕** hide.
  - **Camera image:** click a missed card to search there, or drag a box around it.
  - Switches in the header: **Auto-detect** (off: cards only via click/box), **Pack opening**, **Hit from** threshold.
- Confirmations are learned locally as references and stored as training examples
  (`engine/data/learned`, stays on your computer).
- **Pack opening:** enable it in the filter under *Pack opening* (or in the dock). Every **reliably** recognized card
  with a price counts towards the session (the same card only once); from the hit threshold on, the hit effect is
  shown. The video shows “Session value” and “Recent hits”; the dock has a **Session** section with list, total,
  **New session** and **Export CSV** (`engine/data/sessions`; English CSV uses commas and a decimal point). The
  button *Add session display to current scene* adds the session display as a freely placeable browser source.
  Optionally OBS switches scenes on a hit (and back after a few seconds) or plays a media source as a sound.
  Uncertain prints never count – no price, no hit.

## How it works

```
OBS filter (C++) ──frame via shared memory──► Engine (Python, GPU)
      ▲                                          │ YOLO11-OBB: find cards (rotated, perspective)
      │                                          │ tracking + position lock (calm overlay)
      └────── overlay panels (RGBA) ◄────────────┤ DINOv3 embedding → search in the card index
                                                 │ OCR of the footer / set code → exact print
            Dock (browser, HTTP 47830) ◄─────────┤ Cardmarket price guide + price history
                                                 └ learning store from confirmations
```

The technical documentation is in German: [docs/ARCHITEKTUR.md](docs/ARCHITEKTUR.md) (architecture),
[docs/PLUGIN.md](docs/PLUGIN.md) (plugin and protocol), [docs/DOCK.md](docs/DOCK.md) (dock and learning),
[docs/DATENPAKETE.md](docs/DATENPAKETE.md) (data packs), [docs/YUGIOH.md](docs/YUGIOH.md).

## Development

```powershell
cd engine
.\.venv\Scripts\python.exe -m pytest tests -q
.\.venv\Scripts\python.exe server.py --port 47820 --dock-port 47830
# dock without engine/OBS (fake data), e.g. in English:
.\.venv\Scripts\python.exe tools\dock_mock.py --lang en
```

## Limitations

- Only tested on Windows with an NVIDIA GPU.
- Prints with identical artwork are told apart only by the footer or set code; if it is too small or reflects,
  there is deliberately no price. A camera closer to the table helps (card ≥ 600 px tall).
- Rarities within the same set (Yu-Gi-Oh!) can hardly be distinguished optically.
- Prices are guide values from the public Cardmarket price guide (updated once a day), without guarantee.

## License

[AGPL-3.0](LICENSE). Models, code and data sources used, with their licenses: [NOTICE.md](NOTICE.md).
Not affiliated with Cardmarket, Wizards of the Coast, The Pokémon Company, Konami, Scryfall, TCGdex, YGOPRODeck
or MTGJSON.
