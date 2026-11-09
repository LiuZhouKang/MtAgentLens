# 离线标注与汇总实施计划

状态：已实现并完成聚焦验证（2026-11-06）。三人分别离线标注、绑定校验与合并、场景裁决、IdentityBench/AttributionBench 映射导出及 FuxiBench GT 新目录发布均已接入现有 CLI/工作台；新增统一入口把场景和运行证据工作台放入同一标签页窗口。现有发布数据未由实现写入或覆盖。

## 完成标准

三人可使用同一冻结任务包分别在本地完成标注；每份导出身份绑定、修订可追溯；汇总器拒绝不匹配或不完整的数据；各实验导出独立通过现有 schema；旧数据与结果不被修改。

## 顺序

1. **通用离线包协议** — 已实现任务包/workspace 摘要协议；FuxiBench 场景工作流使用 `scene-pack` 和单人固定身份 SQLite 工作区。
2. **离线合并器** — `scene-merge` 与 `runtime-merge` 校验三份来源身份、内容绑定和完整提交后生成新汇总文件。
3. **场景 GT 适配** — 场景工作台支持单人离线标注、三方差异/一致性、追加裁决、正式 schema 校验及新目录原子发布。
4. **运行级适配** — 复用 `RecordStore` 与冻结 evidence bundle，支持隔离标注、合并和新建裁决库。
5. **实验专用导出** — IdentityBench 与 AttributionBench 分开映射；要求完整映射、有效人工裁决和对应 schema 字段。
6. **入口与说明** — 更新 annotator 中文手册，覆盖场景/运行工作流、统一入口命令、字段与发布约束。

## 每步验证

- 用小型合成包验证创建、独立标注、导出、跨工作区合并、差异/裁决和导出拒绝路径。
- 使用现有 93 个场景与已集中化的 IdentityBench/AttributionBench 规范标签进行只读映射检查；不得把旧汇总标签伪装成逐人记录。
- 已运行 annotator 全套测试（470 passed、16 subtests）、`scripts/evaluation/validate_gt.py --dataset datasets/FuxiBench`（93/93 通过）、`py_compile` 和 `git diff --check`。
- 检查工作区变更，确认没有触碰既有人工 GT、评测分数或用户已有无关修改。
