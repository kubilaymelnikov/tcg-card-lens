"""ONNX-Backend ohne GPU und ohne Netz: OBB-Nachverarbeitung (Dekodierung, rotiertes NMS), Backend-/Provider-Auswahl,
Modell-Download mit Prüfsumme. Vergleiche mit Ultralytics laufen nur, wenn PyTorch installiert ist."""
import hashlib
import json
import math

import numpy as np
import pytest

from cardengine import backend, models_fetch
from cardengine.detection import obb_post


# ---------------------------------------------------------------- OBB-Nachverarbeitung
def test_probiou_gleich_fern_symmetrisch():
    a = np.array([[100, 100, 60, 90, 0.3]], np.float32)
    b = np.array([[400, 400, 60, 90, 0.3]], np.float32)
    assert obb_post.probiou_matrix(a, a)[0, 0] > 0.99
    assert obb_post.probiou_matrix(a, b)[0, 0] < 0.01
    c = np.array([[110, 95, 60, 90, 0.5]], np.float32)
    assert obb_post.probiou_matrix(a, c)[0, 0] == pytest.approx(obb_post.probiou_matrix(c, a)[0, 0], abs=1e-9)


def test_probiou_drehung_um_180_grad_gleich():
    a = np.array([[50, 50, 40, 80, 0.2]], np.float32)
    b = a.copy()
    b[0, 4] += math.pi
    assert obb_post.probiou_matrix(a, b)[0, 0] > 0.99


def test_nms_behaelt_beste_und_trennt_klassen():
    boxes = np.array([[100, 100, 60, 90, 0.0], [102, 101, 60, 90, 0.05], [300, 300, 60, 90, 0.0]], np.float32)
    scores = np.array([0.8, 0.9, 0.5], np.float32)
    keep = obb_post.nms_rotated(boxes, scores, np.array([0, 0, 0]), iou=0.7)
    assert keep.tolist() == [1, 2]  # nach Score absteigend, überdeckte Box 0 fällt weg
    keep = obb_post.nms_rotated(boxes, scores, np.array([0, 1, 0]), iou=0.7)
    assert sorted(keep.tolist()) == [0, 1, 2]  # verschiedene Klassen unterdrücken sich nicht
    keep = obb_post.nms_rotated(boxes, scores, np.array([0, 1, 0]), iou=0.7, agnostic=True)
    assert keep.tolist() == [1, 2]


def test_nms_fast_nms_semantik():
    """Wie Ultralytics fast_nms: Auch eine bereits verworfene Box unterdrückt schwächere."""
    boxes = np.array([[100, 100, 60, 90, 0], [112, 100, 60, 90, 0], [124, 100, 60, 90, 0]], np.float32)
    scores = np.array([0.9, 0.8, 0.7], np.float32)
    iou = obb_post.probiou_matrix(boxes, boxes)
    thr = (iou[0, 1] + iou[0, 2]) / 2  # A überdeckt B, aber nicht C; B überdeckt C
    assert iou[0, 1] > thr > iou[0, 2] and iou[1, 2] > thr
    assert obb_post.nms_rotated(boxes, scores, None, iou=thr).tolist() == [0]


def test_nms_leer_und_max_det():
    assert obb_post.nms_rotated(np.zeros((0, 5), np.float32), np.zeros(0, np.float32)).size == 0
    boxes = np.array([[i * 200, 0, 50, 50, 0] for i in range(5)], np.float32)
    assert len(obb_post.nms_rotated(boxes, np.linspace(0.9, 0.5, 5), None, max_det=3)) == 3


def _raw(boxes: list, nc: int = 2) -> np.ndarray:
    """Rohausgabe (4 + nc + 1, N) aus Zeilen (x, y, w, h, Klassenwerte…, Winkel)."""
    return np.array(boxes, np.float32).T.reshape(4 + nc + 1, -1)


def test_decode_beste_klasse_und_schwelle():
    pred = _raw([[10, 20, 30, 40, 0.1, 0.8, 0.5], [50, 60, 30, 40, 0.3, 0.2, 0.1], [0, 0, 1, 1, 0.05, 0.1, 0]])
    boxes, scores, classes = obb_post.decode(pred, conf=0.25, nc=2)
    assert boxes.tolist() == [[10, 20, 30, 40, 0.5], [50, 60, 30, 40, pytest.approx(0.1)]]
    assert scores.tolist() == [pytest.approx(0.8), pytest.approx(0.3)]
    assert classes.tolist() == [1, 0]
    with pytest.raises(ValueError):
        obb_post.decode(pred, conf=0.25, nc=3)


def test_letterbox_rechteck_wie_ultralytics():
    img = np.zeros((720, 1280, 3), np.uint8)
    out, gain, pad = obb_post.letterbox(img, 640, auto=True)
    assert out.shape == (384, 640, 3)  # 640 × 360 → auf Vielfaches von 32 aufgefüllt
    assert gain == (0.5, 0.5) and pad == (0, 12)
    assert (out[0] == 114).all() and (out[200] == 0).all()
    out, _, pad = obb_post.letterbox(img, 640, auto=False)
    assert out.shape == (640, 640, 3) and pad == (0, 140)


def test_postprocess_zurueck_ins_originalbild():
    img = np.zeros((720, 1280, 3), np.uint8)
    _, gain, pad = obb_post.letterbox(img, 640, auto=True)
    # Karte im Original: Mitte (640, 360), 200 × 280, 30°; im Netzbild halbe Größe, 12 px tiefer
    pred = _raw([[320, 192, 100, 140, 0.9, 0.1, math.radians(30)]])
    corners, scores, classes = obb_post.postprocess(pred, 2, conf=0.25, iou=0.7, gain=gain, pad=pad)
    assert corners.shape == (1, 4, 2) and classes.tolist() == [0]
    assert corners[0].mean(0) == pytest.approx([640, 360], abs=1e-3)
    sides = sorted(np.linalg.norm(corners[0] - np.roll(corners[0], 1, 0), axis=1))
    assert sides == pytest.approx([200, 200, 280, 280], abs=1e-3)


def test_vergleich_mit_ultralytics():
    """Nur mit PyTorch-Umgebung: ProbIoU und NMS gegen die Originalfunktionen."""
    torch = pytest.importorskip("torch")
    pytest.importorskip("ultralytics")
    from ultralytics.utils.metrics import batch_probiou
    from ultralytics.utils.nms import TorchNMS

    rng = np.random.default_rng(1)
    boxes = np.column_stack([rng.uniform(0, 300, (60, 2)), rng.uniform(20, 120, (60, 2)),
                             rng.uniform(0, math.pi, 60)]).astype(np.float32)
    scores = rng.uniform(0.2, 1, 60).astype(np.float32)
    ref = batch_probiou(torch.from_numpy(boxes), torch.from_numpy(boxes)).numpy()
    assert np.abs(obb_post.probiou_matrix(boxes, boxes) - ref).max() < 1e-4
    keep_ref = TorchNMS.fast_nms(torch.from_numpy(boxes), torch.from_numpy(scores), 0.3, iou_func=batch_probiou)
    assert obb_post.nms_rotated(boxes, scores, None, iou=0.3).tolist() == keep_ref.tolist()


# ---------------------------------------------------------------- Backend-Auswahl
@pytest.fixture
def clean_env(monkeypatch, tmp_path):
    monkeypatch.delenv(backend.BACKEND_ENV, raising=False)
    monkeypatch.delenv(backend.PROVIDER_ENV, raising=False)
    from cardengine import paths

    monkeypatch.setattr(paths, "DATA_DIR", str(tmp_path))
    return tmp_path


def test_backend_auto_nach_torch(clean_env):
    assert backend.resolve_backend(torch_ok=False) == "onnx"
    assert backend.resolve_backend(torch_ok=True) == "torch"


def test_backend_erzwingen(clean_env, monkeypatch):
    monkeypatch.setenv(backend.BACKEND_ENV, "onnx")
    assert backend.resolve_backend(torch_ok=True) == "onnx"
    monkeypatch.setenv(backend.BACKEND_ENV, "torch")
    assert backend.resolve_backend(torch_ok=None) == "torch"
    with pytest.raises(RuntimeError):
        backend.resolve_backend(torch_ok=False)
    monkeypatch.setenv(backend.BACKEND_ENV, "tensorflow")
    with pytest.raises(ValueError):
        backend.resolve_backend()
    assert backend.resolve_backend("onnx", torch_ok=True) == "onnx"  # Parameter vor Umgebung


def test_backend_aus_config(clean_env):
    (clean_env / "config.json").write_text(json.dumps({"backend": "onnx", "ort_provider": "cpu"}), "utf-8")
    assert backend.resolve_backend(torch_ok=True) == "onnx"
    assert backend.provider_chain([backend.CUDA, backend.CPU]) == [backend.CPU]


def test_provider_kette(clean_env, monkeypatch):
    trt = "TensorrtExecutionProvider"
    assert backend.provider_chain([trt, backend.CUDA, backend.CPU]) == [backend.CUDA, backend.CPU]
    assert backend.provider_chain([backend.DML, backend.CPU]) == [backend.DML, backend.CPU]
    assert backend.provider_chain([backend.CPU]) == [backend.CPU]
    assert backend.provider_chain([backend.DML, backend.CPU], "cuda") == [backend.CPU]  # nicht da → CPU
    monkeypatch.setenv(backend.PROVIDER_ENV, "dml")
    assert backend.provider_chain([backend.CUDA, backend.DML, backend.CPU]) == [backend.DML, backend.CPU]
    with pytest.raises(ValueError):
        backend.provider_chain([backend.CPU], "vulkan")


def test_torch_installed_ohne_import(monkeypatch):
    import importlib.util

    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)
    assert backend.torch_installed() is False


# ---------------------------------------------------------------- Modelle bereitstellen
@pytest.fixture
def onnx_dir(monkeypatch, tmp_path):
    monkeypatch.setenv(models_fetch.ONNX_DIR_ENV, str(tmp_path))
    return tmp_path


def test_vorhandene_modelle_ohne_netz(onnx_dir):
    for m in models_fetch.ONNX_MODELS.values():
        (onnx_dir / m.file).write_bytes(b"x")
    paths = models_fetch.ensure_onnx_models(verbose=False, base_url="https://example.invalid/PLATZHALTER")
    assert set(paths) == {"detector", "embedder"}


def test_fehlende_modelle_ohne_quelle_und_torch(onnx_dir, monkeypatch):
    monkeypatch.setattr(backend, "torch_installed", lambda: False)
    with pytest.raises(models_fetch.ModelError):
        models_fetch.ensure_onnx_models(verbose=False, base_url="https://example.invalid/PLATZHALTER")


class _FakeResponse:
    def __init__(self, data: bytes):
        self.data = data

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def raise_for_status(self):
        pass

    def iter_content(self, n):
        yield self.data


def test_download_prueft_sha256(onnx_dir, monkeypatch):
    import requests

    data = b"onnx-modell"
    monkeypatch.setattr(requests, "get", lambda *a, **k: _FakeResponse(data))
    good = models_fetch.OnnxModel("detector", "d.onnx", hashlib.sha256(data).hexdigest(), len(data), "x")
    dest = onnx_dir / "d.onnx"
    models_fetch.download_model(good, "https://example.org/dl", str(dest), log=lambda *a: None)
    assert dest.read_bytes() == data
    bad = models_fetch.OnnxModel("detector", "e.onnx", "0" * 64, len(data), "x")
    with pytest.raises(models_fetch.ModelError):
        models_fetch.download_model(bad, "https://example.org/dl", str(onnx_dir / "e.onnx"), log=lambda *a: None)
    assert sorted(p.name for p in onnx_dir.iterdir()) == ["d.onnx"]  # keine halbe/falsche Datei


def test_dateinamen_mit_version():
    for m in models_fetch.ONNX_MODELS.values():
        assert m.file.startswith(f"tcglens-{m.kind}-") and m.file.endswith(".onnx") and "-v" in m.file
        assert len(m.sha256) == 64 and m.size > 0


def test_embedder_vorverarbeitung_ohne_torch():
    from cardengine.recognition.embedder import _preprocess_dinov3

    img = np.full((340, 247, 3), 128, np.uint8)
    x = _preprocess_dinov3([img, img])
    assert x.shape == (2, 3, 224, 224) and x.dtype == np.float32
    expect = (128 / 255 - np.array([0.485, 0.456, 0.406])) / np.array([0.229, 0.224, 0.225])
    assert x[0, :, 100, 100] == pytest.approx(expect, abs=1e-4)
