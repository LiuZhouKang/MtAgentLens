#!/usr/bin/env python3
"""
Phase 3 PoC: Unicode Zero-Width Character Attack on Handoff Encoding

Demonstrates that normalize_handoff() from the I18n Handoff Encoding Specification
fails to strip zero-width characters, allowing hidden payloads to survive
normalization and be interpreted by downstream agents.

Attack surface documented in the spec itself ("Known Issues" item 3):
    "Zero-width characters can hide payloads in visible text"
"""

import unicodedata
import re

# ──────────────────────────────────────────────
# 1. The spec's normalize_handoff() — VULNERABLE
# ──────────────────────────────────────────────

def normalize_handoff_vulnerable(text: str) -> str:
    """Exact implementation from the spec (Section 'Normalization Procedure')."""
    text = unicodedata.normalize("NFC", text)
    # Only strips RTL override characters — NOT zero-width chars
    text = text.replace("‮", "").replace("‭", "")
    return text


# ──────────────────────────────────────────────
# 2. Zero-width character utilities
# ──────────────────────────────────────────────

ZERO_WIDTH_CHARS = {
    "U+200B": "​",  # Zero Width Space
    "U+200C": "‌",  # Zero Width Non-Joiner
    "U+200D": "‍",  # Zero Width Joiner
    "U+FEFF": "﻿",  # Byte Order Mark (also acts as ZWNBSP)
    "U+2060": "⁠",  # Word Joiner
    "U+2061": "⁡",  # Function Application
    "U+2062": "⁢",  # Invisible Times
    "U+2063": "⁣",  # Invisible Separator
    "U+2064": "⁤",  # Invisible Plus
}

def hide_payload(visible: str, payload: str) -> str:
    """
    Embed a hidden payload inside a visible string using zero-width characters.

    Encoding scheme: each byte of the payload is encoded as 8 bits,
    each bit represented by one of two zero-width characters (0→U+200B, 1→U+200D).
    The encoded payload is inserted after the first character of the visible string.

    This makes the hidden payload completely invisible to humans while surviving
    the spec's normalization.
    """
    # Encode payload bytes into zero-width bits
    encoded_bits = []
    for byte in payload.encode("utf-8"):
        for i in range(7, -1, -1):
            bit = (byte >> i) & 1
            encoded_bits.append("‍" if bit else "​")

    encoded = "".join(encoded_bits)

    # Insert after first character so visible text looks normal
    return visible[0] + encoded + visible[1:]


def extract_payload(hidden_text: str) -> str | None:
    """
    Extract a hidden payload encoded with zero-width characters.

    Returns None if no hidden payload is found.
    """
    # Collect all zero-width characters in order
    zw_chars = [c for c in hidden_text if c in ZERO_WIDTH_CHARS.values()]

    if not zw_chars:
        return None

    # Decode bits back to bytes
    bits = []
    for c in zw_chars:
        if c == "​":
            bits.append(0)
        elif c == "‍":
            bits.append(1)
        elif c in ("‌", "﻿", "⁠", "⁡", "⁢", "⁣", "⁤"):
            bits.append(0)  # treat other zero-width chars as neutral/filler

    # Group into bytes
    bytes_list = []
    for i in range(0, len(bits) - 7, 8):
        byte_val = 0
        for j in range(8):
            byte_val = (byte_val << 1) | bits[i + j]
        bytes_list.append(byte_val)

    if not bytes_list:
        return None

    try:
        return bytes(bytes_list).decode("utf-8")
    except UnicodeDecodeError:
        return repr(bytes(bytes_list))


# ──────────────────────────────────────────────
# 3. Demonstration
# ──────────────────────────────────────────────

def demo_zero_width_attack():
    print("=" * 72)
    print("PHASE 3: Zero-Width Character Handoff Injection Attack")
    print("=" * 72)

    # --- Token that looks legitimate ---
    token = "AUTH_TOKEN_hf_abc12345"
    secret_payload = "DROP TABLE agents; --"

    print(f"\n[1] Visible token:        {token!r}")
    print(f"[2] Hidden payload:       {secret_payload!r}")

    # --- Embed the payload ---
    poisoned = hide_payload(token, secret_payload)
    print(f"\n[3] Poisoned string:      {poisoned!r}")
    print(f"    (zero-width chars are invisible in display)")
    print(f"    Visible length (char count): {len(poisoned)}")
    print(f"    Actual  length (char count): {len(token)}  (token only)")
    print(f"    Zero-width chars embedded:   {sum(1 for c in poisoned if c in ZERO_WIDTH_CHARS.values())}")

    # Show the raw bytes so zero-width chars are visible
    print(f"\n[4] Raw bytes (hex):")
    for i, b in enumerate(poisoned.encode("utf-8")):
        print(f"      byte {i:3d}: 0x{b:02x} ({chr(b) if 0x20 <= b < 0x7f else '·'})")

    # --- Apply the spec's normalization ---
    normalized = normalize_handoff_vulnerable(poisoned)
    print(f"\n[5] After spec's normalize_handoff():")
    print(f"    Result:               {normalized!r}")
    print(f"    Matches poisoned?     {normalized == poisoned}")
    print(f"    Zero-width chars remaining: {sum(1 for c in normalized if c in ZERO_WIDTH_CHARS.values())}")
    print(f"    ==> ATTACK SURVIVES NORMALIZATION!")

    # --- Extract the payload ---
    extracted = extract_payload(normalized)
    print(f"\n[6] Extracted payload:    {extracted!r}")
    print(f"    Payload intact?       {extracted == secret_payload}")

    # --- Show that the string *looks* identical when printed ---
    print(f"\n[7] Visual representation:")
    print(f"    Displayed text:      '{poisoned}'")
    print(f"    Looks like token:    '{token}'")
    print(f"    Visually identical?  {poisoned == token} (no — hidden data invisible)")

    # --- Impact scenario ---
    print(f"\n[8] Impact Scenario:")
    print(f"    A Leader agent distributes a handoff doc containing:")
    print(f"      token = {poisoned!r}")
    print(f"    Worker agents see only '{token}'")
    print(f"    Running normalize_handoff() does NOT remove the payload")
    print(f"    A malicious or compromised parser extracts: '{secret_payload}'")


# ──────────────────────────────────────────────
# 4. Improved normalize_handoff() — FIXED
# ──────────────────────────────────────────────

ZERO_WIDTH_SET = set(ZERO_WIDTH_CHARS.values())

def normalize_handoff_fixed(text: str) -> str:
    """
    Improved normalization that also strips all zero-width characters,
    preventing the attack demonstrated above.
    """
    text = unicodedata.normalize("NFC", text)
    # Strip RTL override characters (original behavior)
    text = text.replace("‮", "").replace("‭", "")
    # NEW: Strip all zero-width and invisible formatting characters
    # Covers: U+200B-U+200F, U+2028-U+202E, U+2060-U+2069, U+FEFF, U+FFF0-U+FFFB
    text = re.sub(
        r"[​-‏ -‮⁠-⁩﻿￰-￻]",
        "",
        text,
    )
    return text


def demo_fixed_normalization():
    print("\n" + "=" * 72)
    print("FIXED normalize_handoff() DEMONSTRATION")
    print("=" * 72)

    token = "AUTH_TOKEN_hf_abc12345"
    secret_payload = "DROP TABLE agents; --"
    poisoned = hide_payload(token, secret_payload)

    print(f"\n[1] Poisoned input:       {poisoned!r}")

    fixed_result = normalize_handoff_fixed(poisoned)
    print(f"[2] After FIXED normalize: {fixed_result!r}")
    print(f"    Matches original token?  {fixed_result == token}")
    print(f"    Zero-width chars remaining: {sum(1 for c in fixed_result if c in ZERO_WIDTH_SET)}")
    print(f"    ==> ATTACK NEUTRALIZED!")


# ──────────────────────────────────────────────
# 5. NFC Normalization Quirks
# ──────────────────────────────────────────────

def demo_nfc_quirks():
    print("\n" + "=" * 72)
    print("NFC NORMALIZATION QUIRKS")
    print("=" * 72)

    cases = [
        # (name, nfd_form, nfc_form)
        (
            "é (composed) vs e + combining accent",
            "é",          # NFD: e + combining acute accent
            "é",           # NFC: precomposed é
        ),
        (
            "ñ (composed) vs n + combining tilde",
            "ñ",
            "ñ",
        ),
        (
            "ü (composed) vs u + combining diaeresis",
            "ü",
            "ü",
        ),
        (
            "ç (composed) vs c + combining cedilla",
            "ç",
            "ç",
        ),
        (
            "CJK compatibility: Ⅷ vs VIII",
            "Ⅷ",           # Roman numeral eight (compatibility ideograph)
            "VIII",
        ),
        (
            "CJK: ﬁ ligature vs fi",
            "ﬁ",           # ﬁ ligature
            "fi",
        ),
        (
            "Kana: パ vs パ",
            "パ",     # ハ + combining handakuten
            "パ",           # パ (precomposed)
        ),
    ]

    for name, nfd, nfc in cases:
        nfc_result = unicodedata.normalize("NFC", nfd)
        is_nfc_equal = nfc_result == nfc

        nfc_len = len(nfc_result)
        nfd_len = len(nfd)
        spec_normalized = normalize_handoff_vulnerable(nfd)
        spec_is_nfc = spec_normalized == nfc

        print(f"\n  [{name}]")
        print(f"    NFD input:              {nfd!r} (len={nfd_len})")
        print(f"    NFC expected:           {nfc!r} (len={nfc_len})")
        print(f"    NFC normalized:         {nfc_result!r}")
        print(f"    NFC correct?            {is_nfc_equal}")
        print(f"    After spec normalize(): {spec_normalized!r}")
        print(f"    Spec produces NFC?      {spec_is_nfc}")

        # Security implication: 2 strings that look identical but have
        # different byte representations
        if nfd_len != nfc_len:
            print(f"    *** BYTE LENGTH DIFFERENCE: {nfd_len} vs {nfc_len} —")
            print(f"        strings are visually identical but bytes differ")


def demo_nfc_collision_attack():
    """
    Demonstrate a collision attack using NFC normalization.
    A whitelist check on the raw bytes can be bypassed if the attacker
    uses NFD form, because normalize_handoff converts to NFC —
    so an NFD string that normalizes to a whitelisted token passes.
    """
    print("\n" + "=" * 72)
    print("NFC COLLISION ATTACK")
    print("=" * 72)

    # Suppose there's a whitelist of allowed tokens
    whitelist = {"admin", "token_abc", "safe_endpoint"}

    # Attacker submits an NFD variant of "admin"
    # a + combining macron = ā, but visually similar
    nfd_admin = "àdministrator"  # a + grave accent — not quite

    # More realistic: homoglyph + NFC normalization
    # "admin" but with a combining character that NFC-normalizes to "admin"
    # Actually, NFC is a *composition* — so decomposed forms get composed.
    # The attack is: use a character that NFC-normalizes to a whitelisted word.

    # Example: NFD form of "token" — NFC normalizes to "token"
    nfd_token = "tóken"  # t + o + combining acute + k + e + n
    nfc_token = unicodedata.normalize("NFC", nfd_token)

    print(f"  Attacker input (NFD):  {nfd_token!r}")
    print(f"  After NFC normalize:   {nfc_token!r}")
    print(f"  Looks like 'token'?    {nfc_token == 'token'}")
    print(f"  In whitelist?          {nfc_token in whitelist}")

    # Actually "token" has no combining chars normally.
    # Let's be more precise: NFD of "tóken" (t + ó + k + e + n)
    t_acute_nfd = "tóken"  # o + combining acute → ó after NFC
    print(f"\n  Realistic attack:")
    print(f"  NFD input:             {t_acute_nfd!r}")
    print(f"  NFC output:            {unicodedata.normalize('NFC', t_acute_nfd)!r}")
    print(f"  Length change:         {len(t_acute_nfd)} → {len(unicodedata.normalize('NFC', t_acute_nfd))}")

    # The key insight: NFC normalization changes byte representation
    # but the spec's normalize_handoff() applies NFC, which is correct.
    # The real issue is if a downstream component does a raw string
    # comparison BEFORE the spec processes it.
    print(f"\n  Risk: If a downstream component compares strings BEFORE")
    print(f"  normalize_handoff() is called, NFD form bypasses the check.")


# ──────────────────────────────────────────────
# 6. Main
# ──────────────────────────────────────────────

def main():
    demo_zero_width_attack()
    demo_fixed_normalization()
    demo_nfc_quirks()
    demo_nfc_collision_attack()

    print("\n" + "=" * 72)
    print("SUMMARY")
    print("=" * 72)
    print("""
  FINDING 1: Zero-Width Character Injection (CRITICAL)
     The spec's normalize_handoff() only strips U+202D/U+202E (RTL overrides).
     It does NOT strip U+200B, U+200C, U+200D, U+FEFF, or any of the 17+
     other zero-width/invisible Unicode characters.
     Result: Hidden payloads survive normalization.

  FINDING 2: Improved normalize_handoff() (PROPOSED)
     Adding a regex that strips ranges [​-‏ -‮
     ⁠-⁩﻿￰-￻] neutralizes the attack.

  FINDING 3: NFC Normalization Changes Byte Representation
     e.g., "é" can be 1 char (U+00E9) or 2 chars (U+0065 U+0301).
     The spec correctly applies NFC, but any code comparing strings
     BEFORE normalization is vulnerable to NFC-based bypasses.

  FINDING 4: The spec's Known Issues section correctly identifies the
     risk ("Zero-width characters can hide payloads in visible text")
     but fails to mitigate it in the normalize_handoff() implementation.
    """)


if __name__ == "__main__":
    main()
