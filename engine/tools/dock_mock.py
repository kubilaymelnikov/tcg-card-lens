"""Mock-Server für das Bestätigungs-Dock (engine/dock/dock.html).

Simuliert die HTTP-API aus docs/DOCK.md mit Fake-Daten, damit die Oberfläche ohne Engine/OBS
entwickelt werden kann. Nur Python-Standardbibliothek.

    python tools/dock_mock.py [--port 47839] [--host 127.0.0.1] [--flaky]

Kamerabild-Vorschau: Jede Sitzung liefert "frame"/"tracks" (1920×1080-Raster mit Kartenplätzen), /api/frame/<sid>
ein generiertes SVG-Kamerabild mit grauen Karten. Auf einigen Plätzen liegt eine Karte ohne Track („übersehen“):
Ein Klick dort (POST /api/probe) legt eine neue Karte an (how "detektor"), ein Klick auf leeren Tisch liefert
found=false, ein aufgezogener Rahmen findet immer eine Karte (how "rahmen"). --no-frame lässt frame/tracks weg
(alte Engine bzw. Engine ohne Bild).

--flaky simuliert eine unruhige Engine: unsichere Karten fehlen zufällig für 1–3 Abfragen, wechseln ihren
state (uncertain/failed/rejected), melden kurz needs_confirmation=false oder recheck=true, bekommen gelegentlich
einen anderen Vorschlag; Karte D hält die Frage mit "sticky": true offen; /api/state liefert gelegentlich 500
oder antwortet erst nach 4 s.

Dann im Browser (oder als OBS-Browser-Dock) http://127.0.0.1:47839/ öffnen.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import random
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from xml.sax.saxutils import escape

DOCK_HTML = Path(__file__).resolve().parent.parent / "dock" / "dock.html"

# Katalog für /api/search und zum Auflösen von Schlüsseln bei /api/confirm.
CATALOG = [
    # Pokémon
    {"key": "pokemon|sv3pt5-25", "name": "Pikachu", "set_name": "151", "set_code": "MEW", "number": "025/165"},
    {"key": "pokemon|sv3pt5-173", "name": "Pikachu", "set_name": "151", "set_code": "MEW", "number": "173/165"},
    {"key": "pokemon|sv4pt5-131", "name": "Pikachu", "set_name": "Paldeas Schicksale", "set_code": "PAF", "number": "131/091"},
    {"key": "pokemon|swsh45-SV013", "name": "Pikachu", "set_name": "Glänzendes Schicksal", "set_code": "SHF", "number": "SV013"},
    {"key": "pokemon|sv3pt5-6", "name": "Glurak-ex", "set_name": "151", "set_code": "MEW", "number": "006/165"},
    {"key": "pokemon|sv3pt5-199", "name": "Glurak-ex", "set_name": "151", "set_code": "MEW", "number": "199/165"},
    {"key": "pokemon|sv3pt5-150", "name": "Mewtu", "set_name": "151", "set_code": "MEW", "number": "150/165"},
    {"key": "pokemon|sv3pt5-151", "name": "Mew-ex", "set_name": "151", "set_code": "MEW", "number": "151/165"},
    {"key": "pokemon|sv2-missing", "name": "Bisasam", "set_name": "Entwicklungen in Paldea", "set_code": "PAL", "number": "001/193"},
    {"key": "pokemon|sv3pt5-1", "name": "Bisasam", "set_name": "151", "set_code": "MEW", "number": "001/165"},
    # Magic
    {"key": "mtg|lea-161", "name": "Lightning Bolt", "set_name": "Limited Edition Alpha", "set_code": "LEA", "number": "161"},
    {"key": "mtg|m10-146", "name": "Lightning Bolt", "set_name": "Magic 2010", "set_code": "M10", "number": "146"},
    {"key": "mtg|2xm-129", "name": "Lightning Bolt", "set_name": "Double Masters", "set_code": "2XM", "number": "129"},
    {"key": "mtg|c21-263", "name": "Sol Ring", "set_name": "Commander 2021", "set_code": "C21", "number": "263"},
    {"key": "mtg|cmr-472", "name": "Sol Ring", "set_name": "Commander Legends", "set_code": "CMR", "number": "472"},
    {"key": "mtg|ltc-284", "name": "Sol Ring", "set_name": "Tales of Middle-earth Commander", "set_code": "LTC", "number": "284"},
    {"key": "mtg|2xm-51", "name": "Counterspell", "set_name": "Double Masters", "set_code": "2XM", "number": "51"},
    {"key": "mtg|mh2-267", "name": "Counterspell", "set_name": "Modern Horizons 2", "set_code": "MH2", "number": "267"},
    {"key": "mtg|dmu-107", "name": "Sheoldred, the Apocalypse", "set_name": "Dominaria United", "set_code": "DMU", "number": "107"},
]
BY_KEY = {c["key"]: c for c in CATALOG}


def img_url(key: str) -> str:
    game, cid = key.split("|", 1)
    return f"/api/card-image/{game}/{urllib.parse.quote(cid)}"


def cand(key: str, sim: float) -> dict:
    c = BY_KEY[key]
    return {"key": key, "name": c["name"], "set_name": c["set_name"], "number": c["number"],
            "similarity": sim, "image": img_url(key)}


def fake_price(key: str) -> dict:
    h = int(hashlib.md5(key.encode()).hexdigest(), 16)
    value = round(0.1 + (h % 9000) / 100.0, 2)
    change = round(((h >> 16) % 400) / 10.0 - 20.0, 1)
    return {"value": value, "currency": "EUR", "variant": "normal", "change_30d": change,
            "updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}


def card(label: str, track: int, key: str | None, *, game: str, name: str | None = None, sim: float = 0.95,
         needs: bool = False, confirmed: bool = False, method: str = "embedding", reason: str | None = None,
         price: bool = True, candidates: list | None = None, state: str = "identified") -> dict:
    base = BY_KEY.get(key, {}) if key else {}
    return {
        "label": label, "track": track, "state": state, "needs_confirmation": needs, "confirmed": confirmed,
        "game": game, "key": key, "name": name or base.get("name"),
        "set_name": base.get("set_name"), "set_code": base.get("set_code"), "number": base.get("number"),
        "similarity": sim, "method": method, "reason": reason,
        "image": img_url(key) if key else None,
        "crop": None,  # wird pro Antwort gesetzt
        "cardmarket_url": (f"https://www.cardmarket.com/de/{'Pokemon' if game == 'pokemon' else 'Magic'}"
                           f"/Products?idProduct={abs(hash(key)) % 900000 + 100000}") if key else None,
        "price": fake_price(key) if (key and price and not needs) else None,
        "candidates": candidates or [],
        "_v": 1,
    }


# ---------- Kamerabild-Raster (1920×1080) ----------
FRAME_W, FRAME_H = 1920, 1080
CARD_W, CARD_H = 230, 320


def slot_quad(i: int) -> list:
    """Normierte Ecken (oben links, im Uhrzeigersinn) des Kartenplatzes i: 2 Reihen × 5 Spalten, leicht gedreht."""
    col, row = i % 5, i // 5
    cx, cy = 200 + col * 380, 270 + row * 520
    ang = math.radians(((i * 37) % 17) - 8)
    ca, sa = math.cos(ang), math.sin(ang)
    out = []
    for dx, dy in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
        x, y = dx * CARD_W / 2, dy * CARD_H / 2
        out.append([round((cx + x * ca - y * sa) / FRAME_W, 4), round((cy + x * sa + y * ca) / FRAME_H, 4)])
    return out


def point_in_quad(x: float, y: float, q: list) -> bool:
    """Punkt in konvexem Viereck (Pixelraum, damit das Seitenverhältnis stimmt)."""
    pts = [(a * FRAME_W, b * FRAME_H) for a, b in q]
    px, py = x * FRAME_W, y * FRAME_H
    sign = 0
    for i in range(4):
        (x0, y0), (x1, y1) = pts[i], pts[(i + 1) % 4]
        cr = (x1 - x0) * (py - y0) - (y1 - y0) * (px - x0)
        if cr != 0:
            if sign and (cr > 0) != (sign > 0):
                return False
            sign = cr
    return True


def place_cards(s: dict, slots: list[int], drawn: list[int]) -> None:
    """Karten auf Plätze legen; drawn = Plätze, auf denen physisch eine Karte liegt (auch ohne Track)."""
    s["_drawn"] = sorted(set(drawn) | set(slots))
    for c, i in zip(s["cards"], slots):
        c["_slot"] = i
        c["_quad"] = slot_quad(i)


def initial_state() -> dict:
    s1 = {
        "id": "s1", "source": "Kamera Tisch", "highlight": None, "_hl_until": 0.0,
        "cards": [
            card("A", 11, "pokemon|sv3pt5-25", game="pokemon", sim=0.81, needs=True, reason="geringe Ähnlichkeit",
                 candidates=[cand("pokemon|sv3pt5-173", 0.79), cand("pokemon|sv4pt5-131", 0.74),
                             cand("pokemon|swsh45-SV013", 0.70)], state="uncertain"),
            card("B", 12, "pokemon|sv3pt5-6", game="pokemon", sim=0.96),
            card("C", 13, "mtg|lea-161", game="mtg", sim=0.97, confirmed=True, method="bestätigt"),
            card("D", 14, None, game="mtg", name="Sol Ring", sim=0.88, needs=True,
                 reason="Druck unklar (mehrere Ausgaben gleich ähnlich)", state="uncertain",
                 candidates=[cand("mtg|c21-263", 0.88), cand("mtg|cmr-472", 0.87), cand("mtg|ltc-284", 0.86)]),
            card("E", 15, "mtg|dmu-107", game="mtg", sim=0.93, method="embedding+ocr"),
        ],
    }
    s2 = {
        "id": "s2", "source": "Kamera Hand", "highlight": None, "_hl_until": 0.0,
        "cards": [
            card("A", 21, "pokemon|sv3pt5-150", game="pokemon", sim=0.94, method="gelernt"),
            card("B", 22, "mtg|2xm-51", game="mtg", sim=0.91, price=False),
            card("C", 23, "pokemon|sv2-missing", game="pokemon", sim=0.92),  # Referenzbild fehlt (404)
        ],
    }
    place_cards(s1, [0, 1, 2, 5, 7], [3, 6, 8])   # Plätze 3, 6, 8: übersehene Karten
    place_cards(s2, [1, 2, 3], [6])
    return {"engine": "0.2.0-mock", "status": "bereit",
            "learned": {"references": 12, "negatives": 3, "dataset": 15},
            "sessions": [s1, s2]}


LOCK = threading.Lock()
STATE = initial_state()
START = time.time()
FLAKY = False
NO_FRAME = False
HIDDEN: dict[tuple, int] = {}  # (session, track) -> noch fehlende Abfragen (nur --flaky)


def flaky_swap_suggestion(c: dict) -> None:
    """Dauerhaft einen anderen Vorschlag setzen (bisheriger wandert zu den Alternativen)."""
    if not c["candidates"]:
        return
    new = random.choice(c["candidates"])
    info = BY_KEY[new["key"]]
    old_key = c["key"]
    c["candidates"] = [x for x in c["candidates"] if x["key"] != new["key"]]
    if old_key in BY_KEY:
        c["candidates"].append(cand(old_key, round(max(0.5, c["similarity"] - 0.02), 2)))
    c.update(key=new["key"], name=info["name"], set_name=info["set_name"], set_code=info["set_code"],
             number=info["number"], image=img_url(new["key"]), similarity=new["similarity"])


def flaky_cards(sid: str, cards: list, origs: list) -> list:
    out = []
    for c, orig in zip(cards, origs):
        if not orig["needs_confirmation"]:
            out.append(c)
            continue
        k = (sid, c["track"])
        if HIDDEN.get(k, 0) > 0:
            HIDDEN[k] -= 1
            continue
        if random.random() < 0.12:
            HIDDEN[k] = random.randint(1, 3) - 1
            continue
        if random.random() < 0.04:
            flaky_swap_suggestion(orig)
            c.update({x: orig[x] for x in ("key", "name", "set_name", "set_code", "number", "image",
                                            "similarity", "candidates")})
            c["candidates"] = copy.deepcopy(orig["candidates"])
        if random.random() < 0.15:
            c["state"] = random.choice(["uncertain", "failed", "rejected"])
        if random.random() < 0.08:
            c["needs_confirmation"] = False
        if random.random() < 0.10:
            c["recheck"] = True
        if c["label"] == "D" and sid == "s1":
            c["sticky"] = True
        out.append(c)
    return out


def find(session_id, track):
    for s in STATE["sessions"]:
        if s["id"] == session_id:
            for c in s["cards"]:
                if c["track"] == track:
                    return s, c
            return s, None
    return None, None


def public_state() -> dict:
    now = time.time()
    tick = int((now - START) / 2)  # Kamera-Ausschnitt ändert sich alle 2 s
    out = copy.deepcopy(STATE)
    for s, orig in zip(out["sessions"], STATE["sessions"]):
        if orig["_hl_until"] < now:
            orig["highlight"] = None
            s["highlight"] = None
        for k in [k for k in s if k.startswith("_")]:
            s.pop(k)
        for c, oc in zip(s["cards"], orig["cards"]):
            if oc.get("_ident_at") and oc["_ident_at"] <= now:
                finish_identify(oc)
                c.update(copy.deepcopy(oc))
            c["crop"] = f"/api/crop/{s['id']}/{c['track']}?v={c['_v'] + tick}"
        quads = {c["track"]: c.get("_quad") for c in s["cards"]}
        for c in s["cards"]:
            for k in [k for k in c if k.startswith("_")]:
                c.pop(k)
        if FLAKY:
            s["cards"] = flaky_cards(s["id"], s["cards"], orig["cards"])
        if not NO_FRAME:
            s["frame"] = {"w": FRAME_W, "h": FRAME_H, "url": f"/api/frame/{s['id']}"}
            s["tracks"] = [{"label": c["label"], "track": c["track"], "state": c["state"],
                            "certain": bool(c["confirmed"] or (c["state"] == "identified"
                                                               and not c["needs_confirmation"])),
                            "quad": quads[c["track"]]}
                           for c in s["cards"] if quads.get(c["track"]) and c["state"] != "rejected"]
    return out


def free_label(s: dict) -> str:
    used = {c["label"] for c in s["cards"]}
    for ch in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        if ch not in used:
            return ch
    return "?"


def new_track(s: dict, quad: list, slot: int | None) -> dict:
    """Neue Karte (wird gerade erkannt, nach ~2,5 s identifiziert)."""
    track = 1 + max([c["track"] for sess in STATE["sessions"] for c in sess["cards"]] + [100])
    c = card(free_label(s), track, None, game="pokemon", state="identifying", sim=0.0, price=False)
    c.update(_quad=quad, _slot=slot, _ident_at=time.time() + 2.5)
    s["cards"].append(c)
    return c


def finish_identify(c: dict) -> None:
    key = random.choice(CATALOG)["key"]
    info = BY_KEY[key]
    sim = round(random.uniform(0.80, 0.97), 2)
    needs = sim < 0.88
    c.update(key=key, name=info["name"], set_name=info["set_name"], set_code=info["set_code"],
             number=info["number"], image=img_url(key), game=key.split("|", 1)[0], similarity=sim,
             state="uncertain" if needs else "identified", needs_confirmation=needs,
             reason="geringe Ähnlichkeit" if needs else None,
             price=None if needs else fake_price(key), _ident_at=None)


def probe(s: dict, body: dict) -> dict:
    """Gezielte Suche (unter LOCK): Klick auf übersehene Karte → gefunden, Rahmen → immer gefunden."""
    if body.get("rect"):
        x0, y0, x1, y1 = (min(1.0, max(0.0, float(v))) for v in body["rect"])
        x0, x1 = sorted((x0, x1))
        y0, y1 = sorted((y0, y1))
        c = new_track(s, [[x0, y0], [x1, y0], [x1, y1], [x0, y1]], None)
        return {"ok": True, "found": True, "label": c["label"], "track": c["track"], "how": "rahmen"}
    x, y = float(body.get("x", -1)), float(body.get("y", -1))
    for c in s["cards"]:
        if c.get("_quad") and point_in_quad(x, y, c["_quad"]):
            c["_v"] += 100
            return {"ok": True, "found": True, "label": c["label"], "track": c["track"], "how": "vorhanden"}
    used = {c.get("_slot") for c in s["cards"]}
    for i in s["_drawn"]:
        if i not in used and point_in_quad(x, y, slot_quad(i)):
            c = new_track(s, slot_quad(i), i)
            return {"ok": True, "found": True, "label": c["label"], "track": c["track"], "how": "detektor"}
    return {"ok": True, "found": False, "error": "Keine Karte gefunden – Rahmen um die Karte ziehen"}


def svg_frame(s: dict, width: int) -> bytes:
    """Generiertes Kamerabild: dunkler Tisch, graue Karten auf den belegten Plätzen, laufende Uhr."""
    width = max(80, min(1280, width))
    height = round(width * FRAME_H / FRAME_W)
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
             f'viewBox="0 0 {FRAME_W} {FRAME_H}">',
             f'<rect width="{FRAME_W}" height="{FRAME_H}" fill="#3b3430"/>',
             f'<rect x="40" y="40" width="{FRAME_W - 80}" height="{FRAME_H - 80}" rx="30" fill="#4a423c"/>']
    quads = [slot_quad(i) for i in s["_drawn"]]
    quads += [c["_quad"] for c in s["cards"] if c.get("_quad") and c.get("_slot") is None]
    for q in quads:
        pts = " ".join(f"{x * FRAME_W:.0f},{y * FRAME_H:.0f}" for x, y in q)
        parts.append(f'<polygon points="{pts}" fill="#9a9a9a" stroke="#d8d8d8" stroke-width="6"/>')
    sx = 960 + 700 * math.sin(time.time() / 3)
    parts.append(f'<circle cx="{sx:.0f}" cy="1010" r="18" fill="#f0b429" opacity=".7"/>')
    parts.append(f'<text x="1880" y="80" text-anchor="end" font-family="Segoe UI,Arial" font-size="48" '
                 f'fill="#ddd">{escape(s["source"])} · {time.strftime("%H:%M:%S")}</text></svg>')
    return "".join(parts).encode("utf-8")


# ---------- Bilder (SVG) ----------
def svg_card(title: str, sub: str, hue: int, dark: bool = False) -> bytes:
    bg = f"hsl({hue},45%,{22 if dark else 38}%)"
    fg = f"hsl({hue},60%,80%)"
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="244" height="340" viewBox="0 0 244 340">
<rect width="244" height="340" rx="12" fill="{bg}"/>
<rect x="12" y="12" width="220" height="316" rx="8" fill="none" stroke="{fg}" stroke-width="3" opacity=".6"/>
<rect x="24" y="48" width="196" height="140" rx="4" fill="{fg}" opacity=".18"/>
<text x="122" y="36" text-anchor="middle" font-family="Segoe UI,Arial" font-size="18" font-weight="700" fill="{fg}">{escape(title)}</text>
<text x="122" y="226" text-anchor="middle" font-family="Segoe UI,Arial" font-size="14" fill="{fg}">{escape(sub)}</text>
</svg>"""
    return svg.encode("utf-8")


def hue_of(s: str) -> int:
    return int(hashlib.md5(s.encode()).hexdigest()[:4], 16) % 360


class Handler(BaseHTTPRequestHandler):
    server_version = "DockMock/1.0"

    def log_message(self, fmt, *args):  # leiser
        if "/api/state" not in (args[0] if args else ""):
            super().log_message(fmt, *args)

    def send(self, code: int, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def json(self, obj, code: int = 200):
        self.send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def do_GET(self):
        u = urllib.parse.urlsplit(self.path)
        path = u.path
        if path in ("/", "/index.html", "/dock.html"):
            try:
                self.send(200, DOCK_HTML.read_bytes(), "text/html; charset=utf-8")
            except OSError:
                self.send(404, b"dock.html fehlt", "text/plain; charset=utf-8")
            return
        if path == "/api/state":
            if FLAKY:
                r = random.random()
                if r < 0.06:
                    return self.json({"error": "simulierter Fehler"}, 500)
                if r < 0.11:
                    time.sleep(4.0)
            with LOCK:
                self.json(public_state())
            return
        if path == "/api/search":
            q = urllib.parse.parse_qs(u.query)
            term = (q.get("q", [""])[0]).strip().lower()
            game = q.get("game", [""])[0]
            limit = int(q.get("limit", ["20"])[0] or 20)
            res = [dict(c, image=img_url(c["key"])) for c in CATALOG
                   if term and (term in c["name"].lower() or term in c["set_name"].lower()
                                or term in c["set_code"].lower())
                   and (not game or c["key"].startswith(game + "|"))]
            time.sleep(0.15)
            self.json({"results": res[:limit]})
            return
        if path.startswith("/api/card-image/"):
            parts = path.split("/")
            if len(parts) != 5:
                return self.send(404, b"", "text/plain")
            game, cid = parts[3], urllib.parse.unquote(parts[4])
            key = f"{game}|{cid}"
            c = BY_KEY.get(key)
            if not c or "missing" in cid:
                return self.send(404, b"", "text/plain")
            return self.send(200, svg_card(c["name"], f"{c['set_code']} {c['number']}", hue_of(key)),
                             "image/svg+xml")
        if path.startswith("/api/frame/"):
            parts = path.split("/")
            w = urllib.parse.parse_qs(u.query).get("w", ["480"])[0]
            with LOCK:
                s, _ = find(parts[3] if len(parts) == 4 else "", None)
                if not s or NO_FRAME:
                    return self.send(404, b"", "text/plain")
                body = svg_frame(s, int(w) if w.isdigit() else 480)
            return self.send(200, body, "image/svg+xml")
        if path.startswith("/api/crop/"):
            parts = path.split("/")
            if len(parts) != 5:
                return self.send(404, b"", "text/plain")
            v = urllib.parse.parse_qs(u.query).get("v", ["0"])[0]
            hue = (hue_of(parts[3] + parts[4]) + int(v) * 7) % 360
            return self.send(200, svg_card("Kamera", f"{parts[3]}/{parts[4]} · v{v}", hue, dark=True),
                             "image/svg+xml")
        self.send(404, b"nicht gefunden", "text/plain; charset=utf-8")

    def do_POST(self):
        path = urllib.parse.urlsplit(self.path).path
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}")
        except (ValueError, json.JSONDecodeError):
            return self.json({"ok": False, "error": "ungültiges JSON"}, 400)
        time.sleep(0.2 if path != "/api/highlight" else 0)
        if path == "/api/probe":
            time.sleep(0.9)  # die echte Suche dauert etwas
            with LOCK:
                s, _ = find(body.get("session"), None)
                if not s or NO_FRAME:
                    return self.json({"ok": False, "error": "Kein Kamerabild"}, 404)
                return self.json(probe(s, body))
        with LOCK:
            if path == "/api/highlight":
                s, _ = find(body.get("session"), None)
                if not s:
                    return self.json({"ok": False, "error": "unbekannte Sitzung"}, 404)
                s["highlight"] = body.get("label")
                s["_hl_until"] = time.time() + 3.0
                return self.json({"ok": True})
            if path == "/api/forget":
                STATE["learned"]["references"] = max(0, STATE["learned"]["references"] - 1)
                return self.json({"ok": True})
            s, c = find(body.get("session"), body.get("track"))
            if not c:
                return self.json({"ok": False, "error": "Karte nicht (mehr) vorhanden"}, 404)
            if FLAKY and path in ("/api/confirm", "/api/reject") and HIDDEN.get((s["id"], c["track"]), 0) > 0:
                return self.json({"ok": False, "error": "Karte nicht mehr im Bild"}, 409)
            if path == "/api/confirm":
                key = body.get("key")
                info = BY_KEY.get(key)
                if not info:
                    return self.json({"ok": False, "error": "unbekannter Kartenschlüssel"}, 400)
                if "fail" in (c.get("name") or "").lower():
                    return self.json({"ok": False, "error": "Simulierter Fehler"}, 500)
                old_key = c["key"]
                c.update(key=key, name=info["name"], set_name=info["set_name"], set_code=info["set_code"],
                         number=info["number"], image=img_url(key), needs_confirmation=False, confirmed=True,
                         method="bestätigt", state="identified", reason=None, price=fake_price(key),
                         game=key.split("|", 1)[0],
                         cardmarket_url=f"https://www.cardmarket.com/de/Products?idProduct={abs(hash(key)) % 900000}")
                c["candidates"] = [x for x in c["candidates"] if x["key"] != key]
                if old_key and old_key != key and old_key in BY_KEY:
                    c["candidates"].insert(0, cand(old_key, c["similarity"]))
                STATE["learned"]["references"] += 1
                STATE["learned"]["dataset"] += 1
                return self.json({"ok": True})
            if path == "/api/reject":
                s["cards"].remove(c)
                if s["highlight"] == c["label"]:
                    s["highlight"] = None
                STATE["learned"]["negatives"] += 1
                STATE["learned"]["dataset"] += 1
                return self.json({"ok": True})
            if path == "/api/rotate":  # ↻: Lage drehen → neuer Kamera-Ausschnitt
                c["_v"] += 1
                return self.json({"ok": True})
            if path == "/api/dismiss":  # ✕: ohne Gründe ausblenden, nichts lernen
                s["cards"].remove(c)
                if s["highlight"] == c["label"]:
                    s["highlight"] = None
                return self.json({"ok": True})
            if path == "/api/refresh":
                c["_v"] += 100
                if c["key"]:
                    p = fake_price(c["key"] + str(random.random()))
                    c["price"] = p if not c["needs_confirmation"] else None
                return self.json({"ok": True})
        self.json({"ok": False, "error": "unbekannter Pfad"}, 404)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=47839)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--flaky", action="store_true",
                    help="unruhige Engine simulieren (fehlende Karten, State-Wechsel, 500er, Verzögerungen)")
    ap.add_argument("--no-frame", action="store_true", help="kein Kamerabild (frame/tracks fehlen, alte Engine)")
    a = ap.parse_args()
    global FLAKY, NO_FRAME
    FLAKY = a.flaky
    NO_FRAME = a.no_frame
    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    print(f"Dock-Mock läuft: http://{a.host}:{a.port}/{'  [flaky]' if FLAKY else ''}  (Strg+C beendet)", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
