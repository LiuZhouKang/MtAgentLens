> **文件组织更新（2026-10-29）**：开销实验统一存放于 [compare/results/overhead/](../../results/overhead/README.md)。当前采用一张综合图与精简分账表；下文旧 F2、14 图／8 表名称仅指历史导出，旧图表已归档。原始数值与冻结证据保留。

# 证据索引（审稿意见 → 证据文档 → 产物 → 门禁测试）

> **历史快照（2026-10-26）**：文中的 84/26/9 是当时口径；2026-10-27 盲标+裁决后 9 例困难良性已计入分母，
> 当前为 **93 = 58 attack + 35 benign、detection_excluded 0**。见 `docs/dataset/FUXIBENCH-2.0-CONTRACT.md`。

> 想直接重算数字？见 [`REPRODUCE.md`](REPRODUCE.md)（每条命令 + 期望输出）。
> 一页看清"每条主张背后有什么"。数字硬规则见
> [`docs/paper/claim-evidence-matrix.md`](../paper/claim-evidence-matrix.md) 与
> [`docs/paper/数字审计-2026-10-25.md`](../paper/数字审计-2026-10-25.md)；
> 无 artifact 数字见[数字修正](../paper/数字修正-2026-10-25.md)。
>
> **canonical 数据集（2026-10-26 起）**：
> `datasets/FuxiBench/`（自包含 **FuxiBench 2.0.0**，`taxonomy_version=fuxibench-2type/1`）。
> GT **93 = 58 attack + 35 benign**；新类型 **A 47 / B 11**（旧 A 移除、旧 B→A、旧 C→B）；
> **9** 个审计 benign 场景 `detection_excluded`；检测参与集 **84 = 58 attack（A47/B11）
> + 26 benign**；**无人工标签/κ/金标**。包身份：`release.json`
> `release_id=ec1ba74da25ffd61fcda4ae427ed33fd0c779171acca7d0c194ab7067de45a2a`、
> 3,532 文件、`checksums.sha256`；文档入口
> [`../datasets/FuxiBench/DATASET-CARD.md`](../../datasets/FuxiBench/DATASET-CARD.md)、
> [`../datasets/FuxiBench/AI-SILVER-RELEASE.md`](../../datasets/FuxiBench/AI-SILVER-RELEASE.md)。
> 旧仓库内数据集树已退役，不再作为当前数据集路径；旧 v4 字节归档只保留在
> `datasets/FuxiBench/evidence/legacy/v4/`。
>
> **当前可选用的系统测量仍是 RC 2026-10-25-fix4**（binary `de3a0894…`、release
> `aa364a45…`、config `6d44789c…`、61 run）。另有一项独立于检测 campaign 的 **2026-10-28 完整开销测量**（36/36 条件，见 §6.6；不是 fix4 的检测分母）。**最终门禁（2026-10-26）**：
> `./scripts/verify.sh` → **18/18 all gates passed**（含 FuxiBench 包 portability/clean-room
> 第 18 门）；compare **1266 passed + 16 subtests**、env **365 passed**、
> scripts **60 + 7 subtests**、claims **54/54**、`validate_gt` **93/58/35/problems=[]**。

| 审稿意见 / 主题 | 证据文档 | 冻结产物（含路径/哈希处） | 门禁内测试 |
|---|---|---|---|
| 意见 1 判定流程 | [`../paper/decision-procedure.md`](../paper/decision-procedure.md)；`collector/src/responsibility/classification/final_decision.rs` | — | `cargo test`（final_decision/classification 套件） |
| 意见 2 前半 身份判据 | [`e5-identity-microbenchmark.md`](e5-identity-microbenchmark.md)（T1–T6 全 F1=1.000） | 旧 e5-identity 工作包已删除；可用 REPRODUCE §3 重算 | `compare/tests/test_e5_identity_microbenchmark.py`（7）；resolver 单测 |
| 意见 2 后半 根因定量 | [`../paper/根因指标定义.md`](../paper/根因指标定义.md) | —（E6/运行级根因待付费 campaign） | `compare/tests/test_root_cause_metrics.py`（12） |
| 意见 3 前半 基线公平性 | [`../paper/意见3-应用层基线-全球调研与落地方案.md`](../paper/意见3-应用层基线-全球调研与落地方案.md) §9 | `local/history/experiments/e8-dryrun/`（dry-run 2 trace/0 label） | `compare/tests/test_app_baseline.py`（13） |
| 意见 3 后半 InjecAgent 协议 | [`experimental-design.md`](experimental-design.md)（协议审计） | `compare/results/external/InjecAgent/results.json`（canonical TPR 0.8605；`injecagent_final.json` 的 0.9934 不得引用） | 记录于设计文档 |
| 具体意见 同级方法 | [`../paper/能力矩阵-同级方法.md`](../paper/能力矩阵-同级方法.md) | — | —（文档级，复现待额度） |
| 意见 4 指标核对 | [`../paper/数字审计-2026-10-25.md`](../paper/数字审计-2026-10-25.md) | `docs/paper/claims.json`（54 条） | `test_paper_claims_artifact.py`（2）+ `test_metrics_reproducible.py` + `verify_claims` 54/54 |
| 意见 5 完整开销 | [Overhead 中文报告](../../results/overhead/README.md)（14 图 + 8 表，供写作）；[`EXPERIMENT-SUITE-7-REVISED §6.6`](EXPERIMENT-SUITE-7-REVISED-2026-10-26.md#66-实测结果2026-10-28-完成)（摘要）；[`observer-cost-2026-10-25.md`](observer-cost-2026-10-25.md)、[`alert-latency-2026-10-25.md`](alert-latency-2026-10-25.md)（历史线索） | 正式 36/36 条件：`compare/results/overhead/raw/full-system/report/{T8.md,T8.csv,F2.pdf|svg|png,raw-per-run.json}`；execution report `complete=true`。C3 并发1 墙钟9.27 s/RSS 376.0 MiB/Observer $0.005232；并发32 墙钟150.91 s/RSS 772.7 MiB/进程源丢弃13,439–16,442而受控操作0/1536缺失；TLS 873.4 ns/成功读写操作；正式费用上界$0.6204。T8.md sha256 `7bd95ce4…`、T8.csv `bbc538d5…`、F2.png `cce37005…` | `test_overhead_paper_assets.py`（2）、`test_overhead_report.py`（18）、`test_overhead_driver.py`、`test_overhead_evidence.py`、`test_run_overhead_experiment.py`、`test_probe_overhead.py`、`test_overhead_diagnostics.py`、`test_overhead_workload.py`；聚焦套件 **109 passed**；`claims-overhead-2026-10-28.json` **16/16**（`verify_claims.py`） |
| 意见 6 敏感性 | [`compare/results/sensitivity/README.md`](../../results/sensitivity/README.md)（110 点阈值 + 六维权重/gate OAT + 1000 次联合 LHS） | `compare/results/sensitivity/{data,metrics,tables,figures}`；baseline 93/93 精确重放；dev/test 43/50 仅回溯披露 | `test_experiment_suite_audit.py` 等 |
| 意见 7 PBC 混杂 | [`e2-pbc-activity-baseline.md`](e2-pbc-activity-baseline.md)；`datasets/FuxiBench/evidence/pbc/` | 61 单智能体 run（18 benign+43 attack）；attack PBC=0 **28/43**、benign 13/18；rank AUROC **0.5277777777777778**；PBC≥1 FPR 27.8%；PBC≥10 TPR 0.50（2/4）/FPR 0。**旧 Type C 有效 run=0；非检测分母** | `compare/tests/test_e2_pbc_activity.py`（4）；`test_pbc_distribution.py`（17） |
| 意见 7 E7 | [`e7-difficult-benign-plan.md`](e7-difficult-benign-plan.md)：仅规格（已取消新场景） | `env/scenarios/_drafts/e7-difficult-benign/spec.json` | `env/tests/test_e7_difficult_benign_spec.py`（2） |
| 消融粒度 | [`e4-ablation.md`](e4-ablation.md)（逐维 LOO 0 翻转；L0.5 未做） | `2026-10-25-e3-fix4/summary.json` 的 `ablation_points` | `replay/sensitivity.rs` 单测 |
| 数据集 2.0（场景 GT） | [`../datasets/FuxiBench/DATASET-CARD.md`](../../datasets/FuxiBench/DATASET-CARD.md)；[`../datasets/FuxiBench/AI-ANNOTATION-PROTOCOL.md`](../../datasets/FuxiBench/AI-ANNOTATION-PROTOCOL.md) | `datasets/FuxiBench/ground_truth/`（93 条：58 attack + 35 benign；A47/B11；9 excluded） | `compare/tests/test_fuxibench_scope.py`（13）；包 clean-room 门 18/18 |
| GT 证据/翻转/合并 | `datasets/FuxiBench/evidence/v5/`、`evidence/merge/` | 3 标注 279 会话；17 v4-v5 差异（15 confirmed / 2 retained / 0 unknown）；`detection_excluded` 9 | `gt_rebuild/gt_merge/validate_gt`；package validator |
| 数据集 release（2.0） | `datasets/FuxiBench/release.json`；`checksums.sha256` | `release_id=72f04576…`；3,532 文件；`paper_metrics_available=false` | `test_fuxibench_scope.py`；`validate_fuxibench_package.py --clean-room` |
| 真实场景验证（post-fix） | `datasets/FuxiBench/evidence/validation/`；[`../paper/REVIEW-RESPONSE-EVIDENCE-2026-10-26.md`](../paper/REVIEW-RESPONSE-EVIDENCE-2026-10-26.md) | binary `4372a153…`、release `71e10e45…`；3 run capture 0 丢帧/claims 0；recon 缺口披露；不入论文分母 | 包内证据 + 诊断记录 |
| 历史（仅追溯） | `datasets/FuxiBench/evidence/legacy/v4/` | 旧 v4 GT/reviewer 283 文件、旧 AI-silver v1 只作历史；不是当前 label/指标 | — |
| 单页 studio（产品/标注） | [`studio-acceptance-2026-10-25.md`](studio-acceptance-2026-10-25.md) | 权威 store（无写入） | `tools/annotator/tests/test_studio_ux.py` |
| 冻结 RC 路径 | [`final-rc-checklist.md`](final-rc-checklist.md) §7 | — | `compare/tests/test_freeze_rc.py` |
| Replay 等价性边界 | [`replay-parity-check.md`](replay-parity-check.md) | 当前构建拒绝重放 fix2（指纹门） | `replay/checkpoint.rs` 单测（2） |
| 事件级对齐（不可用） | ADR-0016 | — | —（明确不声称） |

## 不能声称的（一页红线）

- 不引用**没有 artifact** 的数字（0.836 / 0.960 / 0.895 / 0.681 / "86 场景 route、0.399
  复算"等）；替换口径见 [`../paper/数字修正-2026-10-25.md`](../paper/数字修正-2026-10-25.md)。
- InjecAgent headline 只用 canonical `compare/results/external/InjecAgent/results.json`
  （TPR 0.8605）；`injecagent_final.json` 的 0.9934 不得引用。
- 开销数字必须注明构建与日期；218.2624 ns 是 101.3125+57.5763+59.3736 三个程序均值之和，
  不是探针级或 SSL 操作级成本；36.9 MB / 6.24% CPU / 1425 MiB 是旧构建，不得写成 fix4 当前值。
  正式 36 条件已完成并支持 strict 导出；但不得把该单主机/单内核/单一负载结果写成跨环境结论，不得把受控操作零缺失写成全局零丢失，也不得把低告警覆盖率写成漏报率。
- 不写 TPR/FPR 点估计而不给弃权率与覆盖率；不把 fix2 写成当前构建。
- 不写"人工 κ/人工复核/人工金标/人机等效"；GT 2.0 的 κ（0.8479/0.8441）只是同模型过程诊断。
- 不写"六维权重/门控已做独立开发集验证"；不写"eBPF 与 PBC 贡献已拆分"。
- 不引用未复现方法的性能数字；不写"外泄检出率"。
- 不把 `detection_excluded` 的 9 个场景重新计入检测分母；直接遍历 GT 的指标脚本必须过滤。
- 不报告 Type C/PBC 分布（旧 fix4 Type C 有效 run=0）；PBC 产物早于 2.0 分类迁移、非检测分母。
- 不把旧 v4/v5 或旧 AI-silver v1 当当前 label/指标；不把旧 v4 provenance 差异当人工待办。

> **证据损失披露（2026-10-26 清理）**：旧 `local/workpackages/2026-10-25-codex-revision/sensitivity-summary.json` 在清理时被删除；引用它的 8 条相对权重 claims 已从 `claims.json` 移除，现为 54 条（54/54 hold）。E3 25 点网格与 6 维 LOO 证据保留在 `datasets/FuxiBench/evidence/pbc/fix4/e3/summary.json`；54 点相对权重结果当前不可复算，论文中不得再引用。
