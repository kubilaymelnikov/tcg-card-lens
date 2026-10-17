"""Datenpakete bauen: je Spiel Katalog + Erkennungs-Index als ZIP, dazu packs.json (Aufbau: docs/DATENPAKETE.md).

Liest nur (Katalog und Index, auch die alte gemeinsame Indexdatei – sie wird dabei NICHT aufgeteilt).
Referenzbilder werden nicht eingepackt.

    python tools/build_data_pack.py                          # alle Spiele mit Index -> data/packs
    python tools/build_data_pack.py --games pokemon,mtg --out D:/release
    python tools/build_data_pack.py --model occ-dinov3s --no-normalize   # Index eines anderen Modells
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import time
from types import SimpleNamespace
from urllib.request import pathname2url

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from cardengine.card_database.catalog import DB_PATH  # noqa: E402
from cardengine.data_packs import build_pack, write_packs_index  # noqa: E402
from cardengine.paths import DATA_DIR  # noqa: E402
from cardengine.recognition.embedder import DEFAULT_MODEL, Embedder  # noqa: E402
from cardengine.recognition.index import INDEX_DIR, available_games, index_path  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--games", help="kommagetrennt (Standard: alle Spiele mit Index)")
    ap.add_argument("--out", default=os.path.join(DATA_DIR, "packs"), help="Zielordner (Standard: data/packs)")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--no-normalize", action="store_true", help="Index ohne photometrische Normierung")
    ap.add_argument("--version", help="Paketversion (Standard: JJJJMMTT)")
    ap.add_argument("--catalog", default=DB_PATH, help="Katalog-Datenbank (Standard: data/catalog.sqlite)")
    ap.add_argument("--index-dir", default=INDEX_DIR)
    args = ap.parse_args()

    name = Embedder.default_index_name(args.model, normalize=not args.no_normalize)
    if args.games:
        games = [g.strip() for g in args.games.split(",") if g.strip()]
    else:
        games = available_games(name, args.index_dir)
        if os.path.exists(index_path(name, None, args.index_dir)):  # alte gemeinsame Datei: Spiele aus den Schlüsseln
            with np.load(index_path(name, None, args.index_dir), allow_pickle=False) as d:
                games += [g for g in dict.fromkeys(str(k).split("|", 1)[0] for k in d["keys"]) if g not in games]
    if not games:
        print(f"Kein Index für Modell {name} gefunden.")
        return 1
    # Nur lesend öffnen (Live-Daten der laufenden Engine bleiben unberührt)
    cat = SimpleNamespace(db=sqlite3.connect(f"file:{pathname2url(os.path.abspath(args.catalog))}?mode=ro", uri=True))
    entries = []
    for game in games:
        t0 = time.time()
        e = build_pack(game, name, args.out, catalog=cat, index_dir=args.index_dir, version=args.version)
        entries.append(e)
        print(f"{e['file']}: {e['cards']} Karten, {e['vectors']} Vektoren, {e['size'] / 1e6:.1f} MB "
              f"({time.time() - t0:.0f} s)")
    print(f"Index: {write_packs_index(args.out, entries)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
