# 实验文档

正式结果、图表和原始数据集中在 [compare/results](../../results/README.md)。本目录维护实验方法与复现说明。

| 任务 | 入口 |
|---|---|
| 查看当前实验和数据范围 | [结果状态](CURRENT-EVIDENCE.md) |
| 按审稿问题查证据 | [证据索引](EVIDENCE-INDEX.md) |
| 离线核对和重建结果 | [复现手册](REPRODUCE.md) |
| 理解实验单位与冻结流程 | [方法](experimental-design.md)、[验收清单](final-rc-checklist.md) |
| 隔离 Agent 配置与采集目标 | [测量隔离](measurement-isolation.md) |
| 使用运行级标注 release | [数据契约](annotation-dataset-release.md)、[外部校验](annotation-release-consumer.md) |
| 查冻结开销条件 | [实验协议 §6](EXPERIMENT-SUITE-7-REVISED-2026-10-26.md#6-overheadbench5) |
| 查身份规格检查 | [E5 合成微基准](e5-identity-microbenchmark.md) |

## 历史来源

旧 RC、FPR、试点和机制审计报告已退出现行文档，可按原路径从 Git 历史查询。历史 fix4 的54条机器主张仍绑定原有 `local/workpackages/` 现场，见 [claims.json](../paper/claims.json)；当前93场景结果使用自己的冻结产物。

两份旧基线 JSON 原字节保留于[测试 fixture](../../../collector/tests/fixtures/baselines/README.md)，只承担完整性与契约校验。新测量写入 `local/`，不自动覆盖 fixture 或重建历史文档目录。

冻结 GT 曾引用 `docs/evaluation/fresh-2026-10-19/action-trace-audit.json`，该来源原字节保存在[审计证据](../../../docs/dataset/evidence/action-trace-audit-2026-10-19.json)。GT 中的旧路径记录原审阅现场，保持不变。

[场景设计原文](scenarios/README.md)继续保留，因为冻结 GT 和裁决证据引用其内容；当前标签以 FuxiBench 发布包为准。
