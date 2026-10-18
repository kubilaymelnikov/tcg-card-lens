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
SESSION_HTML = os.path.join(HERE, "dock", "session.html")  # Session-Anzeige (OBS-Browser-Quelle, Pack-Opening)
CM_GAME = {"pokemon": "Pokemon", "mtg": "Magic", "ygo": "YuGiOh"}
CM_LANGS = {"de", "en", "fr", "it", "es"}
CROP_SIZE = (244, 340)
HIGHLIGHT_S = 3.0
LIKELY_CARD_SCORE = 0.7  # Detektor-Sicherheit, ab der eine nicht erkannte Stelle im Dock trotzdem nachgefragt wird
INSTANCE = uuid.uuid4().hex[:12]  # je Engine-Start neu: das Dock verwirft dann alte Kacheln
ASK_GONE_S = 20.0  # gestellte Frage bleibt so lange stehen, wenn die Karte nicht mehr erkannt wird
ASK_MIN_SHOWN_S = 2.0  # kürzer gezeigte Fragen bleiben nicht als „nicht im Bild“ stehen
ASK_RELEASE_S = 10.0  # so lange muss eine gefragte Karte sicher erkannt sein, bevor die Frage von selbst endet


def _split_query(q: str) -> tuple[str, str | None, str | None]:
    """„Glurak 4/102“ → ("Glurak", "4", "102"); „Sol Ring 409“ → ("Sol Ring", "409", None)."""
    import re

    m = re.search(r"(?:^|\s)#?([A-Za-z]{0,6}\d+[A-Za-z]?)\s*(?:/\s*(\d+))?\s*$", q)
    if not m:
        return q, None, None
    return q[:m.start()].strip(), m.group(1), m.group(2)


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
    def state(self, overlay: bool = False) -> dict:
        if not overlay:  # Overlay-Seiten (Session-Anzeige) zählen nicht als „Dock offen“
            self.shared.dock_seen = time.time()
        learned = self.shared.learned.counts() if self.shared.learned is not None else {}
        sessions = []
        for sess in list(self.shared.session_list):
            if sess.pipeline is None:
                sessions.append({"id": sess.sid, "source": sess.source, "highlight": None, "cards": []})
                continue
            cards = self._sticky_cards(sess)
            cards.sort(key=lambda c: (len(c["label"]), c["label"]))
            entry = {"id": sess.sid, "source": sess.source, "highlight": sess.current_highlight(), "cards": cards}
            entry.update(self._preview(sess))
            entry["settings"] = {"auto_detect": bool(sess.settings.get("auto_detect", True)),
                                 "pack_enabled": bool(sess.settings.get("pack_enabled", False)),
                                 "pack_threshold": float(sess.settings.get("pack_threshold", 10.0))}
            entry["pack"] = self._pack_state(sess)
            sessions.append(entry)
        try:
            rev = str(int(os.path.getmtime(DOCK_HTML)))
        except OSError:
            rev = None
        return {"engine": __version__, "status": self.shared.status, "learned": learned, "sessions": sessions,
                "dock_rev": rev, "instance": INSTANCE}

    # ---------------------------------------------------------------- Pack-Opening (Session-Liste)
    def _pack(self, sid: str):
        sess = self._session(sid)
        if sess is None:
            raise KeyError("Quelle nicht verbunden")
        return sess, self.shared.pack_sessions.get(sess.source)

    def _pack_state(self, sess) -> dict | None:
        """Session der Quelle fürs Dock; None, wenn der Modus aus ist und noch nichts gesammelt wurde."""
        if not hasattr(self.shared, "pack_sessions"):
            return None
        with self.shared.pack_lock:
            ps = self.shared.pack_sessions.get(sess.source)
            enabled = bool(sess.settings.get("pack_enabled"))
            if ps is None and not enabled:
                return None
            data = ps.to_dict() if ps is not None else {"started": None, "threshold": None, "total": 0.0,
                                                        "count": 0, "hits": 0, "cards": []}
        for c in data["cards"]:
            c["image"] = self._image_url(c["key"])
        data["enabled"] = enabled
        data["csv"] = f"/api/pack/csv/{sess.sid}"
        return data

    def pack_reset(self, sid: str) -> dict:
        """Neue Session; eine nicht leere alte Session wird vorher als CSV gesichert."""
        from cardengine.pack_opening import save_csv

        sess, ps = self._pack(sid)
        saved = None
        with self.shared.pack_lock:
            if ps is not None and ps.cards:
                saved = save_csv(ps, sess.source)
            sess.pack_reset()
        return {"ok": True, "saved": saved}

    def pack_export(self, sid: str) -> dict:
        from cardengine.pack_opening import save_csv

        sess, ps = self._pack(sid)
        if ps is None or not ps.cards:
            return {"ok": False, "error": "Session ist leer"}
        with self.shared.pack_lock:
            path = save_csv(ps, sess.source)
        return {"ok": True, "path": path}

    def pack_remove(self, sid: str, entry_id) -> dict:
        _, ps = self._pack(sid)
        with self.shared.pack_lock:
            ok = ps is not None and ps.remove(int(entry_id))
        return {"ok": True} if ok else {"ok": False, "error": "Eintrag nicht gefunden"}

    def pack_csv(self, sid: str) -> tuple[bytes, str] | None:
        """(CSV-Bytes mit BOM, Dateiname) für den Download."""
        sess, ps = self._pack(sid)
        if ps is None:
            return None
        with self.shared.pack_lock:
            text = ps.to_csv()
            name = time.strftime("session-%Y-%m-%d_%H%M%S.csv", time.localtime(ps.started))
        return ("﻿" + text).encode("utf-8"), name

    def _preview(self, sess) -> dict:
        """Kamerabild-Vorschau: Bildgröße und Umrisse aller sichtbaren Karten (normiert 0..1)."""
        img = sess.last_frame_img
        if img is None:
            return {}
        h, w = img.shape[:2]
        tracks = []
        for t in list(sess.pipeline.tracker.tracks):
            if t.hidden or (t.state == "rejected" and t.user_rejected):
                continue
            q = (t.display / np.float32([w, h])).round(4).tolist()
            tracks.append({"label": sess.label_for(t.id), "track": t.id, "state": t.state,
                           "certain": bool(t.confirmed or (t.identity is not None and t.identity.certain)),
                           "quad": q})
        return {"frame": {"w": int(w), "h": int(h), "url": f"/api/frame/{sess.sid}"}, "tracks": tracks}

    def frame_jpeg(self, sid: str, width: int = 480) -> bytes | None:
        sess = self._session(sid)
        if sess is None or sess.last_frame_img is None:
            return None
        img = sess.last_frame_img
        width = int(np.clip(width, 80, 1280))
        if img.shape[1] > width:
            img = cv2.resize(img, (width, round(img.shape[0] * width / img.shape[1])), interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 80])
        return buf.tobytes() if ok else None

    def probe(self, sid: str, body: dict) -> dict:
        """Gezielte Suche an einer Stelle der Vorschau (normierte Koordinaten)."""
        sess = self._session(sid)
        if sess is None or sess.pipeline is None or sess.last_frame_img is None:
            return {"ok": False, "error": "Kein Kamerabild"}
        h, w = sess.last_frame_img.shape[:2]
        if body.get("rect"):
            x0, y0, x1, y1 = (float(v) for v in body["rect"])
            res = sess.pipeline.probe(rect=(x0 * w, y0 * h, x1 * w, y1 * h))
        else:
            res = sess.pipeline.probe(point=(float(body["x"]) * w, float(body["y"]) * h))
        if not res.get("found"):
            return {"ok": True, "found": False,
                    "error": res.get("error") or "Keine Karte gefunden – Rahmen um die Karte ziehen"}
        return {"ok": True, "found": True, "track": res["track"], "label": sess.label_for(res["track"]),
                "how": res.get("how")}

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
        # Automatisch als „keine Karte“ abgelegt, aber der Detektor ist sich der Kartenform sicher (z. B. Druck ohne
        # Referenzbild, starker Glanz): trotzdem fragen – nur echte Hintergrundstellen bleiben stumm
        likely_card = t.state == "rejected" and not t.user_rejected and t.score >= LIKELY_CARD_SCORE
        if ident is None and t.state != "failed" and not likely_card:
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
        if likely_card:
            card["state"] = "failed"  # fürs Dock „nicht erkannt“ statt „verworfen“ (sonst blendet es die Kachel aus)
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

    def search(self, q: str, game: str | None, limit: int, lang: str = "de") -> dict:
        """Suche nach Name (englisch oder in einer geladenen Sprache, z. B. „Glurak“) und/oder Nummer
        („4/102“, „025“, „CC002“). Beispiele: „Glurak 4/102“, „Sol Ring 409“, „4/102“."""
        cat = self.catalog
        q = (q or "").strip()
        if cat is None or len(q) < 2:
            return {"results": []}
        name_q, number, total = _split_query(q)
        where, args = [], []
        if game:
            where.append("game = ?")
            args.append(game)
        if number:
            # Nummern ohne führende Nullen vergleichen ("025" = "25"); Buchstaben-Nummern (CC002, SWSH066) exakt
            where.append("(ltrim(lower(number), '0') = ltrim(lower(?), '0'))")
            args.append(number)
        if total:
            where.append("set_total = ?")
            args.append(int(total))
        if name_q:
            # Ganzer Name (auch übersetzt) – oder jedes Wort in Name bzw. Set-Name („Glurak Classic“)
            ids = [cid for g, cid in self.shared.names.find_ids(name_q) if not game or g == game]
            cond, cargs = "name LIKE ?", [f"%{name_q}%"]
            if ids:
                en = self._english_names(ids)
                cond += f" OR id IN ({','.join('?' * len(ids))})" + (f" OR name IN ({','.join('?' * len(en))})" if en else "")
                cargs += ids + en
            words = name_q.split()
            if len(words) > 1:
                parts = []
                for w in words:
                    wids = [cid for g, cid in self.shared.names.find_ids(w) if not game or g == game]
                    wen = self._english_names(wids) if wids else []
                    part = ("(name LIKE ? OR set_name LIKE ?"
                            + (f" OR id IN ({','.join('?' * len(wids))})" if wids else "")
                            + (f" OR name IN ({','.join('?' * len(wen))})" if wen else "") + ")")
                    parts.append(part)
                    cargs += [f"%{w}%", f"%{w}%"] + wids + wen
                cond += " OR (" + " AND ".join(parts) + ")"
            where.append(f"({cond})")
            args += cargs
        if not where:
            return {"results": []}
        sql = "WHERE " + " AND ".join(where) + " ORDER BY released DESC LIMIT ?"
        recs = cat._rows(sql, tuple(args + [int(limit)]))
        return {"results": [{"key": card_key(r.game, r.id), "name": self.shared.names.name_nowait(r, lang),
                             "set_name": r.set_name, "set_code": r.set_code, "number": r.printed_number,
                             "image": self._image_url(card_key(r.game, r.id))} for r in recs]}

    def _english_names(self, ids: list[str]) -> list[str]:
        """Englische Katalognamen zu übersetzt gefundenen Karten („Glurak“ → „Charizard“) – so werden auch Drucke
        ohne übersetzten Namen gefunden (z. B. Celebrations Classic Collection)."""
        if not ids:
            return []
        rows = self.catalog.db.execute(
            f"SELECT DISTINCT name FROM cards WHERE id IN ({','.join('?' * len(ids[:300]))}) LIMIT 20", ids[:300]).fetchall()
        return [r[0] for r in rows]

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
                    return self._json(api.state(overlay="overlay" in parse_qs(url.query)))
                if url.path in ("/session", "/session.html"):
                    with open(SESSION_HTML, "rb") as f:
                        return self._send(200, f.read(), "text/html; charset=utf-8")
                if url.path == "/api/search":
                    qs = parse_qs(url.query)
                    return self._json(api.search(qs.get("q", [""])[0], qs.get("game", [None])[0] or None,
                                                 min(50, int(qs.get("limit", ["20"])[0]))))
                if parts[:2] == ["api", "frame"] and len(parts) == 3:
                    qs = parse_qs(url.query)
                    data = api.frame_jpeg(parts[2], int(qs.get("w", ["480"])[0]))
                    return self._send(200, data, "image/jpeg") if data else self._send(404, b"", "text/plain")
                if parts[:3] == ["api", "pack", "csv"] and len(parts) == 4:
                    res = api.pack_csv(parts[3])
                    if res is None:
                        return self._send(404, b"", "text/plain")
                    data, name = res
                    return self._send(200, data, "text/csv; charset=utf-8",
                                      {"Content-Disposition": f'attachment; filename="{name}"'})
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
                        # Ohne lokale Bildersammlung (Datenpakete enthalten keine Bilder): einmal laden und behalten
                        from cardengine.card_database.build import _download

                        try:
                            _download(rec)
                        except Exception as err:
                            print(f"[dock] Bild {game}/{cid} nicht geladen: {err}")
                        if os.path.exists(path):
                            with open(path, "rb") as f:
                                return self._send(200, f.read(), "image/jpeg", {"Cache-Control": "max-age=86400"})
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
                if path == "/api/settings":
                    sess = api._session(body["session"])
                    if sess is None:
                        return self._json({"ok": False, "error": "Quelle nicht verbunden"})
                    changed = sess.patch_settings({k: v for k, v in body.items() if k != "session"})
                    return self._json({"ok": bool(changed), "changed": changed} if changed
                                      else {"ok": False, "error": "keine änderbare Einstellung"})
                if path == "/api/probe":
                    return self._json(api.probe(body["session"], body))
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
                if path == "/api/pack/reset":
                    return self._json(api.pack_reset(body["session"]))
                if path == "/api/pack/export":
                    return self._json(api.pack_export(body["session"]))
                if path == "/api/pack/remove":
                    return self._json(api.pack_remove(body["session"], body["entry"]))
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
