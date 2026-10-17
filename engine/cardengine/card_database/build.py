"""Katalog und Referenzbilder lokal aufbauen.

    python -m cardengine.card_database.build pokemon                 # alle Pokémon-Sets (englisch)
    python -m cardengine.card_database.build pokemon --sets sv03.5,30th
    python -m cardengine.card_database.build mtg --sets fdn,fdc,blb  # Magic: Metadaten komplett, Bilder nur für diese Sets
    python -m cardengine.card_database.build mtg --all-images        # alle Magic-Bilder (~100k, dauert lange)

Bilder werden nur lokal gespeichert (Rechte bei Wizards/TPCi; Scryfall-Richtlinien) und nicht verteilt.
"""
from __future__ import annotations

import argparse
import gzip
import io
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor

import cv2
import numpy as np
import requests

from ..paths import DATA_DIR, USER_AGENT
from .catalog import Catalog, CardRecord, image_path

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})
REF_HEIGHT = 340  # Referenzbilder einheitlich auf diese Höhe (Embedding-Eingabe ist 224 px)


def get_json(url: str, retries: int = 3):
    for i in range(retries):
        r = SESSION.get(url, timeout=60)
        if r.status_code == 429:
            time.sleep(2 + 2 * i)
            continue
        r.raise_for_status()
        return r.json()
    r.raise_for_status()


# ---------------- Pokémon (TCGdex) ----------------
def build_pokemon(cat: Catalog, sets: list[str] | None, lang: str = "en") -> list[CardRecord]:
    all_sets = get_json(f"https://api.tcgdex.net/v2/{lang}/sets")
    wanted = [s for s in all_sets if not sets or s["id"] in sets]
    records: list[CardRecord] = []
    for i, s in enumerate(wanted, 1):
        detail = get_json(f"https://api.tcgdex.net/v2/{lang}/sets/{s['id']}")
        total = (detail.get("cardCount") or {}).get("official")
        for c in detail.get("cards", []):
            if not c.get("image"):
                continue
            records.append(CardRecord(
                game="pokemon", id=c["id"], name=c["name"], set_code=s["id"], set_name=detail.get("name"),
                number=str(c["localId"]), set_total=total, lang=lang, released=detail.get("releaseDate"),
                cardmarket_id=None,  # kommt in Phase 2 aus den Kartendetails bzw. der Cardmarket-Produktliste
                illustration_id=None, finishes=None, image_url=f"{c['image']}/low.webp",
            ))
        print(f"\r[pokemon] Sets {i}/{len(wanted)}, Karten {len(records)}", end="", flush=True)
    print()
    cat.upsert(records)
    return records


# ---------------- Magic (Scryfall Bulk) ----------------
def build_mtg(cat: Catalog, sets: list[str] | None, all_images: bool) -> list[CardRecord]:
    meta = get_json("https://api.scryfall.com/bulk-data/default-cards")
    url = meta.get("jsonl_download_uri") or meta.get("download_uri")
    cache = os.path.join(DATA_DIR, "scryfall-default-cards.jsonl.gz")
    if not os.path.exists(cache) or time.time() - os.path.getmtime(cache) > 7 * 86400:
        print(f"[mtg] lade {url} …")
        with SESSION.get(url, stream=True, timeout=600) as r:
            r.raise_for_status()
            with open(cache + ".part", "wb") as f:
                for chunk in r.iter_content(1 << 20):
                    f.write(chunk)
        os.replace(cache + ".part", cache)
    records: list[CardRecord] = []
    with gzip.open(cache, "rt", encoding="utf-8") as f:
        for line in f:
            line = line.strip().rstrip(",")
            if not line or line in "[]":
                continue
            c = json.loads(line)
            if c.get("digital") or "paper" not in c.get("games", ["paper"]) or c.get("layout") in ("art_series", "token", "double_faced_token", "emblem"):
                continue
            img = (c.get("image_uris") or (c.get("card_faces") or [{}])[0].get("image_uris") or {}).get("normal")
            records.append(CardRecord(
                game="mtg", id=c["id"], name=c["name"], set_code=c["set"].upper(), set_name=c.get("set_name"),
                number=c["collector_number"], set_total=None, lang=c.get("lang"), released=c.get("released_at"),
                cardmarket_id=c.get("cardmarket_id"),
                illustration_id=c.get("illustration_id") or (c.get("card_faces") or [{}])[0].get("illustration_id"),
                finishes=",".join(c.get("finishes", [])), image_url=img,
            ))
    cat.upsert(records)
    print(f"[mtg] {len(records)} Drucke im Katalog")
    if not all_images:
        wanted = {s.upper() for s in (sets or [])}  # leere Liste → nur Metadaten
        records = [r for r in records if r.set_code in wanted]
    return records


# ---------------- Bilder ----------------
def _download(rec: CardRecord) -> bool:
    path = image_path(rec.game, rec.id)
    if os.path.exists(path) or not rec.image_url:
        return False
    for attempt in range(3):
        try:
            r = SESSION.get(rec.image_url, timeout=60, headers={"Accept": "image/*"})
            if r.status_code == 429:
                time.sleep(2 + attempt * 3)
                continue
            r.raise_for_status()
            img = cv2.imdecode(np.frombuffer(r.content, np.uint8), cv2.IMREAD_COLOR)
            if img is None:
                return False
            h, w = img.shape[:2]
            img = cv2.resize(img, (round(w * REF_HEIGHT / h), REF_HEIGHT), interpolation=cv2.INTER_AREA)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            cv2.imwrite(path, img, [cv2.IMWRITE_JPEG_QUALITY, 92])
            return True
        except requests.RequestException:
            time.sleep(1 + attempt)
    return False


def download_images(records: list[CardRecord], workers: int, progress=None) -> None:
    """Fehlende Referenzbilder laden. `progress(done, total)` wird regelmäßig aufgerufen (sonst Konsolenausgabe)."""
    todo = [r for r in records if r.image_url and not os.path.exists(r.image_path)]
    print(f"[bilder] {len(todo)} zu laden ({len(records) - len(todo)} vorhanden)", flush=True)
    done = 0
    with ThreadPoolExecutor(workers) as pool:
        for ok in pool.map(_download, todo):
            done += 1
            if done % 100 == 0 or done == len(todo):
                if progress:
                    progress(done, len(todo))
                else:
                    print(f"\r[bilder] {done}/{len(todo)}", end="", flush=True)
    if not progress:
        print()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("game", choices=["pokemon", "mtg"])
    ap.add_argument("--sets", help="kommagetrennte Set-IDs (TCGdex) bzw. Set-Codes (Scryfall)")
    ap.add_argument("--lang", default="en")
    ap.add_argument("--all-images", action="store_true", help="Magic: Bilder für alle Drucke laden")
    ap.add_argument("--no-images", action="store_true")
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()
    sets = [s.strip() for s in args.sets.split(",")] if args.sets else None
    cat = Catalog()
    if args.game == "pokemon":
        recs = build_pokemon(cat, sets, args.lang)
    else:
        # Scryfall bittet um maßvolle Last auf dem Bild-CDN
        recs = build_mtg(cat, sets, args.all_images)
        args.workers = min(args.workers, 6)
    if not args.no_images:
        download_images(recs, args.workers)
    print(f"Katalog: {cat.count('pokemon')} Pokémon, {cat.count('mtg')} Magic")


if __name__ == "__main__":
    main()
