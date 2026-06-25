# Phase 3 报告：零宽字符注入攻击 PoC 执行与修复验证

**执行时间**: 2026-06-08
**场景**: Multi-Agent Security Testing — Handoff Unicode Attack
**攻击面**: Unicode 零宽字符绕过 normalize_handoff() 规范化

---

## 1. PoC 执行结果

### 1.1 核心攻击流程

完整的 PoC 脚本 (`zero_width_attack.py`) 已成功执行，输出如下关键发现：

```
Token:     AUTH_TOKEN_hf_abc12345             (22 字符)
Payload:   DROP TABLE agents; --              (21 字符)
Poisoned:  [A] + {168 零宽字符} + [UTH_TOKEN...]  (190 字符)
```

编码方案：每个 payload 字节编码为 8 位，U+200B (ZERO WIDTH SPACE) = 0，U+200D (ZERO WIDTH JOINER) = 1。21 字节 payload → 168 个零宽字符。

### 1.2 关键发现验证

| 验证项 | 预期 | 实际结果 | 确认 |
|--------|------|----------|------|
| normalize_handoff() 能否阻止零宽字符注入？ | 不能 | 168 个零宽字符全部幸存 | **确认** |
| 隐藏 payload 在规范化后是否仍可提取？ | 是 | 成功提取 `'DROP TABLE agents; --'` | **确认** |
| normalize_handoff_fixed() 是否成功清除所有零宽字符？ | 是 | 零宽字符剩余 0，结果与原始 token 完全一致 | **确认** |
| NFC 规范化是否会改变字节表示？ | 会 | NFD/NFC 对可视化相同但字节不同 (2→1) | **确认** |

---

## 2. 隐蔽性分析

### 2.1 人类肉眼可见性

在终端渲染环境中，零宽字符可能显示为不可见或点状伪影（取决于终端和字体）。但在以下环境中，它们是**完全不可见的**：

- 浏览器 DOM 渲染
- GUI 文本编辑器（VS Code 默认字体）
- IDE 中的代码视图
- Markdown 预览
- Agent handoff 文档的视觉呈现

**结论**：人类读者**无法**区分原始文本 `AUTH_TOKEN_hf_abc12345` 和携带 payload 的文本。攻击的隐蔽性极高。

### 2.2 可见差异检测

```
原始:    |AUTH_TOKEN_hf_abc12345|
中毒:    |AUTH_TOKEN_hf_abc12345|  (视觉上完全相同)
```

`repr()` 对比：
```
原始:   'AUTH_TOKEN_hf_abc12345'
中毒:   'A​‍​​​‍...'  (168 个转义字符)
```

---

## 3. Payload 编码密度

### 3.1 当前编码方案（二进制）

- 使用 2 种零宽字符 (U+200B, U+200D)
- 每零宽字符携带 1 bit 信息
- 168 零宽字符 → 21 字节 payload
- **密度**: 1 bit/zw-char, 0.125 字节/zw-char

### 3.2 攻击者可选择的更高密度方案

| 编码基数 | 可用字符数 | 每字符比特数 | 编码 21 字节所需字符数 | 膨胀比 |
|----------|-----------|-------------|---------------------|--------|
| 二进制 (当前) | 2 | 1.0 bit | 168 | 8:1 |
| 已知零宽字符 | 9 | 3.17 bit | 53 | 2.5:1 |
| 全部 Cf 类别字符 | 170 | 7.4 bit | 23 | 1.1:1 |
| Tag 块 (U+E0020-U+E007E) | 95 | 6.6 bit | 26 | 1.2:1 |

**关键发现**：使用全部 170 个 Unicode 格式字符，攻击者可以将 payload 膨胀比降低到接近 1:1——即每个可见字符携带接近 1 字节的隐藏数据，同时保持完全不可见。

---

## 4. 修复方案完备性分析

### 4.1 normalize_handoff_fixed() 的 regex 覆盖范围

修复版本使用的正则表达式：
```python
r'[​-‏ -‮⁠-⁩﻿￰-￻]'
```

覆盖的 Unicode 范围：
- U+200B~U+200F: 零宽空格/连接符/非连接符 + LRM/RLM (5 个字符)
- U+2028~U+202E: 行分隔符/段分隔符 + 方向嵌入/覆盖/POP (7 个字符)
- U+2060~U+2069: 词连接符 + 不可见数学运算符 + 方向隔离符 (10 个字符)
- U+FEFF: BOM/零宽不断空格 (1 个字符)
- U+FFF0~U+FFFB: 行间注释锚点/分隔符/终止符 (4 个字符，含 3 个 Cf)

**总覆盖**: 23/170 个 Unicode Format (Cf) 类别字符

### 4.2 未覆盖的高危字符

| 码点 | 名称 | 威胁级别 | 说明 |
|------|------|----------|------|
| U+00AD | SOFT HYPHEN | **高** | 不可见断字符，在绝大多数渲染中不显示 |
| U+061C | ARABIC LETTER MARK | **高** | 不可见格式化，RTL 攻击面 |
| U+180E | MONGOLIAN VOWEL SEPARATOR | **中** | 历史上为零宽字符，现今为格式字符 |
| U+206A~U+206F | 对称交换/数字形状控制 | **中** | 已弃用但可能仍存在于遗留数据 |
| U+E0001 | LANGUAGE TAG | **高** | 标签序列起始，完全不可见 |
| U+E0020~U+E007E | TAG 字符 (95 个) | **极高** | 每个都是不可见的 ASCII 编码字符，可组成字符串 |
| U+E007F | CANCEL TAG | **高** | 标签序列终止 |

**Tag 块特别危险**：U+E0001~U+E007F 可以构造完整的不可见 ASCII 字符串。`U+E0001 U+E0048 U+E0045 U+E004C U+E004C U+E004F U+E007F` 在视觉上完全不可见，但解码后为 "HELLO"。

### 4.3 更完备的修复建议

**推荐方案：剥离所有 Unicode 格式字符 (General Category = Cf)**

```python
import unicodedata

def normalize_handoff_robust(text: str) -> str:
    """完备的 handoff 规范化：NFC + 剥离所有格式字符"""
    text = unicodedata.normalize('NFC', text)
    # 剥离所有 Unicode General Category 为 'Cf' 的格式字符
    # 这包括所有零宽字符、方向控制字符、标签字符等
    return ''.join(c for c in text if unicodedata.category(c) != 'Cf')
```

**验证**：
- 输入: 190 字符的中毒字符串 (含 168 个 Cf 字符)
- 输出: 22 字符的原始 token
- 提取 payload: None (payload 完全被清除)

---

## 5. NFC 规范化攻击面

### 5.1 字节表示变化

NFC 规范化会将分解形式组合为预组合形式，可视化不变但字节表示改变：

| 字符 | NFD 形式 (输入) | NFC 形式 (输出) | 字节变化 |
|------|----------------|-----------------|---------|
| é | U+0065 U+0301 (2 字符, 3 字节) | U+00E9 (1 字符, 2 字节) | 3→2 |
| ñ | U+006E U+0303 (2 字符, 3 字节) | U+00F1 (1 字符, 2 字节) | 3→2 |
| ü | U+0075 U+0308 (2 字符, 3 字节) | U+00FC (1 字符, 2 字节) | 3→2 |
| パ | U+30CF U+309A (2 字符, 6 字节) | U+30D1 (1 字符, 3 字节) | 6→3 |

### 5.2 攻击含义

1. **NFD 绕过检查**：如果下游组件在 `normalize_handoff()` 之前进行字符串比较，NFD 形式的字符串（视觉相同但字节不同）可以绕过允许列表检查。
2. **意外等价**：某些 Unicode 字符 NFC 规范化后与敏感关键词等效，可能被滥用。
3. **长度爆炸**：NFD 形式可能包含大量组合字符（每个 base 字符可以有多个组合标记），导致规范化后显著缩短。

**PoC 确认**：spec 正确应用了 NFC，但任何在 NFC 规范化之前的字符串比较都是脆弱的。

---

## 6. Rust 实现建议

### 6.1 当前项目中的风险点

MtAgentLens 的 `collector/` (Rust) 和 `frontend/` (TypeScript) 中，任何处理 agent handoff 数据的组件都可能受影响。需要检查的点：

- `collector/src/framework/analyzers/` 中的字符串处理逻辑
- `collector/src/accountability/` 中的 identity/claims 字符串匹配
- 前端中的 Markdown 渲染和 handoff 数据显示

### 6.2 Rust 实现方案

```rust
use unicode_normalization::UnicodeNormalization;
use unicode_categories::UnicodeCategories;

/// 完备的 handoff 字符串规范化
///
/// 1. NFC 规范化（与 Python 的 unicodedata.normalize('NFC', text) 等效）
/// 2. 剥离所有 Unicode 格式字符 (General Category Cf)
///    包括：零宽字符、方向控制、标签字符、BOM 等
pub fn normalize_handoff(text: &str) -> String {
    text
        .nfc()                                    // NFC 规范化
        .filter(|c| !c.is_other_format())         // 剥离 Cf 类别字符
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_strips_zero_width_chars() {
        let poisoned = "A\u{200b}\u{200d}UTH_TOKEN";
        let cleaned = normalize_handoff(poisoned);
        assert_eq!(cleaned, "AUTH_TOKEN");
    }

    #[test]
    fn test_nfc_normalization() {
        // e + combining acute accent -> é
        let nfd = "e\u{0301}";
        let normalized = normalize_handoff(nfd);
        assert_eq!(normalized, "\u{00e9}"); // é
    }

    #[test]
    fn test_no_visible_impact_on_clean_text() {
        let clean = "Hello, World! 123";
        assert_eq!(normalize_handoff(clean), clean);
    }
}
```

所需依赖 (`Cargo.toml`)：
```toml
[dependencies]
unicode-normalization = "0.1"
unicode-categories = "0.1"
```

### 6.3 使用 regex crate 的替代方案

如果偏好 regex (与 Python 版本更接近)：

```rust
use regex::Regex;
use unicode_normalization::UnicodeNormalization;
use once_cell::sync::Lazy;

static CF_STRIPPER: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"\p{General_Category=Format}").unwrap()
});

pub fn normalize_handoff_regex(text: &str) -> String {
    let nfc: String = text.nfc().collect();
    CF_STRIPPER.replace_all(&nfc, "").to_string()
}
```

Rust `regex` crate 完全支持 `\p{General_Category=Format}` 或简写 `\p{Cf}`。

### 6.4 Mojo/C 实现建议

- **C**: 使用 ICU4C 库的 `unorm2_normalize()` (NFC) + `u_charType()` 过滤 `U_FORMAT_CHAR`。
- **Mojo**: 当前 Mojo 生态中 Unicode 支持有限。建议：
  1. 调用宿主系统 Python 的 `unicodedata` (与 PoC 相同方案)
  2. 嵌入 ICU 绑定
  3. 使用预编译的 Cf 字符移除查找表

---

## 7. 总结

### 7.1 风险评级

| 维度 | 评级 | 依据 |
|------|------|------|
| **可利用性** | 高 | 攻击脚本简单，零外部依赖 |
| **隐蔽性** | 极高 | 在标准渲染中完全不可见 |
| **影响范围** | 中-高 | 影响所有使用 normalize_handoff() 的组件 |
| **检测难度** | 高 | 需要字节级检查，肉眼无法发现 |
| **综合** | **CRITICAL** | 已知漏洞记录在 spec 中但未修复 |

### 7.2 修复优先级

1. **立即**：在所有 normalize_handoff() 调用点添加 Cf 字符剥离
2. **立即**：在 Rust collector 中实现 `normalize_handoff()` 并使用 `\p{Cf}` 正则
3. **短期**：审计所有字符串比较点，确保在 NFC 规范化**之后**进行
4. **中期**：添加 CI 检查——自动扫描提交的 handoff 数据中是否含 Cf 字符

### 7.3 PoC 脚本执行验证

```
脚本路径: env/scenarios/fixtures/multi_agent_attack/handoff-unicode/poc/zero_width_attack.py
执行状态: ✅ 成功（退出码 0）
所有四个演示函数均正确执行
关键断言全部通过
```
