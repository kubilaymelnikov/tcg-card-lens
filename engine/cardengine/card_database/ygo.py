"""Yu-Gi-Oh-Katalog (YGOPRODeck API v7), Referenzbilder und Cardmarket-Zuordnung.

    python -m cardengine.card_database.ygo                 # Katalog + Cardmarket-Zuordnung + fehlende Bilder
    python -m cardengine.card_database.ygo --no-images

Quellen und Bedingungen:
- Kartendaten: https://db.ygoprodeck.com/api/v7/cardinfo.php (höchstens 20 Anfragen/s, sonst 1 h Sperre; wir
  brauchen genau eine Anfrage für alle Karten). Bilder von images.ygoprodeck.com dürfen nicht „gehotlinkt“ werden →
  einmal laden, lokal speichern (Höhe 680 px), nie verteilen. Kartenbilder © Konami/Studio Dice.
- Ein Katalogeintrag je **Druck** (Set-Code + Seltenheit): id = "<passcode>-<set-code>-<seltenheit>", z. B.
  "76792184-LCYW-EN078-C". Viele Drucke teilen ein Artwork → eine Bilddatei je Artwork (`catalog.image_path`).
  Zusätzliche Artworks (Alt-Arts, deren Druck YGOPRODeck nicht zuordnet) und Karten ganz ohne TCG-Druck (nur OCG,
  Duel Links …) bekommen reine Artwork-Einträge "<passcode>-art<bild-id>" ohne Set-Code: Sie liefern den Namen,
  aber nie einen sicheren Druck (also keinen Preis).
- Cardmarket: `products_singles_3.json` hat je Produkt nur Name + idExpansion (ohne Set-Code/Seltenheit). Die
  Zuordnung Expansion ↔ YGOPRODeck-Set erfolgt über die Übereinstimmung der Kartennamen (und den Namen der
  Expansion aus `products_nonsingles_3.json` bei Gleichstand); ein Druck bekommt nur dann eine idProduct, wenn das
  Set genau einen Druck dieses Namens enthält und die Expansion genau ein Produkt dieses Namens (sonst wären
  Seltenheiten/Versionen nicht sicher zu trennen → kein Preis).
"""
from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import threading
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor

import cv2
import numpy as np
import requests

from ..paths import DATA_DIR, USER_AGENT
from .catalog import Catalog, CardRecord

API = "https://db.ygoprodeck.com/api/v7/"
IMG_URL = "https://images.ygoprodeck.com/images/cards/{art}.jpg"
CM_URL = "https://downloads.s3.cardmarket.com/productCatalog/productList/products_{kind}_3.json"
DIR = os.path.join(DATA_DIR, "ygo")
REF_HEIGHT = 680  # Zielgröße wie die entzerrte Karte (488 × 680); Yu-Gi-Oh-Format 59 × 86 mm → 467 × 680
MAX_AGE = 7 * 86400
MAX_RPS = 10.0  # Bild-Downloads gesamt (YGOPRODeck sperrt ab 20/s)

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})

def _get(url: str, timeout: int = 120):
    for attempt in range(4):
        r = SESSION.get(url, timeout=timeout)
        if r.status_code == 429:
            time.sleep(5 + attempt * 5)
            continue
        r.raise_for_status()
        return r.json()
    r.raise_for_status()


def _cached_json(name: str, url: str, max_age: float = MAX_AGE):
    os.makedirs(DIR, exist_ok=True)
    path = os.path.join(DIR, name)
    if not os.path.exists(path) or time.time() - os.path.getmtime(path) > max_age:
        print(f"[ygo] lade {url} …", flush=True)
        data = _get(url)
        with open(path + ".part", "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(path + ".part", path)
        time.sleep(0.2)  # Rate-Limit der API großzügig einhalten
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _rarity_code(s: dict) -> str:
    code = (s.get("set_rarity_code") or "").strip("() ")
    if code:
        return code
    words = re.findall(r"[A-Za-z0-9]+", s.get("set_rarity") or "")
    return "".join(w[0] for w in words).upper() or "X"


# ---------------------------------------------------------------- Katalog
def records_from_api(cards: list[dict], set_dates: dict[str, str] | None = None) -> list[CardRecord]:
    """YGOPRODeck-`cardinfo`-Daten → Katalogeinträge (Drucke + reine Artwork-Einträge)."""
    set_dates = set_dates or {}
    out: list[CardRecord] = []
    for c in cards:
        passcode = str(c["id"])
        arts = [str(i["id"]) for i in c.get("card_images") or [] if i.get("id")]
        main = passcode if passcode in arts or not arts else arts[0]
        seen: dict[str, str] = {}
        for s in c.get("card_sets") or []:
            code = (s.get("set_code") or "").strip().upper()
            if "-" not in code:
                continue  # ohne Bindestrich (wenige Sonderfälle) lässt sich der Code nicht lesen/zuordnen
            rid = f"{passcode}-{code}-{_rarity_code(s)}"
            if rid in seen:
                if seen[rid] == s.get("set_name"):
                    continue  # doppelte Zeile in der API
                n = 2
                while f"{rid}-{n}" in seen:
                    n += 1
                rid = f"{rid}-{n}"
            seen[rid] = s.get("set_name")
            prefix, number = code.split("-", 1)
            out.append(CardRecord(
                game="ygo", id=rid, name=c["name"], set_code=prefix, set_name=s.get("set_name"), number=number,
                set_total=None, lang="en", released=set_dates.get(s.get("set_name")), cardmarket_id=None,
                illustration_id=main, finishes=s.get("set_rarity"), image_url=IMG_URL.format(art=main),
            ))
        for art in arts:
            if art == main and seen:
                continue  # Hauptartwork ist über die Drucke abgedeckt
            out.append(CardRecord(
                game="ygo", id=f"{passcode}-art{art}", name=c["name"], set_code="", set_name=None, number="",
                set_total=None, lang="en", released=None, cardmarket_id=None, illustration_id=art, finishes=None,
                image_url=IMG_URL.format(art=art),
            ))
    return out


def build_ygo(cat: Catalog, with_prices: bool = True) -> list[CardRecord]:
    cards = _cached_json("cardinfo.json", API + "cardinfo.php")["data"]
    try:
        sets = _cached_json("cardsets.json", API + "cardsets.php")
        dates = {s["set_name"]: s.get("tcg_date") for s in sets}
    except (requests.RequestException, ValueError, KeyError) as err:
        print(f"[ygo] Set-Liste nicht verfügbar ({err})")
        dates = {}
    records = records_from_api(cards, dates)
    if with_prices:
        try:
            singles = _cached_json("products_singles_3.json", CM_URL.format(kind="singles"), max_age=86400)["products"]
            sealed = _cached_json("products_nonsingles_3.json", CM_URL.format(kind="nonsingles"), max_age=86400)["products"]
            mapping, stats = cardmarket_products(records, singles, sealed)
            for r in records:
                r.cardmarket_id = mapping.get(r.id)
            print(f"[ygo] Cardmarket: {stats['mapped']}/{stats['prints']} Drucke mit eindeutiger idProduct "
                  f"({stats['mapped'] / max(1, stats['prints']):.1%}), Sets {stats['sets_mapped']}/{stats['sets']}")
        except (requests.RequestException, ValueError, KeyError) as err:
            print(f"[ygo] Cardmarket-Produktliste nicht verfügbar ({err}) – keine Preise")
    # Veraltete Einträge (z. B. umbenannte Drucke) entfernen, dann neu schreiben
    cat.db.execute("DELETE FROM cards WHERE game = 'ygo'")
    cat.upsert(records)
    prints = sum(1 for r in records if r.set_code)
    print(f"[ygo] {prints} Drucke, {len(records) - prints} reine Artwork-Einträge, "
          f"{len({r.illustration_id for r in records})} Artworks", flush=True)
    return records


# ---------------------------------------------------------------- Cardmarket
def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.replace('""', '"').lower())


_SEALED_SUFFIX = re.compile(
    r"\s*(:\s*)?(Special Edition|Booster Box|Booster|Display|Structure Deck|Starter Deck|Deck|Tin|Box|"
    r"Collector'?s? Tins?|Bundle|Pack|Case)(\s+Display)?\s*$", re.I)


def _expansion_names(sealed: list[dict]) -> dict[int, list[str]]:
    out: dict[int, set[str]] = defaultdict(set)
    for p in sealed:
        name = p.get("name") or ""
        for _ in range(2):
            name = _SEALED_SUFFIX.sub("", name)
        if name:
            out[p["idExpansion"]].add(name.lower())
    return {k: sorted(v) for k, v in out.items()}


def _months(day_a: str, day_b: str) -> int:
    """Monate von `day_a` bis `day_b` (positiv, wenn a früher liegt); 0 bei fehlendem/Altbestands-Datum."""
    if len(day_a) < 7 or len(day_b) < 7 or day_a.startswith("2007-01-01"):
        return 0
    return (int(day_b[:4]) * 12 + int(day_b[5:7])) - (int(day_a[:4]) * 12 + int(day_a[5:7]))


def _name_sim(set_name: str, names: list[str]) -> float:
    return max((difflib.SequenceMatcher(None, set_name.lower(), n).ratio() for n in names), default=0.0)


def cardmarket_products(records: list[CardRecord], singles: list[dict], sealed: list[dict] | None = None,
                        min_overlap: float = 0.6, tie: float = 0.1) -> tuple[dict[str, int], dict]:
    """Druck → idProduct, nur eindeutige Zuordnungen.

    1. Expansion ↔ Set: Anteil gemeinsamer Kartennamen (bezogen auf das größere von beiden) ≥ `min_overlap`.
       Liegen zwei Kandidaten näher als `tie` beieinander, entscheidet der Expansionsname (aus den Sealed-Produkten)
       – nur bei deutlichem Vorsprung, sonst bleibt das Set ohne Zuordnung. Beanspruchen mehrere Sets dieselbe
       Expansion (z. B. Neuauflage mit gleicher Kartenliste), gilt dasselbe.
    2. Druck: genau ein Druck dieses Namens im Set und genau ein Produkt dieses Namens in der Expansion.
    """
    exp_names = _expansion_names(sealed or [])
    exp_cards: dict[int, Counter] = defaultdict(Counter)
    exp_product: dict[tuple[int, str], list[int]] = defaultdict(list)
    for p in singles:
        n = _norm(p.get("name") or "")
        exp_cards[p["idExpansion"]][n] += 1
        exp_product[(p["idExpansion"], n)].append(int(p["idProduct"]))
    by_name: dict[str, set[int]] = defaultdict(set)  # Kartenname → Expansionen (Kandidatensuche)
    for e, names in exp_cards.items():
        for n in names:
            by_name[n].add(e)

    set_prints: dict[str, list[CardRecord]] = defaultdict(list)
    for r in records:
        if r.set_code and r.set_name:
            set_prints[r.set_name].append(r)

    def score(names: set[str], e: int) -> float:
        en = set(exp_cards[e])
        return len(names & en) / max(len(names), len(en))

    choice: dict[str, tuple[int, float, float]] = {}  # Set → (Expansion, Überlappung, Namensähnlichkeit)
    for set_name, prints in set_prints.items():
        names = {_norm(r.name) for r in prints}
        cands = Counter(e for n in names for e in by_name.get(n, ()))
        scored = sorted(((score(names, e), e) for e in cands), reverse=True)
        scored = [(s, e) for s, e in scored if s >= min_overlap]
        if not scored:
            continue
        close = [(s, e) for s, e in scored if s >= scored[0][0] - tie]
        sims = {e: _name_sim(set_name, exp_names.get(e, [])) for _, e in close}
        if len(close) > 1:
            ranked = sorted(close, key=lambda x: sims[x[1]], reverse=True)
            if sims[ranked[0][1]] < 0.5 or sims[ranked[0][1]] - sims[ranked[1][1]] < 0.15:
                continue  # nicht sicher unterscheidbar
            best = ranked[0]
        else:
            best = close[0]
        if len(names) < 5 and sims.get(best[1], _name_sim(set_name, exp_names.get(best[1], []))) < 0.6:
            continue  # sehr kleine Sets (Promos) nur mit passendem Expansionsnamen
        choice[set_name] = (best[1], best[0], sims.get(best[1], 0.0))

    # Eine Expansion darf nur einem Set gehören
    claims: dict[int, list[str]] = defaultdict(list)
    for set_name, (e, _, _) in choice.items():
        claims[e].append(set_name)
    for e, set_names in claims.items():
        if len(set_names) < 2:
            continue
        ranked = sorted(set_names, key=lambda s: (_name_sim(s, exp_names.get(e, [])), choice[s][1]), reverse=True)
        top, second = ranked[0], ranked[1]
        keep = (_name_sim(top, exp_names.get(e, [])) - _name_sim(second, exp_names.get(e, [])) >= 0.15
                and _name_sim(top, exp_names.get(e, [])) >= 0.5)
        for s in ranked[(1 if keep else 0):]:
            choice.pop(s, None)

    added = {int(p["idProduct"]): (p.get("dateAdded") or "")[:10] for p in singles}
    mapping: dict[str, int] = {}
    for set_name, (e, _, _) in list(choice.items()):
        per_name: dict[str, list[CardRecord]] = defaultdict(list)
        for r in set_prints[set_name]:
            per_name[_norm(r.name)].append(r)
        found = {}
        for n, prints in per_name.items():
            products = exp_product.get((e, n), [])
            if len(prints) == 1 and len(products) == 1:
                found[prints[0].id] = products[0]
        # Plausibilität: Produkte, die Cardmarket deutlich (> 6 Monate) vor Erscheinen des Sets angelegt hat,
        # gehören zu einer anderen Expansion → ganzes Set verwerfen ("2007-01-01" = Altbestand ohne Datum)
        released = min((r.released for r in set_prints[set_name] if r.released), default=None)
        if released and any(_months(added.get(pid, ""), released) > 6 for pid in found.values()):
            choice.pop(set_name)
            continue
        mapping.update(found)
    n_prints = sum(len(v) for v in set_prints.values())
    stats = {"prints": n_prints, "mapped": len(mapping), "sets": len(set_prints), "sets_mapped": len(choice)}
    return mapping, stats


# ---------------------------------------------------------------- Bilder
class _RateLimiter:
    def __init__(self, rps: float):
        self.interval = 1.0 / rps
        self.next = 0.0
        self.lock = threading.Lock()

    def wait(self) -> None:
        with self.lock:
            now = time.monotonic()
            t = max(now, self.next)
            self.next = t + self.interval
        if t > now:
            time.sleep(t - now)


def _download(url: str, path: str, limiter: _RateLimiter) -> bool:
    for attempt in range(3):
        try:
            limiter.wait()
            r = SESSION.get(url, timeout=60, headers={"Accept": "image/*"})
            if r.status_code == 429:
                time.sleep(10 + attempt * 10)
                continue
            if r.status_code == 404:
                return False
            r.raise_for_status()
            img = cv2.imdecode(np.frombuffer(r.content, np.uint8), cv2.IMREAD_COLOR)
            if img is None:
                return False
            h, w = img.shape[:2]
            if h > REF_HEIGHT:
                img = cv2.resize(img, (round(w * REF_HEIGHT / h), REF_HEIGHT), interpolation=cv2.INTER_AREA)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            cv2.imwrite(path + ".tmp.jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 90])
            os.replace(path + ".tmp.jpg", path)
            return True
        except requests.RequestException:
            time.sleep(2 + attempt * 2)
    return False


def download_images(records: list[CardRecord], workers: int = 8, progress=None, limit: int | None = None) -> int:
    """Fehlende Artwork-Bilder laden (eine Datei je Artwork, gedrosselt auf `MAX_RPS`)."""
    todo: dict[str, str] = {}
    for r in records:
        if r.image_url and r.image_path not in todo and not os.path.exists(r.image_path):
            todo[r.image_path] = r.image_url
    items = list(todo.items())[:limit] if limit else list(todo.items())
    print(f"[ygo-bilder] {len(items)} zu laden", flush=True)
    limiter = _RateLimiter(MAX_RPS)
    done = 0
    with ThreadPoolExecutor(workers) as pool:
        for _ in pool.map(lambda kv: _download(kv[1], kv[0], limiter), items):
            done += 1
            if done % 100 == 0 or done == len(items):
                if progress:
                    progress(done, len(items))
                else:
                    print(f"\r[ygo-bilder] {done}/{len(items)}", end="", flush=True)
    if not progress and items:
        print()
    return len(items)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-images", action="store_true")
    ap.add_argument("--no-prices", action="store_true", help="ohne Cardmarket-Zuordnung")
    ap.add_argument("--limit", type=int, help="höchstens so viele Bilder laden (Test)")
    args = ap.parse_args()
    cat = Catalog()
    recs = build_ygo(cat, with_prices=not args.no_prices)
    if not args.no_images:
        download_images(recs, limit=args.limit)
    print(f"Katalog: {cat.count('ygo')} Yu-Gi-Oh-Einträge")


if __name__ == "__main__":
    main()
