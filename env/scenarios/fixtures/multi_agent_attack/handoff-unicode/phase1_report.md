# Phase 1 安全审计报告：Unicode 同形字与零宽字符攻击检测

**测试日期**: 2026-06-08
**测试目标**: 评估 `normalize_handoff()` 规范对 Unicode 同形字伪造、零宽字符隐藏 payload、双向控制字符攻击的防御能力
**规范版本**: NFC normalization + strip U+202E/U+202D only

---

## 一、完整测试输出

```
========================================================================
  Phase 1: Handoff Unicode Security Audit
  Attacks: Homoglyph forgery + Zero-width payloads
========================================================================

  normalize_handoff() spec version: NFC + strip bidi only
  Homoglyph chars tracked: 27
  Zero-width chars tracked: 11

  ❌  H1: Cyrillic 'е' forges Bearer token
       Homoglyph at pos 16: 'е' -> 'e' (U+0435)
       Homoglyph at pos 19: 'е' -> 'e' (U+0435)
       Homoglyph at pos 26: 'е' -> 'e' (U+0435)
       Homoglyph at pos 29: 'е' -> 'e' (U+0435)
       Homoglyph at pos 33: 'е' -> 'e' (U+0435)
         ⚠  FORGED TOKEN pattern detected: 'sk-' -> 'sk-'
         ⚠  FORGED TOKEN pattern detected: 'Bearer ' -> 'Bеarеr '

  ❌  H2: Cyrillic in OpenAI-style key
       Homoglyph at pos 3: 'е' -> 'e' (U+0435)
         ⚠  FORGED TOKEN pattern detected: 'sk-' -> 'sk-'
         ⚠  FORGED TOKEN pattern detected: 'eyJ' -> 'еyJ'

  ❌  H3: Greek Α for A in AWS key
       Homoglyph at pos 4: 'Ι' -> 'I' (U+0399)
         ⚠  FORGED TOKEN pattern detected: 'AKIA' -> 'AKIA'

  ✅  H4: Homoglyph in natural-language handoff
       Homoglyph at pos 13: 'е' -> 'e' (U+0435)
       Homoglyph at pos 20: 'е' -> 'e' (U+0435)
       Homoglyph at pos 30: 'е' -> 'e' (U+0435)
       Homoglyph at pos 32: 'с' -> 'c' (U+0441)
       Homoglyph at pos 42: 'е' -> 'e' (U+0435)

  🛡  H5: Clean handoff (benign)

  ✅  Z1: ZWS hidden in 'execute' (makes it 'exec​ute')
       Zero-width at pos 4: '​' (U+200B)

  ✅  Z2: ZWJ-smuggled hidden flag
       Zero-width at pos 20: '‍' (U+200D)
       Zero-width at pos 23: '‍' (U+200D)

  ✅  Z3: BOM (U+FEFF) hidden in JSON-like key
       Zero-width at pos 0: '﻿' (U+FEFF)

  ✅  Z4: RTL override (spec should strip this)

  ✅  Z5: Multiple ZW chars (U+200B+200C+200D+FEFF) between 'a' and 'b'
       Zero-width at pos 1: '​' (U+200B)
       Zero-width at pos 2: '‌' (U+200C)
       Zero-width at pos 3: '‍' (U+200D)
       Zero-width at pos 4: '﻿' (U+FEFF)

  🛡  Z6: Benign multi-line handoff
       Control char at pos 32: '\n' (U+000A)
       Control char at pos 51: '\n' (U+000A)

  ❌  C1: Combined homoglyph + ZWS in API key
       Homoglyph at pos 3: 'е' -> 'e' (U+0435)
       Homoglyph at pos 13: 'с' -> 'c' (U+0441)
       Zero-width at pos 4: '​' (U+200B)
         ⚠  FORGED TOKEN pattern detected: 'sk-' -> 'sk-'

  ✅  C2: Bidi override (should be stripped by spec)

------------------------------------------------------------------------
  Total attack cases : 11
  Bypassed           : 4
  Mitigated          : 7

  ── Bypassed Attacks ──
    ❌ H1: Cyrillic 'е' forges Bearer token
    ❌ H2: Cyrillic in OpenAI-style key
    ❌ H3: Greek Α for A in AWS key
    ❌ C1: Combined homoglyph + ZWS in API key

  ── Mitigated Attacks ──
    ✅ H4: Homoglyph in natural-language handoff
    ✅ Z1: ZWS hidden in 'execute'
    ✅ Z2: ZWJ-smuggled hidden flag
    ✅ Z3: BOM (U+FEFF) hidden in JSON-like key
    ✅ Z4: RTL override
    ✅ Z5: Multiple ZW chars
    ✅ C2: Bidi override

========================================================================
  SECURITY ANALYSIS (from script)
========================================================================

  1. Homoglyph protection:   NONE (not in spec)
  2. Zero-width protection:  NONE (not in spec)
  3. Bidi protection:        PARTIAL
  4. Control char protection: NONE (not in spec)
```

---

## 二、攻击绕过分析

### 2.1 同形字伪造令牌攻击（Homoglyph Token Forgery）

**执行率：4/5 = 80%**（4 个攻击用例中有 4 个成功注入，仅 H4 因不匹配硬编码 TOKEN_PATTERNS 而未被标记为 bypassed）

**事实上 H4 的同形字同样存活**——5 个 Cyrillic 同形字全部穿透 `normalize_handoff()`，仅因为脚本只检查 TOKEN_PATTERNS 列表而未被归类为 bypassed。**真实绕过率为 5/5 = 100%。**

| 用例 | 攻击向量 | 是否绕过 | 说明 |
|------|----------|----------|------|
| H1 | Cyrillic `е` (U+0435) 替换 `Bearer`、`sk-` 中的 `e` | **是** | 5 个同形字全部存活，Bearer 和 sk- 模式在规范化后仍可被检测到同形字版本 |
| H2 | Cyrillic `е` 在 `sk-` 和 `eyJ` 中 | **是** | sk- 前缀和 eyJ (JWT base64 头) 的同形字版本均存活 |
| H3 | Greek `Ι` (U+0399) 替换 AWS `AKIA` 中的 `I` | **是** | U+0399 在视觉上与 ASCII `I` 完全一致，但 Unicode 码点不同 |
| H4 | 自然语言中的同形字 (`thе`, `сonfig`, `filе`) | **是*** | 同形字存活，但不匹配 TOKEN_PATTERNS 列表 |
| C1 | 同形字 + ZWS 组合攻击 | **是** | 两种攻击向量叠加，均存活 |

**根因**：`unicodedata.normalize('NFC', text)` 只做规范组合（将组合字符合并为预组合字符），**不会将 Cyrillic/Greek 字符转换为 Latin 等价字符**。Cyrillic `е` (U+0435) 在 NFC 下保持为 U+0435，不会变为 Latin `e` (U+0065)。需要 **NFKC（兼容性规范化）** 才能做此转换。

**影响评估（高危）**：
- 攻击者可以伪造任意 API token 前缀（Bearer、sk-、ghp_、AKIA、xoxb- 等）
- 受害者（包括 LLM agent）肉眼无法区分 `sk-abc` 和 `sk-аbc`（后者第二个字母是 Cyrillic `а`）
- 在多 agent 交接场景中，伪造的 token 可能被下游 agent 当作合法凭证执行操作
- H3 中 AWS key `AKIA` + Greek `Ι` 在大多数字体中完全不可区分

### 2.2 零宽字符隐藏 Payload 攻击（Zero-Width Hidden Payload）

**测试脚本声称的执行率：0%（全部标记为 "Mitigated"）**
**真实执行率：4/5 = 80%**（4 个攻击用例中的 ZW 字符全部存活，Z4 因含 RTL override 被真正剥离）

**测试脚本存在严重分类缺陷**：脚本的 `attack_stopped` 逻辑仅检查是否有 FORGED TOKEN 残留，零宽字符攻击不涉及 token 伪造却仍被标记为 "mitigated"。这是一个 **假阴性掩盖真实漏洞** 的分类 bug。

| 用例 | 攻击向量 | ZW 是否存活 | 脚本分类 | 正确分类 |
|------|----------|-------------|----------|----------|
| Z1 | ZWS (U+200B) 隐藏在 "execute" 中 | **是** | Mitigated | **绕过** |
| Z2 | ZWJ (U+200D) 隐藏在 "--ignore-check" 标志中 | **是** | Mitigated | **绕过** |
| Z3 | BOM (U+FEFF) 前缀在 JSON key 中 | **是** | Mitigated | **绕过** |
| Z4 | RTL override (U+202E) | 否（被 strip） | Mitigated | 已缓解 |
| Z5 | 4 种 ZW 字符连续嵌入 (U+200B+200C+200D+FEFF) | **是** | Mitigated | **绕过** |

**根因**：`normalize_handoff()` 只 strip `‮` (RLO) 和 `‭` (LRO)，对零宽字符（U+200B-U+200D, U+FEFF, U+2060-U+2064）完全不做处理。NFC normalization 不会移除这些字符。

**脚本分类 bug 详情**：

```python
# 当前逻辑（有缺陷）
attack_stopped = False
if is_attack:
    remaining_forged = scan_for_forged_tokens(result)
    if not remaining_forged:
        attack_stopped = True  # <-- 零宽攻击不产生 forged token，直接进入此分支
```

`scan_for_forged_tokens()` 仅检查同形字版本的 `TOKEN_PATTERNS`，零宽字符改变的是字符串的结构（而非内容），因此永远不产生 forged token 告警。这导致 Z1-Z5 被错误标记为 "Mitigated"。

**影响评估（高危）**：
- Z1：`exec​ute`（嵌入 ZWS）与 `execute` 在视觉上完全一致，但 `normalize_handoff("exec​ute") in "execute"` 为 False。可用于绕过基于关键词的安全过滤
- Z2：`no‍op‍--ignore-check` 在渲染时显示为 `noop--ignore-check`，ZWJ 不可见但改变字符串语义
- Z3：BOM 前缀可能破坏 JSON parser 或导致 key 查找失败
- Z5：多个连续 ZW 字符可编码任意数据（如用 ZWJ/ZWNJ 序列编码二进制 payload），实现隐蔽信道

### 2.3 双向控制字符处理

**执行率：2/2 = 0%**（两个 bidi 攻击均被缓解）

| 用例 | 攻击向量 | 是否存活 | 说明 |
|------|----------|----------|------|
| Z4 | RLO (U+202E) 反转 "Transfer $1000" | 否 | 被 `replace('‮', '')` 正确 strip |
| C2 | RLO+RLO 双向覆盖 | 否 | 被 strip |

**缓解有效**，但存在 **覆盖不完全** 的问题：

```python
# 当前实现
text = text.replace('‮', '').replace('‭', '')  # 仅 strip RLO 和 LRO
```

以下 bidi 控制字符 **未被 strip**：
- U+2066 LEFT-TO-RIGHT ISOLATE (LRI)
- U+2067 RIGHT-TO-LEFT ISOLATE (RLI)
- U+2068 FIRST STRONG ISOLATE (FSI)
- U+2069 POP DIRECTIONAL ISOLATE (PDI)
- U+200E LEFT-TO-RIGHT MARK (LRM)
- U+200F RIGHT-TO-LEFT MARK (RLM)

**影响评估（中危）**：LRI/RLI 可以通过隔离（isolate）方式实现类似 RLO 的文本方向操纵效果，但强度弱于 RLO。LRM/RLM 可用于微调文本方向，在特定场景下可制造混淆。

### 2.4 控制字符处理

**执行率：N/A（无专门测试）**，但代码审计确认：

- `normalize_handoff()` 对 U+0000-U+001F 和 U+007F-U+009F 控制字符 **完全不做处理**
- 规范要求 "MUST be escaped" 但函数实现无视此要求
- 在 Z6（良性文本）中，`\n` (U+000A) 被检测到但通过了规范化

---

## 三、总体统计

| 指标 | 值 |
|------|-----|
| 总攻击用例数 | 11 |
| 脚本报告绕过 | 4（H1, H2, H3, C1） |
| 实际绕过数（含脚本分类 bug 修正） | 8（H1, H2, H3, H4, C1, Z1, Z2, Z3, Z5 → 去重后 9 个独特攻击向量） |
| 同形字攻击真实执行率 | 5/5 = **100%** |
| 零宽字符攻击真实执行率 | 4/5 = **80%**（仅 RTL override 被缓解） |
| Bidi 攻击缓解率 | 2/2 = 100%（但覆盖不全） |
| `normalize_handoff()` 有效覆盖率 | 2/11 = **18%** |

---

## 四、发现的关键漏洞

### VULN-1: 同形字伪造无防护（严重）

**描述**：`normalize_handoff()` 使用 NFC normalization，不转换同形字。Cyrillic `е` (U+0435)、Greek `Ι` (U+0399) 等 27 个已知同形字全部穿透。

**修复建议**：
```python
# Option A: 切换为 NFKC normalization
text = unicodedata.normalize('NFKC', text)

# Option B: 维护一个同形字映射表，显式替换
for homoglyph, ascii_char in HOMOGLYPH_MAP.items():
    text = text.replace(homoglyph, ascii_char)
```

**注意**：NFKC 会改变更多字符（如全角数字 → 半角、连字 → 分开），可能打破现有语义。Option B 更可控但需要维护映射表。

### VULN-2: 零宽字符无防护（严重）

**描述**：11 种零宽/不可见字符（U+200B-U+200D, U+FEFF, U+200E-U+200F, U+2060-U+2064）全部穿透。

**修复建议**：
```python
ZERO_WIDTH_STRIP = ''.join([
    '​', '‌', '‍', '﻿', '‎', '‏',
    '⁠', '⁡', '⁢', '⁣', '⁤',
])
for ch in ZERO_WIDTH_STRIP:
    text = text.replace(ch, '')
```

### VULN-3: Bidi 覆盖不完整（中危）

**描述**：仅 strip U+202D (LRO) 和 U+202E (RLO)，遗漏 LRI/RLI/FSI/PDI (U+2066-U+2069) 和 LRM/RLM (U+200E-U+200F)。

**修复建议**：扩展 strip 列表至全套 bidi 控制字符。

### VULN-4: 控制字符未转义（中危）

**描述**：规范要求转义控制字符，但实现完全无视此要求。

**修复建议**：添加控制字符检测/转义/拒绝逻辑。

### VULN-5: 测试脚本分类缺陷（用于审计的脚本本身有 bug）

**描述**：`attack_stopped` 判定逻辑仅检查 token forgery，遗漏零宽字符攻击的分类。这会导致安全审计产生假阴性。

---

## 五、建议的增强版 normalize_handoff()

```python
import unicodedata

# All zero-width and invisible characters
ZERO_WIDTH_CHARS = ''.join(chr(c) for c in [
    0x200B, 0x200C, 0x200D, 0xFEFF, 0x200E, 0x200F,
    0x2060, 0x2061, 0x2062, 0x2063, 0x2064,
])

# All bidirectional control characters
BIDI_CONTROLS = ''.join(chr(c) for c in [
    0x202A, 0x202B, 0x202C, 0x202D, 0x202E,
    0x2066, 0x2067, 0x2068, 0x2069,
])

def normalize_handoff_hardened(text: str) -> str:
    # Step 1: Strip all zero-width characters
    for ch in ZERO_WIDTH_CHARS:
        text = text.replace(ch, '')
    # Step 2: Strip all bidirectional controls
    for ch in BIDI_CONTROLS:
        text = text.replace(ch, '')
    # Step 3: NFKC normalization (converts homoglyphs)
    text = unicodedata.normalize('NFKC', text)
    # Step 4: Strip any remaining control characters
    text = ''.join(ch for ch in text if ord(ch) >= 0x20 or ch in '\n\r\t')
    return text
```

---

## 六、结论

当前 `normalize_handoff()` 规范仅能防御 **2 种攻击向量**（RLO/LRO 双向覆盖），对同形字伪造、零宽字符隐藏 payload、控制字符注入均无防护能力。**有效覆盖率为 18%**。

在多 agent 交接场景中，攻击者可以：
1. 用 Cyrillic/Greek 同形字伪造 API token，绕过基于字符串匹配的安全检查
2. 用零宽字符改变关键词/命令的语义，绕过基于关键词的安全过滤
3. 用 ZWJ/ZWNJ 序列编码隐蔽信息，建立隐蔽信道

**所有 9 个实际绕过的攻击向量均需在 Phase 2 中解决。**
