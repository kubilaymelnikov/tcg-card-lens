"""Unterstützte Kartenspiele und die Engine-Einstellung `games`.

Interne Kürzel: "pokemon", "mtg", "ygo" (Schlüssel überall "game|id").
Einstellung `games` (rückwärtskompatibel):
    "both"                      → Pokémon + Magic (Bedeutung wie vor Yu-Gi-Oh)
    "all"                       → alle Spiele
    "pokemon" | "mtg" | "ygo"   → nur dieses Spiel
    "pokemon,ygo" oder ["pokemon", "ygo"] → Auswahl (auch "+" als Trenner)
"""
from __future__ import annotations

GAMES = ("pokemon", "mtg", "ygo")
GAME_LABELS = {"pokemon": "Pokémon", "mtg": "Magic", "ygo": "Yu-Gi-Oh!"}
ALIASES = {"both": ("pokemon", "mtg"), "all": GAMES, "magic": ("mtg",), "yugioh": ("ygo",)}


def parse_games(value) -> tuple[str, ...] | None:
    """Einstellungswert → Tupel der gewählten Spiele; None = alle (keine Einschränkung der Suche).
    Unbekannte oder leere Werte fallen auf "both" zurück (bisheriges Standardverhalten)."""
    if isinstance(value, (list, tuple, set)):
        parts = [str(v).strip().lower() for v in value]
    else:
        parts = [p.strip().lower() for p in str(value or "").replace("+", ",").split(",")]
    chosen: list[str] = []
    for p in parts:
        for g in ALIASES.get(p, (p,)):
            if g in GAMES and g not in chosen:
                chosen.append(g)
    if not chosen:
        chosen = list(ALIASES["both"])
    return None if set(chosen) == set(GAMES) else tuple(g for g in GAMES if g in chosen)
