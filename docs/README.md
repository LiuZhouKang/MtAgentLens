# 文档

按你要完成的任务选择入口。使用指南、技术参考和维护历史分别导航；历史报告不代表当前版本状态。

## 使用

| 任务 | 文档 |
|---|---|
| 安装、构建和首次采集 | [项目 README](../README.md#快速开始) |
| 配置 collector、查看命令与回放 | [collector](../collector/README.md) |
| 检查探针与底层采集 | [bpftools](../bpftools/README.zh-CN.md) |
| 运行 benchmark 场景 | [执行环境](../env/README.md) |
| 冻结输入和对比评估 | [compare](../compare/README.md) |
| 理解场景标注与数据来源 | [FuxiBench](../datasets/FuxiBench/README.md) |
| 报告漏洞、了解数据处理边界 | [安全策略](../SECURITY.md) |

## 开发参考

| 主题 | 文档 |
|---|---|
| 开发环境、测试与贡献规则 | [贡献指南](../CONTRIBUTING.md) |
| 系统模块和数据流 | [架构](architecture.md) |
| 已实现能力与限制 | [能力清单](capabilities.json) |
| 架构决策及变更理由 | [ADR](adr/README.md) |
| 前端开发 | [frontend](../frontend/README.md) |
| 验证、发布与维护脚本 | [脚本目录](../scripts/README.md) |
| 威胁模型 | [安全设计](security/threat-model.md) |
| 运行级归因标注 | [annotator](../tools/annotator/README.md) |

## 维护与研究记录

- [**当前实验与论文证据入口**](evaluation/CURRENT-EVIDENCE.md)：T4/T5/T6/T7/T7-S/T8/PBC 的状态、权威路径和图/表索引。
- [**结果交付目录**](../compare/results/README.md)：每个实验的 README、MANIFEST、原始数据、图/表与复现脚本。
- [历史 RC（2026-10-25 fix2）](evaluation/rc-2026-10-25-fix2/README.md)：binary `db3191bb…` 的冻结身份、协议与结果；历史 RC 只作对照，不作为当前构建结论。
- [AI/CODEX/DSH 历史交接归档](archive/ai-2026-10-30/README.md)：已移出顶层，不是当前入口。
- [标注数据集 release](evaluation/annotation-dataset-release.md)与[运行级 codebook `pilot/1`](../tools/annotator/PILOT-CODEBOOK.md)：`mtagentlens.annotation-dataset/1` 消费契约与规则冻结。
- [实验方法](evaluation/experimental-design.md)与[测量隔离](evaluation/measurement-isolation.md)：后续实验的设计参考，具体适用性以冻结版本为准。
- [意见5完整开销结果](evaluation/EXPERIMENT-SUITE-7-REVISED-2026-10-26.md#66-实测结果2026-10-28-完成)：36/36 正式条件已完成，T8/F2 在 `compare/results/overhead/raw/full-system/report/`；TLS 微基准与其他探针短诊断已实测，旧 60 条件方案不再执行。
- [Overhead 中文报告](../compare/results/overhead/README.md)：14 张独立科研图、8 张表、完整设计/结果/写作建议，供论文 §6.4/表12 与相关图改写。
- [复现手册](evaluation/REPRODUCE.md)：每个离线数字对应的命令与期望输出。
- [证据索引](evaluation/EVIDENCE-INDEX.md)：审稿意见 → 证据文档 → 产物 → 门禁测试一页速查。
- [论文工作区](paper/README.md)：研究与修稿材料。关键文件：
  - [claim-evidence-matrix.md](paper/claim-evidence-matrix.md)：主张-证据矩阵（论文主张与证据的对应关系）；
  - [claim-evidence-matrix-archive.md](paper/claim-evidence-matrix-archive.md)：历史测量/审计存档（不得引用）；
  - [claims.json](paper/claims.json) + `scripts/evaluation/verify_claims.py`：机器可校验的数字层（门禁内置）；
  - [数字审计-2026-10-25.md](paper/数字审计-2026-10-25.md)：初稿中不可引用数字与替换口径；
  - [修改说明-草稿.md](paper/修改说明-草稿.md) §0.4：审稿意见逐条状态；
  - [能力矩阵-同级方法.md](paper/能力矩阵-同级方法.md)：同级方法输入/单元/输出/可复现性对比；
  - [根因指标定义.md](paper/根因指标定义.md)：E6 指标定义与落地顺序。
- [历史会话工作日志](evaluation/session-log-2026-10-25.md)：保留当时逐轮证据，不代表当前版本状态。
- [历史设计与评审](superpowers/archive/)：已被替代的方案和交接，不是当前任务入口。
- [历史测量](evaluation/)：含旧基线、试点与审计；不作为当前效果或性能结论。
- [比赛材料](决赛文档/)与[演示](demo/)：提交和展示记录。

新增文档优先补入现有主题，维护规则见[贡献指南](../CONTRIBUTING.md#文档与仓库维护)。导航按读者任务组织，参考 [Diátaxis](https://diataxis.fr/)；不要求为了凑目录结构增加文件。
