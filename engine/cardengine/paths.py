import os

ENGINE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ENGINE_DIR, "data")
MODELS_DIR = os.path.join(ENGINE_DIR, "models")
USER_AGENT = "TCGCardLens/0.2 (local hobby tool)"
