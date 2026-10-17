"""HTTP-Server für das OBS-Browser-Dock (Bestätigen, Hervorheben, Kartenliste) – API: docs/DOCK.md.

Läuft im Engine-Prozess neben dem TCP-Server für das Plugin und greift direkt auf die Sitzungen zu. Nur 127.0.0.1.
Änderungen am Tracking laufen über `CardPipeline.post()` im Verarbeitungs-Thread der jeweiligen Sitzung.
"""
from __future__ import annotations

import json
import os
import threading
import time
import traceback
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlparse

import cv2
import numpy as np

from cardengine import __version__
from cardengine.card_database.catalog import image_path
from cardengine.geometry import rectify

HERE = os.path.dirname(os.path.abspath(__file__))
DOCK_HTML = os.path.join(HERE, "dock", "dock.html")
CM_GAME = {"pokemon": "Pokemon", "mtg": "Magic", "ygo": "YuGiOh"}
CM_LANGS = {"de", "en", "fr", "it", "es"}
CROP_SIZE = (244, 340)
HIGHLIGHT_S = 3.0
INSTANCE = uuid.uuid4().hex[:12]  # je Engine-Start neu: das Dock verwirft dann alte Kacheln
ASK_GONE_S = 20.0  # gestellte Frage bleibt so lange stehen, wenn die Karte nicht mehr erkannt wird
ASK_MIN_SHOWN_S = 2.0  # kürzer gezeigte Fragen bleiben nicht als „nicht im Bild“ stehen
ASK_RELEASE_S = 10.0  # so lange muss eine gefragte Karte sicher erkannt sein, bevor die Frage von selbst endet


def card_key(game: str, card_id: str) -> str:
    return f"{game}|{card_id}"


class DockApi:
    """Zustand und Aktionen für das Dock; `shared` ist server.Shared."""

    def __init__(self, shared):
        self.shared = shared
        # Gestellte Fragen (Sitzung, Track) -> {"card": letzter Inhalt, "seen": Zeit, "certain_since": Zeit|None}.
        # Eine Frage verschwindet nicht, nur weil die Karte kurz neu geprüft wird oder aus dem Bild ist.
        self._asked: dict[tuple[str, int], dict] = {}
        self._asked_lock = threading.Lock()

    # ---------------------------------------------------------------- Hilfen
    @property
    def catalog(self):
        ident = self.shared.identifier
        return ident.catalog if ident is not None else None

    def _record(self, key: str):
        if not key or "|" not in key or self.catalog is None:
            return None
        game, cid = key.split("|", 1)
        return self.catalog.get(game, cid)

    def _session(self, sid: str):
        return next((s for s in list(self.shared.session_list) if s.sid == sid), None)

    @staticmethod
    def _track(sess, track_id):
        if sess is None or sess.pipeline is None:
            return None
        return next((t for t in list(sess.pipeline.tracker.tracks) if t.id == int(track_id)), None)

    def _cardmarket_url(self, game: str, name: str, pid: int | None, lang: str) -> str:
        lang = lang if lang in CM_LANGS else "en"
        g = CM_GAME.get(game, "Magic")
        if pid:
            return f"https://www.cardmarket.com/{lang}/{g}/Products?idProduct={pid}"
        return f"https://www.cardmarket.com/{lang}/{g}/Products/Search?searchString={quote(name)}"

    @staticmethod
    def _image_url(key: str) -> str:
        game, cid = key.split("|", 1)
        return f"/api/card-image/{game}/{quote(cid, safe='')}"

    def _candidate(self, key: str, sim: float | None, lang: str) -> dict | None:
        rec = self._record(key)
        if rec is None:
            return None
        return {"key": key, "name": self.shared.names.name_nowait(rec, lang), "set_name": rec.set_name,
                "set_code": rec.set_code, "number": rec.printed_number,
                "similarity": None if sim is None else round(float(sim), 3), "image": self._image_url(key)}

    # ---------------------------------------------------------------- Zustand
    def state(self) -> dict:
        self.shared.dock_seen = time.time()
        learned = self.shared.learned.counts() if self.shared.learned is not None else {}
        sessions = []
        for sess in list(self.shared.session_list):
            if sess.pipeline is None:
                sessions.append({"id": sess.sid, "source": sess.source, "highlight": None, "cards": []})
                continue
            cards = self._sticky_cards(sess)
            cards.sort(key=lambda c: (len(c["label"]), c["label"]))
            sessions.append({"id": sess.sid, "source": sess.source, "highlight": sess.current_highlight(), "cards": cards})
        try:
            rev = str(int(os.path.getmtime(DOCK_HTML)))
        except OSError:
            rev = None
        return {"engine": __version__, "status": self.shared.status, "learned": learned, "sessions": sessions,
                "dock_rev": rev, "instance": INSTANCE}

    def _sticky_cards(self, sess) -> list[dict]:
        now = time.time()
        out, alive = [], set()
        with self._asked_lock:
            for t in list(sess.pipeline.tracker.tracks):
                k = (sess.sid, t.id)
                alive.add(k)
                c = self._card(sess, t)
                asked = self._asked.get(k)
                if t.hidden:
                    self._asked.pop(k, None)
                    continue
                if t.user_rejected or t.confirmed:
                    self._asked.pop(k, None)
                elif c is not None and c["needs_confirmation"]:
                    first = asked["first"] if asked else now
                    self._asked[k] = {"card": c, "seen": now, "certain_since": None, "first": first}
                    c["sticky"] = True
                elif asked is not None:
                    asked["seen"] = now
                    if c is None:  # wird gerade neu geprüft (oder automatisch verworfen): letzte Frage zeigen
                        c = dict(asked["card"], recheck=True, sticky=True, state=t.state)
                    elif c["uncertain"]:  # weiterhin unsicher (z. B. gerade bewegt): Frage bleibt offen
                        c = dict(c, needs_confirmation=True, sticky=True)
                        asked["card"] = c
                    else:  # inzwischen sicher erkannt: Frage noch kurz offen lassen, dann von selbst beenden
                        asked["certain_since"] = asked["certain_since"] or now
                        if now - asked["certain_since"] < ASK_RELEASE_S:
                            c["sticky"] = True
                        else:
                            self._asked.pop(k, None)
                if c is not None:
                    out.append(c)
            # Karten, die gerade gar nicht erkannt werden: Frage noch eine Weile stehen lassen
            for k, asked in list(self._asked.items()):
                if k[0] != sess.sid or k in alive:
                    continue
                if now - asked["seen"] > ASK_GONE_S or asked["seen"] - asked["first"] < ASK_MIN_SHOWN_S:
                    del self._asked[k]  # lange weg – oder nur kurz aufgeblitzt (z. B. beim Verschieben)
                else:
                    out.append(dict(asked["card"], recheck=True, sticky=True, gone=True))
        return out

    def _resolve(self, sid: str, track_id) -> None:
        with self._asked_lock:
            self._asked.pop((sid, int(track_id)), None)

    def _card(self, sess, t) -> dict | None:
        ident = t.identity
        if ident is None and t.state != "failed":
            return None  # noch in Erkennung oder verworfen
        lang = sess.settings.get("display_language", "de")
        label = sess.label_for(t.id)
        crop_v = (t.orient_rev * 1_000_000 + sum(t.unlocks.values()) * 1000
                  + (0 if t.finalized else int(time.time() // 2) % 1000))
        card = {
            "label": label, "track": t.id, "state": t.state, "confirmed": bool(t.confirmed),
            "uncertain": not t.confirmed and (ident is None or not ident.certain),
            # Gefragt wird erst, wenn die Karte ruhig liegt (nicht beim Verschieben oder in der Hand)
            "needs_confirmation": not t.confirmed and (ident is None or not ident.certain)
                                  and (t.locked or t.still_frames >= 15),
            "game": ident.game if ident else t.game, "key": None, "name": "Unbekannte Karte",
            "set_name": None, "set_code": None, "number": None, "similarity": None,
            "method": None, "reason": "nicht erkannt" if ident is None else ident.reason,
            "image": None, "crop": f"/api/crop/{sess.sid}/{t.id}?v={crop_v}",
            "cardmarket_url": None, "price": None, "candidates": [],
        }
        if ident is None:
            return card
        key = card_key(ident.game, ident.card_id) if ident.card_id else None
        card.update({
            "key": key, "name": getattr(ident, "display_name", None) or ident.name, "set_name": ident.set_name,
            "set_code": ident.set_code, "number": ident.number, "similarity": round(float(ident.similarity), 3),
            "method": ident.method, "image": self._image_url(key) if key else None,
        })
        price = ident.price
        pid = getattr(price, "id_product", None) if price is not None else None
        card["cardmarket_url"] = self._cardmarket_url(ident.game, ident.name, pid, lang)
        q = price.primary if price is not None else None
        if q is not None and q.main is not None and ident.certain:
            card["price"] = {"value": q.main, "currency": q.currency, "variant": q.variant,
                             "change_30d": price.changes.get("30T"), "updated": q.as_of}
        # Alternativen: Treffer der Suche (mit Ähnlichkeit), ersatzweise die Kandidatenliste
        alts = list(getattr(ident, "alternatives", None) or [])
        if not alts:
            alts = [(k if "|" in k else card_key(ident.game, k), None) for k in (ident.candidates or [])]
        seen = {key}
        for k, sim in alts:
            if k in seen:
                continue
            seen.add(k)
            if (c := self._candidate(k, sim, lang)) is not None:
                card["candidates"].append(c)
            if len(card["candidates"]) >= 8:
                break
        return card

    # ---------------------------------------------------------------- Aktionen
    def _view(self, sess, t, key: str | None):
        """(Embedding, entzerrtes Bild, Lage) des aktuellen Kamerabilds; Lage passend zur Karte `key` wählen."""
        frame = sess.last_frame_img
        ident = self.shared.identifier
        if frame is None or ident is None:
            raise RuntimeError("kein Kamerabild")
        corners = t.corners.copy()
        if t.identity is not None and getattr(t.identity, "rotate180", None) is not None and (
                not key or key == card_key(t.identity.game, t.identity.card_id or "")):
            rot = bool(t.identity.rotate180)
            emb, rect = ident.embed_view(frame, corners, rot)
            return emb, rect, rot
        # Lage unbekannt: beide einbetten, die zur Referenz der Karte ähnlichere nehmen
        views = [ident.embed_view(frame, corners, r) for r in (False, True)]
        ref = ident.index.vector(key) if key else None
        if ref is None:
            return (*views[0], False)
        rot = float(np.dot(views[1][0], ref)) > float(np.dot(views[0][0], ref))
        return (*views[int(rot)], rot)

    def confirm(self, sid: str, track_id, key: str) -> dict:
        from cardengine.recognition.identifier import Identity

        sess = self._session(sid)
        t = self._track(sess, track_id)
        rec = self._record(key)
        if t is None:
            return {"ok": False, "error": "Karte gerade nicht im Bild – kurz warten oder Karte ruhig hinlegen"}
        if rec is None:
            return {"ok": False, "error": "Karte nicht im Katalog"}
        old = t.identity
        emb, rect, rot = self._view(sess, t, key)
        if self.shared.learned is not None:
            proposed = card_key(old.game, old.card_id) if old is not None and old.card_id else None
            self.shared.learned.add_reference(key, emb, rect, {
                "proposed_key": proposed, "similarity": None if old is None else float(old.similarity),
                "method": None if old is None else old.method, "source": sess.source, "game": rec.game,
                "user_turns": int(getattr(t, "user_turns", 0)),  # Karte vorher im Dock gedreht (Lage war falsch)
            })
        identity = Identity(game=rec.game, card_id=rec.id, name=rec.name, set_code=rec.set_code,
                            set_name=rec.set_name, number=rec.printed_number, certain=True, similarity=1.0,
                            method="bestätigt")
        identity.rotate180 = rot
        identity.turns = 2 if rot else 0
        identity.alternatives = list(getattr(old, "alternatives", None) or [])
        if self.shared.prices is not None:
            try:
                identity.price = self.shared.prices.info(rec)
            except Exception as err:
                print(f"[dock] Preis für {key} fehlgeschlagen: {err}")
        sess._localize(identity)
        sess.pipeline.confirm(t.id, identity)
        self._resolve(sid, t.id)
        return {"ok": True}

    def reject(self, sid: str, track_id) -> dict:
        sess = self._session(sid)
        t = self._track(sess, track_id)
        if t is None:  # Karte nicht mehr im Bild: nur die offene Frage schließen
            self._resolve(sid, track_id)
            return {"ok": True}
        if self.shared.learned is not None:
            try:
                emb, rect, _ = self._view(sess, t, None)
                old = t.identity
                self.shared.learned.add_negative(emb, rect, {
                    "proposed_key": card_key(old.game, old.card_id) if old is not None and old.card_id else None,
                    "source": sess.source,
                })
            except Exception as err:
                print(f"[dock] Negativbeispiel nicht gespeichert: {err}")
        sess.pipeline.reject(t.id)
        self._resolve(sid, t.id)
        return {"ok": True}

    def dismiss(self, sid: str, track_id) -> dict:
        sess = self._session(sid)
        t = self._track(sess, track_id)
        self._resolve(sid, track_id)
        if t is not None:
            sess.pipeline.dismiss(t.id)
        return {"ok": True}

    def rotate(self, sid: str, track_id) -> dict:
        sess = self._session(sid)
        t = self._track(sess, track_id)
        if t is None:
            return {"ok": False, "error": "Karte gerade nicht im Bild – kurz warten oder Karte ruhig hinlegen"}
        sess.pipeline.rotate(t.id)
        return {"ok": True}

    def refresh(self, sid: str, track_id) -> dict:
        sess = self._session(sid)
        t = self._track(sess, track_id)
        if t is None:
            return {"ok": False, "error": "Karte nicht mehr im Bild"}
        sess.pipeline.refresh(t.id)
        return {"ok": True}

    def highlight(self, sid: str, label: str | None) -> dict:
        sess = self._session(sid)
        if sess is None:
            return {"ok": False, "error": "Quelle nicht verbunden"}
        sess.highlight = (label, time.time() + HIGHLIGHT_S) if label else None
        return {"ok": True}

    def forget(self, key: str) -> dict:
        if self.shared.learned is None:
            return {"ok": False, "error": "Lernspeicher nicht geladen"}
        return {"ok": True, "removed": self.shared.learned.forget(key)}

    def search(self, q: str, game: str | None, limit: int) -> dict:
        cat = self.catalog
        q = (q or "").strip()
        if cat is None or len(q) < 2:
            return {"results": []}
        sql = "WHERE name LIKE ?" + (" AND game = ?" if game else "") + " ORDER BY (name = ?) DESC, released DESC LIMIT ?"
        args = [f"%{q}%"] + ([game] if game else []) + [q, int(limit)]
        recs = cat._rows(sql, tuple(args))
        return {"results": [{"key": card_key(r.game, r.id), "name": r.name, "set_name": r.set_name,
                             "set_code": r.set_code, "number": r.printed_number,
                             "image": self._image_url(card_key(r.game, r.id))} for r in recs]}

    def crop(self, sid: str, track_id) -> bytes | None:
        sess = self._session(sid)
        t = self._track(sess, track_id)
        if t is None or sess.last_frame_img is None:
            return None
        rot = bool(getattr(t.identity, "rotate180", False)) if t.identity is not None else False
        img = cv2.resize(rectify(sess.last_frame_img, t.corners, rotate180=rot), CROP_SIZE, interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 85])
        return buf.tobytes() if ok else None


# -------------------------------------------------------------------- HTTP
def _handler(api: DockApi):
    class Handler(BaseHTTPRequestHandler):
        server_version = "TCGCardLensDock"

        def log_message(self, *a):  # kein Zugriffslog
            pass

        def _send(self, code: int, body: bytes, ctype: str, extra: dict | None = None):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, code: int = 200):
            self._send(code, json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8"), "application/json; charset=utf-8")

        def do_GET(self):
            try:
                url = urlparse(self.path)
                parts = [p for p in url.path.split("/") if p]
                if url.path in ("/", "/index.html"):
                    with open(DOCK_HTML, "rb") as f:
                        return self._send(200, f.read(), "text/html; charset=utf-8")
                if url.path == "/api/state":
                    return self._json(api.state())
                if url.path == "/api/search":
                    qs = parse_qs(url.query)
                    return self._json(api.search(qs.get("q", [""])[0], qs.get("game", [None])[0] or None,
                                                 min(50, int(qs.get("limit", ["20"])[0]))))
                if parts[:2] == ["api", "crop"] and len(parts) == 4:
                    data = api.crop(parts[2], parts[3])
                    return self._send(200, data, "image/jpeg") if data else self._send(404, b"", "text/plain")
                if parts[:2] == ["api", "card-image"] and len(parts) == 4:
                    from urllib.parse import unquote

                    game, cid = parts[2], unquote(parts[3])
                    path = image_path(game, cid)
                    if os.path.exists(path):
                        with open(path, "rb") as f:
                            return self._send(200, f.read(), "image/jpeg", {"Cache-Control": "max-age=86400"})
                    rec = api._record(card_key(game, cid))
                    if rec is not None and rec.image_url:
                        self.send_response(302)
                        self.send_header("Location", rec.image_url)
                        self.end_headers()
                        return
                    return self._send(404, b"", "text/plain")
                self._send(404, b"nicht gefunden", "text/plain")
            except Exception as err:
                traceback.print_exc()
                self._json({"ok": False, "error": str(err)}, 500)

        def do_POST(self):
            try:
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}") if n else {}
                path = urlparse(self.path).path
                if path == "/api/confirm":
                    return self._json(api.confirm(body["session"], body["track"], body["key"]))
                if path == "/api/reject":
                    return self._json(api.reject(body["session"], body["track"]))
                if path == "/api/rotate":
                    return self._json(api.rotate(body["session"], body["track"]))
                if path == "/api/dismiss":
                    return self._json(api.dismiss(body["session"], body["track"]))
                if path == "/api/refresh":
                    return self._json(api.refresh(body["session"], body["track"]))
                if path == "/api/highlight":
                    return self._json(api.highlight(body["session"], body.get("label")))
                if path == "/api/forget":
                    return self._json(api.forget(body["key"]))
                self._json({"ok": False, "error": "unbekannte Aktion"}, 404)
            except Exception as err:
                traceback.print_exc()
                self._json({"ok": False, "error": str(err)}, 500)

    return Handler


def start_dock_server(shared, host: str = "127.0.0.1", port: int = 47830) -> ThreadingHTTPServer | None:
    try:
        httpd = ThreadingHTTPServer((host, port), _handler(DockApi(shared)))
    except OSError as err:
        print(f"[dock] Port {port} belegt – Dock nicht verfügbar ({err})", flush=True)
        return None
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True, name="dock-http").start()
    print(f"[dock] Dock unter http://{host}:{port}/", flush=True)
    return httpd
