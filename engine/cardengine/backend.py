"""Inferenz-Backend und ONNX-Runtime-Provider wählen.

Backends
- `onnx`  (Standard der schlanken Installation): Detektor und Embedder als ONNX-Modelle über ONNX Runtime.
  Kein PyTorch nötig (~0,5–1 GB statt ~4 GB), läuft auch auf AMD- und Intel-GPUs (DirectML).
- `torch` (Entwicklung): Ultralytics + Hugging Face Transformers wie bisher; nötig für Training und ONNX-Export.

Auswahl: Umgebungsvariable `TCGLENS_BACKEND` (auto | onnx | torch) > `data/config.json: "backend"` > auto.
`auto` nimmt PyTorch nur, wenn torch, ultralytics und transformers installiert sind – sonst ONNX.

Provider (nur ONNX): `TCGLENS_ORT_PROVIDER` (auto | cuda | dml | cpu) > `config.json: "ort_provider"` > auto.
`auto` = CUDA → DirectML → CPU, jeweils nur, was das installierte onnxruntime-Paket anbietet
(onnxruntime-gpu: CUDA + CPU, onnxruntime-directml: DirectML + CPU, onnxruntime: CPU).

Dieses Modul importiert weder torch noch onnxruntime beim Laden – die Prüfungen sind billig und ohne GPU/Netz testbar.
"""
from __future__ import annotations

import importlib.util
import json
import os
import threading

BACKEND_ENV = "TCGLENS_BACKEND"
PROVIDER_ENV = "TCGLENS_ORT_PROVIDER"
BACKENDS = ("onnx", "torch")
TORCH_PACKAGES = ("torch", "ultralytics", "transformers")

CUDA = "CUDAExecutionProvider"
DML = "DmlExecutionProvider"
CPU = "CPUExecutionProvider"
PROVIDER_ALIASES = {"cuda": CUDA, "dml": DML, "directml": DML, "cpu": CPU}
AUTO_ORDER = (CUDA, DML, CPU)


def _config_value(key: str) -> str:
    """Einzelner Wert aus data/config.json (ohne config.load_config, damit dieses Modul leicht bleibt)."""
    from . import paths

    try:
        with open(os.path.join(paths.DATA_DIR, "config.json"), encoding="utf-8-sig") as f:
            data = json.load(f)
        return str(data.get(key) or "") if isinstance(data, dict) else ""
    except (OSError, ValueError):
        return ""


def torch_installed() -> bool:
    """Sind alle Pakete des PyTorch-Backends installiert? (nur Suche, kein Import)"""
    return all(importlib.util.find_spec(p) is not None for p in TORCH_PACKAGES)


def resolve_backend(requested: str | None = None, torch_ok: bool | None = None) -> str:
    """'onnx' oder 'torch'. `requested` (z. B. aus einem Kommandozeilenparameter) hat Vorrang vor Umgebung/Config."""
    value = (requested or os.environ.get(BACKEND_ENV) or _config_value("backend") or "auto").strip().lower()
    if value in ("pytorch", "pt"):
        value = "torch"
    if value not in ("auto", *BACKENDS):
        raise ValueError(f"unbekanntes Backend {value!r} (erlaubt: auto, onnx, torch)")
    if value == "auto":
        if torch_ok is None:
            torch_ok = torch_installed()
        return "torch" if torch_ok else "onnx"
    if value == "torch" and torch_ok is False:
        raise RuntimeError("PyTorch-Backend gewählt, aber torch/ultralytics/transformers fehlen "
                           "(pip install -r requirements-dev.txt)")
    return value


def provider_chain(available: list[str], requested: str | None = None) -> list[str]:
    """Provider-Liste für eine InferenceSession: gewünschter bzw. CUDA → DirectML → CPU, nur verfügbare.
    Ein ausdrücklich gewünschter, aber nicht verfügbarer Provider fällt auf CPU zurück (mit Meldung beim Aufrufer)."""
    value = (requested or os.environ.get(PROVIDER_ENV) or _config_value("ort_provider") or "auto").strip().lower()
    if value == "auto":
        chain = [p for p in AUTO_ORDER if p in available]
    else:
        if value not in PROVIDER_ALIASES:
            raise ValueError(f"unbekannter Provider {value!r} (erlaubt: auto, cuda, dml, cpu)")
        wanted = PROVIDER_ALIASES[value]
        chain = [wanted] if wanted in available else []
    if CPU not in chain:
        chain.append(CPU)  # CPU ist in jedem onnxruntime-Paket enthalten
    return chain


# ---------------------------------------------------------------- ONNX Runtime
_preload_lock = threading.Lock()
_preloaded = False


def _preload_cuda(ort) -> None:
    """CUDA-/cuDNN-DLLs aus den Pip-Paketen (nvidia-*) laden, bevor eine CUDA-Sitzung entsteht.
    Ohne PyTorch findet onnxruntime-gpu sie sonst nicht (mit PyTorch liefert dessen Import sie mit)."""
    global _preloaded
    with _preload_lock:
        if _preloaded:
            return
        _preloaded = True
        if hasattr(ort, "preload_dlls"):
            try:
                ort.preload_dlls(cuda=True, cudnn=True, msvc=True)
            except Exception as err:  # fehlende Pakete: CUDA-Sitzung scheitert später sauber → CPU
                print(f"[onnx] CUDA-Bibliotheken nicht vorladbar: {err}", flush=True)


def available_providers() -> list[str]:
    import onnxruntime as ort

    return list(ort.get_available_providers())


def providers_for_session(requested: str | None = None) -> list:
    """Provider samt Optionen für InferenceSession (CUDA: Speicher nur nach Bedarf, VRAM ist mit OBS geteilt)."""
    import onnxruntime as ort

    chain = provider_chain(available_providers(), requested)
    if CUDA in chain:
        _preload_cuda(ort)
    out = []
    for p in chain:
        if p == CUDA:
            out.append((CUDA, {"device_id": 0, "arena_extend_strategy": "kSameAsRequested"}))
        elif p == DML:
            out.append((DML, {"device_id": 0}))
        else:
            out.append(p)
    return out


def create_session(model_path: str, requested: str | None = None, dim_overrides: dict[str, int] | None = None):
    """InferenceSession mit Provider-Kette; meldet, welcher Provider tatsächlich rechnet.
    `dim_overrides`: symbolische Eingabemaße fest setzen (z. B. {"batch": 1}) – für DirectML wichtig."""
    import onnxruntime as ort

    ort.set_default_logger_severity(3)  # Hinweise wie „Memcpy nodes added“ nicht ins Engine-Log
    providers = providers_for_session(requested)
    names = [p[0] if isinstance(p, tuple) else p for p in providers]
    so = ort.SessionOptions()
    so.log_severity_level = 3  # nur Fehler
    if DML in names:
        # DirectML verlangt sequentielle Ausführung ohne Speichermuster
        so.enable_mem_pattern = False
        so.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    for name, value in (dim_overrides or {}).items():
        so.add_free_dimension_override_by_name(name, int(value))
    try:
        session = ort.InferenceSession(model_path, sess_options=so, providers=providers)
    except Exception as err:
        if names == [CPU]:
            raise
        print(f"[onnx] {names[0]} nicht nutzbar ({err}) – weiter mit CPU", flush=True)
        session = ort.InferenceSession(model_path, sess_options=so, providers=[CPU])
    active = session.get_providers()[0]
    if active != names[0]:
        print(f"[onnx] {os.path.basename(model_path)}: {names[0]} nicht verfügbar – rechnet mit {active}", flush=True)
    return session


class OnnxRunner:
    """ONNX-Modell mit einem Eingang. Unter DirectML bekommt jede Eingabeform eine eigene Sitzung mit festen Maßen:
    DirectML optimiert nur feste Formen (RTX 2080 SUPER: Embedder 6 statt 24 ms, Detektor 4 statt 14 ms je Bild).
    CUDA und CPU kommen mit dynamischen Maßen gut zurecht – dort eine Sitzung für alles."""

    def __init__(self, model_path: str, provider: str | None = None, max_shapes: int = 3):
        from collections import OrderedDict

        self.model_path = model_path
        self.requested = provider
        self.max_shapes = max_shapes
        self.session = create_session(model_path, provider)
        self.provider = self.session.get_providers()[0]
        inp = self.session.get_inputs()[0]
        self.input_name = inp.name
        self.input_shape = list(inp.shape)
        self.fixed_shapes = self.provider == DML and any(not isinstance(d, int) for d in self.input_shape)
        self._by_shape: OrderedDict = OrderedDict()

    def _session_for(self, shape: tuple[int, ...]):
        s = self._by_shape.get(shape)
        if s is None:
            overrides = {d: n for d, n in zip(self.input_shape, shape) if isinstance(d, str)}
            s = create_session(self.model_path, self.requested, overrides)
            self._by_shape[shape] = s
            if len(self._by_shape) > self.max_shapes:
                self._by_shape.popitem(last=False)
        else:
            self._by_shape.move_to_end(shape)
        return s

    def run(self, x):
        s = self._session_for(tuple(x.shape)) if self.fixed_shapes else self.session
        return s.run(None, {self.input_name: x})


def ocr_flags() -> dict:
    """RapidOCR-Einstellungen passend zum besten verfügbaren Provider (CUDA bzw. DirectML, sonst CPU)."""
    try:
        chain = provider_chain(available_providers())
    except Exception:
        return {}
    if chain[0] == CUDA:
        import onnxruntime as ort

        _preload_cuda(ort)
        return {"EngineConfig.onnxruntime.use_cuda": True}
    if chain[0] == DML:
        return {"EngineConfig.onnxruntime.use_dml": True}
    return {}
