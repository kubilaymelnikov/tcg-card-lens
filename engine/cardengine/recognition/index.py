"""Referenz-Index: Embeddings aller Katalogbilder, Kosinus-Suche (exakt, auf CPU/NumPy – bei ~120k × 384
float16 rund 90 MB und wenige Millisekunden pro Anfrage)."""
from __future__ import annotations

import os
import time

import cv2
import numpy as np

from ..card_database.catalog import Catalog
from ..paths import DATA_DIR

INDEX_DIR = os.path.join(DATA_DIR, "index")


def index_path(name: str) -> str:
    """Ein Index je Embedding-Modell (Vektoren verschiedener Modelle sind nicht vergleichbar)."""
    return os.path.join(INDEX_DIR, f"cards-{name}.npz")


class EmbeddingIndex:
    def __init__(self, keys: list[str], vectors: np.ndarray):
        self.keys = keys  # "game|id"
        self.vectors = vectors.astype(np.float16)
        self._key_pos = {k: i for i, k in enumerate(keys)}

    @classmethod
    def load(cls, name: str) -> "EmbeddingIndex":
        data = np.load(index_path(name), allow_pickle=False)
        return cls(list(data["keys"]), data["vectors"])

    def save(self, name: str) -> None:
        path = index_path(name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        # Atomar: erst Temp-Datei, dann ersetzen – ein Abbruch oder ein zweiter Prozess hinterlässt nie eine halbe Datei
        tmp = f"{path}.{os.getpid()}.tmp"
        with open(tmp, "wb") as f:
            np.savez(f, keys=np.array(self.keys), vectors=self.vectors)
        os.replace(tmp, path)

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


def build_index(embedder, games: tuple[str, ...] = ("pokemon", "mtg"), batch: int = 128) -> EmbeddingIndex:
    """Embeddings für alle Katalogkarten mit vorhandenem Referenzbild berechnen (inkrementell)."""
    old = EmbeddingIndex.load(embedder.index_name) if os.path.exists(index_path(embedder.index_name)) else None
    cat = Catalog()
    keys, vecs, todo = [], [], []
    for rec in (r for g in games for r in cat.all(g)):
        key = f"{rec.game}|{rec.id}"
        if not os.path.exists(rec.image_path):
            continue
        v = old.vector(key) if old else None
        if v is not None:
            keys.append(key)
            vecs.append(v)
        else:
            todo.append((key, rec.image_path))
    print(f"[index] {len(keys)} vorhanden, {len(todo)} neu")
    t0 = time.time()
    for i in range(0, len(todo), batch):
        part = todo[i:i + batch]
        imgs = [cv2.imread(p) for _, p in part]
        ok = [(k, im) for (k, _), im in zip(part, imgs) if im is not None]
        if ok:
            emb = embedder.embed([im for _, im in ok])
            keys += [k for k, _ in ok]
            vecs += list(emb)
        print(f"\r[index] {min(i + batch, len(todo))}/{len(todo)} ({time.time() - t0:.0f} s)", end="", flush=True)
    print()
    index = EmbeddingIndex(keys, np.stack(vecs) if vecs else np.zeros((0, 1), np.float32))
    index.save(embedder.index_name)
    return index


if __name__ == "__main__":
    import sys

    from .embedder import DEFAULT_MODEL, Embedder

    build_index(Embedder(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_MODEL))
