"""Austauschbare Preisquellen. Alle Preise in EUR."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class PriceQuote:
    """Aktueller Referenzpreis einer Kartenvariante (z. B. normal / foil / reverse holo)."""

    source: str  # z. B. "cardmarket-priceguide"
    variant: str  # "normal" | "foil" (Magic) | "reverse" (Pokémon Reverse Holo)
    trend: float | None = None
    low: float | None = None
    avg: float | None = None
    avg1: float | None = None
    avg7: float | None = None
    avg30: float | None = None
    as_of: str | None = None  # Erstellungszeitpunkt der Quelle (ISO)
    currency: str = "EUR"

    @property
    def main(self) -> float | None:
        return self.trend if self.trend is not None else self.avg


@dataclass
class PricePoint:
    day: str  # YYYY-MM-DD
    price: float
    source: str


@dataclass
class PriceInfo:
    """Alles, was das Overlay für eine Karte anzeigt."""

    id_product: int | None
    quotes: dict[str, PriceQuote] = field(default_factory=dict)  # variant -> Preis
    history: list[PricePoint] = field(default_factory=list)  # Hauptvariante, aufsteigend
    changes: dict[str, float] = field(default_factory=dict)  # "7T"/"30T"/"90T" -> Prozent
    note: str = ""

    @property
    def primary(self) -> PriceQuote | None:
        return self.quotes.get("normal") or next(iter(self.quotes.values()), None)


class PriceProvider(ABC):
    name = "base"

    @abstractmethod
    def quotes(self, game: str, id_product: int) -> dict[str, PriceQuote]:
        """Aktuelle Preise aller Varianten eines Cardmarket-Produkts."""

    def refresh(self, force: bool = False) -> bool:
        """Daten aktualisieren (z. B. tägliche Datei laden). True, wenn neue Daten vorliegen."""
        return False
