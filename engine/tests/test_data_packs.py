"""Datenpakete, Index je Spiel, Aufteilung der alten Indexdatei, Wartungs-Fallback – ohne Netz, mit Fake-Daten."""
import json
import os
import zipfile

import cv2
import numpy as np
import pytest

from cardengine import data_packs, maintenance
from cardengine.card_database import catalog as catalog_mod
from cardengine.card_database.catalog import Catalog, CardRecord
from cardengine.config import load_config, save_config
from cardengine.data_packs import PackError, build_pack, install_pack_file, install_packs, write_packs_index
from cardengine.recognition import index as index_mod
from cardengine.recognition.index import (
    LEGACY_SUFFIX, EmbeddingIndex, available_games, build_index, has_index, index_path, migrate_legacy,
)

NAME = "fake-norm"
DIM = 8


def rec(game, cid, name="Karte", url="http://bild"):
    return CardRecord(game, cid, name, "SET", "Set", cid[-1], None, "en", "2026-01-01", 1, None, "normal", url)


def vec(i):
    v = np.zeros(DIM, np.float32)
    v[i % DIM] = 1.0
    return v


@pytest.fixture
def data(tmp_path, monkeypatch):
    """Komplett isolierter Datenordner: Katalog, Bilder, Index, Stempel."""
    d = tmp_path / "data"
    monkeypatch.setattr(index_mod, "INDEX_DIR", str(d / "index"))
    monkeypatch.setattr(catalog_mod, "IMG_DIR", str(d / "refimg"))
    monkeypatch.setattr(maintenance, "DATA_DIR", str(d))
    monkeypatch.setattr(data_packs.paths, "DATA_DIR", str(d))
    # Standard-Pfad von Catalog() umbiegen (gilt für alle Module, die Catalog() ohne Pfad aufrufen)
    monkeypatch.setattr(Catalog.__init__, "__defaults__", (str(d / "catalog.sqlite"),))
    return d


def source_data(tmp_path):
    """Quelle: Katalog mit 3 Pokémon + 2 Magic-Karten und Index je Spiel."""
    cat = Catalog(str(tmp_path / "src.sqlite"))
    recs = [rec("pokemon", f"p{i}") for i in range(3)] + [rec("mtg", f"m{i}") for i in range(2)]
    cat.upsert(recs)
    idx_dir = str(tmp_path / "src-index")
    EmbeddingIndex([f"{r.game}|{r.id}" for r in recs], np.stack([vec(i) for i in range(5)])).save(NAME, idx_dir)
    return cat, idx_dir


def test_config_defaults_and_roundtrip(tmp_path):
    path = str(tmp_path / "config.json")
    assert load_config(path)["games"] == ["pokemon", "mtg"]
    save_config({"games": ["ygo", "pokemon"], "languages": "fr,EN", "pack_url": "x"}, path)
    cfg = load_config(path)
    assert cfg["games"] == ["ygo", "pokemon"] and cfg["languages"] == ["fr", "en"] and cfg["pack_url"] == "x"
    with open(path, "w", encoding="utf-8-sig") as f:  # PowerShell-BOM wird vertragen
        json.dump({"games": ["mtg"]}, f)
    assert load_config(path)["games"] == ["mtg"]


def test_save_writes_one_file_per_game_and_load_merges(tmp_path):
    _, idx_dir = source_data(tmp_path)
    assert sorted(available_games(NAME, idx_dir)) == ["mtg", "pokemon"]
    both = EmbeddingIndex.load(NAME, index_dir=idx_dir)
    assert len(both.keys) == 5
    only = EmbeddingIndex.load(NAME, games=["pokemon"], index_dir=idx_dir)
    assert only.keys == ["pokemon|p0", "pokemon|p1", "pokemon|p2"]
    assert np.allclose(only.vector("pokemon|p1"), vec(1))
    # fehlendes Spiel wird übersprungen, nur fehlende -> Fehler
    assert len(EmbeddingIndex.load(NAME, games=["mtg", "ygo"], index_dir=idx_dir).keys) == 2
    with pytest.raises(FileNotFoundError):
        EmbeddingIndex.load(NAME, games=["ygo"], index_dir=idx_dir)


def test_available_games_does_not_mix_overlapping_model_names(tmp_path):
    d = str(tmp_path)
    EmbeddingIndex(["pokemon|a"], np.stack([vec(0)])).save("m", d)
    EmbeddingIndex(["mtg|b"], np.stack([vec(1)])).save("m-norm", d)
    assert available_games("m", d) == ["pokemon"]
    assert available_games("m-norm", d) == ["mtg"]


def test_legacy_index_is_split_once(tmp_path):
    d = str(tmp_path)
    keys = ["pokemon|p0", "mtg|m0", "pokemon|p1"]
    legacy = index_path(NAME, None, d)
    with open(legacy, "wb") as f:
        np.savez(f, keys=np.array(keys), vectors=np.stack([vec(i) for i in range(3)]).astype(np.float16))
    # nur lesend (Werkzeuge): alte Datei bleibt unangetastet
    ro = EmbeddingIndex.load(NAME, index_dir=d, migrate=False)
    assert sorted(ro.keys) == sorted(keys) and os.path.exists(legacy)
    assert not has_index(NAME, "pokemon", d)

    idx = EmbeddingIndex.load(NAME, index_dir=d)
    assert sorted(idx.keys) == sorted(keys)
    assert has_index(NAME, "pokemon", d) and has_index(NAME, "mtg", d)
    assert not os.path.exists(legacy) and os.path.exists(legacy + LEGACY_SUFFIX)
    assert np.allclose(idx.vector("mtg|m0"), vec(1))
    assert migrate_legacy(NAME, d) == []  # zweiter Start: nichts mehr zu tun
    assert not any(n.endswith(".tmp") for n in os.listdir(d))


def test_legacy_split_keeps_existing_game_file(tmp_path):
    d = str(tmp_path)
    EmbeddingIndex(["mtg|neu"], np.stack([vec(5)])).save(NAME, d)
    with open(index_path(NAME, None, d), "wb") as f:
        np.savez(f, keys=np.array(["pokemon|p0", "mtg|alt"]), vectors=np.stack([vec(0), vec(1)]).astype(np.float16))
    assert migrate_legacy(NAME, d) == ["pokemon"]
    assert EmbeddingIndex.load(NAME, games=["mtg"], index_dir=d).keys == ["mtg|neu"]


def test_build_and_install_pack_roundtrip(tmp_path, data):
    cat, idx_dir = source_data(tmp_path)
    out = str(tmp_path / "packs")
    entries = [build_pack(g, NAME, out, catalog=cat, index_dir=idx_dir, version="20261017") for g in ("pokemon", "mtg")]
    write_packs_index(out, entries)
    listing = json.load(open(os.path.join(out, "packs.json"), encoding="utf-8"))
    assert {e["file"] for e in listing["packs"]} == {"tcglens-pokemon-fake-norm-20261017.zip",
                                                    "tcglens-mtg-fake-norm-20261017.zip"}
    with zipfile.ZipFile(os.path.join(out, entries[0]["file"])) as z:
        assert sorted(z.namelist()) == ["catalog.jsonl", "index.npz", "manifest.json"]  # keine Bilder
        m = json.loads(z.read("manifest.json"))
    assert m["game"] == "pokemon" and m["cards"] == 3 and m["vectors"] == 3 and m["index_name"] == NAME

    done = install_packs(["pokemon", "mtg"], NAME, pack_dir=out, url="https://x/PLATZHALTER")
    assert set(done) == {"pokemon", "mtg"}
    # Pokémon (TCGdex, MIT) bringt den Katalog mit; Magic (Scryfall) nur Embeddings – Katalog kommt von der Quelle
    assert Catalog().count("pokemon") == 3 and Catalog().count("mtg") == 0
    with zipfile.ZipFile(os.path.join(out, entries[1]["file"])) as z:
        assert sorted(z.namelist()) == ["index.npz", "manifest.json"]
        assert json.loads(z.read("manifest.json"))["catalog"] is False
    idx = EmbeddingIndex.load(NAME, games=["pokemon", "mtg"])
    src = EmbeddingIndex.load(NAME, index_dir=idx_dir)
    assert sorted(idx.keys) == sorted(src.keys)
    for k in src.keys:
        assert np.allclose(idx.vector(k), src.vector(k))


def test_install_from_single_zip_and_dir_without_listing(tmp_path, data):
    cat, idx_dir = source_data(tmp_path)
    out = str(tmp_path / "packs")
    e = build_pack("mtg", NAME, out, catalog=cat, index_dir=idx_dir, version="20261017")
    assert set(install_packs(["mtg"], NAME, pack_dir=os.path.join(out, e["file"]), url="")) == {"mtg"}
    # Ordner ohne packs.json: Dateiname; anderes Modell mit überlappendem Namen wird nicht genommen
    assert install_packs(["mtg"], "fake", pack_dir=out, url="") == {}


def test_install_rejects_wrong_checksum_model_and_tampering(tmp_path, data):
    cat, idx_dir = source_data(tmp_path)
    out = str(tmp_path / "packs")
    e = build_pack("pokemon", NAME, out, catalog=cat, index_dir=idx_dir)
    path = os.path.join(out, e["file"])
    with pytest.raises(PackError, match="Prüfsumme"):
        install_pack_file(path, NAME, expected_sha256="0" * 64)
    with pytest.raises(PackError, match="Modell"):
        install_pack_file(path, "anderes-modell", expected_sha256=e["sha256"])
    with pytest.raises(PackError, match="erwartet"):
        install_pack_file(path, NAME, game="mtg")
    # Inhalt verändert, Manifest nicht -> innere Prüfsumme schlägt an
    bad = str(tmp_path / "bad.zip")
    with zipfile.ZipFile(path) as src, zipfile.ZipFile(bad, "w") as dst:
        for n in src.namelist():
            data_ = src.read(n)
            dst.writestr(n, data_ + b"\n{}" if n == "catalog.jsonl" else data_)
    with pytest.raises(PackError, match="Prüfsumme"):
        install_pack_file(bad, NAME)
    assert Catalog().count() == 0 and not has_index(NAME, "pokemon")


def test_install_keeps_locally_added_vectors(tmp_path, data):
    cat, idx_dir = source_data(tmp_path)
    out = str(tmp_path / "packs")
    e = build_pack("pokemon", NAME, out, catalog=cat, index_dir=idx_dir)
    EmbeddingIndex(["pokemon|lokal", "pokemon|p0"], np.stack([vec(7), vec(6)])).save(NAME)
    install_pack_file(os.path.join(out, e["file"]), NAME, e["sha256"])
    idx = EmbeddingIndex.load(NAME, games=["pokemon"])
    assert sorted(idx.keys) == ["pokemon|lokal", "pokemon|p0", "pokemon|p1", "pokemon|p2"]
    assert np.allclose(idx.vector("pokemon|p0"), vec(0))  # Paket hat Vorrang


def test_placeholder_or_unreachable_url_does_not_raise(data, monkeypatch):
    import requests

    def boom(*a, **k):
        raise requests.ConnectionError("kein Netz")

    monkeypatch.setattr(requests, "get", boom)
    msgs = []
    assert install_packs(["pokemon"], NAME, url="https://github.com/PLATZHALTER/x/releases", log=msgs.append) == {}
    assert any("Platzhalter" in m for m in msgs)
    assert install_packs(["pokemon"], NAME, url="https://example.invalid/packs", log=msgs.append) == {}
    assert any("nicht erreichbar" in m for m in msgs)


# ---------------------------------------------------------------- Wartung
class FakeEmbedder:
    index_name = NAME

    def __init__(self):
        self.calls = 0

    def embed(self, imgs, batch=32):
        self.calls += len(imgs)
        return np.stack([vec(int(im[0, 0, 0]) % DIM) for im in imgs])


def fake_world(monkeypatch):
    """Katalog-Aufbau und Bild-Download ohne Netz."""
    def build(cat):
        recs = [rec("pokemon", f"p{i}") for i in range(3)]
        cat.upsert(recs)
        return recs

    def download(records, workers, progress=None):
        for r in records:
            os.makedirs(os.path.dirname(r.image_path), exist_ok=True)
            cv2.imwrite(r.image_path, np.full((20, 14, 3), int(r.id[-1]), np.uint8))

    monkeypatch.setitem(maintenance.CATALOG_BUILDERS, "pokemon", build)
    monkeypatch.setattr(maintenance, "download_images", download)
    monkeypatch.setattr(maintenance.Maintenance, "prices", lambda self: None)


def test_maintenance_falls_back_to_local_build_without_packs(data, monkeypatch):
    fake_world(monkeypatch)
    emb = FakeEmbedder()
    updates = []
    m = maintenance.Maintenance(embedder_factory=lambda: emb, on_index_updated=updates.append, games=["pokemon"],
                                pack_url=data_packs.DEFAULT_PACK_URL)
    m.initial()
    assert emb.calls == 3 and has_index(NAME, "pokemon") and m.status == "bereit"
    assert len(updates[-1].keys) == 3


def test_maintenance_prefers_pack_and_only_embeds_new_cards(tmp_path, data, monkeypatch):
    cat, idx_dir = source_data(tmp_path)
    out = str(tmp_path / "packs")
    write_packs_index(out, [build_pack("pokemon", NAME, out, catalog=cat, index_dir=idx_dir)])
    fake_world(monkeypatch)
    emb = FakeEmbedder()
    m = maintenance.Maintenance(embedder_factory=lambda: emb, games=["pokemon"], pack_dir=out, pack_url="")
    m.initial()
    assert emb.calls == 0  # alles aus dem Paket, keine Bilder geladen
    assert not os.path.exists(Catalog().get("pokemon", "p0").image_path)
    # neue Karte nach Katalog-Update: nur sie wird geladen und eingebettet, Paket-Vektoren bleiben
    Catalog().upsert([rec("pokemon", "p9")])
    assert [r.id for r in m.images_pending("pokemon")] == ["p9"]
    m.images_chunk()
    m.index()
    assert emb.calls == 1
    assert len(EmbeddingIndex.load(NAME, games=["pokemon"]).keys) == 4


def test_build_index_writes_per_game_and_skips_unchanged(data, monkeypatch):
    fake_world(monkeypatch)
    maintenance.CATALOG_BUILDERS["pokemon"](Catalog())
    maintenance.download_images(Catalog().all("pokemon"), 1)
    emb = FakeEmbedder()
    idx = build_index(emb, games=["pokemon", "mtg"])
    assert len(idx.keys) == 3 and has_index(NAME, "pokemon") and not has_index(NAME, "mtg")
    mtime = os.path.getmtime(index_path(NAME, "pokemon"))
    build_index(emb, games=["pokemon"])
    assert emb.calls == 3 and os.path.getmtime(index_path(NAME, "pokemon")) == mtime
