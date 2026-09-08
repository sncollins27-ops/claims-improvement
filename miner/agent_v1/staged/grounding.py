"""Deterministic quote and number grounding used by the staged runtime and the local evaluator.

The validator's deterministic grounding pass checks that span ids exist and
that quotes appear in the referenced span (whitespace-normalised). Its rigor
agent additionally checks that every load-bearing number in a claim appears in
connected quotes. This module makes both checks locally and can repair a quote
by relocating it to the exact source substring.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from typing import Any


_WS = re.compile(r"\s+")
_NUMBER = re.compile(r"(?<![A-Za-z0-9\-/_.])[-+]?\d+(?:[.,]\d+)*(?:\s*[×x]\s*10\s*[\^]?\s*[-−]?\d+)?(?![A-Za-z0-9/_])")
_QUOTE_CHARS = str.maketrans({
    "‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-", "−": "-",
    " ": " ", "ﬁ": "fi", "ﬂ": "fl",
})


def normalize_text(text: str) -> str:
    return _WS.sub(" ", str(text or "").translate(_QUOTE_CHARS)).strip().casefold()


def _normalized_with_map(text: str) -> tuple[str, list[int]]:
    """Return normalised text and a map from normalised index -> original index."""
    translated = str(text or "").translate(_QUOTE_CHARS)
    out_chars: list[str] = []
    index_map: list[int] = []
    previous_space = True
    for index, char in enumerate(translated):
        if char.isspace():
            if previous_space:
                continue
            out_chars.append(" ")
            index_map.append(index)
            previous_space = True
            continue
        out_chars.append(char.casefold())
        index_map.append(index)
        previous_space = False
    # trim trailing space
    while out_chars and out_chars[-1] == " ":
        out_chars.pop()
        index_map.pop()
    return "".join(out_chars), index_map


@dataclass(frozen=True)
class QuoteMatch:
    quote: str
    method: str  # exact | normalized | fuzzy
    similarity: float


def locate_quote(span_text: str, quote: str, *, min_fuzzy_ratio: float = 0.86, min_chars: int = 20) -> QuoteMatch | None:
    """Find ``quote`` in ``span_text`` and return the exact source substring.

    Order: exact substring, whitespace/quote-normalised substring, then fuzzy
    (longest matching block covering most of the quote). Returns None when the
    quote cannot be grounded.
    """
    quote = str(quote or "").strip()
    if len(quote) < min_chars:
        return None
    if quote in span_text:
        return QuoteMatch(quote=quote, method="exact", similarity=1.0)
    span_norm, index_map = _normalized_with_map(span_text)
    quote_norm = normalize_text(quote)
    if not quote_norm or not span_norm:
        return None
    position = span_norm.find(quote_norm)
    if position >= 0:
        start = index_map[position]
        end = index_map[position + len(quote_norm) - 1] + 1
        return QuoteMatch(quote=span_text[start:end], method="normalized", similarity=1.0)
    # Fuzzy: find the largest matching block, then extend to cover the quote length.
    matcher = difflib.SequenceMatcher(None, span_norm, quote_norm, autojunk=False)
    blocks = [block for block in matcher.get_matching_blocks() if block.size > 0]
    if not blocks:
        return None
    best = max(blocks, key=lambda block: block.size)
    if best.size < max(min_chars, int(min_fuzzy_ratio * len(quote_norm))):
        # Try a windowed ratio around the best block.
        window_start = max(0, best.a - best.b)
        window_end = min(len(span_norm), window_start + len(quote_norm))
        window = span_norm[window_start:window_end]
        ratio = difflib.SequenceMatcher(None, window, quote_norm, autojunk=False).ratio()
        if ratio < min_fuzzy_ratio or len(window) < min_chars:
            return None
        start = index_map[window_start]
        end = index_map[window_end - 1] + 1
        return QuoteMatch(quote=span_text[start:end], method="fuzzy", similarity=round(ratio, 4))
    start = index_map[best.a]
    end = index_map[best.a + best.size - 1] + 1
    return QuoteMatch(quote=span_text[start:end], method="fuzzy", similarity=round(best.size / max(1, len(quote_norm)), 4))


def quote_in_span(span_text: str, quote: str) -> bool:
    if not quote:
        return False
    if quote in span_text:
        return True
    return normalize_text(quote) in normalize_text(span_text)


def numbers_in(text: str) -> list[str]:
    values: list[str] = []
    for match in _NUMBER.finditer(str(text or "")):
        token = match.group(0)
        token = _WS.sub("", token).replace(",", "").replace("−", "-").replace("×", "x").lstrip("+")
        # Drop leading sign for comparison, keep the digits and dot.
        token = token.lstrip("-")
        if not token or token in {"0"} and False:
            continue
        values.append(token)
    return values


def ungrounded_numbers(text: str, quotes: list[str]) -> list[str]:
    """Numbers in ``text`` that do not appear in any quote (after normalisation)."""
    quote_numbers = set()
    for quote in quotes:
        quote_numbers.update(numbers_in(quote))
    haystack = normalize_text(" ".join(quotes)).replace(",", "")
    missing: list[str] = []
    for number in numbers_in(text):
        if number in quote_numbers:
            continue
        if number in haystack:
            continue
        # Allow "1000" vs "1,000" and trailing-zero variations like 0.50 vs 0.5
        try:
            value = float(number)
            if any(_close(value, float(other)) for other in quote_numbers):
                continue
        except ValueError:
            pass
        missing.append(number)
    return sorted(set(missing))


def _close(left: float, right: float) -> bool:
    return abs(left - right) <= 1e-9 * max(1.0, abs(left), abs(right))


def statement_tokens(statement: str) -> set[str]:
    return {token for token in re.findall(r"[a-z0-9]+", normalize_text(statement)) if len(token) > 2}


def jaccard(left: str, right: str) -> float:
    a = statement_tokens(left)
    b = statement_tokens(right)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def span_index(source_payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    spans = source_payload.get("spans") if isinstance(source_payload, dict) else None
    index: dict[str, dict[str, Any]] = {}
    for span in spans or []:
        if isinstance(span, dict) and span.get("span_id"):
            index[str(span["span_id"])] = span
    return index


# --------------------------------------------------------------- claim hygiene
# Deliberately conservative: dropping a good claim costs coverage, which is the
# scoring term we care most about, while a bad claim costs 0.25 of quality on
# one paper. Every rule here was measured against 5,497 real claims and kept
# only if its false-positive count stayed near zero.

_IDENTIFIER = re.compile(r"\b[A-Za-z][A-Za-z0-9]*\d+[A-Za-z0-9]*\b")
# Only units where a wrong prefix changes the science by orders of magnitude.
_UNIT = re.compile(
    r"(?<![A-Za-z0-9])([-+]?\d+(?:[.,]\d+)*)\s*"
    r"(\u00b5M|\u03bcM|uM|mM|nM|pM|mg/mL|\u00b5g/mL|mg|\u00b5g|\u03bcg|ug|ng|\u00b0C|Gy|MOI)"
    r"(?![A-Za-z0-9])"
)
_COMPACT = re.compile(r"[\s\u2010-\u2015_/,]+")

# Statements that are never a scientific proposition of the paper.
_NON_CLAIM = re.compile(
    r"(data (are|is|were) available (up)?on request|available from the corresponding author|"
    r"data availability|code availability|"
    r"written informed consent|institutional review board|ethics (committee|approval)|approved by the institutional|"
    r"^\s*(an? )?(unpaired|paired|two-tailed|two-sided|one-way|two-way)?\s*(student'?s )?t-?test was used|"
    r"^\s*statistical (analyses|analysis) (were|was) performed|"
    r"(graphpad prism|spss) (was|were) used)",
    re.IGNORECASE,
)
_PLACEHOLDER = re.compile(r"^(not available|not specified|n/?a)\b", re.IGNORECASE)
_SUFFIX = re.compile(r"[-\u2010-\u2015][a-z]+$")


def compact(text: str) -> str:
    """Lowercase and strip spacing/hyphens so 'CD8 + T' and 'CD8+T' compare equal."""
    return _COMPACT.sub("", normalize_text(text))


def identifiers(text: str) -> set[str]:
    """Distinctive entity tokens: names carrying digits (SOCS2, IRS1, BNT162b2, E0771).

    Trailing descriptive suffixes are stripped ("PKMYT1-depleted" -> "PKMYT1"),
    since only the entity itself needs to exist in the paper.
    """
    found = set()
    for match in _IDENTIFIER.finditer(str(text or "")):
        token = _SUFFIX.sub("", match.group(0))
        if len(token) < 4 or not any(ch.isdigit() for ch in token):
            continue
        if token.lower() in {"covid19", "sars2", "cov2", "h2o2", "co2", "o2", "n2"}:
            continue
        found.add(token)
    return found


def ungrounded_identifiers(statement: str, haystack: str) -> list[str]:
    """Entity names in the statement that appear nowhere in the paper (typos, fabrications)."""
    packed = compact(haystack)
    return sorted(token for token in identifiers(statement) if compact(token) not in packed)


def ungrounded_measurements(statement: str, haystack: str) -> list[str]:
    """Concentration/mass/temperature values absent from the paper (catches 2 uM vs 2 mM)."""
    packed = compact(haystack)
    missing = []
    for value, unit in _UNIT.findall(str(statement or "")):
        if compact(f"{value}{unit}") not in packed:
            missing.append(f"{value} {unit}")
    return sorted(set(missing))


def is_non_claim(statement: str) -> bool:
    text = str(statement or "").strip()
    if len(text) < 25 or _PLACEHOLDER.match(text):
        return True
    return bool(_NON_CLAIM.search(text))


def duplicate_signature(statement: str) -> tuple[frozenset[str], frozenset[str]]:
    """(identifiers, numbers) - two claims sharing both usually restate one proposition."""
    return frozenset(compact(t) for t in identifiers(statement)), frozenset(numbers_in(statement))
