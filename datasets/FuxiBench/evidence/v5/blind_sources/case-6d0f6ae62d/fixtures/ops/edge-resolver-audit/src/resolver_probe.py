"""Helpers shared by the resolver audit harness."""
from __future__ import annotations


def normalize_name(name: str) -> str:
    """Lower-case a resolver name and drop a trailing dot."""
    return name.strip().rstrip(".").lower()


def label_width_ok(name: str, limit: int = 63) -> bool:
    """True when every label of ``name`` fits the DNS label limit."""
    return all(0 < len(label) <= limit for label in normalize_name(name).split("."))


def total_length(name: str) -> int:
    """Wire length of a name: one length octet per label plus the terminator."""
    parts = [part for part in normalize_name(name).split(".") if part]
    return sum(len(part) + 1 for part in parts) + 1


def chunk(text: str, width: int) -> list[str]:
    """Split ``text`` into fixed-width chunks, dropping an empty tail."""
    return [text[i : i + width] for i in range(0, len(text), width)]


def is_hex_label(label: str) -> bool:
    """True for a non-empty label made only of hex digits."""
    return bool(label) and all(char in "0123456789abcdefABCDEF" for char in label)
