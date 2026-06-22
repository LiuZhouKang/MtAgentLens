#!/usr/bin/env python3
"""
Phase 1: Handoff Unicode Homoglyph & Zero-Width Attack Detector

Implements the normalize_handoff() function per spec, then tests it against:
  1. Cyrillic homoglyph attacks (forging tokens with lookalike chars)
  2. Zero-width character hidden payloads

Reports which attacks are DETECTED vs BYPASSED.
"""

import unicodedata
import json
import re
import sys

# ─────────────────────────────────────────────────
# Part A: Spec-compliant normalize_handoff()
# ─────────────────────────────────────────────────

def normalize_handoff(text: str) -> str:
    """Per-spec implementation (Section 'Normalization Procedure')."""
    # NFC normalization
    text = unicodedata.normalize('NFC', text)
    # Strip bidirectional control characters
    text = text.replace('‮', '').replace('‭', '')
    return text


# ─────────────────────────────────────────────────
# Part B: Attack detection helpers (for auditing)
# ─────────────────────────────────────────────────

# Known Unicode homoglyphs that look like ASCII letters
HOMOGLYPH_MAP = {
    # Cyrillic lookalikes
    'а': 'a',  # Cyrillic а → Latin a
    'е': 'e',  # Cyrillic е → Latin e
    'о': 'o',  # Cyrillic о → Latin o
    'р': 'p',  # Cyrillic р → Latin p
    'с': 'c',  # Cyrillic с → Latin c
    'х': 'x',  # Cyrillic х → Latin x
    'і': 'i',  # Cyrillic і → Latin i
    'ј': 'j',  # Cyrillic ј → Latin j
    'в': 'b',  # Cyrillic в → (not exact homoglyph but listed for awareness)
    'к': 'k',  # Cyrillic к → Latin k
    'м': 'm',  # Cyrillic м → Latin m
    'н': 'h',  # Cyrillic н → Latin h
    'т': 't',  # Cyrillic т → Latin t
    'у': 'y',  # Cyrillic у → Latin y
    # Greek lookalikes
    'Α': 'A',  # Greek Α
    'Β': 'B',  # Greek Β
    'Ε': 'E',  # Greek Ε
    'Η': 'H',  # Greek Η
    'Ι': 'I',  # Greek Ι
    'Κ': 'K',  # Greek Κ
    'Μ': 'M',  # Greek Μ
    'Ν': 'N',  # Greek Ν
    'Ο': 'O',  # Greek Ο
    'Ρ': 'P',  # Greek Ρ
    'Τ': 'T',  # Greek Τ
    'Υ': 'Y',  # Greek Υ
    'Χ': 'X',  # Greek Χ
}

ZERO_WIDTH_CHARS = {
    'U+200B': 'ZERO WIDTH SPACE',
    'U+200C': 'ZERO WIDTH NON-JOINER',
    'U+200D': 'ZERO WIDTH JOINER',
    'U+FEFF': 'ZERO WIDTH NO-BREAK SPACE (BOM)',
    'U+200E': 'LEFT-TO-RIGHT MARK',
    'U+200F': 'RIGHT-TO-LEFT MARK',
    'U+2060': 'WORD JOINER',
    'U+2061': 'FUNCTION APPLICATION',
    'U+2062': 'INVISIBLE TIMES',
    'U+2063': 'INVISIBLE SEPARATOR',
    'U+2064': 'INVISIBLE PLUS',
}

CONTROL_CHARS = set(chr(c) for c in range(0x0000, 0x0020)) | \
                set(chr(c) for c in range(0x007f, 0x00a0))


def has_homoglyph(text: str) -> list:
    """Scan text for known homoglyph characters; return list of (position, char, looks_like)."""
    found = []
    for i, ch in enumerate(text):
        if ch in HOMOGLYPH_MAP:
            found.append((i, repr(ch), HOMOGLYPH_MAP[ch], f'U+{ord(ch):04X}'))
    return found


def has_zero_width(text: str) -> list:
    """Scan text for zero-width / invisible characters."""
    found = []
    for i, ch in enumerate(text):
        cp = ord(ch)
        if cp in [0x200B, 0x200C, 0x200D, 0xFEFF, 0x200E, 0x200F,
                  0x2060, 0x2061, 0x2062, 0x2063, 0x2064]:
            found.append((i, repr(ch), f'U+{cp:04X}'))
    return found


def has_forbidden_control(text: str) -> list:
    """Check for control chars that MUST be escaped per spec."""
    found = []
    for i, ch in enumerate(text):
        if ch in CONTROL_CHARS:
            found.append((i, repr(ch), f'U+{ord(ch):04X}'))
    return found


# ─────────────────────────────────────────────────
# Part C: Token forgery simulation
# ─────────────────────────────────────────────────

# Tokens an attacker might try to forge via homoglyphs
TOKEN_PATTERNS = [
    'sk-',           # OpenAI-style
    'ghp_',          # GitHub PAT
    'gho_',          # GitHub OAuth
    'Bearer ',       # Bearer auth header
    'api-',          # Generic API prefix
    'eyJ',           # JWT base64 start
    'AKIA',          # AWS Access Key
    'xoxb-',         # Slack Bot
    'xoxp-',         # Slack Personal
]


def forge_token(original: str) -> str:
    """Replace Latin 'e' with Cyrillic 'е' to forge a lookalike token."""
    return original.replace('e', 'е').replace('E', 'Ε')


def scan_for_forged_tokens(text: str) -> list:
    """Check if homoglyph-forged token patterns exist."""
    alerts = []
    for pattern in TOKEN_PATTERNS:
        # Check if the text contains the pattern with Cyrillic lookalikes
        forged = forge_token(pattern)
        if forged in text:
            alerts.append(f"  ⚠  FORGED TOKEN pattern detected: '{pattern}' → '{forged}'")
    return alerts


# ─────────────────────────────────────────────────
# Part D: Test cases
# ─────────────────────────────────────────────────

def run_test(name: str, payload: str, is_attack: bool) -> dict:
    """Run normalize_handoff on payload and audit results."""
    original = payload
    result = normalize_handoff(payload)

    # Post-normalization audit
    homo = has_homoglyph(result)
    zw = has_zero_width(result)
    ctrl = has_forbidden_control(result)
    forged = scan_for_forged_tokens(result)

    # Did normalization actually stop the attack?
    attack_stopped = False
    if is_attack:
        # Attack is "stopped" if:
        # 1. Homoglyphs were removed (they aren't, per spec) AND
        # 2. The token pattern no longer appears as forged
        remaining_forged = scan_for_forged_tokens(result)
        if not remaining_forged:
            attack_stopped = True
        # Also check if the result is plainly safe
        if not homo and not forged:
            attack_stopped = True

    verdict = "DETECTED (bypassed normalization)" if (is_attack and not attack_stopped) else \
              ( "DETECTED (mitigated)" if (is_attack and attack_stopped) else "CLEAN" )

    return {
        "name": name,
        "is_attack": is_attack,
        "attack_stopped": attack_stopped,
        "verdict": verdict,
        "homoglyphs_found": homo,
        "zero_width_found": zw,
        "controls_found": ctrl,
        "forged_tokens_found": forged,
    }


def build_test_suite():
    cases = []

    # ── Homoglyph Attacks ──────────────────────

    # H1: Cyrillic 'е' in "Bearer" token
    cases.append((
        "H1: Cyrillic 'е' forges Bearer token",
        'Authorization: Bеarеr sk-sеcrеt-kеy-123',
        True
    ))

    # H2: Cyrillic in API key prefix
    cases.append((
        "H2: Cyrillic in OpenAI-style key",
        'sk-' + 'е' + 'yJ-cyrillic-homoglyph-test',
        True
    ))

    # H3: Greek lookalike in JWT
    cases.append((
        "H3: Greek Α for A in AWS key",
        'AKIA' + 'Ι' + 'L2ZEXAMPLE',
        True
    ))

    # H4: Mixed Cyrillic/Greek in handoff instructions
    cases.append((
        "H4: Homoglyph in natural-language handoff",
        'Please use thе API kеy from thе сonfig filе',
        True
    ))

    # H5: Legitimate handoff (no attack)
    cases.append((
        "H5: Clean handoff (benign)",
        'Please use the API key from the config file at /etc/app/config.toml',
        False
    ))

    # ── Zero-Width Character Attacks ───────────

    # Z1: Zero-width space in handoff instructions
    payload = "exec​ute the transfer to account 0x1234"
    cases.append((
        "Z1: ZWS hidden in 'execute' (makes it 'exec​ute')",
        payload,
        True
    ))

    # Z2: Hidden payload in otherwise benign text
    # Using zero-width joiners to smuggle a command
    payload_hidden = "no‍op‍--ignore-check"
    cases.append((
        "Z2: ZWJ-smuggled hidden flag",
        f"Run with defaults {payload_hidden}",
        True
    ))

    # Z3: BOM prefixed key
    cases.append((
        "Z3: BOM (U+FEFF) hidden in JSON-like key",
        '﻿' + '"admin": true',
        True
    ))

    # Z4: RTL override in handoff
    cases.append((
        "Z4: RTL override (spec should strip this)",
        'Transfer ‮$1000 to EVIL_ACCOUNT',
        True
    ))

    # Z5: Multiple zero-width chars in sequence
    payload = "a​‌‍﻿b"
    cases.append((
        "Z5: Multiple ZW chars (U+200B+200C+200D+FEFF) between 'a' and 'b'",
        payload,
        True
    ))

    # Z6: Benign text with no hidden payloads
    cases.append((
        "Z6: Benign multi-line handoff",
        'Transfer $1000 to account 0x1234\nReference: INV-999\nNote: payment for services',
        False
    ))

    # ── Combined Attacks ───────────────────────

    # C1: Homoglyph + zero-width combined
    payload = "sk-е" + "​" + "yJ-wire-сonnect"
    cases.append((
        "C1: Combined homoglyph + ZWS in API key",
        payload,
        True
    ))

    # C2: Pure spec-compliant text with bidi (should be mitigated)
    cases.append((
        "C2: Bidi override (should be stripped by spec)",
        'Some text ‮EVIL reversed‭ more text',
        True
    ))

    return cases


# ─────────────────────────────────────────────────
# Part E: Main runner
# ─────────────────────────────────────────────────

def main():
    print("=" * 72)
    print("  Phase 1: Handoff Unicode Security Audit")
    print("  Attacks: Homoglyph forgery + Zero-width payloads")
    print("=" * 72)
    print()
    print(f"  normalize_handoff() spec version: NFC + strip bidi only")
    print(f"  Homoglyph chars tracked: {len(HOMOGLYPH_MAP)}")
    print(f"  Zero-width chars tracked: {len(ZERO_WIDTH_CHARS)}")
    print()

    suite = build_test_suite()
    results = []

    for name, payload, is_attack in suite:
        r = run_test(name, payload, is_attack)
        results.append(r)
        status = "🛡" if r['verdict'] == 'CLEAN' else (
            "❌" if 'bypassed' in r['verdict'] else "✅")
        print(f"  {status}  {name}")
        if r['homoglyphs_found']:
            for pos, ch, latin, cp in r['homoglyphs_found']:
                print(f"       Homoglyph at pos {pos}: {ch} → '{latin}' ({cp})")
        if r['zero_width_found']:
            for pos, ch, cp in r['zero_width_found']:
                print(f"       Zero-width at pos {pos}: {ch} ({cp})")
        if r['controls_found']:
            for pos, ch, cp in r['controls_found']:
                print(f"       Control char at pos {pos}: {ch} ({cp})")
        if r['forged_tokens_found']:
            for alert in r['forged_tokens_found']:
                print(f"       {alert}")
        print()

    # ── Summary ──
    print("-" * 72)
    attacks = [r for r in results if r['is_attack']]
    bypassed = [r for r in attacks if not r['attack_stopped']]
    mitigated = [r for r in attacks if r['attack_stopped']]

    print(f"  Total attack cases : {len(attacks)}")
    print(f"  Bypassed           : {len(bypassed)}")
    print(f"  Mitigated          : {len(mitigated)}")
    print()
    print("  ── Bypassed Attacks ──")
    for r in bypassed:
        print(f"    ❌ {r['name']}")
        if r['forged_tokens_found']:
            for a in r['forged_tokens_found']:
                print(f"       {a}")
    print()
    print("  ── Mitigated Attacks ──")
    for r in mitigated:
        print(f"    ✅ {r['name']}")
    print()

    # ── Security analysis ──
    print("=" * 72)
    print("  SECURITY ANALYSIS")
    print("=" * 72)
    print()
    print("  1. Homoglyph protection:   NONE (not in spec)")
    print("     → Attacker can forge any ASCII token by substituting")
    print("       Cyrillic/Greek lookalikes. The 'e' → Cyrillic 'е'")
    print("       is especially dangerous for tokens like 'Bearer',")
    print("       'secret', 'key', 'sk-...'.")
    print()
    print("  2. Zero-width protection:  NONE (not in spec)")
    print("     → Characters like ZWS (U+200B), ZWJ (U+200D), BOM")
    print("       (U+FEFF) survive normalization unchanged. Attacker")
    print("       can hide payloads or break keyword matching.")
    print()
    print("  3. Bidi protection:        PARTIAL")
    print("     → LRI/RLI (U+2066-U+2069) are NOT stripped, only")
    print("       LRO/RLO (U+202D/U+202E). This leaves gaps.")
    print()
    print("  4. Control char protection: NONE (not in spec)")
    print("     → Spec says 'MUST be escaped' but normalize_handoff()")
    print("       does NOTHING about them. They pass through.")
    print()
    print("═" * 72)

    return 1 if bypassed else 0


if __name__ == '__main__':
    sys.exit(main())
