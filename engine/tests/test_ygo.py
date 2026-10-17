"""Yu-Gi-Oh: Set-Code-Parser, Katalog-Umwandlung, Cardmarket-Zuordnung, Spielauswahl, Identifikation (ohne Netz/Modelle)."""
import os

import numpy as np

from cardengine.card_database.catalog import IMG_DIR, CardRecord, Catalog, image_path
from cardengine.card_database.ygo import cardmarket_products, records_from_api
from cardengine.games import parse_games
from cardengine.recognition.footer import parse_ygo_codes, ygo_code_key
from cardengine.recognition.identifier import CardIdentifier
from cardengine.recognition.index import EmbeddingIndex

# ---------------------------------------------------------------- Set-Code


def test_code_key_maps_regions_to_one_key():
    keys = {ygo_code_key(c) for c in ("LOB-EN001", "LOB-DE001", "LOB-G001", "LOB-E001", "LOB-001", "lob-fr001")}
    assert keys == {"L0B-001"}  # O/0 im Präfix vereinheitlicht
    assert ygo_code_key("YGLD-ENC32") == ygo_code_key("YGLD-DEC32") == "YGLD-C32"
    assert ygo_code_key("SR03-ENTKN") == "SR03-TKN"
    assert ygo_code_key("LCYW-EN078") != ygo_code_key("LCYW-EN079")
    assert ygo_code_key("kein code") is None


def test_parse_ygo_codes_ocr_variants():
    assert parse_ygo_codes("LCYW-DE078") == ["LCYW-078"]
    assert parse_ygo_codes("1. Auflage  LOB-G001") == ["L0B-001"]
    assert parse_ygo_codes("PP01–EN002") == ["PP01-002"]  # Gedankenstrich statt Bindestrich
    assert parse_ygo_codes("LOB-DEOO1") == ["L0B-001"]  # O statt 0 in der Nummer
    assert parse_ygo_codes("LOB_DE0I2") == ["L0B-012"]
    assert parse_ygo_codes("YGLD-DEC32") == ["YGLD-C32"]
    assert parse_ygo_codes("ATK/3000 DEF/2500") == []
    assert parse_ygo_codes("") == []


# ---------------------------------------------------------------- Katalog

API_SAMPLE = [
    {"id": 76792184, "name": "Black Magic Ritual",
     "card_sets": [
         {"set_name": "Premium Pack (TCG)", "set_code": "PP01-EN002", "set_rarity": "Secret Rare",
          "set_rarity_code": "(ScR)"},
         {"set_name": "Yugi's Legendary Decks", "set_code": "YGLD-ENC32", "set_rarity": "Common",
          "set_rarity_code": "(C)"},
         {"set_name": "Yugi's Legendary Decks", "set_code": "YGLD-ENC32", "set_rarity": "Common",
          "set_rarity_code": "(C)"},  # doppelte Zeile
     ],
     "card_images": [{"id": 76792184}]},
    {"id": 46986414, "name": "Dark Magician",
     "card_sets": [
         {"set_name": "Legend of Blue Eyes White Dragon", "set_code": "LOB-EN005", "set_rarity": "Ultra Rare",
          "set_rarity_code": "(UR)"},
         {"set_name": "Starter Deck: Yugi", "set_code": "SDY-006", "set_rarity": "Ultra Rare",
          "set_rarity_code": ""},
         {"set_name": "Starter Deck: Yugi Reloaded", "set_code": "SDY-006", "set_rarity": "Ultra Rare",
          "set_rarity_code": ""},  # gleicher Code in anderem Set
     ],
     "card_images": [{"id": 46986414}, {"id": 46986415}]},
    {"id": 11111111, "name": "Nur OCG", "card_images": [{"id": 11111111}]},
]


def test_records_from_api_prints_and_artworks():
    recs = records_from_api(API_SAMPLE, {"Premium Pack (TCG)": "2007-01-01"})
    by_id = {r.id: r for r in recs}
    assert "76792184-PP01-EN002-ScR" in by_id and "76792184-YGLD-ENC32-C" in by_id
    assert len([r for r in recs if r.name == "Black Magic Ritual"]) == 2  # Duplikat entfernt, kein Artwork-Eintrag
    pp = by_id["76792184-PP01-EN002-ScR"]
    assert (pp.set_code, pp.number, pp.printed_number, pp.finishes) == ("PP01", "EN002", "PP01-EN002", "Secret Rare")
    assert pp.released == "2007-01-01" and pp.illustration_id == "76792184"
    # Seltenheit ohne Kürzel → aus dem Namen; gleicher Code in zweitem Set → eindeutige ID
    assert "46986414-SDY-006-UR" in by_id and "46986414-SDY-006-UR-2" in by_id
    # Alt-Art und Karte ohne Druck → reine Artwork-Einträge ohne Set-Code
    alt = by_id["46986414-art46986415"]
    assert alt.set_code == "" and alt.printed_number == "" and alt.illustration_id == "46986415"
    assert by_id["11111111-art11111111"].set_code == ""
    assert "46986414-art46986414" not in by_id  # Hauptartwork ist über die Drucke abgedeckt


def test_image_path_shared_per_artwork():
    a = image_path("ygo", "46986414-LOB-EN005-UR")
    assert a == image_path("ygo", "46986414-SDY-006-UR-2") == os.path.join(IMG_DIR, "ygo", "46986414.jpg")
    assert image_path("ygo", "46986414-art46986415") == os.path.join(IMG_DIR, "ygo", "46986415.jpg")
    assert image_path("pokemon", "sv01-1") == os.path.join(IMG_DIR, "pokemon", "sv01-1.jpg")  # unverändert


# ---------------------------------------------------------------- Cardmarket

def _print(cid, name, set_name, code, rarity="C", released="2020-01-01"):
    prefix, number = code.split("-", 1)
    return CardRecord(game="ygo", id=cid, name=name, set_code=prefix, set_name=set_name, number=number,
                      set_total=None, lang="en", released=released, cardmarket_id=None, illustration_id="1",
                      finishes=rarity, image_url=None)


def test_cardmarket_products_only_unique():
    names = [f"Karte {i}" for i in range(10)]
    recs = [_print(f"a{i}", n, "Set Alpha", f"ALP-EN{i:03d}") for i, n in enumerate(names)]
    recs.append(_print("a0-sr", "Karte 0", "Set Alpha", "ALP-EN000", "SR"))  # zweite Seltenheit
    recs += [_print(f"b{i}", n, "Set Beta", f"BET-EN{i:03d}") for i, n in enumerate(names[:6])]
    singles = [{"idProduct": 100 + i, "name": n, "idExpansion": 1, "dateAdded": "2020-01-01 10:00:00"}
               for i, n in enumerate(names)]
    singles.append({"idProduct": 199, "name": "Karte 0", "idExpansion": 1, "dateAdded": "2020-01-01 10:00:00"})
    singles += [{"idProduct": 200 + i, "name": n, "idExpansion": 2, "dateAdded": "2020-01-01"} for i, n in enumerate(names[:6])]
    singles.append({"idProduct": 299, "name": "Karte 5", "idExpansion": 2, "dateAdded": "2020-01-01"})  # 2 Versionen
    sealed = [{"name": "Set Alpha Booster", "idExpansion": 1}, {"name": "Set Beta Booster Box", "idExpansion": 2}]
    mapping, stats = cardmarket_products(recs, singles, sealed)
    assert mapping["a1"] == 101 and mapping["a9"] == 109 and mapping["b2"] == 202
    assert "a0" not in mapping and "a0-sr" not in mapping  # zwei Seltenheiten / zwei Produkte
    assert "b5" not in mapping  # zwei Produkte gleichen Namens in der Expansion
    assert stats["sets_mapped"] == 2 and stats["mapped"] == len(mapping) == 9 + 5


def test_cardmarket_products_ambiguous_expansion_and_dates():
    names = [f"Karte {i}" for i in range(8)]
    recs = [_print(f"a{i}", n, "Original", f"ORG-EN{i:03d}") for i, n in enumerate(names)]
    # zwei Expansionen mit identischer Kartenliste und nichtssagenden Namen → keine Zuordnung
    singles = [{"idProduct": 100 * e + i, "name": n, "idExpansion": e, "dateAdded": "2007-01-01 00:00:00"}
               for e in (1, 2) for i, n in enumerate(names)]
    mapping, _ = cardmarket_products(recs, singles, [])
    assert mapping == {}
    # mit eindeutigem Expansionsnamen → Zuordnung zur richtigen
    sealed = [{"name": "Original Booster", "idExpansion": 2}, {"name": "Zweitauflage Booster", "idExpansion": 1}]
    mapping, _ = cardmarket_products(recs, singles, sealed)
    assert mapping["a3"] == 203
    # Produkt lange vor Erscheinen des Sets angelegt → unplausibel, verwerfen
    late = [_print(f"a{i}", n, "Original", f"ORG-EN{i:03d}", released="2024-05-01") for i, n in enumerate(names)]
    early = [{"idProduct": 300 + i, "name": n, "idExpansion": 3, "dateAdded": "2019-01-01 12:00:00"}
             for i, n in enumerate(names)]
    assert cardmarket_products(late, early, [{"name": "Original Booster", "idExpansion": 3}])[0] == {}


# ---------------------------------------------------------------- Spielauswahl

def test_parse_games_backward_compatible():
    assert parse_games("both") == ("pokemon", "mtg")
    assert parse_games("pokemon") == ("pokemon",)
    assert parse_games("mtg") == ("mtg",)
    assert parse_games("ygo") == ("ygo",)
    assert parse_games("all") is None
    assert parse_games("pokemon,ygo") == ("pokemon", "ygo")
    assert parse_games(["ygo", "mtg"]) == ("mtg", "ygo")
    assert parse_games("pokemon+mtg+ygo") is None
    assert parse_games("") == ("pokemon", "mtg") and parse_games("unsinn") == ("pokemon", "mtg")


def test_index_search_multiple_games():
    keys = ["pokemon|a", "mtg|b", "ygo|c"]
    index = EmbeddingIndex(keys, np.eye(3, dtype=np.float32))
    q = np.float32([0.2, 0.5, 0.9])
    assert index.search(q, k=1)[0][0] == "ygo|c"
    assert index.search(q, k=1, game=("pokemon", "mtg"))[0][0] == "mtg|b"
    assert index.search(q, k=1, game="pokemon")[0][0] == "pokemon|a"


# ---------------------------------------------------------------- Identifikation

DIM = 8
FRAME = np.full((720, 1280, 3), 100, np.uint8)
CORNERS = np.float32([[500, 150], [757, 150], [757, 525], [500, 525]])  # 59 × 86-Format


def unit(i):
    v = np.zeros(DIM, np.float32)
    v[i] = 1
    return v


class FakeEmbedder:
    def __init__(self, query):
        self.query = query

    def embed(self, images):
        out = [self.query] + [np.zeros(DIM, np.float32)] * (len(images) - 1)
        return np.stack(out).astype(np.float32)


class FakeOcr:
    def __init__(self, text):
        self.text = text
        self.calls = 0

    def read_set_code(self, card):
        self.calls += 1
        return self.text


def make_ygo(tmp_path, query, ocr_text):
    cat = Catalog(str(tmp_path / "catalog.sqlite"))
    cards = [
        {"id": 76792184, "name": "Black Magic Ritual", "card_images": [{"id": 76792184}], "card_sets": [
            {"set_name": "Legendary Collection 3", "set_code": "LCYW-EN078", "set_rarity": "Common", "set_rarity_code": "(C)"},
            {"set_name": "Premium Pack", "set_code": "PP01-EN002", "set_rarity": "Secret Rare", "set_rarity_code": "(ScR)"},
            {"set_name": "Rarity Set", "set_code": "RA01-EN010", "set_rarity": "Super Rare", "set_rarity_code": "(SR)"},
            {"set_name": "Rarity Set", "set_code": "RA01-EN010", "set_rarity": "Ultra Rare", "set_rarity_code": "(UR)"}]},
        {"id": 55761792, "name": "Black Luster Ritual", "card_images": [{"id": 55761792}], "card_sets": [
            {"set_name": "Starter Deck: Yugi", "set_code": "SDY-E037", "set_rarity": "Common", "set_rarity_code": "(C)"}]},
        {"id": 22222222, "name": "Einzeldruck", "card_images": [{"id": 22222222}], "card_sets": [
            {"set_name": "Promo", "set_code": "PRO-EN001", "set_rarity": "Common", "set_rarity_code": "(C)"}]},
    ]
    cat.upsert(records_from_api(cards))
    # Index: ein Eintrag je Artwork (wie build_index mit Bild-Deduplizierung)
    keys = ["ygo|76792184-LCYW-EN078-C", "ygo|55761792-SDY-E037-C", "ygo|22222222-PRO-EN001-C"]
    vecs = np.stack([unit(0), unit(1), unit(2)])
    ocr = FakeOcr(ocr_text) if ocr_text is not None else None
    return CardIdentifier(EmbeddingIndex(keys, vecs), cat, FakeEmbedder(query), ocr=ocr), ocr


def test_ygo_german_set_code_selects_print(tmp_path):
    ident, ocr = make_ygo(tmp_path, unit(0), "LCYW-DE078")
    res = ident.identify(FRAME, CORNERS, game="ygo")
    assert res.certain and res.card_id == "76792184-LCYW-EN078-C" and res.method == "embedding+ocr"
    assert res.number == "LCYW-EN078" and res.name == "Black Magic Ritual" and ocr.calls == 1


def test_ygo_unreadable_code_is_uncertain(tmp_path):
    ident, _ = make_ygo(tmp_path, unit(0), "")
    res = ident.identify(FRAME, CORNERS, game="ygo")
    assert not res.certain and res.card_id is None and res.name == "Black Magic Ritual"
    assert len(res.candidates) == 4 and "Set-Code nicht lesbar" in res.reason


def test_ygo_several_rarities_stay_uncertain(tmp_path):
    ident, _ = make_ygo(tmp_path, unit(0), "RA01-EN010")
    res = ident.identify(FRAME, CORNERS, game="ygo")
    assert not res.certain and res.card_id is None and len(res.candidates) == 2
    assert res.number == "RA01-EN010" and "Seltenheiten" in res.reason


def test_ygo_code_of_other_card_is_uncertain(tmp_path):
    ident, _ = make_ygo(tmp_path, unit(0), "SDY-G037")  # Code gehört zu Black Luster Ritual
    res = ident.identify(FRAME, CORNERS, game="ygo")
    assert not res.certain and res.card_id is None


def test_ygo_set_code_decides_close_names(tmp_path):
    query = (unit(0) + unit(1) * 0.98)
    ident, _ = make_ygo(tmp_path, query / np.linalg.norm(query), "SDY-G037")
    res = ident.identify(FRAME, CORNERS, game="ygo")
    assert res.certain and res.card_id == "55761792-SDY-E037-C" and res.name == "Black Luster Ritual"


def test_ygo_single_print_without_ocr_text(tmp_path):
    ident, _ = make_ygo(tmp_path, unit(2), "")
    res = ident.identify(FRAME, CORNERS, game="ygo")
    assert res.certain and res.card_id == "22222222-PRO-EN001-C" and res.method == "embedding"
