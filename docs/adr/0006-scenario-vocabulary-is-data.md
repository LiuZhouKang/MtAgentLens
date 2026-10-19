---
status: accepted
date: 2026-10-18
---

# 0006 — 场景词表是数据，不是代码

## Context

三个分析器共享同一套「共享工件传播」场景知识，但它以代码形式存在：

- `handoff_injection.rs` 的 `default_injection_patterns()`：6 条模式（shared 后缀、
  probe 后缀、接受标记、scenario_id）。
- `multi_agent_anomaly.rs` 的 `SHARED_ARTIFACT_PATTERNS`：**同样 6 条**，字段名
  相同、值逐字相同，只是 `&'static str` 而非 `String`。
- `security_signoff.rs` 的 `authorized_roles` 默认值：`security-performance-auditor`。

这些值的形状由基准语料（`env/scenarios/fixtures/...`）决定，不是 agent 流量的
普遍属性。结果是：部署方想适配自己的场景必须改 Rust 源码；两处副本可以悄悄
漂移；「产品能检测什么」这个问题只能靠读代码回答。

## Decision

1. 词表移入 `collector/patterns/scenarios.toml`，由
   `src/framework/analyzers/scenario_vocabulary.rs` 单一加载：
   - `include_str!` 嵌入默认值（缺文件 = 编译失败）；
   - `ScenarioVocabulary::load()` 尊重 `MTAGENTLENS_SCENARIO_PATTERNS`；
   - 配置了但读不到/解析失败 → **启动失败**，绝不静默回退默认值；
   - 启动时 `load_and_install()` 安装为进程级只读词表，`effective()` 供三个
     分析器读取，首次安装生效。
2. 删除两处重复表；`handoff_injection::InjectionPattern` 成为共享类型别名，
   `default_injection_patterns()` / `default_shared_artifact_patterns()` 都从
   `effective()` 读取，`security_signoff` 的默认角色同样来自词表。
3. 默认值仍由基准语料塑形——这一点写在数据文件头部，是**显式承认的默认**，
   不是「通用检测能力」的伪装。

## Consequences

- 三个分析器不可能再各持一份表；改词表只改一个数据文件。
- 第三方可以在不改代码的前提下替换词表，并用 `toml` 可读格式审计它。
- 词表格式错误会阻止进程启动；这是有意的：带着运维没要求的检测策略运行比
  启动失败更危险。
- 仍未解决：检测逻辑本身仍以「后缀 + 接受句」为判据，天然偏向基准语料；
  真正的通用化（内容流传播而非句子匹配）不在本 ADR 范围。

## Confirmation

- `scenario_vocabulary.rs` 4 个测试：嵌入表解析且 scenario_id 顺序稳定；
  `every_analyzer_sees_the_same_default_table` 逐字段比较两个分析器的视图；
  `load_from_path` 覆盖替换与「坏文件必须报错」；
  `startup_install_path...` 覆盖首次安装生效、后续安装被忽略。
- `include_str!` 使「数据文件存在且可解析」成为编译期条件；orphan-module 门保证
  新模块确实被编译。
- 预算不变：clippy 257（预算 259），`/home/<user>` 0（预算 0）。
