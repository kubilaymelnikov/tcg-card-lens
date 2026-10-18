"""Datenpakete: Katalog + Erkennungs-Index eines Spiels fertig ausliefern statt lokal neu aufzubauen.

Ein Paket (`tcglens-<spiel>-<indexname>-<JJJJMMTT>.zip`) enthält
- `manifest.json`  Spiel, Indexname (Embedding-Modell), Kartenanzahl, Erstellungsdatum, Version, SHA256 der Dateien
- `catalog.jsonl`  Katalogzeilen des Spiels (Tabelle `cards`) – nur wo die Datenquelle Weitergabe erlaubt
  (`CATALOG_IN_PACK`); sonst lädt jede Installation den Katalog selbst bei der Quelle (Minuten statt Stunden)
- `index.npz`      Embeddings des Spiels für genau dieses Modell (Schlüssel "spiel|id", float16)
KEINE Referenzbilder (Bildrechte) – die Engine lädt Bilder nur bei Bedarf (neue Karten, Dock-Vorschau).

Neben den Paketen liegt `packs.json` (Liste: Datei, Größe, SHA256, Spiel, Indexname, Version).
Quellen in dieser Reihenfolge: lokaler Ordner/ZIP (`--dir`, Offline-Installation), dann Basis-URL
(`TCGLENS_PACK_URL` > `config.json: pack_url` > `DEFAULT_PACK_URL`).

    python -m cardengine.data_packs                      # gewählte Spiele aus config.json installieren
    python -m cardengine.data_packs --games pokemon --dir D:\\packs
    python -m cardengine.data_packs --url https://…/download
Paket bauen: `tools/build_data_pack.py`. Aufbau und Hosting: docs/DATENPAKETE.md.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import sys
import time
import zipfile

import numpy as np

from . import paths
from .card_database.catalog import CardRecord, Catalog
from .i18n import Msg

PACK_FORMAT = 1
# Datenpakete liegen als Assets am jeweils neuesten GitHub-Release (…/releases/latest/download/<datei>).
# Steht "PLATZHALTER" in einer URL (z. B. in Tests), wird kein Netzzugriff versucht (-> lokaler Aufbau).
DEFAULT_PACK_URL = "https://github.com/kubilaymelnikov/tcg-card-lens/releases/latest/download"
PLACEHOLDER = "PLATZHALTER"
INDEX_FILE = "packs.json"
CARD_FIELDS = list(CardRecord.__dataclass_fields__)
# Katalogdaten nur mitliefern, wo die Quelle die Weitergabe erlaubt: TCGdex (Pokémon) steht unter MIT.
# Scryfall (Magic) bittet, Daten nicht neu zu verpacken; YGOPRODeck (Yu-Gi-Oh) untersagt die Weitergabe.
# Deren Pakete enthalten nur die selbst berechneten Embeddings; der Katalog kommt direkt von der Quelle.
CATALOG_IN_PACK = {"pokemon": True}


class PackError(Exception):
    pass


def pack_url(cfg: dict | None = None) -> str:
    if os.environ.get("TCGLENS_PACK_URL"):
        return os.environ["TCGLENS_PACK_URL"]
    if cfg is None:
        from .config import load_config

        cfg = load_config()
    return cfg.get("pack_url") or DEFAULT_PACK_URL


def is_placeholder(url: str) -> bool:
    return PLACEHOLDER in (url or "")


def pack_filename(game: str, index_name: str, version: str) -> str:
    return f"tcglens-{game}-{index_name}-{version}.zip"


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------- bauen
def build_pack(game: str, index_name: str, out_dir: str, catalog: Catalog | None = None,
               index_dir: str | None = None, version: str | None = None, include_catalog: bool | None = None) -> dict:
    """Paket eines Spiels aus lokalem Katalog + Index bauen (liest nur). Liefert den packs.json-Eintrag."""
    from .recognition.index import read_game

    catalog = catalog or Catalog()
    index = read_game(index_name, game, index_dir)  # auch aus alter gemeinsamer Datei, ohne sie zu verändern
    if index is None or not index.keys:
        raise PackError(f"kein Index für {game} ({index_name})")
    cur = catalog.db.execute("SELECT * FROM cards WHERE game = ? ORDER BY id", (game,))
    cols = [d[0] for d in cur.description]
    rows = [dict(zip(cols, r)) for r in cur]
    known = {r["id"] for r in rows}
    # Nur Vektoren mit Katalogzeile ausliefern (sonst kann die Engine den Treffer nicht anzeigen)
    keep = [i for i, k in enumerate(index.keys) if k.split("|", 1)[1] in known]
    keys = [index.keys[i] for i in keep]
    vectors = index.vectors[np.asarray(keep, dtype=int)]

    created = time.strftime("%Y-%m-%dT%H:%M:%S")
    version = version or time.strftime("%Y%m%d")
    if include_catalog is None:
        include_catalog = CATALOG_IN_PACK.get(game, False)
    catalog_bytes = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows).encode("utf-8")
    buf = io.BytesIO()
    np.savez(buf, keys=np.array(keys, dtype=str), vectors=vectors.astype(np.float16),
             index_name=np.array(index_name), game=np.array(game))
    index_bytes = buf.getvalue()
    from . import __version__

    manifest = {
        "format": PACK_FORMAT, "game": game, "index_name": index_name, "cards": len(rows), "vectors": len(keys),
        "dim": int(vectors.shape[1]) if len(keys) else 0, "created": created, "version": version,
        "engine": __version__,
        "catalog": include_catalog,
        "files": {
            "index.npz": {"sha256": hashlib.sha256(index_bytes).hexdigest(), "size": len(index_bytes)},
        },
    }
    if include_catalog:
        manifest["files"]["catalog.jsonl"] = {"sha256": hashlib.sha256(catalog_bytes).hexdigest(),
                                              "size": len(catalog_bytes)}
    os.makedirs(out_dir, exist_ok=True)
    name = pack_filename(game, index_name, version)
    path = os.path.join(out_dir, name)
    tmp = path + ".tmp"
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        z.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        if include_catalog:
            z.writestr("catalog.jsonl", catalog_bytes)
        z.writestr("index.npz", index_bytes)
    os.replace(tmp, path)
    return {"game": game, "index_name": index_name, "file": name, "size": os.path.getsize(path),
            "sha256": sha256_file(path), "cards": len(rows), "vectors": len(keys), "version": version,
            "created": created}


def write_packs_index(out_dir: str, entries: list[dict]) -> str:
    """packs.json schreiben/ergänzen: je (Spiel, Indexname) gilt der neueste Eintrag."""
    path = os.path.join(out_dir, INDEX_FILE)
    old = []
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            old = json.load(f).get("packs", [])
    new_ids = {(e["game"], e["index_name"]) for e in entries}
    packs = [e for e in old if (e["game"], e["index_name"]) not in new_ids] + entries
    data = {"format": PACK_FORMAT, "updated": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "packs": sorted(packs, key=lambda e: (e["game"], e["index_name"]))}
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(path + ".tmp", path)
    return path


# ---------------------------------------------------------------- prüfen + installieren
def read_manifest(zip_path: str) -> dict:
    with zipfile.ZipFile(zip_path) as z:
        return json.loads(z.read("manifest.json").decode("utf-8"))


def install_pack_file(zip_path: str, index_name: str, expected_sha256: str | None = None,
                      game: str | None = None, catalog: Catalog | None = None,
                      index_dir: str | None = None) -> dict:
    """Paket prüfen (SHA256 der ZIP und der enthaltenen Dateien, Modell, Spiel) und einspielen:
    Katalogzeilen per upsert, danach die Spieldatei des Index (lokal ergänzte Karten bleiben erhalten)."""
    from .recognition.index import EmbeddingIndex, read_game

    if expected_sha256 and sha256_file(zip_path).lower() != expected_sha256.lower():
        raise PackError(f"Prüfsumme falsch: {os.path.basename(zip_path)}")
    try:
        with zipfile.ZipFile(zip_path) as z:
            manifest = json.loads(z.read("manifest.json").decode("utf-8"))
            if manifest.get("format", 0) > PACK_FORMAT:
                raise PackError(f"Paketformat {manifest.get('format')} zu neu – Engine aktualisieren")
            if manifest.get("index_name") != index_name:
                raise PackError(f"Paket gehört zu Modell {manifest.get('index_name')!r}, benötigt {index_name!r}")
            if game and manifest.get("game") != game:
                raise PackError(f"Paket enthält {manifest.get('game')!r}, erwartet {game!r}")
            game = manifest["game"]
            blobs = {}
            for fname, info in manifest["files"].items():
                data = z.read(fname)
                if hashlib.sha256(data).hexdigest() != info["sha256"]:
                    raise PackError(f"Prüfsumme falsch: {fname} in {os.path.basename(zip_path)}")
                blobs[fname] = data
    except (zipfile.BadZipFile, KeyError, ValueError) as err:
        raise PackError(f"Paket beschädigt: {os.path.basename(zip_path)} ({err})") from err

    with np.load(io.BytesIO(blobs["index.npz"]), allow_pickle=False) as data:
        keys = [str(k) for k in data["keys"]]
        vectors = data["vectors"]
    if any(not k.startswith(game + "|") for k in keys):
        raise PackError("Index enthält Karten eines anderen Spiels")
    records = []
    # Pakete ohne Katalog (Quelle erlaubt keine Weitergabe): den Katalog baut die Wartung direkt von der Quelle
    for line in blobs.get("catalog.jsonl", b"").decode("utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            records.append(CardRecord(**{f: row.get(f) for f in CARD_FIELDS}))
    if any(r.game != game for r in records):
        raise PackError("Katalog enthält Karten eines anderen Spiels")

    catalog = catalog or Catalog()
    for i in range(0, len(records), 5000):
        catalog.upsert(records[i:i + 5000])
    index = EmbeddingIndex(keys, vectors)
    old = read_game(index_name, game, index_dir, legacy_ok=False)
    if old is not None:  # lokal schon eingebettete Karten, die das Paket (noch) nicht kennt, behalten
        extra = [i for i, k in enumerate(old.keys) if k not in index._key_pos]
        if extra:
            index = EmbeddingIndex.merge([index, EmbeddingIndex([old.keys[i] for i in extra], old.vectors[extra])])
    index.save_game(index_name, game, index_dir)
    return manifest


def _find_local(source: str, game: str, index_name: str) -> tuple[str, str | None] | None:
    """Paketdatei im Ordner (über packs.json oder Dateinamen) bzw. die angegebene ZIP selbst."""
    if os.path.isfile(source):
        try:
            m = read_manifest(source)
        except (OSError, zipfile.BadZipFile, KeyError, ValueError):
            return None
        return (source, None) if m.get("game") == game and m.get("index_name") == index_name else None
    if not os.path.isdir(source):
        return None
    listing = os.path.join(source, INDEX_FILE)
    if os.path.exists(listing):
        with open(listing, encoding="utf-8") as f:
            for e in json.load(f).get("packs", []):
                path = os.path.join(source, e["file"])
                if e["game"] == game and e["index_name"] == index_name and os.path.exists(path):
                    return path, e.get("sha256")
    prefix = f"tcglens-{game}-{index_name}-"
    names = sorted(n for n in os.listdir(source)
                   if n.startswith(prefix) and n.endswith(".zip") and "-" not in n[len(prefix):-4])
    return (os.path.join(source, names[-1]), None) if names else None


def _fetch_listing(base_url: str) -> list[dict]:
    import requests

    r = requests.get(f"{base_url.rstrip('/')}/{INDEX_FILE}", timeout=20,
                     headers={"User-Agent": paths.USER_AGENT})
    r.raise_for_status()
    return r.json().get("packs", [])


def _download(url: str, dest: str, log) -> None:
    import requests

    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with requests.get(url, stream=True, timeout=60, headers={"User-Agent": paths.USER_AGENT}) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length") or 0)
        done, last = 0, 0.0
        with open(dest + ".part", "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
                done += len(chunk)
                if time.time() - last > 2:
                    last = time.time()
                    log(Msg("packs.downloading_of", done=f"{done / 1e6:.0f}", total=f"{total / 1e6:.0f}") if total
                        else Msg("packs.downloading", done=f"{done / 1e6:.0f}"))
    os.replace(dest + ".part", dest)


def install_packs(games: list[str], index_name: str, pack_dir: str | None = None, url: str | None = None,
                  catalog: Catalog | None = None, index_dir: str | None = None, log=None) -> dict[str, dict]:
    """Pakete für `games` installieren (erst lokaler Ordner, dann URL). Fehler je Spiel werden gemeldet, nicht
    geworfen. Liefert {spiel: manifest} der installierten Pakete."""
    log = log or (lambda msg: print(f"[pakete] {msg}", flush=True))
    done: dict[str, dict] = {}
    todo = list(games)
    if pack_dir:
        for game in list(todo):
            found = _find_local(pack_dir, game, index_name)
            if not found:
                log(Msg("packs.none_in_dir", game=game, dir=str(pack_dir)))
                continue
            try:
                log(Msg("packs.installing_file", game=game, file=os.path.basename(found[0])))
                done[game] = install_pack_file(found[0], index_name, found[1], game, catalog, index_dir)
                todo.remove(game)
            except (PackError, OSError) as err:
                log(Msg("packs.game_error", game=game, err=str(err)))
    if todo and url is None:
        url = pack_url()
    if todo and url and os.path.isdir(url):  # Basis-"URL" darf auch ein Ordner sein
        done.update(install_packs(todo, index_name, pack_dir=url, catalog=catalog, index_dir=index_dir, log=log))
        return done
    if todo and url and is_placeholder(url):
        log(Msg("packs.placeholder"))
        return done
    if todo and url:
        try:
            listing = _fetch_listing(url)
        except Exception as err:  # kein Netz, 404 …
            log(Msg("packs.list_unreachable", err=str(err)))
            return done
        tmp_dir = os.path.join(paths.DATA_DIR, "packs", "download")
        for game in todo:
            entry = next((e for e in listing if e["game"] == game and e["index_name"] == index_name), None)
            if entry is None:
                log(Msg("packs.no_pack_for_model", game=game, model=index_name))
                continue
            dest = os.path.join(tmp_dir, entry["file"])
            try:
                log(Msg("packs.downloading_game", game=game, mb=f"{entry.get('size', 0) / 1e6:.0f}"))
                _download(f"{url.rstrip('/')}/{entry['file']}", dest, log)
                log(Msg("packs.verifying", game=game))
                done[game] = install_pack_file(dest, index_name, entry.get("sha256"), game, catalog, index_dir)
            except Exception as err:
                log(Msg("packs.failed", game=game, err=str(err)))
            finally:
                for p in (dest, dest + ".part"):
                    if os.path.exists(p):
                        os.remove(p)
    for game, m in done.items():
        log(Msg("packs.installed", game=game, cards=m['cards'], vectors=m['vectors'], date=m['created'][:10]))
    return done


def main() -> int:
    from .config import load_config
    from .recognition.embedder import Embedder

    ap = argparse.ArgumentParser(description="Datenpakete (Katalog + Erkennungs-Index) installieren")
    ap.add_argument("--games", help="kommagetrennt (Standard: config.json)")
    ap.add_argument("--dir", help="lokaler Ordner mit Paketen oder eine Paket-ZIP")
    ap.add_argument("--url", help="Basis-URL (Standard: TCGLENS_PACK_URL / config.json / Platzhalter)")
    args = ap.parse_args()
    cfg = load_config()
    games = [g.strip() for g in args.games.split(",") if g.strip()] if args.games else cfg["games"]
    done = install_packs(games, Embedder.default_index_name(), pack_dir=args.dir, url=args.url or pack_url(cfg))
    missing = [g for g in games if g not in done]
    if missing:
        print(f"[pakete] nicht installiert: {', '.join(missing)}", flush=True)
    return 0 if not missing else 1


if __name__ == "__main__":
    sys.exit(main())
