"""
python-services/shared/plate_normalize.py

Kazakhstan plate normalisation (spec §9.3, §9A.7). Implements:
  - Cyrillic → Latin lookalike replacement
  - format detection: kz_new (2012+), kz_old (A-type), kz_diplo (D/T/HC/M/H/F),
    and `unknown` for invalid/foreign
  - position-aware OCR confusion fix (O/0, I/1, B/8, …)
  - region-code extraction + KZ 01–20 region name map
  - `is_diplomatic` hint (spec §9 — diplomatic plates often escape fines;
    business rule decided downstream)

All functions are pure / synchronous and safe for unit testing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

# --------------------------------------------------------------------------- #
# Regex — spec §9.3
# --------------------------------------------------------------------------- #
KZ_NEW_RE = re.compile(r"^(\d{3})([A-Z]{3})(\d{2})$")
KZ_OLD_RE = re.compile(r"^([A-Z])(\d{3})([A-Z]{2,3})$")
# Diplomatic prefixes: D (diplomat), T (technical), HC (head consul),
# M (mission), H, F (foreign individual).
KZ_DIPLO_RE = re.compile(r"^(D|T|HC|M|H|F)(\d{3})([A-Z]{2,3})$")

# Valid region codes (spec §9.3 table).
KZ_VALID_REGIONS: frozenset[str] = frozenset(
    {f"{n:02d}" for n in range(1, 21)}
)

# Cyrillic → Latin lookalikes (KZ/RU camera OCR often returns Cyrillic).
CYRILLIC_TO_LATIN: dict[str, str] = {
    "А": "A", "В": "B", "Е": "E", "К": "K", "М": "M", "Н": "H",
    "О": "O", "Р": "P", "С": "C", "Т": "T", "У": "Y", "Х": "X",
    "І": "I",
}

# Position-aware fix maps (spec §9.3).
DIGIT_FIX: dict[str, str] = {
    "O": "0", "D": "0", "Q": "0", "I": "1", "L": "1",
    "Z": "2", "S": "5", "B": "8", "G": "6",
}
LETTER_FIX: dict[str, str] = {
    "0": "O", "1": "I", "2": "Z", "5": "S", "8": "B", "6": "G",
}

PlateFormat = Literal["kz_new", "kz_old", "kz_diplo", "unknown"]


# --------------------------------------------------------------------------- #
# Region names (spec §9A.7) — KZ + RU transliterated
# --------------------------------------------------------------------------- #
KZ_REGION_NAMES: dict[str, str] = {
    "01": "Astana (Нұр-Сұлтан)",
    "02": "Almaty qalasy",
    "03": "Aqmola oblysy",
    "04": "Aqtöbe oblysy",
    "05": "Almaty oblysy",
    "06": "Atyrau oblysy",
    "07": "Batys Qazaqstan (Oral)",
    "08": "Zhambyl (Taraz)",
    "09": "Qaraghandy",
    "10": "Qostanai",
    "11": "Qyzylorda",
    "12": "Mangystau (Aqtau)",
    "13": "Türkistan",
    "14": "Pavlodar",
    "15": "Soltüstik Qazaqstan (Petropavl)",
    "16": "Shymkent qalasy",
    "17": "Shyghys Qazaqstan",
    "18": "Abai oblysy",
    "19": "Jetisu oblysy",
    "20": "Ulytau oblysy",
}


@dataclass(frozen=True, slots=True)
class NormalizedPlate:
    """Result of `normalize_kz_plate` — structured, easy to log/persist."""

    raw: str
    text: str | None
    format_type: PlateFormat
    region_code: str | None
    region_name: str | None
    is_valid: bool
    is_diplomatic: bool


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def normalize_kz_plate(raw: str) -> NormalizedPlate:
    """
    Normalise an OCR output to canonical KZ plate form. Returns a structured
    `NormalizedPlate` — callers pick what they need. Never raises.
    """
    if not raw:
        return _unknown("")

    cleaned = _cyrillic_to_latin(raw.upper())
    cleaned = re.sub(r"[^A-Z0-9]", "", cleaned)

    if not (6 <= len(cleaned) <= 9):
        return _unknown(raw, text=cleaned or None)

    # ---- kz_new (DDDLLLDD) ----
    if len(cleaned) == 8:
        fixed = _fix_by_position_new(cleaned)
        m = KZ_NEW_RE.match(fixed)
        if m and m.group(3) in KZ_VALID_REGIONS:
            region = m.group(3)
            return NormalizedPlate(
                raw=raw,
                text=fixed,
                format_type="kz_new",
                region_code=region,
                region_name=KZ_REGION_NAMES.get(region),
                is_valid=True,
                is_diplomatic=False,
            )

    # ---- kz_diplo (D/T/HC/M/H/F prefix) ----
    # Check diplo BEFORE kz_old: a plate like "D123AB" also matches the
    # old A-type regex (one letter + 3 digits + 2 letters), but spec §9.3
    # reserves D/T/HC/M/H/F prefixes for diplomatic missions — treat
    # those as diplomatic unambiguously.
    m = KZ_DIPLO_RE.match(cleaned)
    if m:
        return NormalizedPlate(
            raw=raw,
            text=cleaned,
            format_type="kz_diplo",
            region_code=None,
            region_name=None,
            is_valid=True,
            is_diplomatic=True,
        )

    # ---- kz_old (L DDD LL(L)) ----
    if 6 <= len(cleaned) <= 7:
        fixed = _fix_by_position_old(cleaned)
        m = KZ_OLD_RE.match(fixed)
        if m:
            return NormalizedPlate(
                raw=raw,
                text=fixed,
                format_type="kz_old",
                region_code=None,
                region_name=None,
                is_valid=True,
                is_diplomatic=False,
            )

    return _unknown(raw, text=cleaned)


def extract_region_code(plate: str | None, format_type: PlateFormat) -> str | None:
    """Return the 2-digit region code, or None for non-kz_new formats."""
    if plate and format_type == "kz_new" and len(plate) == 8:
        code = plate[-2:]
        return code if code in KZ_VALID_REGIONS else None
    return None


def is_plate_likely_valid(p: NormalizedPlate, min_confidence: float = 0.0) -> bool:
    """Convenience predicate for downstream filters (plate_votes, etc.)."""
    return p.is_valid and p.text is not None


# --------------------------------------------------------------------------- #
# Internals
# --------------------------------------------------------------------------- #
def _unknown(raw: str, *, text: str | None = None) -> NormalizedPlate:
    return NormalizedPlate(
        raw=raw,
        text=text,
        format_type="unknown",
        region_code=None,
        region_name=None,
        is_valid=False,
        is_diplomatic=False,
    )


def _cyrillic_to_latin(s: str) -> str:
    return "".join(CYRILLIC_TO_LATIN.get(c, c) for c in s)


def _fix_by_position_new(s: str) -> str:
    """kz_new: DDD LLL DD — positions 0-2 digit, 3-5 letter, 6-7 digit."""
    if len(s) != 8:
        return s
    out: list[str] = []
    for i, c in enumerate(s):
        if i in (0, 1, 2, 6, 7):
            out.append(DIGIT_FIX.get(c, c))
        else:
            out.append(LETTER_FIX.get(c, c))
    return "".join(out)


def _fix_by_position_old(s: str) -> str:
    """kz_old: L DDD LL(L) — position 0 letter, 1-3 digit, rest letter."""
    out: list[str] = []
    for i, c in enumerate(s):
        if i == 0:
            out.append(LETTER_FIX.get(c, c))
        elif 1 <= i <= 3:
            out.append(DIGIT_FIX.get(c, c))
        else:
            out.append(LETTER_FIX.get(c, c))
    return "".join(out)


__all__ = [
    "CYRILLIC_TO_LATIN",
    "DIGIT_FIX",
    "KZ_DIPLO_RE",
    "KZ_NEW_RE",
    "KZ_OLD_RE",
    "KZ_REGION_NAMES",
    "KZ_VALID_REGIONS",
    "LETTER_FIX",
    "NormalizedPlate",
    "PlateFormat",
    "extract_region_code",
    "is_plate_likely_valid",
    "normalize_kz_plate",
]
