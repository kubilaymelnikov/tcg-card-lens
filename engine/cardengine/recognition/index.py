"""Referenz-Index: Embeddings aller Katalogbilder, Kosinus-Suche (exakt, auf CPU/NumPy – bei ~120k × 384
float16 rund 90 MB und wenige Millisekunden pro Anfrage).

Ablage: eine Datei je Embedding-Modell UND Spiel, `data/index/cards-<indexname>-<spiel>.npz` (Schlüssel
"spiel|id", Vektoren float16, dazu `index_name` und `game` als Metadaten). So lassen sich Spiele einzeln per
Datenpaket installieren, aktualisieren oder abwählen; `EmbeddingIndex.load(name, games=…)` führt sie zusammen.

Rückwärtskompatibel: die frühere gemeinsame Datei `cards-<indexname>.npz` wird beim ersten Laden einmalig in
Spieldateien aufgeteilt (atomar geschrieben) und erst danach in `….npz.vor-aufteilung` umbenannt.
"""
from __future__ import annotations

import glob
import os
import time

import cv2
import numpy as np

from ..card_database.catalog import Catalog
from ..paths import DATA_DIR

INDEX_DIR = os.path.join(DATA_DIR, "index")
LEGACY_SUFFIX = ".vor-aufteilung"


def _dir(index_dir: str | None) -> str:
    return index_dir or INDEX_DIR


def index_path(name: str, game: str | None = None, index_dir: str | None = None) -> str:
    """Ein Index je Embedding-Modell (Vektoren verschiedener Modelle sind nicht vergleichbar) und Spiel.
    Ohne `game`: Pfad der alten gemeinsamen Datei."""
    fname = f"cards-{name}.npz" if game is None else f"cards-{name}-{game}.npz"
    return os.path.join(_dir(index_dir), fname)


def _write_npz(path: str, keys: list[str], vectors: np.ndarray, name: str, game: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    # Atomar: erst Temp-Datei, dann ersetzen – ein Abbruch oder ein zweiter Prozess hinterlässt nie eine halbe Datei
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "wb") as f:
        np.savez(f, keys=np.array(keys, dtype=str), vectors=vectors.astype(np.float16),
                 index_name=np.array(name), game=np.array(game))
    os.replace(tmp, path)


def _read_npz(path: str) -> tuple[list[str], np.ndarray]:
    with np.load(path, allow_pickle=False) as data:
        return [str(k) for k in data["keys"]], data["vectors"]


def _split_by_game(keys: list[str], vectors: np.ndarray) -> dict[str, tuple[list[str], np.ndarray]]:
    rows: dict[str, list[int]] = {}
    for i, k in enumerate(keys):
        rows.setdefault(k.split("|", 1)[0], []).append(i)
    return {g: ([keys[i] for i in idx], vectors[np.asarray(idx)]) for g, idx in rows.items()}


def available_games(name: str, index_dir: str | None = None) -> list[str]:
    """Spiele, für die eine Spieldatei dieses Modells existiert (anhand der gespeicherten Metadaten – Namen wie
    `occ-dinov3s` und `occ-dinov3s-norm` überlappen sonst im Dateinamen)."""
    games = []
    for path in sorted(glob.glob(os.path.join(glob.escape(_dir(index_dir)), f"cards-{glob.escape(name)}-*.npz"))):
        try:
            with np.load(path, allow_pickle=False) as data:
                if "index_name" in data.files and str(data["index_name"]) == name:
                    games.append(str(data["game"]))
        except (OSError, ValueError):
            continue
    return games


def has_index(name: str, game: str, index_dir: str | None = None) -> bool:
    return os.path.exists(index_path(name, game, index_dir))


def index_keys(name: str, game: str, index_dir: str | None = None) -> set[str]:
    """Schlüssel der Spieldatei (ohne die Vektoren zu laden); leer, wenn es sie nicht gibt."""
    path = index_path(name, game, index_dir)
    if not os.path.exists(path):
        return set()
    with np.load(path, allow_pickle=False) as data:
        return {str(k) for k in data["keys"]}


def migrate_legacy(name: str, index_dir: str | None = None) -> list[str]:
    """Alte gemeinsame Datei einmalig in Spieldateien aufteilen. Bereits vorhandene Spieldateien haben Vorrang.
    Die alte Datei wird erst umbenannt, wenn alle Spieldateien geschrieben sind."""
    legacy = index_path(name, None, index_dir)
    if not os.path.exists(legacy):
        return []
    keys, vectors = _read_npz(legacy)
    written = []
    for game, (k, v) in _split_by_game(keys, vectors).items():
        path = index_path(name, game, index_dir)
        if os.path.exists(path):
            continue
        _write_npz(path, k, v, name, game)
        written.append(game)
    os.replace(legacy, legacy + LEGACY_SUFFIX)
    print(f"[index] alte Indexdatei aufgeteilt: {', '.join(written) or 'nichts neu'} "
          f"(Original: {os.path.basename(legacy)}{LEGACY_SUFFIX})", flush=True)
    return written


def read_game(name: str, game: str, index_dir: str | None = None, legacy_ok: bool = True) -> "EmbeddingIndex | None":
    """Index eines Spiels NUR LESEND holen – auch aus der alten gemeinsamen Datei, ohne sie aufzuteilen
    (für Werkzeuge wie das Paket-Bauen, die Live-Daten nicht verändern dürfen)."""
    path = index_path(name, game, index_dir)
    if os.path.exists(path):
        return EmbeddingIndex(*_read_npz(path))
    legacy = index_path(name, None, index_dir)
    if legacy_ok and os.path.exists(legacy):
        keys, vectors = _read_npz(legacy)
        part = _split_by_game(keys, vectors).get(game)
        if part:
            return EmbeddingIndex(*part)
    return None


class EmbeddingIndex:
    def __init__(self, keys: list[str], vectors: np.ndarray):
        self.keys = keys  # "game|id"
        self.vectors = vectors.astype(np.float16)
        self._key_pos = {k: i for i, k in enumerate(keys)}

    @classmethod
    def load(cls, name: str, games: list[str] | tuple[str, ...] | None = None, index_dir: str | None = None,
             migrate: bool = True) -> "EmbeddingIndex":
        """Spieldateien der gewünschten Spiele (Standard: alle vorhandenen) zusammenführen.
        Fehlt ein gewünschtes Spiel, wird es übersprungen; fehlen alle, gibt es FileNotFoundError."""
        if migrate:
            migrate_legacy(name, index_dir)
        wanted = list(games) if games else available_games(name, index_dir)
        if not games and not migrate and os.path.exists(index_path(name, None, index_dir)):
            legacy_keys, _ = _read_npz(index_path(name, None, index_dir))
            wanted += [g for g in dict.fromkeys(k.split("|", 1)[0] for k in legacy_keys) if g not in wanted]
        parts = []
        for game in wanted:
            part = read_game(name, game, index_dir, legacy_ok=not migrate)
            if part is None:
                print(f"[index] kein Index für {game} ({name})", flush=True)
            else:
                parts.append(part)
        if not parts:
            raise FileNotFoundError(f"kein Erkennungs-Index für {name} ({', '.join(wanted) or 'keine Spiele'})")
        return cls.merge(parts)

    @classmethod
    def merge(cls, parts: list["EmbeddingIndex"]) -> "EmbeddingIndex":
        parts = [p for p in parts if p is not None and len(p.keys)]
        if not parts:
            return cls([], np.zeros((0, 1), np.float16))
        if len(parts) == 1:
            return parts[0]
        return cls([k for p in parts for k in p.keys], np.concatenate([p.vectors for p in parts]))

    def games(self) -> list[str]:
        return list(dict.fromkeys(k.split("|", 1)[0] for k in self.keys))

    def subset(self, game: str) -> "EmbeddingIndex":
        idx = [i for i, k in enumerate(self.keys) if k.startswith(game + "|")]
        return EmbeddingIndex([self.keys[i] for i in idx], self.vectors[np.asarray(idx, dtype=int)])

    def save_game(self, name: str, game: str, index_dir: str | None = None) -> None:
        """Nur die Karten dieses Spiels in seine Spieldatei schreiben (atomar)."""
        part = self.subset(game)
        _write_npz(index_path(name, game, index_dir), part.keys, part.vectors, name, game)

    def save(self, name: str, index_dir: str | None = None) -> None:
        """Alle enthaltenen Spiele je in ihre Spieldatei schreiben."""
        for game, (keys, vectors) in _split_by_game(self.keys, self.vectors).items():
            _write_npz(index_path(name, game, index_dir), keys, vectors, name, game)

    def search(self, query: np.ndarray, k: int = 10, game: str | None = None) -> list[tuple[str, float]]:
        sims = self.vectors @ query.astype(np.float16)
        if game:
            mask = np.array([key.startswith(game + "|") for key in self.keys])
            sims = np.where(mask, sims, -1)
        top = np.argpartition(-sims, min(k, len(sims) - 1))[:k]
        top = top[np.argsort(-sims[top])]
        return [(self.keys[i], float(sims[i])) for i in top]

    def vector(self, key: str) -> np.ndarray | None:
        i = self._key_pos.get(key)
        return None if i is None else self.vectors[i].astype(np.float32)


def build_index(embedder, games: list[str] | tuple[str, ...] | None = None, batch: int = 128,
                index_dir: str | None = None, catalog: Catalog | None = None) -> EmbeddingIndex:
    """Embeddings je Spiel inkrementell ergänzen: vorhandene Vektoren (auch aus Datenpaketen, ohne lokales Bild)
    bleiben, neu berechnet werden nur Katalogkarten ohne Vektor, deren Referenzbild lokal vorliegt.
    Schreibt nur geänderte Spieldateien und liefert den zusammengeführten Index der Spiele."""
    if games is None:
        from ..config import load_config

        games = load_config()["games"]
    name = embedder.index_name
    migrate_legacy(name, index_dir)
    cat = catalog or Catalog()
    parts = []
    for game in games:
        old = read_game(name, game, index_dir, legacy_ok=False)
        keys, vecs, todo = [], [], []
        for rec in cat.all(game):
            key = f"{rec.game}|{rec.id}"
            i = old._key_pos.get(key) if old else None
            if i is not None:
                keys.append(key)
                vecs.append(old.vectors[i])
            elif os.path.exists(rec.image_path):
                todo.append((key, rec.image_path))
        print(f"[index] {game}: {len(keys)} vorhanden, {len(todo)} neu", flush=True)
        if not todo and old is not None and len(keys) == len(old.keys):
            parts.append(old)  # unverändert – Datei nicht neu schreiben
            continue
        t0 = time.time()
        for i in range(0, len(todo), batch):
            part = todo[i:i + batch]
            imgs = [cv2.imread(p) for _, p in part]
            ok = [(k, im) for (k, _), im in zip(part, imgs) if im is not None]
            if ok:
                emb = embedder.embed([im for _, im in ok])
                keys += [k for k, _ in ok]
                vecs += list(emb.astype(np.float16))
            print(f"\r[index] {game}: {min(i + batch, len(todo))}/{len(todo)} ({time.time() - t0:.0f} s)",
                  end="", flush=True)
        if todo:
            print()
        if not keys:
            continue  # keine Datei für ein leeres Spiel – so bleibt "fehlt" erkennbar (Datenpaket/Neuaufbau)
        index = EmbeddingIndex(keys, np.stack(vecs))
        index.save_game(name, game, index_dir)
        parts.append(index)
    return EmbeddingIndex.merge(parts)


if __name__ == "__main__":
    import sys

    from .embedder import DEFAULT_MODEL, Embedder

    build_index(Embedder(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_MODEL))
