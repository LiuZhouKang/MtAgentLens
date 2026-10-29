# MtAgentLens 修复计划与证据追踪

## 授权、范围与验收

用户已授权实施、团队协作、必要工具与真实验证。工作树已有改动全部保留，不自动提交或发布。任务属于跨模块重量级修复：先锁定契约、聚焦失败测试，再实现、独立审查和端到端验收。工具中没有仓库旧流程提到的 Superpowers/GSD 专用命令，使用独立规划、实现、验证代理履行相同职责。

目标是逐项消除 `INDEPENDENT-AUDIT-2026-10-21-CODEX.md` 的 25 组问题并检查邻近缺陷；不承诺不可证实的“零未知问题”或科研领先。真实来源不得改写成人类参与；缺失标注不得由自动规则伪造。

## 锁定的跨模块契约

1. 原始观测保留 syscall 返回值及 succeeded/failed/pending/unknown；尝试不等同于完成。运行质量按 source 保存统计完整性与损失，未知不当作零。
2. 观察者裁决绑定候选 ID 和证据版本；只关闭实际审核范围。旧 Safe 不能消除新候选。
3. run 内实体使用含 run_id 的复合唯一键和外键。API 明确选择 run，歧义拒绝；数据库导出使用一致快照。
4. 版本化分析 checkpoint 保存重新分析必需输入及配置，缺数据必须显式报告；不可用默认值冒充同构回放。
5. 数据集发布绑定场景、fixture、prompt、GT 与记录 hash。最终运行裁决、执行资格、采集质量分开统计；所有重复实验以 run 唯一键关联。
6. GT 审阅原始记录保留，机器检查只验证完整性与一致性，不生成语义标注或人类签署。

## 实施分工

| 工作包 | 所有者 | 文件边界 | 审计项 | 当前状态 |
|---|---|---|---|---|
| 采集事实/关闭/健康 | kernel agent | bpftools、framework/runners/common、collector_manager 与专属测试 | 07–12 | 已实现；真实 Linux 7.0 无害验证；消费端/sys/kernel与EALREADY邻近漏项已TDD修复 |
| 分析/身份/Observer/回放 | core agent | responsibility 中除 persistence 的分析模块及测试 | 13–18、10/11 消费者 | 已实现；全target1038通过，冻结待重实验 |
| GT/评估协议/盲标 | evaluation agent | scripts/evaluation、compare/tools、评估脚本、annotator 与测试 | 01–06 | 已实现协议与来源校验；Uncertain质量分母与精确claim归因已修，297回归通过 |
| 数据库/服务端 | 主审 | responsibility/persistence、server、必要 Cargo feature | 19–21 | 56 项 persistence、49 项 server 聚焦验证通过；已交叉审查 |
| 前端调查/并发加载 | 主审 | frontend | 22–24 | 539 单测；三浏览器24项通过；lint/type/build 通过 |
| 文档/门禁/独立验收 | 主审及交叉审查 | 文档、verify、集成检查 | 25、全链路 | 门禁扩大至17项；17/17最终通过；科学待办单独列出 |

Cargo 检查经 `/tmp/mtagentlens-cargo.lock` 串行；不对整个仓库并发运行格式化。实现者在自己的边界内修改，交叉审查不直接改他人正在编辑的文件。

## 持久化接口约定

schema v4 增加 `analysis_checkpoints(run_id, checkpoint_kind, version, payload_json)`，主键 `(run_id, checkpoint_kind)`。

Writer 提供 `PersistRecord::AnalysisCheckpoint { run_id: String, kind: String, version: u32, payload: serde_json::Value }`；Reader 提供 `get_analysis_checkpoint(run_id, kind) -> Result<Option<(u32, serde_json::Value)>>`。业务 checkpoint 结构与版本由分析层拥有，schema 不复制业务类型。

## 验证顺序

1. 聚焦红绿回归与真实实现复现；失败必须先证明不是测试环境故障。
2. 各工作包窄门禁；迁移覆盖 v1/v2/v3、重复运行、事务失败和外键。
3. Rust 全 target、C 测试、Python 相关套件、前端 lint/type/test/build 和统一 verify。
4. 无害合成场景的内核加载、退出排空和浏览器调查验收；环境不支持时明确失败原因。
5. 独立复查25项与改动邻接路径，真实证据更新报告。正式效果实验在代码/数据协议冻结后开展。

## 当前增补与限制

- 独立交叉审查继续发现并修复邻近缺口：Rust 路径噪声过滤吞掉 `/sys/kernel` 事实、非阻塞 connect 完成态、Uncertain 被评估当作安全分母、legacy NULL 归档泄漏。
- 网络审计实测发现新披露的 RUSTSEC-2026-0382/0381；锁文件升级 `rustls-webpki=0.103.16`、`webpki-roots=1.0.10`，未新增忽略项。上游依据：[webpki](https://rustsec.org/advisories/RUSTSEC-2026-0382.html)、[roots](https://rustsec.org/advisories/RUSTSEC-2026-0381.html)。重审为零 live vulnerabilities，仍有一个未维护依赖警告。
- GT 93/93 结构与来源验证通过，不代表人工签署完成；74 项人工复核仍待真实执行。正式模型 baseline 未运行，依赖缺失明确失败。
- 14 份不完整实验描述符移至草案并保留字节摘要；活动实验配置157份不等于正式数据集93份。20条历史 payload 检查保留在历史测试目录，不伪造新fixture使其通过。
- 新结果未采集；旧准确率、召回率、消融、性能全部作废。没有进行付费模型调用、正式攻击运行、提交或发布。

最终结果、二进制摘要及未完成的真实科研证据统一见 [当前交接](HANDOFF-2026-10-21-CODEX.md)。工程回归通过不代表全部科研与生产成熟度待办已完成。
