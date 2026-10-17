import os

ENGINE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Datenordner (Katalog, Index, Preise …); per Umgebungsvariable umlenkbar, z. B. für Tests oder eine zweite Installation.
# Wird beim Import ausgewertet – die Variable muss also vor dem ersten Import von cardengine gesetzt sein.
DATA_DIR = os.environ.get("TCGLENS_DATA_DIR") or os.path.join(ENGINE_DIR, "data")
MODELS_DIR = os.path.join(ENGINE_DIR, "models")
USER_AGENT = "TCGCardLens/0.2 (local hobby tool)"
