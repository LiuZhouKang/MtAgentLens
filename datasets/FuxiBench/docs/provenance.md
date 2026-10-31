# FuxiBench 来源说明

本文说明场景、标签及历史证据的来源。当前场景标签见 `ground_truth/`；分类和标注方法分别见 [标注规范](codebook.md) 与 [标注协议](annotation.md)。

## 输入与标签

`scenarios/` 包含 93 个受控场景定义，`fixtures/`、`roles/`、`bench/` 和 `prompt_inputs/` 提供相应的输入及支撑材料。场景由人工设计，不是生产流量采样。

场景级 GT 由三个独立的 AI 标注会话与裁决流程形成。`evidence/v5/` 保存标注结果、盲源与裁决记录，`evidence/merge/` 保存合并报告；`ground_truth/` 保存当前标签及其引用。该流程没有人工标签或人工复核。

## 分类与历史迁移

当前分类使用 `fuxibench-2type/1`：A 类对应旧分类的 B 类，B 类对应旧分类的 C 类；旧显式恶意 prompt 的 A 类已从当前分类移除，相关场景按当前审计重新归类。GT 保留 `legacy_label` 和 `legacy_attack_type` 供追溯。当前标签为 58 个 attack（A 47、B 11）及 35 个 benign。

9 个曾排除的场景经后续三路盲标与独立裁决后作为困难良性场景纳入当前检测集；旧 v4 契约保留在相关记录中供追溯。旧 GT 和过程文档存于 `evidence/legacy/`，应按其历史分类解释。2026-10-26 的历史分母见 [历史迁移汇总](../evidence/gt-migration-summary-2026-10-26.json)。

输入修复记录见 `evidence/annotation-source-verification.json`：该记录描述标注输入与包内输入的核对及恢复。角色文件是设计审计背景；实际运行授权应以启动任务为准。

## 历史运行记录

`evidence/pbc/` 和 `evidence/validation/` 是特定历史运行的分析与验证记录，应保留各自的实验条件和分类口径。`traces/` 保存脱敏的历史 eBPF/collector 记录，主机路径已替换为中性占位，fixture 凭据为测试用 decoy；这些数据库不支持当前分析内核回放。详见 [trace 说明](../traces/TRACES-NOTICE.md)。

## 许可和引用

数据采用 CC-BY-4.0，见 [数据许可](../LICENSE)；仓库代码采用 Apache-2.0 OR MIT，见 [Apache-2.0](licenses/LICENSE-APACHE) 和 [MIT](licenses/LICENSE-MIT)。引用信息见 [CITATION.cff](../CITATION.cff)。
