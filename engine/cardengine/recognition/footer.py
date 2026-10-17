"""Sammlernummer und Set-Code aus dem OCR-Text der Kartenfußzeile lesen (Magic ab 2015, Pokémon)."""
from __future__ import annotations

import re
from dataclasses import dataclass

MTG_LANGS = "EN|DE|FR|IT|ES|JA|JP|PT|RU|KO|ZH|PH"


@dataclass
class FooterInfo:
    number: str | None = None  # ohne führende Nullen
    total: int | None = None  # Pokémon-Setgröße ("123/165")
    set_code: str | None = None  # Magic-Set-Code ("DMU")
    digits: str = ""  # alle Ziffern (Fallback, wenn "/" als Ziffer gelesen wurde)


def norm_number(n: str) -> str:
    n = n.strip().upper()
    return n.lstrip("0") or "0" if n.isdigit() else n


def parse_footer(text: str) -> FooterInfo:
    t = re.sub(r"\s+", " ", text.replace("|", "/"))
    info = FooterInfo(digits=re.sub(r"\D", "", t))
    # Pokémon: "151/165"; OCR klebt oft Set-Kürzel/Regulierungszeichen davor ("MEN151", "D020") → nur die Ziffern
    # vor dem Schrägstrich zählen. Ausnahme: Trainer-Galerie u. ä. ("TG05/TG30", "GG12/GG70").
    m = re.search(r"\b(TG|GG|SV|RC)(\d{1,3})\s?/\s?(?:TG|GG|SV|RC)?(\d{2,3})\b", t) or re.search(
        r"(\d{1,3})\s?/\s?(\d{2,3})\b", t
    )
    if m:
        if len(m.groups()) == 3:
            info.number = m.group(1) + m.group(2)
        else:
            info.number = norm_number(m.group(1))
            info.total = int(m.group(2))
    # Magic: "FDC • EN" – der Punkt wird von der OCR beliebig gelesen ("?", "*", "+", nichts)
    s = re.search(rf"\b([A-Z0-9]{{3,5}}?)[^A-Za-z0-9]{{0,4}}(?:{MTG_LANGS})\b", t)
    if s and not s.group(1).isdigit():
        info.set_code = s.group(1)
    if not info.number:
        # Magic: "0123 R" (bis 2023) bzw. "U 0163" / "U0163" (Seltenheit vor der Nummer, ab 2024)
        n = re.search(r"\b(\d{1,4})\s?[CURMSLTP]\b", t) or re.search(r"\b[CURMSLTP]\s?(\d{3,4})\b", t)
        if n:
            info.number = norm_number(n.group(1))
    return info


def matches(rec, info: FooterInfo) -> int:
    """Wie gut passt ein Katalogeintrag zur gelesenen Fußzeile? 0 = gar nicht, höher = besser."""
    score = 0
    num = norm_number(rec.number)
    if info.number and info.number == num:
        score += 2
    if info.total and rec.set_total and info.total == rec.set_total:
        score += 1
    if info.set_code and rec.set_code.upper() == info.set_code:
        score += 2
    if not score and info.digits and rec.set_total and num.isdigit():
        # "/" als Ziffer gelesen: "199/165" -> "1997165"
        if re.search(rf"0*{num}\d?0*{rec.set_total}", info.digits):
            score += 1
    return score
