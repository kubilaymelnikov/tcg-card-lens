"""Sammlernummer und Set-Code aus dem OCR-Text der Kartenfußzeile lesen (Magic ab 2015, Pokémon) sowie den
Yu-Gi-Oh-Set-Code („LOB-DE001“) unter dem Artwork."""
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


# ---------------------------------------------------------------- Yu-Gi-Oh
# Regionskennung im Set-Code: heute EN/DE/FR/IT/SP/PT/JP/KR/AE, früher E/G/F/I/S/P (Europa), ohne = Nordamerika.
# Der Katalog (YGOPRODeck) kennt nur die englischen Codes → Vergleich über einen regionsunabhängigen Schlüssel.
YGO_REGIONS = ("EN", "DE", "FR", "IT", "SP", "PT", "JP", "JA", "KR", "AE", "E", "G", "F", "I", "S", "P", "J", "K", "A")
_YGO_CODE = re.compile(rf"^([A-Z0-9]{{2,5}})-(?:{'|'.join(YGO_REGIONS)})?([A-Z]{{0,3}}\d{{1,4}}[A-Z]?|TKN)$")
_YGO_IN_TEXT = re.compile(r"(?<![A-Z0-9])([A-Z0-9]{2,5}) ?[-‐‑–—_~.] ?([A-Z]{0,4}[0-9OIL]{2,4}[A-Z]?)(?![A-Z0-9])")


def ygo_code_key(code: str) -> str | None:
    """Sprach-/regionsunabhängiger Schlüssel: "LOB-DE001", "LOB-G001", "LOB-EN001", "LOB-001" → "LOB-001".
    O/0 und I/1 werden im Präfix gleich behandelt (OCR verwechselt sie, Präfixe enthalten beides)."""
    m = _YGO_CODE.match(code.strip().upper())
    if not m:
        return None
    prefix, num = m.groups()
    return f"{prefix.replace('O', '0').replace('I', '1')}-{num}"


def parse_ygo_codes(text: str) -> list[str]:
    """Alle plausiblen Set-Codes im OCR-Text als Schlüssel (siehe `ygo_code_key`), Reihenfolge wie gelesen."""
    out: list[str] = []
    for prefix, rest in _YGO_IN_TEXT.findall(text.upper()):
        # In der Nummer liest die OCR Ziffern gern als Buchstaben ("DEOO1", "ENI23")
        m = re.match(r"^([A-Z]*?)([0-9OIL]+)([A-Z]?)$", rest)
        if m:
            digits = m.group(2).replace("O", "0").replace("I", "1").replace("L", "1")
            rest = m.group(1) + digits + m.group(3)
        key = ygo_code_key(f"{prefix}-{rest}")
        if key and key not in out:
            out.append(key)
    return out
