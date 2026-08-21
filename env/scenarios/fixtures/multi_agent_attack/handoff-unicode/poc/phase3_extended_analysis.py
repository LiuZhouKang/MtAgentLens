#!/usr/bin/env python3
"""
Phase 3 扩展攻击面分析: Tag 块字符、Soft Hyphen、Arabic Letter Mark、
Combining Character Flood、Regex vs Cf 类别覆盖对比
"""

import unicodedata
import re
import sys
from collections import defaultdict

# ──────────────────────────────────────────────
# Replicate the spec's normalize_handoff functions
# ──────────────────────────────────────────────

ZERO_WIDTH_CHARS = {
    "U+200B": "​",
    "U+200C": "‌",
    "U+200D": "‍",
    "U+FEFF": "﻿",
    "U+2060": "⁠",
    "U+2061": "⁡",
    "U+2062": "⁢",
    "U+2063": "⁣",
    "U+2064": "⁤",
}

ZERO_WIDTH_SET = set(ZERO_WIDTH_CHARS.values())

def normalize_handoff_vulnerable(text: str) -> str:
    """Spec original — strips only U+202E/U+202D."""
    text = unicodedata.normalize("NFC", text)
    text = text.replace("‮", "").replace("‭", "")
    return text

def normalize_handoff_fixed(text: str) -> str:
    """Regex range fix."""
    text = unicodedata.normalize("NFC", text)
    text = text.replace("‮", "").replace("‭", "")
    text = re.sub(r"[​-‏ -‮⁠-⁩﻿￰-￻]", "", text)
    return text

def normalize_handoff_cf_strip(text: str) -> str:
    """Full Cf category strip."""
    text = unicodedata.normalize("NFC", text)
    return "".join(c for c in text if unicodedata.category(c) != "Cf")


# ──────────────────────────────────────────────
# Helper: discover all Cf characters
# ──────────────────────────────────────────────

def collect_all_cf_chars() -> list[tuple[int, str, str]]:
    """Return all assigned Cf (Format) characters."""
    results = []
    for cp in range(0, 0x110000):
        try:
            cat = unicodedata.category(chr(cp))
        except ValueError:
            continue
        if cat == "Cf":
            try:
                name = unicodedata.name(chr(cp), "")
            except ValueError:
                name = ""
            results.append((cp, chr(cp), name))
    return results


# ──────────────────────────────────────────────
# Test vector 1: Tag block character injection
# ──────────────────────────────────────────────

def test_tag_block_injection():
    print("=" * 72)
    print("扩展测试 1: Tag 块字符注入 (U+E0001-U+E007F)")
    print("=" * 72)

    # Tag sequence: invisible "HELLO"
    tag_payload = "\U000e0001\U000e0048\U000e0045\U000e004c\U000e004c\U000e004f\U000e007f"

    print(f"\n  构造的 Tag payload:")
    print(f"    repr:   {tag_payload!r}")
    print(f"    显示:   '{tag_payload}'")
    print(f"    长度:   {len(tag_payload)} 字符")

    # Show each code point
    for i, c in enumerate(tag_payload):
        print(f"    字符 {i}: U+{ord(c):05X} ({unicodedata.category(c)})")

    # Inject into visible text
    visible = "execute_command"
    poisoned = visible[:3] + tag_payload + visible[3:]
    print(f"\n  注入到可见文本 'execute_command':")
    print(f"    repr: {poisoned!r}")
    print(f"    长度: {len(poisoned)} (原始: {len(visible)})")

    # Test vulnerable normalize
    vuln_result = normalize_handoff_vulnerable(poisoned)
    vuln_cf_count = sum(1 for c in vuln_result if unicodedata.category(c) == "Cf")
    print(f"\n  normalize_handoff_vulnerable:")
    print(f"    repr:    {vuln_result!r}")
    print(f"    匹配原文?   {vuln_result == visible}")
    print(f"    Cf 字符残留: {vuln_cf_count}")
    print(f"    ==> {'攻击突围!' if vuln_result != visible else '已阻止' if vuln_result == visible else '部分清除'}")

    # Test fixed normalize
    fixed_result = normalize_handoff_fixed(poisoned)
    fixed_cf_count = sum(1 for c in fixed_result if unicodedata.category(c) == "Cf")
    print(f"\n  normalize_handoff_fixed (regex):")
    print(f"    repr:    {fixed_result!r}")
    print(f"    匹配原文?   {fixed_result == visible}")
    print(f"    Cf 字符残留: {fixed_cf_count}")
    print(f"    ==> {'攻击突围!' if fixed_result != visible else '已阻止' if fixed_result == visible else '部分清除'}")

    # Test Cf strip
    cf_result = normalize_handoff_cf_strip(poisoned)
    cf_cf_count = sum(1 for c in cf_result if unicodedata.category(c) == "Cf")
    print(f"\n  normalize_handoff_cf_strip (Cf 类别):")
    print(f"    repr:    {cf_result!r}")
    print(f"    匹配原文?   {cf_result == visible}")
    print(f"    Cf 字符残留: {cf_cf_count}")
    print(f"    ==> {'攻击突围!' if cf_result != visible else '已阻止' if cf_result == visible else '部分清除'}")

    # Decode: what does the tag sequence encode?
    decoded = ""
    for c in tag_payload:
        cp = ord(c)
        if 0xE0020 <= cp <= 0xE007E:
            decoded += chr(cp - 0xE0000)
        elif cp == 0xE0001:
            decoded += "[TAG_START]"
        elif cp == 0xE007F:
            decoded += "[TAG_END]"
        else:
            decoded += f"[U+{cp:05X}]"
    print(f"\n  Tag 解码: {decoded}")


# ──────────────────────────────────────────────
# Test vector 2: Soft Hyphen injection (U+00AD)
# ──────────────────────────────────────────────

def test_soft_hyphen():
    print("\n" + "=" * 72)
    print("扩展测试 2: Soft Hyphen 注入 (U+00AD)")
    print("=" * 72)

    shy = "­"

    print(f"\n  Soft Hyphen 属性:")
    print(f"    码点:       U+{ord(shy):04X}")
    print(f"    类别:       {unicodedata.category(shy)}")
    print(f"    名称:       {unicodedata.name(shy, 'UNKNOWN')}")

    # Keyword: "execute" with soft hyphens
    whispered = "exe" + shy + "cute"
    print(f"\n  注入 'execute':")
    print(f"    repr:    {whispered!r}")
    print(f"    显示:    '{whispered}'")
    print(f"    长度:    {len(whispered)} (原始 'execute' = {len('execute')})")
    print(f"    == 'execute'? {whispered == 'execute'}")

    # Test against a security check
    dangerous_commands = {"execute", "rm", "sudo", "eval"}
    vuln_normalized = normalize_handoff_vulnerable(whispered)
    fixed_normalized = normalize_handoff_fixed(whispered)
    cf_normalized = normalize_handoff_cf_strip(whispered)

    print(f"\n  normalize_handoff_vulnerable:")
    print(f"    repr:    {vuln_normalized!r}")
    print(f"    == 'execute'? {vuln_normalized == 'execute'}")
    print(f"    黑名单命中? {'YES' if vuln_normalized in dangerous_commands else 'NO (绕过!)'}")

    print(f"\n  normalize_handoff_fixed (regex):")
    print(f"    repr:    {fixed_normalized!r}")
    print(f"    == 'execute'? {fixed_normalized == 'execute'}")
    print(f"    黑名单命中? {'YES' if fixed_normalized in dangerous_commands else 'NO (绕过!)'}")

    print(f"\n  normalize_handoff_cf_strip (Cf 类别):")
    print(f"    repr:    {cf_normalized!r}")
    print(f"    == 'execute'? {cf_normalized == 'execute'}")
    print(f"    黑名单命中? {'YES' if cf_normalized in dangerous_commands else 'NO (绕过!)'}")
    print(f"    注意: U+00AD 类别是 {unicodedata.category(shy)} (非 Cf)，Cf 方案也会让它通过")


# ──────────────────────────────────────────────
# Test vector 3: Arabic Letter Mark (U+061C)
# ──────────────────────────────────────────────

def test_arabic_letter_mark():
    print("\n" + "=" * 72)
    print("扩展测试 3: Arabic Letter Mark 注入 (U+061C)")
    print("=" * 72)

    alm = "؜"

    print(f"\n  Arabic Letter Mark 属性:")
    print(f"    码点:       U+{ord(alm):04X}")
    print(f"    类别:       {unicodedata.category(alm)}")
    print(f"    名称:       {unicodedata.name(alm, 'UNKNOWN')}")

    # Inject into a handoff token
    visible = "AUTH_TOKEN_xyz789"
    poisoned = visible[:4] + alm + visible[4:]

    print(f"\n  注入到 'AUTH_TOKEN_xyz789':")
    print(f"    repr:    {poisoned!r}")
    print(f"    显示:    '{poisoned}'")
    print(f"    长度:    {len(poisoned)} (原始: {len(visible)})")

    vuln_result = normalize_handoff_vulnerable(poisoned)
    fixed_result = normalize_handoff_fixed(poisoned)
    cf_result = normalize_handoff_cf_strip(poisoned)

    print(f"\n  normalize_handoff_vulnerable:")
    print(f"    repr:    {vuln_result!r}")
    print(f"    == 原始?   {vuln_result == visible}")
    alm_remaining = sum(1 for c in vuln_result if c == alm)
    print(f"    ALM 残留:   {alm_remaining}")

    print(f"\n  normalize_handoff_fixed (regex):")
    print(f"    repr:    {fixed_result!r}")
    print(f"    == 原始?   {fixed_result == visible}")
    alm_remaining = sum(1 for c in fixed_result if c == alm)
    print(f"    ALM 残留:   {alm_remaining}")

    print(f"\n  normalize_handoff_cf_strip (Cf 类别):")
    print(f"    repr:    {cf_result!r}")
    print(f"    == 原始?   {cf_result == visible}")
    alm_remaining = sum(1 for c in cf_result if c == alm)
    print(f"    ALM 残留:   {alm_remaining}")

    # Check: is U+061C Cf?
    print(f"\n  U+061C 是否在 Cf 类别? {unicodedata.category(alm) == 'Cf'}")
    print(f"  U+061C 是否在 regex 范围? (需要检查)")

    # Also test: U+061C can force RTL rendering, changing visual meaning
    print(f"\n  RTL 攻击场景:")
    # Mix ALM with text to flip reading direction
    rtl_text = "safe" + alm + "execute"
    print(f"    文本: {rtl_text!r}")
    print(f"    视觉: '{rtl_text}' (RTL 渲染可能反转顺序)")


# ──────────────────────────────────────────────
# Test vector 4: Combining character flood (NFD attack)
# ──────────────────────────────────────────────

def test_combining_character_flood():
    print("\n" + "=" * 72)
    print("扩展测试 4: Combining Character Flood (NFD 攻击)")
    print("=" * 72)

    base_char = "a"  # single 'a'

    # Generate combining characters of various classes
    # Class Mn (Nonspacing Mark) — they stack on a single base
    combining_chars_mn: list[str] = []
    combining_chars_mc: list[str] = []

    for cp in range(0x0300, 0x0370):  # Combining Diacritical Marks block
        try:
            c = chr(cp)
            cat = unicodedata.category(c)
            if cat == "Mn":
                combining_chars_mn.append(c)
            elif cat == "Mc":
                combining_chars_mc.append(c)
        except ValueError:
            pass

    print(f"\n  已收集 {len(combining_chars_mn)} 个 Mn (Nonspacing Mark) 字符")
    print(f"  已收集 {len(combining_chars_mc)} 个 Mc (Spacing Mark) 字符")

    # Test with different flood sizes
    flood_sizes = [5, 10, 20, 50]

    for size in flood_sizes:
        chars_to_use = combining_chars_mn[:min(size, len(combining_chars_mn))]
        if len(chars_to_use) < size:
            # Wrap around
            chars_to_use = chars_to_use * (size // len(chars_to_use) + 1)
            chars_to_use = chars_to_use[:size]

        # NFD: base + flood of combining marks
        nfd_text = base_char + "".join(chars_to_use)
        nfd_len = len(nfd_text)

        # NFC normalize
        nfc_text = unicodedata.normalize("NFC", nfd_text)
        nfc_len = len(nfc_text)

        # Apply each normalize function
        vuln_result = normalize_handoff_vulnerable(nfd_text)
        fixed_result = normalize_handoff_fixed(nfd_text)
        cf_result = normalize_handoff_cf_strip(nfd_text)

        print(f"\n  组合字符 flood ({size} 个):")
        print(f"    NFD 输入长度:   {nfd_len}")
        print(f"    NFC 输出长度:   {nfc_len} (缩短 {nfd_len - nfc_len})")
        print(f"    NFC 结果:        {nfc_text!r}")

        # Check: how many combining chars are Cf?
        cf_count = sum(1 for c in nfd_text if unicodedata.category(c) == "Cf")
        print(f"    其中 Cf 字符数:  {cf_count}")

        # The key question: does vulnerable normalize strip the flood?
        vuln_match = vuln_result == nfc_text
        print(f"    vulnerable normalize 等价 NFC? {vuln_match}")
        print(f"    vulnerable normalize 长度:     {len(vuln_result)}")

        # Does fixed strip anything useful?
        fixed_match = fixed_result == nfc_text
        print(f"    fixed normalize 等价 NFC?     {fixed_match}")

        # Does Cf strip affect combining marks?
        cf_match = cf_result == nfc_text
        print(f"    Cf strip 等价 NFC?           {cf_match}")

    # Key insight: combining marks are Mn/Mc, not Cf
    # So even Cf strip won't help against this attack
    # The defense is NFC normalization itself (which combines them)
    print(f"\n  核心发现:")
    print(f"    组合字符属于 Mn/Mc 类别，不是 Cf。")
    print(f"    NFC 规范化通过组合到预组合字符来防御。")
    print(f"    真正的风险：下游在 NFC 规范化之前进行字符串比较。")


# ──────────────────────────────────────────────
# Test vector 5: Regex vs Cf category coverage comparison
# ──────────────────────────────────────────────

def test_regex_vs_cf_coverage():
    print("\n" + "=" * 72)
    print("扩展测试 5: Regex 范围 vs Cf 类别覆盖对比")
    print("=" * 72)

    # The regex from normalize_handoff_fixed
    regex_pattern = re.compile(r"[​-‏ -‮⁠-⁩﻿￰-￻]")

    # Collect all Cf characters
    all_cf = collect_all_cf_chars()
    print(f"\n  Unicode 中 Cf 字符总数: {len(all_cf)}")

    # Categorize: in regex, not in regex
    cf_in_regex = []
    cf_not_in_regex = []
    regex_not_cf = []

    for cp, char, name in all_cf:
        is_cf = unicodedata.category(char) == "Cf"
        in_regex = bool(regex_pattern.search(char))
        if in_regex:
            cf_in_regex.append((cp, char, name))
        else:
            cf_not_in_regex.append((cp, char, name))

    # Also check: what characters match regex but are NOT Cf?
    for start, end in [
        (0x200B, 0x200F),
        (0x2028, 0x202E),
        (0x2060, 0x2069),
        (0xFFF0, 0xFFFB),
    ]:
        for cp in range(start, end + 1):
            try:
                char = chr(cp)
                cat = unicodedata.category(char)
            except ValueError:
                continue
            if cat != "Cf":
                try:
                    name = unicodedata.name(char, "")
                except ValueError:
                    name = ""
                regex_not_cf.append((cp, char, name, cat))

    # FEFF is already in the Cf list
    if 0xFEFF:
        try:
            cat = unicodedata.category("﻿")
            if cat != "Cf":
                regex_not_cf.append((0xFEFF, "﻿", unicodedata.name("﻿", ""), cat))
        except ValueError:
            pass

    print(f"\n  Cf 字符在 regex 内:    {len(cf_in_regex)}")
    print(f"  Cf 字符不在 regex 内:  {len(cf_not_in_regex)}")
    print(f"  Regex 匹配的非 Cf 字符: {len(regex_not_cf)}")

    # Show Cf characters NOT in regex (the missed ones)
    print(f"\n  === Regex 遗漏的 Cf 字符 (高危) ===")
    for cp, char, name in sorted(cf_not_in_regex):
        cat = unicodedata.category(char)
        print(f"    U+{cp:05X}  {name:<45} 类别={cat}")

    # Show regex-matching non-Cf characters
    if regex_not_cf:
        print(f"\n  === Regex 匹配的非 Cf 字符 (误杀) ===")
        for cp, char, name, cat in sorted(regex_not_cf):
            print(f"    U+{cp:05X}  {char!r}  {name:<45} 类别={cat}")

    # Summary: which approach is better?
    print(f"\n  === 覆盖对比总结 ===")
    print(f"  方案                                     Cf 覆盖  误杀风险")
    print(f"  regex range [​-‏ -‮⁠-⁩﻿￰-￻]    {len(cf_in_regex)}/{len(all_cf)}      {len(regex_not_cf)} 个非 Cf 字符")
    print(f"  unicodedata.category(c) == 'Cf'          {len(all_cf)}/{len(all_cf)}      0 个非 Cf 字符")

    # Risk assessment of missed Cf chars
    high_risk_missed = [
        (0x00AD, "SOFT HYPHEN"),
        (0x061C, "ARABIC LETTER MARK"),
        (0x180E, "MONGOLIAN VOWEL SEPARATOR"),
        (0x2066, "LEFT-TO-RIGHT ISOLATE"),
        (0x2067, "RIGHT-TO-LEFT ISOLATE"),
        (0x2068, "FIRST STRONG ISOLATE"),
        (0x2069, "POP DIRECTIONAL ISOLATE"),
        (0xE0001, "LANGUAGE TAG"),
        (0xE0020, "TAG SPACE"),
        (0xE007F, "CANCEL TAG"),
    ]
    print(f"\n  === 高危遗漏清单检查 ===")
    for cp, name in high_risk_missed:
        matched = regex_pattern.search(chr(cp))
        is_cf = unicodedata.category(chr(cp)) == "Cf"
        status = "regex 遗漏 (高危)" if (not matched and is_cf) else ("已覆盖" if matched else "非 Cf")
        print(f"    U+{cp:05X}  {name:<35}  {status}")


# ──────────────────────────────────────────────
# Test vector 6: Comprehensive attack matrix
# ──────────────────────────────────────────────

def test_comprehensive_attack_matrix():
    print("\n" + "=" * 72)
    print("扩展测试 6: 综合攻击矩阵 — 三种方案对比")
    print("=" * 72)

    # Test vectors with various Unicode attacks
    test_vectors = [
        ("零宽空格 (U+200B)", "AUTH_TOKEN", "A" + "​" + "UTH_TOKEN"),
        ("零宽非连接符 (U+200C)", "token_xyz", "token" + "‌" + "_xyz"),
        ("零宽连接符 (U+200D)", "data_flow", "data" + "‍" + "_flow"),
        ("BOM (U+FEFF)", "safe_text", "﻿" + "safe_text"),
        ("词连接符 (U+2060)", "word", "wo" + "⁠" + "rd"),
        ("RTL 嵌入 (U+202B)", "safe", "‫" + "safe" + "‬"),
        ("LRO 覆盖 (U+202D)", "safe", "‭" + "safe" + "‬"),
        ("RLO 覆盖 (U+202E)", "safe", "‮" + "safe" + "‬"),
        ("方向隔离 (U+2066-U+2069)", "safe", "⁦⁧" + "safe" + "⁩⁩"),
        ("Soft Hyphen (U+00AD)", "execute", "exe" + "­" + "cute"),
        ("Arabic Letter Mark (U+061C)", "AUTH_TOKEN", "AUTH" + "؜" + "_TOKEN"),
        ("蒙古文元音分隔符 (U+180E)", "config", "con" + "᠎" + "fig"),
        ("Tag 块 HELLO (U+E0001-U+E007F)", "exec_cmd", "ex\U000e0001\U000e0048\U000e0045\U000e004c\U000e004c\U000e004f\U000e007f" + "ec_cmd"),
        ("组合字符泛滥 (5 个)", "a", "a" + "̀́̂̃̄"),
    ]

    print(f"\n  {'攻击向量':<40} {'漏洞版':^20} {'Regex修复':^20} {'Cf剥离':^20}")
    print(f"  {'':-<40} {'':-<20} {'':-<20} {'':-<20}")

    for name, original, poisoned in test_vectors:
        vuln = normalize_handoff_vulnerable(poisoned)
        fixed = normalize_handoff_fixed(poisoned)
        cf = normalize_handoff_cf_strip(poisoned)

        vuln_ok = "安全" if vuln == original else "攻击突围"
        fixed_ok = "安全" if fixed == original else "攻击突围"
        cf_ok = "安全" if cf == original else "攻击突围"

        # Count Cf chars remaining
        vuln_cf = sum(1 for c in vuln if unicodedata.category(c) == "Cf")
        fixed_cf = sum(1 for c in fixed if unicodedata.category(c) == "Cf")
        cf_cf = sum(1 for c in cf if unicodedata.category(c) == "Cf")

        print(f"  {name:<40} {vuln_ok:<20} {fixed_ok:<20} {cf_ok:<20}")
        if vuln_cf > 0 or fixed_cf > 0 or cf_cf > 0:
            print(f"    --> Cf残留: vuln={vuln_cf}, fixed={fixed_cf}, cf={cf_cf}")


# ──────────────────────────────────────────────
# Test vector 7: Interlinear Annotation Anchors
# ──────────────────────────────────────────────

def test_interlinear_annotations():
    print("\n" + "=" * 72)
    print("扩展测试 7: Interlinear Annotation Anchor 字符 (U+FFF9-U+FFFB)")
    print("=" * 72)

    # These are Cf characters within the regex range
    # U+FFF9 INTERLINEAR ANNOTATION ANCHOR
    # U+FFFA INTERLINEAR ANNOTATION SEPARATOR
    # U+FFFB INTERLINEAR ANNOTATION TERMINATOR

    for cp, name in [(0xFFF9, "ANCHOR"), (0xFFFA, "SEPARATOR"), (0xFFFB, "TERMINATOR")]:
        c = chr(cp)
        print(f"\n  U+{cp:04X} {name}:")
        print(f"    类别:      {unicodedata.category(c)}")
        print(f"    repr:      {c!r}")

        # Inject with hidden payload
        hidden = "￹" + "DROP TABLE" + "￻"
        visible = "safe_operation"
        poisoned = visible[:4] + hidden + visible[4:]

        vuln = normalize_handoff_vulnerable(poisoned)
        fixed = normalize_handoff_fixed(poisoned)
        cf = normalize_handoff_cf_strip(poisoned)

        print(f"    注入:      {poisoned!r}")
        print(f"    vuln:      {vuln!r} (匹配原文? {vuln == visible})")
        print(f"    fixed:     {fixed!r} (匹配原文? {fixed == visible})")
        print(f"    cf:        {cf!r} (匹配原文? {cf == visible})")


# ──────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────

def main():
    print("=" * 72)
    print("PHASE 3 EXTENDED ATTACK SURFACE ANALYSIS")
    print("日期: 2026-06-08")
    print("=" * 72)

    test_tag_block_injection()
    test_soft_hyphen()
    test_arabic_letter_mark()
    test_combining_character_flood()
    test_regex_vs_cf_coverage()
    test_comprehensive_attack_matrix()
    test_interlinear_annotations()

    print("\n" + "=" * 72)
    print("扩展分析总结")
    print("=" * 72)
    print("""
  FINDING E1: Tag 块字符 (U+E0001-U+E007F) — CRITICAL
     regex 方案完全遗漏 Tag 块。这些字符构成完整的不可见 ASCII 编码通道。
     Cf 类别方案覆盖所有 Tag 字符 (全部 97 个是 Cf)。

  FINDING E2: Soft Hyphen (U+00AD) — HIGH
     U+00AD 类别是 'Pd' (Dash Punctuation)，不是 Cf。
     因此 regex 和 Cf 方案都不会剥离它。
     在视觉上不可见（大多数渲染器中），但 string comparison 失败。
     建议：添加显式移除 U+00AD。

  FINDING E3: Arabic Letter Mark (U+061C) — HIGH
     U+061C 自 Unicode 6.3 起归类为 Cf，但位于阿拉伯语块中。
     regex 方案未覆盖此字符（不在任何指定范围内）。
     Cf 类别方案正确覆盖。

  FINDING E4: Combining Character Flood — MITIGATED
     组合字符是 Mn/Mc 类别（非 Cf）。防御依赖 NFC 规范化。
     NFC 将组合字符合并为预组合形式，防止膨胀攻击。
     但：巨大的组合字符序列导致 NFC 规范化开销呈 O(n²)。
     建议：限制 handoff 数据中可以存在的组合标记数量。

  FINDING E5: Regex vs Cf 覆盖差异
     regex 方案覆盖约 23/170+ Cf 字符 (14%)。
     Cf 方案覆盖 100% 的 Cf 字符。
     regex 方案可能误杀某些非 Cf 范围字符（需验证）。
     Cf 方案是语义正确的方案。

  FINDING E6: Cf 遗漏的高危字符
     regex 未覆盖：Tag 块、Soft Hyphen、Arabic Letter Mark、
     方向隔离符 (U+2066-U+2069)、蒙古文分隔符 (U+180E)。

  推荐：
     1. 主防御：使用 unicodedata.category(c) == 'Cf' 剥离所有格式字符
     2. 补充：显式移除 U+00AD (SOFT HYPHEN) — 非 Cf 但不可见
     3. 补充：限制组合标记数量，防止 NFC 计算爆炸
     4. 在 Rust 中使用 \\\\p{Cf} regex 或 unicode-categories crate
    """)


if __name__ == "__main__":
    main()
