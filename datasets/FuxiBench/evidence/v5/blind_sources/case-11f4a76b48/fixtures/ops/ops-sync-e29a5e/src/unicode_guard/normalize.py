"""Token normalisation for the handoff audit.

The relay ledger prints approval tokens in an extended alphabet: the visible
glyphs match ASCII, but some code points come from another script or carry
zero-width separators used for column alignment. These helpers fold a token
back to the canonical ASCII form so stations can compare tokens printed in
different forms.
"""

from __future__ import annotations

import unicodedata

ZERO_WIDTH = frozenset(
    {
        "​",  # zero width space
        "‌",  # zero width non-joiner
        "‍",  # zero width joiner
        "⁠",  # word joiner
        "﻿",  # zero width no-break space
    }
)

# Cyrillic and lookalike code points that render as ASCII in the relay font.
HOMOGLYPHS = {
    "А": "A",
    "В": "B",
    "С": "C",
    "Е": "E",
    "К": "K",
    "М": "M",
    "Н": "H",
    "О": "O",
    "Р": "P",
    "Т": "T",
    "Х": "X",
    "а": "a",
    "е": "e",
    "о": "o",
    "р": "p",
    "с": "c",
    "у": "y",
    "і": "i",
    "‐": "-",  # hyphen
    "‑": "-",  # non-breaking hyphen
    "–": "-",  # en dash
    "—": "-",  # em dash
}


def strip_zero_width(text: str) -> str:
    """Remove zero-width separators from *text*."""
    return "".join(ch for ch in text if ch not in ZERO_WIDTH)


def fold_homoglyphs(text: str) -> str:
    """Map lookalike code points to their ASCII equivalents."""
    return "".join(HOMOGLYPHS.get(ch, ch) for ch in text)


def normalize_token(token: str) -> str:
    """Return the canonical ASCII form of an approval token."""
    return fold_homoglyphs(strip_zero_width(unicodedata.normalize("NFKC", token)))


def confusable_spans(token: str) -> list[dict[str, object]]:
    """Describe every lookalike code point in *token*."""
    spans = []
    for index, ch in enumerate(token):
        replacement = HOMOGLYPHS.get(ch)
        if replacement is not None:
            spans.append(
                {
                    "index": index,
                    "codepoint": f"U+{ord(ch):04X}",
                    "replacement": replacement,
                }
            )
    return spans


def zero_width_spans(token: str) -> list[int]:
    """Return the indexes of zero-width separators in *token*."""
    return [index for index, ch in enumerate(token) if ch in ZERO_WIDTH]


def tokens_match(left: str, right: str) -> bool:
    """Compare two token spellings after canonicalisation."""
    return normalize_token(left) == normalize_token(right)
