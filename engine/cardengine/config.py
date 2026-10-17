"""Engine-Voreinstellungen aus der Installation: `data/config.json`.

    {"games": ["pokemon", "mtg"], "languages": ["de", "en"], "pack_url": ""}

- `games`: Spiele, für die Katalog und Erkennungs-Index gepflegt und geladen werden (Auswahl im Installer)
- `languages`: Namenssprachen, deren Namenslisten die Engine vorab lädt
- `pack_url`: Basis-URL der Datenpakete (leer = Standard aus `data_packs.DEFAULT_PACK_URL`;
  die Umgebungsvariable `TCGLENS_PACK_URL` hat Vorrang)

Setzen aus Skripten (setup-engine.ps1), ohne JSON von Hand zu schreiben:

    python -m cardengine.config --games pokemon,mtg --languages de,en [--pack-url URL]
    python -m cardengine.config            # aktuelle Einstellungen anzeigen
"""
from __future__ import annotations

import argparse
import json
import os

from . import paths

# Bekannte Spiele: Kürzel -> Anzeigename. Neue Spiele hier ergänzen (Reihenfolge = Anzeige).
GAMES: dict[str, str] = {
    "pokemon": "Pokémon",
    "mtg": "Magic: The Gathering",
    "ygo": "Yu-Gi-Oh!",
}
DEFAULTS = {"games": ["pokemon", "mtg"], "languages": ["de", "en"], "pack_url": ""}


def config_path() -> str:
    return os.path.join(paths.DATA_DIR, "config.json")


def _as_list(value) -> list[str]:
    if isinstance(value, str):
        value = value.split(",")
    out = []
    for v in value or []:
        v = str(v).strip().lower()
        if v and v not in out:
            out.append(v)
    return out


def load_config(path: str | None = None) -> dict:
    """Voreinstellungen lesen; fehlende oder kaputte Datei -> Standardwerte."""
    cfg = {k: (list(v) if isinstance(v, list) else v) for k, v in DEFAULTS.items()}
    path = path or config_path()
    try:
        with open(path, encoding="utf-8-sig") as f:  # -sig: verträgt eine BOM (PowerShell)
            data = json.load(f)
        if isinstance(data, dict):
            cfg.update(data)
    except FileNotFoundError:
        pass
    except (OSError, ValueError) as err:
        print(f"[config] {path} nicht lesbar ({err}) – Standardwerte", flush=True)
    cfg["games"] = _as_list(cfg.get("games")) or list(DEFAULTS["games"])
    cfg["languages"] = _as_list(cfg.get("languages")) or list(DEFAULTS["languages"])
    cfg["pack_url"] = str(cfg.get("pack_url") or "")
    return cfg


def save_config(cfg: dict, path: str | None = None) -> None:
    path = path or config_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def main() -> None:
    ap = argparse.ArgumentParser(description="Engine-Voreinstellungen (data/config.json) setzen oder anzeigen")
    ap.add_argument("--games", help=f"kommagetrennt, bekannt: {','.join(GAMES)}")
    ap.add_argument("--languages", help="kommagetrennt, z. B. de,en")
    ap.add_argument("--pack-url", help="Basis-URL der Datenpakete (leer = Standard)")
    args = ap.parse_args()
    cfg = load_config()
    if args.games is not None:
        games = _as_list(args.games)
        unknown = [g for g in games if g not in GAMES]
        if unknown:
            print(f"[config] Hinweis: unbekannte Spiele {unknown} – werden gespeichert, aber ggf. nicht gepflegt")
        cfg["games"] = games or list(DEFAULTS["games"])
    if args.languages is not None:
        cfg["languages"] = _as_list(args.languages) or list(DEFAULTS["languages"])
    if args.pack_url is not None:
        cfg["pack_url"] = args.pack_url.strip()
    if args.games is not None or args.languages is not None or args.pack_url is not None:
        save_config(cfg)
        print(f"[config] gespeichert: {config_path()}")
    print(json.dumps(cfg, ensure_ascii=False))


if __name__ == "__main__":
    main()
