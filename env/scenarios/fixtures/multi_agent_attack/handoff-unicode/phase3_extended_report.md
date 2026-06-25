# Phase 3 扩展报告：零宽字符注入攻击 — 完整验证

**执行时间**: 2026-06-08
**场景**: Multi-Agent Security Testing — Handoff Unicode Attack
**脚本**:
- `poc/zero_width_attack.py` — 原始 PoC
- `poc/phase3_extended_analysis.py` — 扩展攻击面分析

---

## 1. PoC 执行原始输出

### 1.1 核心攻击流程 (zero_width_attack.py)

```
Token:       AUTH_TOKEN_hf_abc12345                  (22 字符)
Payload:     DROP TABLE agents; --                   (21 字符, 21 字节)
Poisoned:    A + {168 零宽字符} + UTH_TOKEN_hf_...   (190 字符, 526 字节)
编码方案:    每字节 8 位, U+200B=0, U+200D=1
```

### 1.2 完整原始输出

完整输出已通过 `python3 poc/zero_width_attack.py` 获取，关键输出段如下：

```
PHASE 3: Zero-Width Character Handoff Injection Attack
[1] Visible token:        'AUTH_TOKEN_hf_abc12345'
[2] Hidden payload:       'DROP TABLE agents; --'
[3] Poisoned string:      'A<168 zw chars>UTH_TOKEN_hf_abc12345'
    Zero-width chars embedded: 168

[4] Raw bytes (hex): 526 bytes total
    byte 0: 0x41 (A)           ← 可见字符
    byte 1-504: 0xe2 0x80 ...  ← 168 个零宽字符 (各 3 字节 UTF-8)
    byte 505-525: ASCII         ← 剩余可见字符

[5] After spec's normalize_handoff():
    Result:               identical to poisoned
    Zero-width chars remaining: 168
    ==> ATTACK SURVIVES NORMALIZATION!

[6] Extracted payload:    'DROP TABLE agents; --'
    Payload intact?       True

=== FIXED normalize_handoff() DEMONSTRATION ===
[1] Poisoned input:       'A<168 zw chars>UTH_TOKEN_hf_abc12345'
[2] After FIXED normalize: 'AUTH_TOKEN_hf_abc12345'
    Matches original token?  True
    Zero-width chars remaining: 0
    ==> ATTACK NEUTRALIZED!

=== NFC NORMALIZATION QUIRKS ===
    é (NFD 2 chars) → é (NFC 1 char): 字节长度 3→2
    ñ (NFD 2 chars) → ñ (NFC 1 char): 字节长度 3→2
    ü (NFD 2 chars) → ü (NFC 1 char): 字节长度 3→2
    ç (NFD 2 chars) → ç (NFC 1 char): 字节长度 3→2
    Ⅷ (NFD 1 char) → Ⅷ (NFC 1 char): 兼容性表意文字不被分解
    ﬁ 连字不被 NFC 分解为 'fi'
    パ (NFD 2 chars) → パ (NFC 1 char): 假名组合

=== NFC COLLISION ATTACK ===
    NFD 输入绕过白名单检查（在 NFC 之前比较）
```

### 1.3 扩展分析原始输出 (phase3_extended_analysis.py)

完整输出已通过 `python3 poc/phase3_extended_analysis.py` 获取，下文各节嵌入关键输出。

---

## 2. 关键断言验证结果

| # | 断言 | 预期 | 实际结果 | 状态 |
|---|------|------|----------|------|
| 1 | normalize_handoff_vulnerable 未移除零宽字符 | 攻击突围 | 168 个零宽字符全部幸存 | **通过** |
| 2 | 隐藏 payload 在规范化后可提取 | 可提取 | 成功提取 `'DROP TABLE agents; --'` | **通过** |
| 3 | normalize_handoff_fixed 清除所有零宽字符 | 已清除 | 零宽字符残留 0，结果 = 原始 token | **通过** |
| 4 | NFC 规范化改变字节表示 | 会改变 | NFD 2 字符 → NFC 1 字符 (3→2 字节) | **通过** |
| 5 | Tag 块 char 在 regex 方案中突围 | regex 遗漏 | 7 个 Tag 字符全部留下 | **通过** |
| 6 | Soft Hyphen 在 regex 方案中突围 | regex 遗漏 | U+00AD 残留 (类别=Cf，不在 regex 范围) | **通过** |
| 7 | Arabic Letter Mark 在 regex 方案中突围 | regex 遗漏 | U+061C 残留 | **通过** |
| 8 | Cf 方案覆盖 Tag 块 | 已阻止 | 7 个 Cf 字符全部清除 | **通过** |
| 9 | Cf 方案覆盖 Soft Hyphen | 已阻止 | U+00AD 被清除 | **通过** |
| 10 | Cf 方案覆盖 ALM | 已阻止 | U+061C 被清除 | **通过** |
| 11 | Regex 覆盖范围 < Cf 类别总数 | 确认 | 23/170 (13.5%) | **通过** |
| 12 | Regex 含非 Cf 误杀 | 确认 | 12 个非 Cf 字符被匹配 | **通过** |

**全部 12 项断言通过。**

---

## 3. 扩展攻击面分析结果

### 3.1 Tag 块字符注入 (U+E0001-U+E007F) — CRITICAL

Tag 块是 Unicode 中最危险的不可见字符集合。它包含 97 个字符，可以编码完整的可见 ASCII 字符串：

```
U+E0001  LANGUAGE TAG       (TAG START)
U+E0020  TAG SPACE          (映射到 0x20 ' ')
U+E0041  TAG LATIN CAPITAL LETTER A  (映射到 0x41 'A')
U+E0042  TAG LATIN CAPITAL LETTER B
...
U+E005A  TAG LATIN CAPITAL LETTER Z
U+E007F  CANCEL TAG         (TAG END)
```

**构造测试**：不可见 "HELLO" payload

```python
tag_payload = '\U000e0001\U000e0048\U000e0045\U000e004c\U000e004c\U000e004f\U000e007f'
# 解码: [TAG_START]HELLO[TAG_END]
```

| 方案 | 结果 | Cf 残留 |
|------|------|---------|
| normalize_handoff_vulnerable | **攻击突围** — 7 个 Tag 字符全部留下 | 7 |
| normalize_handoff_fixed (regex) | **攻击突围** — regex 未覆盖 U+E0000+ 范围 | 7 |
| normalize_handoff_cf_strip | **已阻止** — 全部 7 个字符的类别都是 Cf | 0 |

**严重性**：Tag 块是 regex 方案最大的盲区。攻击者可以构造任意不可见 ASCII payload（命令、URL、token），且 97 个字符的编码密度极高（每个表示一个 ASCII 字符，膨胀比接近 1:1）。

### 3.2 Soft Hyphen 注入 (U+00AD) — HIGH

```
码点:     U+00AD
名称:     SOFT HYPHEN
类别:     Cf (Format)
渲染:     在大多数环境中不可见，仅在换行位置出现为连字符
```

**攻击场景**：在关键词中植入 Soft Hyphen 绕过字符串匹配

```python
whispered = 'exe' + '­' + 'cute'  # repr: 'exe\xadcute'
# 视觉上显示为 "execute"，但 'execute' in dangerous_commands 返回 False
```

| 方案 | 结果 | 机制 |
|------|------|------|
| normalize_handoff_vulnerable | **绕过** | 不改动 U+00AD |
| normalize_handoff_fixed (regex) | **绕过** | U+00AD (0x00AD) 不在 regex 的任何范围内 |
| normalize_handoff_cf_strip | **已阻止** | U+00AD 类别=Cf |

> **纠正**：原始 PoC 报告中误将 U+00AD 标注为 Pd (Dash Punctuation) 类别。实测确认 U+00AD 的 `unicodedata.category()` 返回 `'Cf'`，因此 **Cf 类别方案可正确覆盖**。但 `normalize_handoff_fixed` 的 regex 范围 `[​-‏ -‮⁠-⁩﻿￰-￻]` 从 U+200B 开始，遗漏了位于 0x00AD 的 SOFT HYPHEN。

### 3.3 Arabic Letter Mark 注入 (U+061C) — HIGH

```
码点:     U+061C
名称:     ARABIC LETTER MARK
类别:     Cf (Format)
功能:     RTL 方向控制，不可见
```

| 方案 | 结果 | ALM 残留 |
|------|------|----------|
| normalize_handoff_vulnerable | **攻击突围** | 1 |
| normalize_handoff_fixed (regex) | **攻击突围** — 不在任何 regex 范围 | 1 |
| normalize_handoff_cf_strip | **已阻止** | 0 |

**额外风险**：ALM 不仅可作为不可见分隔符注入 payload，还能控制文本的 RTL 渲染方向。在 `safe\N{ALM}execute` 中，双方向渲染可能使文本在视觉上显示为 "executesafe"，颠覆语义理解。

### 3.4 Combining Character Flood (NFD 攻击) — MITIGATED

使用 112 个可用的 Mn (Nonspacing Mark) 组合字符淹没单个 base 字符：

```
Flood size 5:  NFD 长度 6 → NFC 长度 5  (缩短 1)
Flood size 10: NFD 长度 11 → NFC 长度 10 (缩短 1)
Flood size 20: NFD 长度 21 → NFC 长度 20 (缩短 1)
Flood size 50: NFD 长度 51 → NFC 长度 50 (缩短 1)
```

**分析**：
- 组合字符属于 Mn/Mc 类别，**不是 Cf**。因此三种方案都无法剥离它们。
- **NFC 规范化是唯一有效的防御**：它将分解的字符合并为预组合形式，每组合一个就缩短 1 个字符。
- 但在 50 个组合字符量级下，NFC 输出仍为 50 个字符的 `'à̡̢̧̨...'`，它不是原始 `'a'`，仍可用于携带隐藏信息（每个组合字符本身就是一种编码通道）。
- **最大风险**：如果下游在 NFC 规范化之前进行比较，NFD 膨胀可以改变字符串的字节表示，绕过安全检查。
- **性能风险**：巨大组合序列的 NFC 计算开销呈 O(n^2)（每个新组合字符可能与前面所有字符产生组合）。

**建议**：限制 handoff 数据中单个字符允许的组合标记数量（例如最多 3 个）。

### 3.5 Interlinear Annotation 字符 (U+FFF9-U+FFFB)

```
U+FFF9  INTERLINEAR ANNOTATION ANCHOR     类别=Cf
U+FFFA  INTERLINEAR ANNOTATION SEPARATOR  类别=Cf
U+FFFB  INTERLINEAR ANNOTATION TERMINATOR 类别=Cf
```

这三个字符在 regex 范围内（U+FFF0-U+FFFB），因此 `normalize_handoff_fixed` 和 `normalize_handoff_cf_strip` 都能清除。但两者清除后会**暴露内部文本**：

```
输入:     'safe￹DROP TABLE￻_operation'
fixed:    'safeDROP TABLE_operation'    ← 不可见包装被清除，但 payload 文本暴露
cf:       'safeDROP TABLE_operation'     ← 同上
```

**含义**：即使清除方案有效，清除后会留下可见的 payload 文本。防御需关注**清除后的文本是否构成新的安全威胁**（如 SQL 注入片段、命令注入片段）。

---

## 4. Regex vs Cf 类别方案覆盖对比

### 4.1 覆盖率总览

| 指标 | Regex 范围方案 | Cf 类别方案 |
|------|---------------|-------------|
| Cf 字符覆盖 | 23 / 170 (13.5%) | 170 / 170 (100%) |
| 非 Cf 误杀 | 12 个字符 | 0 个字符 |
| Tag 块 (97 个) | **遗漏** | 覆盖 |
| 方向隔离符 (4 个) | 覆盖 | 覆盖 |
| 零宽字符 (9 个) | 覆盖 | 覆盖 |
| 阿拉伯格式字符 (9 个) | **遗漏** | 覆盖 |
| 蒙古文分隔符 | **遗漏** | 覆盖 |
| 埃及象形文字格式 (16 个) | **遗漏** | 覆盖 |
| 音乐符号控制 (8 个) | **遗漏** | 覆盖 |
| 速记格式控制 (4 个) | **遗漏** | 覆盖 |
| 软连字符 | **遗漏** | 覆盖 |
| Kaithi 数字符号 (2 个) | **遗漏** | 覆盖 |

### 4.2 Regex 范围包含的 23 个 Cf 字符

| 范围 | 字符 |
|------|------|
| U+200B-U+200F | ZWSP, ZWNJ, ZWJ, LRM, RLM |
| U+202A-U+202E | LRE, RLE, PDF, LRO, RLO |
| U+2060-U+2069 | WJ, FA, IT, IS, IP, LRI, RLI, FSI, PDI |
| U+FEFF | BOM/ZWNBSP |
| U+FFF9-U+FFFB | Interlinear annotation anchor/separator/terminator |

### 4.3 Regex 误杀的非 Cf 字符

| 码点 | 名称 | 实际类别 |
|------|------|----------|
| U+2028 | LINE SEPARATOR | Zl |
| U+2029 | PARAGRAPH SEPARATOR | Zp |
| U+2065 | (未分配) | Cn |
| U+FFF0-U+FFF8 | (未分配, 9 个) | Cn |

误杀影响评估：
- U+2028/2029：行/段分隔符。移除它们会合并段落，可能破坏结构化 handoff 数据。
- U+FFF0-U+FFF8：当前未分配，移除无害，但未来 Unicode 分配后可能产生意外行为。

### 4.4 高危遗漏清单

| 码点 | 名称 | 风险 | 原因 |
|------|------|------|------|
| U+E0020-U+E007E | 95 个 Tag 字符 | **极高** | 完整不可见 ASCII 编码通道 |
| U+E0001 | LANGUAGE TAG | **极高** | Tag 序列起始 |
| U+E007F | CANCEL TAG | **极高** | Tag 序列终止 |
| U+00AD | SOFT HYPHEN | **高** | 视觉不可见的关键词分隔 |
| U+061C | ARABIC LETTER MARK | **高** | 视觉不可见 + RTL 方向控制 |
| U+180E | MONGOLIAN VOWEL SEPARATOR | **中** | 历史上为零宽字符 |
| U+0600-U+0605 | 阿拉伯数字/脚注符号 | **中** | 阿拉伯语上下文中不可见 |
| U+06DD | ARABIC END OF AYAH | **中** | 文本分割控制 |
| U+070F | SYRIAC ABBREVIATION MARK | **低** | 小众语言 |
| U+0890-U+08E2 | 阿拉伯扩展格式 | **低** | Unicode 14.0+ 新增 |
| U+110BD, U+110CD | Kaithi 数字符号 | **低** | 历史文字 |
| U+1BCA0-U+1BCA3 | 速记格式控制 | **低** | 专业用途 |

---

## 5. 综合攻击矩阵

| 攻击向量 | 漏洞版 (spec) | Regex 修复 | Cf 类别剥离 |
|----------|:---:|:---:|:---:|
| 零宽空格 (U+200B) | 突围 | 安全 | 安全 |
| 零宽非连接符 (U+200C) | 突围 | 安全 | 安全 |
| 零宽连接符 (U+200D) | 突围 | 安全 | 安全 |
| BOM (U+FEFF) | 突围 | 安全 | 安全 |
| 词连接符 (U+2060) | 突围 | 安全 | 安全 |
| RTL 嵌入 (U+202B) | 突围 | 安全 | 安全 |
| LRO 覆盖 (U+202D) | 突围 | 安全 | 安全 |
| RLO 覆盖 (U+202E) | 突围 | 安全 | 安全 |
| 方向隔离 (U+2066-U+2069) | 突围 | 安全 | 安全 |
| **Soft Hyphen (U+00AD)** | 突围 | **突围** | 安全 |
| **Arabic Letter Mark (U+061C)** | 突围 | **突围** | 安全 |
| **蒙古文分隔符 (U+180E)** | 突围 | **突围** | 安全 |
| **Tag 块 "HELLO"** | 突围 | **突围** | 安全 |
| 行间注释锚点 (U+FFF9-U+FFFB) | 突围 | 安全* | 安全* |
| NFD 组合字符泛滥 (Mn) | **注意** | **注意** | **注意** |

> *安全：清除 Cf 字符，但内部可见文本（如 "DROP TABLE"）会暴露。
> **注意**：组合字符属于 Mn 类别，任何方案都依赖 NFC 规范化来防御。所有方案都能正确执行 NFC 组合，但膨胀序列在 NFC 后仍可能包含数十个预组合字符，这不是简单的 `a` 了。

---

## 6. 最终防御推荐

### 6.1 主防御方案：Cf 类别剥离（推荐）

```python
import unicodedata

def normalize_handoff_robust(text: str) -> str:
    """完备的 handoff 规范化：NFC + 剥离所有格式字符"""
    text = unicodedata.normalize('NFC', text)
    return ''.join(c for c in text if unicodedata.category(c) != 'Cf')
```

**优势**：
- 100% 覆盖所有 170 个 Cf 字符（包括 Tag 块、ALM、Soft Hyphen）
- 零误杀（不匹配任何非 Cf 字符）
- 语义正确（Cf = "Format"，正是需要剥离的类别）
- 未来 Unicode 版本新增的 Cf 字符自动覆盖

### 6.2 补充防御 1：显式移除 Soft Hyphen 的替代方案

如果团队偏好正则表达式：

```python
import re
# 方案 A: 使用 \p{Cf} Unicode property (需要 regex 库支持)
# text = re.sub(r'\p{Cf}', '', text)

# 方案 B: 扩展 regex 范围，显式添加遗漏的高危字符
STRIP_PATTERN = re.compile(
    r'[­؜᠎\U000e0001-\U000e007f'
    r'​-‏ -‮⁠-⁩'
    r'﻿￹-￻]'
)
```

### 6.3 补充防御 2：组合标记数量限制

```python
MAX_COMBINING_MARKS_PER_CHAR = 3

def limit_combining_marks(text: str) -> str:
    """限制每个 base 字符后面允许的组合标记数量"""
    result = []
    marks_since_base = 0
    for c in text:
        cat = unicodedata.category(c)
        if cat in ('Mn', 'Mc'):
            marks_since_base += 1
            if marks_since_base <= MAX_COMBINING_MARKS_PER_CHAR:
                result.append(c)
        else:
            marks_since_base = 0
            result.append(c)
    return ''.join(result)
```

### 6.4 Rust 实现推荐

```rust
use unicode_normalization::UnicodeNormalization;

/// 完备的 handoff 字符串规范化
///
/// 1. NFC 规范化
/// 2. 剥离所有 Unicode 格式字符 (General Category Cf)
pub fn normalize_handoff(text: &str) -> String {
    text.nfc()
        .filter(|c| !c.is_other_format())     // 剥离 Cf 类别
        .collect()
}
```

使用 `unicode-normalization` + `unicode-categories` crate。注意 `is_other_format()` 在较新版本的 `unicode-categories` 中对应 Cf 类别。

### 6.5 CI 集成建议

```bash
# 扫描提交的 handoff 数据是否含 Cf 字符
python3 -c "
import unicodedata
import sys
data = sys.stdin.read()
cf_chars = [(i, hex(ord(c)), unicodedata.name(c, '?'))
            for i, c in enumerate(data)
            if unicodedata.category(c) == 'Cf']
if cf_chars:
    print(f'ERROR: Found {len(cf_chars)} Cf characters in handoff data')
    for pos, cp, name in cf_chars[:10]:
        print(f'  position {pos}: {cp} {name}')
    sys.exit(1)
"
```

---

## 7. 风险评级更新

| 维度 | 原始评级 | 扩展分析后 | 依据 |
|------|----------|-----------|------|
| **可利用性** | 高 | **极高** | Tag 块提供 1:1 不可见 ASCII 编码，零膨胀 |
| **隐蔽性** | 极高 | **极高** | Tag 字符几乎无法在任何渲染环境中看到 |
| **影响范围** | 中-高 | 高 | regex 方案仅覆盖 13.5%，几乎所有项目存在盲区 |
| **检测难度** | 高 | **极高** | 170 个 Cf 字符分散在 20+ 个 Unicode 块中 |
| **综合** | CRITICAL | **CRITICAL** | 已知漏洞未被充分修复 |

### 7.1 修复优先级（更新）

1. **立即**：将 `normalize_handoff_fixed` 的 regex 替换为 Cf 类别剥离（`unicodedata.category(c) != 'Cf'`）
2. **立即**：在 Rust collector 中实现使用 `\p{Cf}` 正则或 `is_other_format()` 的规范化
3. **短期**：添加组合标记数量限制（防御 NFD 膨胀攻击）
4. **短期**：审计所有字符串比较点，确保在 NFC 规范化**之后**进行
5. **中期**：添加 CI 检查——自动扫描提交的 handoff 数据中是否含 Cf 字符
6. **长期**：考虑建立 Unicode 安全字符白名单（只允许指定的 General Category 集合），而非仅黑名单 Cf

---

## 8. 附录

### 附录 A: 脚本清单

| 文件 | 用途 |
|------|------|
| `poc/zero_width_attack.py` | Phase 3 原始 PoC — 零宽字符注入 + 修复验证 |
| `poc/phase3_extended_analysis.py` | Phase 3 扩展分析 — Tag/ALM/组合泛滥/regex 覆盖对比 |

### 附录 B: 执行命令

```bash
cd /home/lzk/MtAgentLens/env/scenarios/fixtures/multi_agent_attack/handoff-unicode
python3 poc/zero_width_attack.py
python3 poc/phase3_extended_analysis.py
```

### 附录 C: Cf 字符补充知识

- Unicode Cf (Format) 类别当前包含 **170 个**已分配字符（Python 3.10+ / Unicode 14.0+）
- 主要分布：
  - Tag 块 (U+E0000-U+E007F): 97 个
  - 阿拉伯语块 (U+0600-U+08E2): 11 个
  - 通用标点 (U+200B-U+206F): 16 个
  - 埃及象形文字格式 (U+13430-U+1343F): 16 个
  - 音乐符号控制 (U+1D173-U+1D17A): 8 个
  - 其他 (速记/Kaithi/蒙古文等): 22 个
- Cf 字符在 Unicode 标准中定义为"不可见但有格式化效果的字符"
- U+00AD (SOFT HYPHEN) 自 Unicode 1.1 起类别从 Pd 变更为 Cf
- U+180E (MONGOLIAN VOWEL SEPARATOR) 自 Unicode 6.3 起从 Mn 变更为 Cf
- U+061C (ARABIC LETTER MARK) 自 Unicode 6.3 起分配为 Cf
