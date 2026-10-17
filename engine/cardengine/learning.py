"""Lernen aus Bestätigungen (siehe docs/DOCK.md).

Stufe 1: Embeddings bestätigter Kamera-Ausschnitte als *gelernte Referenzen* je Kartenschlüssel ("game|id") und
„Keine Karte“-Ausschnitte als *Negativbeispiele* – sofort wirksam, ohne Training.
Stufe 2: Jede Bestätigung legt zusätzlich ein Trainingsbeispiel (entzerrtes JPEG + Zeile in labels.jsonl) ab.
"""
from __future__ import annotations

import json
import os
import shutil
import threading
import uuid
from datetime import datetime, timezone

import cv2
import numpy as np

from .paths import DATA_DIR

ROOT = os.path.join(DATA_DIR, "learned")


def _norm(v: np.ndarray) -> np.ndarray:
    v = np.asarray(v, np.float32).reshape(-1)
    n = float(np.linalg.norm(v))
    return v / n if n > 0 else v


class LearnedStore:
    def __init__(self, root: str | None = None, max_per_card: int = 20, max_negatives: int = 500,
                 dataset_max_mb: float = 500.0):
        self.root = root or ROOT
        self.max_per_card = max_per_card
        self.max_negatives = max_negatives
        self.dataset_max_bytes = int(dataset_max_mb * 1024 * 1024)
        self.dataset_dir = os.path.join(self.root, "dataset")
        self.labels_path = os.path.join(self.dataset_dir, "labels.jsonl")
        self._lock = threading.Lock()
        self._refs: dict[str, list[np.ndarray]] = {}  # Schlüssel → Referenzen, älteste zuerst
        self._neg: list[np.ndarray] = []
        self._dataset_lines = 0
        os.makedirs(self.root, exist_ok=True)
        self._load()

    # --- Persistenz ---------------------------------------------------------------------------------------------

    def _path(self, name: str) -> str:
        return os.path.join(self.root, name)

    def _load(self) -> None:
        p = self._path("references.npz")
        if os.path.exists(p):
            try:
                data = np.load(p, allow_pickle=False)
                for k, v in zip(data["keys"], data["vectors"]):
                    self._refs.setdefault(str(k), []).append(np.asarray(v, np.float32))
            except Exception as e:  # beschädigte Datei: lieber leer starten als abstürzen
                print(f"[learned] references.npz nicht lesbar: {e}")
                self._refs = {}
        p = self._path("negatives.npz")
        if os.path.exists(p):
            try:
                data = np.load(p, allow_pickle=False)
                self._neg = [np.asarray(v, np.float32) for v in data["vectors"]]
            except Exception as e:
                print(f"[learned] negatives.npz nicht lesbar: {e}")
                self._neg = []
        if os.path.exists(self.labels_path):
            with open(self.labels_path, encoding="utf-8") as f:
                self._dataset_lines = sum(1 for line in f if line.strip())

    def _save_npz(self, name: str, **arrays) -> None:
        # atomar: erst in tmp schreiben, dann ersetzen
        tmp = self._path(name + ".tmp")
        with open(tmp, "wb") as f:
            np.savez(f, **arrays)
        os.replace(tmp, self._path(name))

    def _save_refs(self) -> None:
        keys = [k for k, vs in self._refs.items() for _ in vs]
        vecs = [v for vs in self._refs.values() for v in vs]
        dim = vecs[0].shape[0] if vecs else 0
        self._save_npz("references.npz", keys=np.array(keys, dtype=str),
                       vectors=np.stack(vecs) if vecs else np.zeros((0, dim), np.float32))

    def _save_neg(self) -> None:
        dim = self._neg[0].shape[0] if self._neg else 0
        self._save_npz("negatives.npz",
                       vectors=np.stack(self._neg) if self._neg else np.zeros((0, dim), np.float32))

    # --- Datensatz (Stufe 2) ------------------------------------------------------------------------------------

    def _add_sample(self, crop_bgr: np.ndarray, key: str | None, label: str, meta: dict | None) -> None:
        now = datetime.now(timezone.utc).astimezone()
        day_dir = os.path.join(self.dataset_dir, now.strftime("%Y-%m-%d"))
        os.makedirs(day_dir, exist_ok=True)
        name = f"{uuid.uuid4().hex}.jpg"
        if not cv2.imwrite(os.path.join(day_dir, name), crop_bgr, [cv2.IMWRITE_JPEG_QUALITY, 90]):
            return
        row = {"image": f"{now.strftime('%Y-%m-%d')}/{name}", "key": key, "label": label,
               "meta": meta or {}, "time": now.isoformat(timespec="seconds")}
        with open(self.labels_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False, default=_json_default) + "\n")
        self._dataset_lines += 1
        self._enforce_dataset_limit()

    def _enforce_dataset_limit(self) -> None:
        """Älteste Tagesordner/Bilder löschen, bis der Datensatz unter die Grenze passt (labels.jsonl bleibt)."""
        files = []
        for dirpath, _, names in os.walk(self.dataset_dir):
            for n in names:
                p = os.path.join(dirpath, n)
                if os.path.abspath(p) == os.path.abspath(self.labels_path):
                    continue
                try:
                    files.append((os.path.getmtime(p), os.path.getsize(p), p))
                except OSError:
                    pass
        total = sum(s for _, s, _ in files)
        if total <= self.dataset_max_bytes:
            return
        for _, size, path in sorted(files):
            if total <= self.dataset_max_bytes:
                break
            try:
                os.remove(path)
                total -= size
            except OSError:
                pass
        # leere Tagesordner entfernen
        for d in os.listdir(self.dataset_dir):
            p = os.path.join(self.dataset_dir, d)
            if os.path.isdir(p) and not os.listdir(p):
                shutil.rmtree(p, ignore_errors=True)

    # --- öffentliche API ----------------------------------------------------------------------------------------

    def add_reference(self, key: str, embedding: np.ndarray, crop_bgr: np.ndarray | None = None,
                      meta: dict | None = None) -> None:
        v = _norm(embedding)
        with self._lock:
            refs = self._refs.setdefault(key, [])
            refs.append(v)
            del refs[:-self.max_per_card]  # älteste fallen raus
            self._save_refs()
            if crop_bgr is not None:
                self._add_sample(crop_bgr, key, "card", meta)

    def add_negative(self, embedding: np.ndarray, crop_bgr: np.ndarray | None = None,
                     meta: dict | None = None) -> None:
        v = _norm(embedding)
        with self._lock:
            self._neg.append(v)
            del self._neg[:-self.max_negatives]
            self._save_neg()
            if crop_bgr is not None:
                self._add_sample(crop_bgr, None, "no_card", meta)

    def match(self, embedding: np.ndarray, game: str | tuple[str, ...] | None = None) -> tuple[str, float, float] | None:
        """Beste gelernte Karte: (Schlüssel, max. Kosinus, Abstand zur besten anderen gelernten Karte).
        `game`: ein Spiel oder mehrere (Tupel), None = alle."""
        q = _norm(embedding)
        prefixes = tuple(g + "|" for g in ((game,) if isinstance(game, str) else game or ()))
        with self._lock:
            scores = [(k, float(np.max(np.stack(vs) @ q))) for k, vs in self._refs.items()
                      if vs and (not prefixes or k.startswith(prefixes))]
        if not scores:
            return None
        scores.sort(key=lambda x: x[1], reverse=True)
        key, sim = scores[0]
        margin = sim - scores[1][1] if len(scores) > 1 else 1.0
        return key, sim, margin

    def negative_similarity(self, embedding: np.ndarray) -> float:
        q = _norm(embedding)
        with self._lock:
            if not self._neg:
                return 0.0
            return float(np.max(np.stack(self._neg) @ q))

    def forget(self, key: str) -> int:
        with self._lock:
            n = len(self._refs.pop(key, []))
            if n:
                self._save_refs()
            return n

    def counts(self) -> dict:
        with self._lock:
            return {"references": sum(len(v) for v in self._refs.values()), "negatives": len(self._neg),
                    "dataset": self._dataset_lines}

    def keys(self) -> list[str]:
        with self._lock:
            return [k for k, v in self._refs.items() if v]


def _json_default(o):
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, (np.floating, np.integer)):
        return o.item()
    return str(o)
