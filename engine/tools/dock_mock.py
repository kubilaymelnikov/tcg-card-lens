"""Mock-Server für das Bestätigungs-Dock (engine/dock/dock.html).

Simuliert die HTTP-API aus docs/DOCK.md mit Fake-Daten, damit die Oberfläche ohne Engine/OBS
entwickelt werden kann. Nur Python-Standardbibliothek.

    python tools/dock_mock.py [--port 47839] [--host 127.0.0.1] [--flaky]

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
    return {"engine": "0.2.0-mock", "status": "bereit",
            "learned": {"references": 12, "negatives": 3, "dataset": 15},
            "sessions": [s1, s2]}


LOCK = threading.Lock()
STATE = initial_state()
START = time.time()
FLAKY = False
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
        s.pop("_hl_until", None)
        for c in s["cards"]:
            v = c.pop("_v") + tick
            c["crop"] = f"/api/crop/{s['id']}/{c['track']}?v={v}"
        if FLAKY:
            s["cards"] = flaky_cards(s["id"], s["cards"], orig["cards"])
    return out


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
    a = ap.parse_args()
    global FLAKY
    FLAKY = a.flaky
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
