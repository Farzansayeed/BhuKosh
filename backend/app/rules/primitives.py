"""Deterministic primitives for the UP-profile rules (plan §6 rulebook v1).

Pure functions, no DB, no engine — every rule is reproducible and versioned.
"""
import re

DEVANAGARI_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")


def normalize_digits(s: str) -> str:
    """Devanagari digits (०-९) → ASCII; leaves ASCII untouched."""
    return (s or "").translate(DEVANAGARI_DIGITS)


def parse_area_bigha(raw: str | None) -> float | None:
    """Parse a printed area string into bigha (decimal), or None if unparseable.

    Plan §7's comparison scenario reads '२-४०' as 2.40 bigha, so the convention
    here is decimal-style: bigha + biswa/100 (+ biswansi/10000 when present).
    Accepts Devanagari or ASCII digits, '-' or '–' separators, optional unit.
    """
    if not raw:
        return None
    s = normalize_digits(raw).strip()
    m = re.fullmatch(
        r"(\d+)\s*[-–]\s*(\d+)(?:\s*[-–]\s*(\d+))?\s*(bigha|बीघा)?", s, re.IGNORECASE
    )
    if not m:
        return None
    bigha, biswa = int(m.group(1)), int(m.group(2))
    biswansi = int(m.group(3)) if m.group(3) else 0
    return bigha + biswa / 100 + biswansi / 10000


def khasra_pattern_ok(raw: str | None) -> bool:
    """Khasra/khewat/gata numbers: digits with optional / or - separators."""
    if not raw:
        return False
    v = normalize_digits(raw).strip().replace(" ", "")
    return bool(re.fullmatch(r"\d+(?:[-/]\d+)*", v))
