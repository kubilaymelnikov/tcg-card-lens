"""Testclient, der sich wie der OBS-Filter verhält (Referenz für die C++-Implementierung, Protokoll: docs/PLUGIN.md).

    python tools/fake_plugin.py --source 0 --seconds 20 --save data/fake_plugin

Legt das Shared Memory an, schreibt Kamerabilder als BGRA in zwei Slots, sendet Frame-Nachrichten, empfängt
Ergebnisse, hält Panel-Texturen je Key vor und setzt Rahmen + Panels selbst ins Bild – genau wie das Plugin.
"""
from __future__ import annotations

import argparse
import base64
import json
import mmap
import os
import socket
import struct
import sys
import threading
import time
import uuid

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from cardengine.video import open_source  # noqa: E402

HEADER = 64


def send(sock, msg):
    data = json.dumps(msg).encode("utf-8")
    sock.sendall(struct.pack("<I", len(data)) + data)


def recv(sock):
    def exact(n):
        buf = bytearray()
        while len(buf) < n:
            c = sock.recv(n - len(buf))
            if not c:
                raise ConnectionError("Engine getrennt")
            buf += c
        return bytes(buf)

    (n,) = struct.unpack("<I", exact(4))
    return json.loads(exact(n))


def draw(frame, result, textures, sx, sy):
    for t in result.get("tracks", []):
        q = (np.array(t["corners"]) * [sx, sy]).astype(np.int32)
        cv2.polylines(frame, [q], True, tuple(int(c) for c in t["color"][::-1]), max(2, int(t["thickness"])), cv2.LINE_AA)
        cx, top, bottom = int(q[:, 0].mean()), int(q[:, 1].min()), int(q[:, 1].max())
        for p in t["panels"]:
            img = textures.get(p["key"])
            if img is None:
                continue
            h, w = img.shape[:2]
            x = int(np.clip(cx - w // 2, 0, frame.shape[1] - w))
            y = top - h - 6 if p["anchor"] == "top" else bottom + 8
            y = int(np.clip(y, 0, frame.shape[0] - h))
            region = frame[y:y + h, x:x + w]
            a = img[..., 3:4].astype(np.float32) / 255
            region[:] = (img[..., 2::-1] * a + region * (1 - a)).astype(np.uint8)
    if "debug" in result:
        cv2.putText(frame, result["debug"]["text"], (10, frame.shape[0] - 12), 0, 0.6, (200, 255, 200), 1)
    return frame


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="0")
    ap.add_argument("--seconds", type=float, default=20)
    ap.add_argument("--fps", type=float, default=15)
    ap.add_argument("--analysis-width", type=int, default=1280)
    ap.add_argument("--port", type=int, default=47820)
    ap.add_argument("--save", default="")
    ap.add_argument("--settings", default="{}", help='JSON, z. B. {"display_language":"de","debug":true}')
    args = ap.parse_args()

    cap = open_source(args.source)
    ok, frame = cap.read()
    h0, w0 = frame.shape[:2]
    aw = min(args.analysis_width, w0)
    ah = round(h0 * aw / w0)
    stride = aw * 4
    slot_size = stride * ah
    name = f"Local\\tcglens_{uuid.uuid4().hex[:12]}"
    shm = mmap.mmap(-1, HEADER + 2 * slot_size, tagname=name)
    shm[:16] = b"TCGL" + struct.pack("<III", 1, 2, slot_size)

    sock = socket.create_connection(("127.0.0.1", args.port))
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    send(sock, {"type": "hello", "proto": 1, "instance": name, "source": "fake-plugin", "shm": name,
                "shm_size": HEADER + 2 * slot_size, "slot_size": slot_size, "settings": json.loads(args.settings)})
    print("Engine:", recv(sock))

    latest = {"result": {}, "n": 0, "lat": []}
    textures: dict[str, np.ndarray] = {}
    sent_at: dict[int, float] = {}

    def reader():
        while True:
            try:
                msg = recv(sock)
            except (ConnectionError, OSError):
                return
            if msg["type"] == "result":
                for t in msg["tracks"]:
                    for p in t["panels"]:
                        if "data" in p:
                            textures[p["key"]] = np.frombuffer(base64.b64decode(p["data"]), np.uint8).reshape(p["h"], p["w"], 4)
                latest["result"] = msg
                latest["n"] += 1
                if msg["seq"] in sent_at:
                    latest["lat"].append((time.perf_counter() - sent_at.pop(msg["seq"])) * 1000)
            else:
                print("Engine:", msg)

    threading.Thread(target=reader, daemon=True).start()
    seq, t_end, slot = 0, time.time() + args.seconds, 0
    if args.save:
        os.makedirs(args.save, exist_ok=True)
    while time.time() < t_end:
        t0 = time.perf_counter()
        ok, frame = cap.read()
        if not ok:
            break
        small = cv2.resize(frame, (aw, ah), interpolation=cv2.INTER_AREA)
        bgra = cv2.cvtColor(small, cv2.COLOR_BGR2BGRA)
        off = HEADER + slot * slot_size
        shm[off:off + slot_size] = bgra.tobytes()
        seq += 1
        sent_at[seq] = time.perf_counter()
        send(sock, {"type": "frame", "seq": seq, "slot": slot, "width": aw, "height": ah, "stride": stride, "ts": time.time()})
        slot = 1 - slot
        if args.save and seq % int(args.fps * 4) == 0:
            vis = draw(frame.copy(), latest["result"], textures, w0 / aw, h0 / ah)
            cv2.imwrite(os.path.join(args.save, f"plugin-{seq:05d}.jpg"), vis)
        time.sleep(max(0, 1 / args.fps - (time.perf_counter() - t0)))
    send(sock, {"type": "bye"})
    lat = sorted(latest["lat"])
    print(f"{seq} Frames gesendet, {latest['n']} Ergebnisse; Latenz Median {lat[len(lat)//2] if lat else 0:.0f} ms, "
          f"p95 {lat[int(len(lat)*0.95)] if lat else 0:.0f} ms; {len(textures)} Panel-Texturen")


if __name__ == "__main__":
    main()
