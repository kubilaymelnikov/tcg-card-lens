import json
import os

import numpy as np

from cardengine.card_database.catalog import CardRecord
from cardengine.geometry import RECT_H, RECT_W
from cardengine.learning import LearnedStore
from cardengine.recognition.identifier import CardIdentifier
from cardengine.recognition.index import EmbeddingIndex

DIM = 16


def unit(i, noise=0.0, seed=0):
    v = np.zeros(DIM, np.float32)
    v[i] = 1.0
    if noise:
        v += np.random.default_rng(seed).normal(0, noise, DIM).astype(np.float32)
    return v / np.linalg.norm(v)


def mix(a, b, w):
    v = (1 - w) * a + w * b
    return (v / np.linalg.norm(v)).astype(np.float32)


# --- LearnedStore ---------------------------------------------------------------------------------------------------

def test_store_persistence_and_counts(tmp_path):
    s = LearnedStore(root=str(tmp_path))
    s.add_reference("pokemon|a", unit(0) * 3)  # wird normiert
    s.add_reference("mtg|b", unit(1))
    s.add_negative(unit(2))
    s2 = LearnedStore(root=str(tmp_path))
    assert s2.counts() == {"references": 2, "negatives": 1, "dataset": 0}
    assert sorted(s2.keys()) == ["mtg|b", "pokemon|a"]
    key, sim, margin = s2.match(unit(0))
    assert key == "pokemon|a" and abs(sim - 1.0) < 1e-5 and abs(margin - 1.0) < 1e-5
    assert not any(n.endswith(".tmp") for n in os.listdir(tmp_path))


def test_store_max_per_card_and_max_negatives(tmp_path):
    s = LearnedStore(root=str(tmp_path), max_per_card=3, max_negatives=2)
    for i in range(5):
        s.add_reference("pokemon|a", unit(i))
    assert s.counts()["references"] == 3
    # die ältesten (unit(0), unit(1)) sind raus
    assert s.match(unit(0))[1] < 0.5 and s.match(unit(4))[1] > 0.99
    for i in range(4):
        s.add_negative(unit(i))
    assert s.counts()["negatives"] == 2
    assert s.negative_similarity(unit(0)) < 0.5 and s.negative_similarity(unit(3)) > 0.99
    assert LearnedStore(root=str(tmp_path)).counts()["references"] == 3


def test_store_match_margin_and_game_filter(tmp_path):
    s = LearnedStore(root=str(tmp_path))
    assert s.match(unit(0)) is None
    assert s.negative_similarity(unit(0)) == 0.0
    s.add_reference("pokemon|a", unit(0))
    s.add_reference("pokemon|b", mix(unit(0), unit(1), 0.3))
    s.add_reference("mtg|c", unit(1))
    key, sim, margin = s.match(unit(0))
    sim_b = float(np.dot(unit(0), mix(unit(0), unit(1), 0.3)))
    assert key == "pokemon|a" and abs(margin - (1.0 - sim_b)) < 1e-5
    assert s.match(unit(1), game="pokemon")[0] == "pokemon|b"
    assert s.match(unit(1), game="mtg")[0] == "mtg|c"
    assert s.match(unit(1), game="mtg")[2] == 1.0  # keine andere Magic-Karte gelernt


def test_store_forget(tmp_path):
    s = LearnedStore(root=str(tmp_path))
    s.add_reference("pokemon|a", unit(0))
    s.add_reference("pokemon|a", unit(1))
    s.add_reference("pokemon|b", unit(2))
    assert s.forget("pokemon|a") == 2
    assert s.forget("pokemon|a") == 0
    assert s.keys() == ["pokemon|b"]
    assert LearnedStore(root=str(tmp_path)).counts()["references"] == 1


def test_store_dataset_files_and_limit(tmp_path):
    s = LearnedStore(root=str(tmp_path))
    crop = (np.random.default_rng(0).random((RECT_H, RECT_W, 3)) * 255).astype(np.uint8)  # schlecht komprimierbar
    s.add_reference("pokemon|a", unit(0), crop, {"proposed_key": "pokemon|x", "similarity": np.float32(0.8)})
    s.add_negative(unit(1), crop, {"source": "Kamera"})
    rows = [json.loads(line) for line in open(tmp_path / "dataset" / "labels.jsonl", encoding="utf-8")]
    assert [r["label"] for r in rows] == ["card", "no_card"]
    assert rows[0]["key"] == "pokemon|a" and rows[0]["meta"]["proposed_key"] == "pokemon|x"
    assert rows[1]["key"] is None and "T" in rows[0]["time"]
    assert os.path.exists(tmp_path / "dataset" / rows[0]["image"])
    assert s.counts()["dataset"] == 2
    # Grenze: Platz für etwa 2,5 Bilder → älteste werden gelöscht
    limit = os.path.getsize(tmp_path / "dataset" / rows[0]["image"]) * 2.5
    s = LearnedStore(root=str(tmp_path), dataset_max_mb=limit / 1024 / 1024)
    for _ in range(4):
        s.add_reference("pokemon|a", unit(0), crop)
    total = sum(os.path.getsize(os.path.join(d, f)) for d, _, fs in os.walk(tmp_path / "dataset")
                for f in fs if f.endswith(".jpg"))
    assert 0 < total <= limit
    assert not os.path.exists(tmp_path / "dataset" / rows[0]["image"])
    assert LearnedStore(root=str(tmp_path)).counts()["dataset"] == 6


# --- Identifier mit gelernten Referenzen -------------------------------------------------------------------------

def record(game, cid, name, number="1"):
    return CardRecord(game=game, id=cid, name=name, set_code="TST", set_name="Testset", number=number, set_total=99,
                      lang="en", released=None, cardmarket_id=None, illustration_id=None, finishes=None,
                      image_url=None)


class FakeCatalog:
    def __init__(self, recs):
        self.recs = {(r.game, r.id): r for r in recs}

    def get(self, game, cid):
        return self.recs.get((game, cid))


class FakeEmbedder:
    """Liefert vorgegebene Embeddings für (0°, 180°), unabhängig vom Bild."""

    def __init__(self, upright, flipped):
        self.vecs = [upright, flipped]

    def embed(self, images):
        return np.stack(self.vecs[:len(images)]).astype(np.float32)


FRAME = np.full((720, 1280, 3), 100, np.uint8)
CORNERS = np.float32([[500, 150], [770, 150], [770, 525], [500, 525]])


def make_identifier(query, learned=None, flipped=None):
    recs = [record("pokemon", "p1", "Pikachu", "1"), record("pokemon", "p2", "Pikachu", "2"),
            record("pokemon", "p3", "Pikachu", "3"), record("pokemon", "e1", "Evoli", "4")]
    # p1/p2/p3 sehen fast gleich aus (verwechselbare Drucke), Evoli weit weg
    vecs = np.stack([unit(0), mix(unit(0), unit(1), 0.05), mix(unit(0), unit(2), 0.06), unit(5)])
    index = EmbeddingIndex([f"{r.game}|{r.id}" for r in recs], vecs)
    emb = FakeEmbedder(query, flipped if flipped is not None else unit(9))
    return CardIdentifier(index, FakeCatalog(recs), emb, ocr=None, learned=learned)


def test_identifier_ambiguous_candidates_are_keys():
    ident = make_identifier(unit(0)).identify(FRAME, CORNERS)
    assert ident is not None and not ident.certain and ident.card_id is None
    assert ident.candidates and all("|" in c for c in ident.candidates)
    assert set(ident.candidates) == {"pokemon|p1", "pokemon|p2", "pokemon|p3"}
    keys = [k for k, _ in ident.alternatives]
    assert keys[0] == "pokemon|p1" and len(keys) == len(set(keys)) == 4
    assert ident.rotate180 is False and ident.embedding is not None and ident.embedding.shape == (DIM,)


def test_identifier_learned_reference_is_certain(tmp_path):
    store = LearnedStore(root=str(tmp_path))
    cam = mix(unit(0), unit(7), 0.5)  # Kamerabild: nur mäßig ähnlich zum Katalog
    store.add_reference("pokemon|p2", cam)
    ident = make_identifier(cam, learned=store).identify(FRAME, CORNERS)
    assert ident.certain and ident.method == "gelernt" and ident.card_id == "p2"
    assert ident.similarity > 0.99 and ident.number == "2/99"
    assert ident.alternatives[0][0] == "pokemon|p2" and len(ident.alternatives) > 1


def test_identifier_learned_orientation_wins(tmp_path):
    store = LearnedStore(root=str(tmp_path))
    store.add_reference("pokemon|e1", unit(8))
    # 0° passt besser zum Katalog, 180° aber sicher zu einer gelernten Referenz
    ident = make_identifier(unit(0), learned=store, flipped=unit(8)).identify(FRAME, CORNERS)
    assert ident.method == "gelernt" and ident.card_id == "e1" and ident.rotate180 is True


def test_identifier_learned_without_margin_falls_back(tmp_path):
    store = LearnedStore(root=str(tmp_path))
    store.add_reference("pokemon|p2", unit(0))
    store.add_reference("pokemon|p3", mix(unit(0), unit(3), 0.01))  # fast gleich → kein Abstand
    ident = make_identifier(unit(0), learned=store).identify(FRAME, CORNERS)
    assert ident.method != "gelernt"


def test_identifier_negative_example_suppresses(tmp_path):
    store = LearnedStore(root=str(tmp_path))
    store.add_negative(unit(0))
    assert make_identifier(unit(0), learned=store).identify(FRAME, CORNERS) is None


def test_embed_view_returns_embedding_and_rectified_image():
    ident = make_identifier(unit(0))
    emb, img = ident.embed_view(FRAME, CORNERS, rotate180=True)
    assert emb.shape == (DIM,) and img.shape == (RECT_H, RECT_W, 3)
