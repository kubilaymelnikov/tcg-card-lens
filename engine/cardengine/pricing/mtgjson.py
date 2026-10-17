"""Magic-Preisverlauf der letzten 90 Tage aus MTGJSON (Cardmarket-Verkaufspreise) nachladen.

    python -m cardengine.pricing.mtgjson

1. AllIdentifiers.json.gz (~230 MB): MTGJSON-UUID → Cardmarket-Produkt-ID (`identifiers.mcmId`)
2. AllPrices.json.gz (~150 MB): je UUID `paper.cardmarket.retail.{normal,foil}` = {Datum: Preis}
Beides wird gestreamt (ijson), nur die Zuordnung liegt im Speicher. Ergebnis landet in der lokalen Historie
(Quelle "mtgjson"); mehrfaches Ausführen verlängert den Verlauf über 90 Tage hinaus.
"""
from __future__ import annotations

import gzip
import os
import time

import ijson
import requests

from ..paths import DATA_DIR, USER_AGENT
from .store import PriceStore

BASE = "https://mtgjson.com/api/v5/"
DIR = os.path.join(DATA_DIR, "mtgjson")


def _fetch(name: str, max_age_s: float = 20 * 3600) -> str:
    os.makedirs(DIR, exist_ok=True)
    path = os.path.join(DIR, name)
    if os.path.exists(path) and time.time() - os.path.getmtime(path) < max_age_s:
        return path
    print(f"[mtgjson] lade {name} …", flush=True)
    with requests.get(BASE + name, headers={"User-Agent": USER_AGENT}, stream=True, timeout=600) as r:
        r.raise_for_status()
        with open(path + ".part", "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
    os.replace(path + ".part", path)
    return path


def uuid_to_mcm() -> dict[str, int]:
    out = {}
    with gzip.open(_fetch("AllIdentifiers.json.gz", 7 * 86400), "rb") as f:
        for uuid, card in ijson.kvitems(f, "data", use_float=True):
            mcm = (card.get("identifiers") or {}).get("mcmId")
            if mcm and str(mcm).isdigit():
                out[uuid] = int(mcm)
    print(f"[mtgjson] {len(out)} Karten mit Cardmarket-ID", flush=True)
    return out


def import_history(store: PriceStore | None = None) -> int:
    store = store or PriceStore()
    mapping = uuid_to_mcm()
    rows, total, t0 = [], 0, time.time()
    with gzip.open(_fetch("AllPrices.json.gz"), "rb") as f:
        for uuid, prices in ijson.kvitems(f, "data", use_float=True):
            pid = mapping.get(uuid)
            retail = ((prices.get("paper") or {}).get("cardmarket") or {}).get("retail") or {}
            if not pid or not retail:
                continue
            for variant in ("normal", "foil"):
                last = None
                for day, price in sorted((retail.get(variant) or {}).items()):
                    if price and price > 0:
                        cents = round(price * 100)
                        if cents != last:  # nur Änderungen speichern (Lücken = unverändert)
                            rows.append(("mtg", pid, variant, day, cents, "mtgjson"))
                            last = cents
            if len(rows) > 200_000:
                store.add_many(rows)
                total += len(rows)
                rows = []
                print(f"\r[mtgjson] {total} Preispunkte ({time.time() - t0:.0f} s)", end="", flush=True)
    store.add_many(rows)
    total += len(rows)
    store.set_meta("mtgjson_imported_at", time.strftime("%Y-%m-%dT%H:%M:%S"))
    print(f"\n[mtgjson] fertig: {total} Preispunkte in {time.time() - t0:.0f} s")
    return total


if __name__ == "__main__":
    import_history()
