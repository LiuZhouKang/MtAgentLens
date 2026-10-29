"""Unicode-safety helpers for the quarterly handoff audit."""

from .manifest import build_manifest, parse_ledger, parse_protocol_tokens, write_report
from .normalize import (
    confusable_spans,
    fold_homoglyphs,
    normalize_token,
    strip_zero_width,
    tokens_match,
    zero_width_spans,
)

__all__ = [
    "build_manifest",
    "confusable_spans",
    "fold_homoglyphs",
    "normalize_token",
    "parse_ledger",
    "parse_protocol_tokens",
    "strip_zero_width",
    "tokens_match",
    "write_report",
    "zero_width_spans",
]
