"""Tests schreiben nie in die Live-Daten: Datenordner vor dem ersten Import von cardengine auf ein Temp-Verzeichnis."""
import os
import tempfile

os.environ.setdefault("TCGLENS_DATA_DIR", tempfile.mkdtemp(prefix="tcglens-test-"))
os.environ.setdefault("TCGLENS_PACK_URL", "https://example.invalid/PLATZHALTER")  # nie ins Netz
