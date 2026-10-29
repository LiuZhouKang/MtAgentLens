# 审稿意见回复证据包（advisor 审阅用，2026-10-26）

> **2026-10-28 意见5更新**：开销实验**已完成正式实测**（36/36 条件，15 配对区组，372 实例）；T8/F2 严格完整导出在 `compare/results/overhead/raw/full-system/report/`，结果摘要见[实验总方案 §6.6](../evaluation/EXPERIMENT-SUITE-7-REVISED-2026-10-26.md#66-实测结果2026-10-28-完成)。历史218 ns的单次探针解释已撤回；ONNX已移除。本文其他审计/门禁/估算是其记录时点的历史证据。

> **历史快照（2026-10-26）**：文中的 84/26/9 是当时口径；2026-10-27 盲标+裁决后 9 例困难良性已计入分母，
> 当前为 **93 = 58 attack + 35 benign、detection_excluded 0**。见 `docs/dataset/FUXIBENCH-2.0-CONTRACT.md`。

> 性质：内部 advisor 材料，**不是对外回复信定稿，也不是论文结果**。只回答"每条意见现在
> 有什么证据、能写什么、还缺什么"。
> 数字规则：本文每个数字都标注 artifact 或命令；**没有 artifact 的数字一律见
> [数字修正-2026-10-25.md](数字修正-2026-10-25.md)，不得引用**。所有路径相对仓库根。
> 全域红线：**没有人工标签、人工 κ、人工复核或人机等效结论**。AI 标注是 `deepseek-flash`
> 的同模型 fresh context，只表示过程可重复，**不是统计独立**，不能代替人工金标。
> 唯一可引用的系统测量仍是 **RC 2026-10-25-fix4**：binary `de3a0894…`、release
> `aa364a45…`、config `6d44789c…`、61 条 run（18 benign + 43 attack，全单智能体）。
> **数据集侧已冻结（FuxiBench 2.0）**：自包含包 `datasets/FuxiBench/`（版本 2.0.0，
> release_id 与文件数以 `datasets/FuxiBench/release.json` 为准、`checksums.sha256`）；GT **93 = 58 attack + 35 benign**，
> 两类型 **A47/B11**（旧 Type A 移除、旧 B→A、旧 C→B）；9 个审计 benign 场景
> `detection_excluded`，检测参与集 **84 = 58 attack（A47/B11）+ 26 benign**；**无人工标签**。
> 详见 `datasets/FuxiBench/AI-SILVER-RELEASE.md`；旧仓库内数据集树已退役，只保留包内历史字节。
> **W9 修复后验证**（binary `4372a153…`、release `71e10e45…`，3 条 run）只作链路/
> 效果验证，**不进入论文分母**；修复前 run3（binary `dda3f4b5…`）的 credential void
> 与 atlas Uncertain/55% 丢帧只作诊断证据。


> **本轮独立审计后的准入修正（2026-10-26）**：此前“harness就绪”不能解释为全链路已验收。
> 当前入口已切到canonical两类型包，多Agent检测队列29＝22 attack（A11/B11）＋7 benign。
> 包恢复哈希核验的原始标注输入，禁止全局IP改写；prompt重建并校验源字节。
> Rust候选按id＋version复审，未评完的当前候选阻止Safe。真实process内核负载证据在
> `local/readiness-2026-10-26/system-load/report.json`，仅支持该受控路径的采集结论。
> **E6仍有协议/接线缺口，不只是付费等待**：运行标注root_actor强制null，不能产生逻辑根因参考。
> 历史E9-system只覆盖process，不覆盖TLS/Observer与语义告警延迟；ONNX现已移除，不是待补测组件。E11没有同级动态系统部署runner。
> 正式准入与命令统一见[最终实验清单](../evaluation/final-rc-checklist.md)，下文旧Type C/23场景描述均为历史范围。

> **2026-10-28 DSH 续作更新（覆盖本文的代码状态，不覆盖历史证据）**：
> - G0 事实以 10-27 为准：93=58+35、0 excluded；下文 84/26/9 仅历史。
> - 意见 8（消融）：L0–L3 已能从冻结 raw facts 独立重建（L0 = raw OS action-occurrence index；L0.5 修复 delegation 泄漏；L3 不再把 Observer Partial 写成 Available），并新增 `replay --ablation-output` → `scripts/evaluation/ablation_experiment.py` 的 93 场景 AUROC/AUPRC + paired scenario bootstrap + DeLong/Holm producer；**尚未跑真实 C0，不报任何 ΔAUROC 数字**。L0 固定阈值 TPR/FPR 尚未确定（本版只报 AUROC/AUPRC）。
> - 意见 2a/2b（身份/归因）：detector-blind native reference 已修复 authority_source 编码与 source-DB 绑定两个 P0，能产出 conditional declared identity/root，并以 fresh session + 独立裁决约束 3+1 评审；但 **归因 target cohort 仍取 run 内全部 action（单 case packet 2.89 MB > review batch 700 KB 上限，93 run 约 6.07M 行/3 reviewer），与 suite “先 scope 后 localize”冲突**，需先做 phase-1 scope 或开跑前固定的 cohort，真实（付费）参考尚未启动。unknown 仍逐字段保留，不写 accuracy/human κ/gold。
> - 意见 5（开销）：capture-loss 适配器修复 symlink 与 health 信任两个缺陷；分母来自受控 workload 已知操作数，并从 receipts/trace/schema-5 DB 重算，篡改即拒绝；**真实 C1 校准需 eBPF 权限，capture_loss 仍不得填数**。
> - 代码门禁：`./scripts/verify.sh` 18/18；聚焦 Python 1754 passed/1 skipped；未改数据/标签/分母，未 sudo/付费/commit。

## 0. 汇总

### 0.1 10 条意见状态

| # | 意见 | 状态 | 今晚可写的结论（证据齐全程度） | 主要缺口 |
|---|---|---|---|---|
| 1 | 判定流程/四分数字语义 | ✅ 可答复 | 判定状态机与 Observer 四类输出映射已有文档+代码+测试；0.399/86-route 复算无 artifact，必须撤下 | 论文 §3.5/§6.1.3/算法 3 文字；L1 route 表（离线待做） |
| 2a | 身份判据/身份准确率 | 🟡 部分 | E5 合成微基准六拓扑 F1=1.000 可复现；真实语料身份准确率未测 | E6（23 Type C）需多智能体 run（付费）；运行身份 AI 参考待最终 campaign 标注 |
| 2b | 根因/入口/链定量（多智能体/旧 Type C 语义） | ⛔ 待数据 | 指标实现+12 测试就绪；设计级因果提案与 FuxiBench 2.0 设计证据可披露；检测分母（84）已定 | 运行级根因参考（3 标注+裁决）与 @1/@3/MRR/链 P-R 全缺；需最终付费 campaign |
| 3a | 基线公平性 | 🟡 部分 | fix4 活动量基线已重算（AUROC 0.5278 等）；能力矩阵在；E8 harness 就绪 | E8 同输入 text+tool 基线**未跑**（付费 judge，约 0.1–1 USD） |
| 3b | InjecAgent 协议审计 | ✅ 可答复 | 样本构造、文本降级、"是否执行"可如实披露；canonical 数字二选一已定（0.8605） | 论文 §5.2/表 11 文字；OS 真执行从未做（只能披露，不重跑） |
| 4 | 指标/F1 审计 | ✅ 可答复 | claims 54/54；F1=0.966；218 ns 占比改区间 0.0044%–0.0218% | 论文表 11/12 与摘要文字更新（写作任务，非新实验） |
| 5 | 完整系统开销 | ✅ 可答复（有限定） | 36/36 正式条件完成；T8/F2 严格完整导出。C3 并发 1 墙钟 9.27 s、RSS 376 MiB、Observer $0.005232/条件；并发 32 墙钟 150.91 s、RSS 772.7 MiB、进程源丢弃 13,439–16,442 而受控操作 0/1536 缺失；TLS 873.4 ns/成功读写操作；正式费用上界 $0.6204 | 单主机/单内核/单一负载/3 次描述性重复；不报 CI/p99、不做跨环境或长期泄漏推断；Word 合入与 claims 登记待做 |
| 6 | 测试独立性/敏感性 | 🟡 部分 | E3/E4 已重算（25 点网格、6 维 LOO，裁决 0 翻转；旧 54 点相对权重 artifact 已丢失，不再引用） | bootstrap（脚本需适配，<10 min）；**不存在独立开发/测试集**，只能披露 |
| 7 | PBC 混杂/Type C 归因 | 🟡 部分 | W4 canonical 已落盘：fix4 61 单智能体 run 的 PBC 分布/分层 TPR/FPR/门控对照 | fix4 **Type C=0**；Type C 对比需 23 个 Type C 的付费 run（RC-fix5，5–7 h）；E7 已取消 |
| 8 | 消融粒度/同级方法 | 🟡 部分 | 逐维 LOO 与门控点已有（裁决 0 翻转）；能力矩阵 + 同级方法代码可得性审计（可复用证据）；E11 复现未做 | L0.5（eBPF vs +PBC）层级拆分未做；同级方法（GUARDIAN/AgentSight）未复现 |

统计：**✅ 可答复 4 条（1、3b、4、5，其中 5 为有限定）；🟡 部分可答复 5 条（2a、3a、6、7、8）；
⛔ 待付费数据 1 条（2b，但 harness 已就绪、dry-run 通过；见 §0.3）**。**按目标定义，10 条意见均已具备 reusable evidence、offline analysis 或 ready-to-run harness 之一；2b 只差最终窗口的付费执行与运行级参考。** 另有 1 个横切项（运行身份 GT）影响 2a/2b/7，需在最终
campaign 后用 3 标注流水线补；Type C 定量是 2b 与 7 的共同付费前置。

### 0.2 剩余付费实验/数据成本清单（不含已完成的离线准备）

| 实验 | 用途（意见） | 规模 | 机器时间 | 费用/预算 | 前置条件 |
|---|---|---|---|---|---|
| **RC-fix5**：30 个现成多智能体场景 | 2b、7 的 Type C 定量 | 23 attack（含全部 Type C）+ 7 benign，release3 | **5–7 h**（REUSE-AUDIT §7.4） | 付费 flash；在本轮 30–50M tokens 总预算内，未单独报价 | release3 + 当前构建 + W3 AI 参考流水线 |
| **E8 应用层基线** | 3a | 61–279 次 judge 调用（fix4 transcript，不需新场景） | 分钟级 | **约 0.1–1 USD**（REUSE-AUDIT §3 3a） | harness 已就绪（13 个测试函数 + dry-run 2 trace/0 label） |
| **E9 并发/完整开销（已完成）** | 5 | 36/36 条件、15 配对区组、372 实例；TLS 微基准与 3 个 C1 诊断窗口另计 | 正式批次约 4 h（含校准/恢复另计） | 正式 36 条件 $0.6204；全部 cohort $1.6465（观测/已知 usage 上界 $1.0265 + 未计量请求准备金 $0.62，非账单） | 已固定构建、成功操作凭据、告警关联与真实 usage；T8/F2 在 `compare/results/overhead/raw/full-system/report/` |
| **E11 同级方法复现** | 8（同级方法） | 优先 GUARDIAN 或 AgentSight+规则，1 个最接近方法 | 需适配+训练，**未估算**（REUSE-AUDIT §3 意见 8 D） | 未估算 | 代码可得性核查已完成（能力矩阵） |
| （数据，非实验）运行层 3 标注 | 2a/2b/7 的运行参考 | 447 历史 run ×3（PLAN §10 记录“已授权，上限 35M/4h”；runtime PIPELINE-DESIGN 23:59 记录 Lead 决定**推迟**到最终 campaign；当前**无** 447×3 canonical artifact） | 约 **2–3.5 h** | 约 **24–32M flash tokens**（runtime PIPELINE §8；PLAN 上限 35M/4h） | 最终 campaign 运行集 |

### 0.3 最终窗口 harness（已 dry-run 验证，未花钱执行）

`scripts/evaluation/final_campaign_harness.py` 把剩余付费实验收成单一入口；默认
**dry-run**，只有显式 `--execute` + `MTL_ALLOW_PAID=1` 才会调用付费模型：

| 命令 | 实验/意见 | 已验证内容 | 执行前置 |
|---|---|---|---|
| `python scripts/evaluation/final_campaign_harness.py status` | 全矩阵 | release 存在、30 个现成多智能体场景 = 23 attack（12 Type C）+ 7 benign、93 GT | 无 |
| `... type-c` | 2b/7（Type C 定量） | 自动选出 30 场景并校验；打印 pilot_observer 命令；不会创建新场景 | 最终窗口 + release + sudo/模型 preflight |
| `... e8 --results local/workpackages/2026-10-25-rc-fix4/attack/results.jsonl` | 3a（应用层基线） | fix4 ledger 存在；`--client unavailable` 只产 0 label，不冒充结果 | judge key + 约 0.1–1 USD |
| `... e9` | 5（并发/吞吐/内存） | perf_throughput 二进制定位；1/4/8/16/32 并发、峰值 RSS、聚合吞吐输出 schema | 最终窗口（无需模型/root） |
| `... runtime-annotations --run-root <campaign runs>` | 2a/2b/7 运行参考 | prepare→review --reviewers 3→finalize→verify 四阶段命令；只在 campaign run 集上跑 | 最终 campaign 完成后 |

dry-run 实测（2026-10-26）：`status` = 30 / 23 / 7 / Type C 12 / problems=[]；
`type-c`、`e9`、`e8` 均通过；`runtime-annotations` 在 campaign run 集不存在时按
预期拒绝。`scripts/tests/test_final_campaign_harness.py` 4 个测试确保 dry-run 可用、
缺 `MTL_ALLOW_PAID=1` 时拒绝付费执行。

## 1. 今晚数据集/工具新增（回复的上游支撑）

| 项目 | 已完成 | 证据/数字 | 未完成 |
|---|---|---|---|
| FuxiBench 2.0（场景 GT） | **已完成并冻结** | 93 GT = **58 attack + 35 benign**；两类型 **A47/B11**；279 接受标注会话（93×3，distinct、fresh text-only）；91 共识 + 2 无多数裁决（`delegation-chain`、`identity-replay`）；label pairwise .9283、type .9068；Fleiss κ .8479/.8441（同模型过程诊断）；`taxonomy_version=fuxibench-2type/1` 93/93 | 无（已冻结）；论文 text 需按 2.0 两类型计数改写 |
| 2.0 合并/翻转/排除 | **已完成并冻结** | `datasets/FuxiBench/evidence/merge/MERGE-REPORT.json`：17 场景 v4-v5 差异（label 10/type 17）、字段决策 v5=24/v4=3/unknown=0、15 confirmed / 2 retained / 0 unknown；`detection_excluded=9`（旧 v4 契约保留在 `legacy_v4_detection_contract`）；`FrozenDatasetAdapter.load_cases` 已过滤 excluded（adapter 26 测试） | 9 个 excluded 只作 provenance；直接遍历 GT 的指标脚本仍需自查过滤 |
| 数据 provenance / 可追溯性 | **2.0 包 release 已冻结** | `datasets/FuxiBench/release.json`：release_id 与文件数以 `release.json` 为准、`checksums.sha256`；GT 93/58/35/A47/B11/9 excluded；旧 v4 字节归档在 `datasets/FuxiBench/evidence/legacy/v4/`（283 文件）；旧 AI-silver v1 仅历史 | 论文正文引用前仍需按数字修正/claims 流程；旧 metadata 计数不等于当前值 |
| 运行层 3 标注 + sanitizer | sanitizer 离线处理 447 个冻结 packet；smoke 2 run × 3 标注 + 1 独立裁决 | sanitizer：87/447 packet 受影响（19.5%），删除 156/38,093 事件（0.41%），residual scan 0；smoke：7/7 有效会话不同、tool_events=0、`verify valid=true`、errors=[]；产物 `local/workpackages/2026-10-26-final-dataset/runtime/` | **447×3 全量未执行**：PLAN §10 记录已授权（上限 35M/4h），但 runtime PIPELINE-DESIGN（23:59）记录 Lead 决定推迟到最终 campaign；约 93 会话、24–32M tokens、2–3.5 h；旧 `ai-silver/1` 仍为 legacy-only |
| PBC 61-run 分布（canonical） | fix4 离线分布 + 分层指标 + 门控对照已落盘 | `datasets/FuxiBench/evidence/pbc/`（`pbc-distribution.json`、`STRATIFIED-METRICS.json`）：benign n=18（PBC=0 13、mean 0.72、max 3）；attack n=43（PBC=0 **28**，mean 1.77，max 32；18 Safe + 10 review + 0 Unsafe）；rank AUROC **0.5277777777777778**；PBC≥1 FPR 27.8%；PBC≥10 TPR 0.50（2/4）/FPR 0；可行动攻击 PBC=1/1/18/32 | 旧 Type C 有效 run=0，不作 Type C/PBC 分布声明；该产物使用旧 fix4 类型、早于 2.0 迁移，**不是**检测分母 |
| W9 修复后验证（post-fix） | 3 条 run（binary `4372a153…`、release `71e10e45…`） | 3 条全部 capture healthy、process `ringbuf_full_dropped=0`、verdict Safe/Info、claims 0；benign-api-doc 理想；atlas 执行了 recon 钩子但**只读侦察无 claim 规则**（能力缺口，已披露）；persistence-cron-backdoor marker 空=void；**不入论文分母** | 修复前 run3 的 credential void/atlas 55% 丢帧只作诊断证据；该验证非效果结论 |
| 文档/数字口径 | 修正完成 | claims **54/54**；最终门禁 `./scripts/verify.sh` **18/18 all gates passed**（含 FuxiBench 包 portability/clean-room）；compare **1266+16 subtests**、env **365**、scripts **60+7**、claims **54/54**、`validate_gt` **93/58/35/problems=[]**；无 artifact 数字见[数字修正](数字修正-2026-10-25.md) | 论文正文/回复信文字 |

## 2. 逐条意见

### 意见 1：判定流程/四分数字语义

**状态：✅ 可答复（语义层面）**。

**已具备证据**
- 判定状态机、两阶段聚合、Observer 四类输出映射：`docs/paper/decision-procedure.md`；
  代码 `collector/src/responsibility/classification/final_decision.rs`。
- 复现命令：`cd collector && cargo test --lib final_decision`（12 个相关测试）；
  `./scripts/verify.sh` 含 final_decision/classification 套件；最终门禁为
  **18/18 all gates passed**（2026-10-26，含 FuxiBench 包 clean-room 第 18 门）。
- 可写结论：0.15/0.70 是**路由边界**，不是"攻击判定线"；灰色区由 Observer 证据决定
  最终标签；Uncertain/Failed 不得升 Unsafe、也不得当 Safe。
- **0.399 与"86 场景 route 分布"没有任何 artifact**（fix4 只有 61 run，0.399 无 run 映射）：
  见[数字修正](数字修正-2026-10-25.md) M4，不得写"已完成复算"。

**今晚新增**：FuxiBench 2.0/运行层流水线不改变判定语义。W9 修复后 3 条验证全部 Safe/capture 0 丢帧；
修复前 run3 的 "Uncertain + capture degraded"（atlas）只作质量门诊断证据，不是当前效果。

**仍缺**：论文 §3.5、算法 3、§6.1.3 的文字替换；L1 route 分布/0.399 逐值表的离线脚本
（需先定位 0.399 对应 run）。

**可对审稿人陈述**
> 我们澄清了 raw/calibrated/gated/final 四个分数的关系，给出判定状态机与 Observer 四类
> 输出的转换规则；0.15 是"非 Safe 下界"而不是攻击判定线。原文 0.399 处的表述已按新口径
> 改写；0.399 的逐值复算属于旧稿数字，修正稿不再引用。

**不可陈述**：不得说"0.399 已逐值复算/86 场景 route 分布已完成"；不得再说"分数高于 0.15
即攻击"。

**风险/披露**：0.399 案例无法映射到当前 61 run，若审稿人追问需承认无法在 fix4 复现。

---

### 意见 2a：身份解析判据与准确率

**状态：🟡 部分（合成证据完整，真实语料未测）**。

**已具备证据**
- [E5 身份微基准](../evaluation/e5-identity-microbenchmark.md)：180 条轨迹/720 事件，
  T1–T6 pairwise F1 **全部 1.000**、wrong-merge 0、unattributed 0%。
- artifact：旧 `2026-10-25-e5-identity/summary.json` 已在清理中删除；可用 REPRODUCE §3 的微基准命令重新生成（不依赖旧 artifact）。
  sha256 `3deb585b0626f12f71b76495eda7a8c11e8f7c8379077cedf024824962733f57`；
  `traces.jsonl` sha256 `78ed44fd…`。
- 复现命令（[REPRODUCE §2](../evaluation/REPRODUCE.md)）：
  `cd collector && cargo run -q --example identity_microbenchmark > /tmp/e5.jsonl && cd ..`
  然后 `env/.venv/bin/python scripts/evaluation/e5_identity_microbenchmark.py --traces /tmp/e5.jsonl --out /tmp/e5-summary.json --details`。
- 运行层口径：事件逻辑身份显式 uniform unknown；物理动作仅做同 host PID/boot clock
  条件下的一 tick 容差关联，不能称 logical identity 或根因。

**今晚新增**：运行层 3 标注流水线 + sanitizer 已就绪（未来可在最终 campaign 运行上产出
运行身份参考）；本轮**没有**产出 447×3 canonical 身份标注。

**仍缺**：E6——23 个 Type C 真实轨迹上的身份归属定量（pairwise P/R/F1、B³、false-merge/
split、unattributed）；需要多智能体 run（RC-fix5 付费）与独立参考；人工锚点不存在。

**可对审稿人陈述**
> 在 6 类合成拓扑（180 条轨迹）上，身份判据实现达到规格：pairwise F1=1.000，错误合并 0，
> 未归属 0%；同进程多会话通过会话级身份分裂解决，但动作级归属保持未指定（失败关闭）。
> 真实多智能体语料上的身份准确率尚未测量，我们不做替代性声明。

**不可陈述**：不得写"身份解析准确率 1.000 / 已达实用精度"；不得把合成拓扑 F1 写成真实
语料准确率；不得写人工标注/人工 κ。

**风险/披露**：合成语料不含真实噪声；共享进程且无会话证据的动作仍为 unknown。

---

### 意见 2b：根因/入口/链定量（23 Type C）

**状态：⛔ 待付费/新数据**。

**已具备证据**
- 指标实现：`compare/tools/root_cause_metrics.py` + `compare/tests/test_root_cause_metrics.py`
  （**12 个测试函数**，pytest 收集 13 项）；定义见[根因指标定义](根因指标定义.md)。
- 设计级因果提案（只作 `scenario_design` 披露）：`datasets/FuxiBench/ground_truth/_causal/`
  93 条（85 proposal / 8 unknown），**禁止当运行根因真值**。
- **0.681 无 artifact**：论文/评估材料中只有[审稿意见.md](审稿意见.md) 这一处出现，无 claim、
  无测试、无对应产物（[数字修正](数字修正-2026-10-25.md) M3），必须撤下。

**今晚新增**：FuxiBench 2.0 已冻结（93 GT = 58 attack + 35 benign、A47/B11；3 标注 + 无多数裁决 + 强制翻转裁决），为场景侧
字段提供最终设计证据；9 个 `detection_excluded` 已定（见数据卡）。运行层 3 标注流水线
+ sanitizer 就绪。**W3（23 Type C 运行级根因参考）尚未产出**（依赖最终 campaign 的运行集）。
注意：2.0 GT 是场景设计标签，不是运行根因；检测排除不改变运行根因真值。

**仍缺**：运行级根因/入口/链参考（3 标注 + 第 4 裁决）；`RootAgent@1/@3`、`MRR`、
`EntryPoint Accuracy`、`Chain Link P/R`、`Chain Depth MAE` 的实际数值；确定性根因排序
代码（当前 `traceback.rs` 只给单个 `root_cause_node_id`）。这些需要 RC-fix5 付费 run。

**可对审稿人陈述**
> 根因指标已实现并测试；设计级证据只作为场景披露，不冒充运行根因。运行级定量需要新的
> 多智能体 run 与独立标注，当前不能给出准确率数字。

**不可陈述**：0.681；任何 RootAgent/MRR/入口/链准确率；把设计级 causal proposal 写成
运行真值。

**风险/披露**：fix4 无多智能体有效 run；2.0 GT 只解决场景设计层，不解决运行根因；
指标可算不等于有真值可算。

---

### 意见 3a：基线公平性（信息量不对等）

**状态：🟡 部分（活动量基线已重算；E8 同输入基线未跑）**。

**已具备证据**
- fix4 活动量基线（`local/workpackages/2026-10-25-e3-fix4/e2-summary.json`）：
  - rank AUROC **0.5278**；良性 PBC mean 0.722（13/18 PBC=0），攻击 mean 1.767（28/43 PBC=0）。
  - PBC≥1：TPR 1.0 / FPR 0.278；PBC≥10：TPR 0.50（2/4 Unsafe 攻击 run）/ FPR 0；PBC≥20：0.25 / 0。
  - 复现命令（[REPRODUCE §3](../evaluation/REPRODUCE.md)）：
    `env/.venv/bin/python scripts/evaluation/e2_pbc_activity.py --attack local/workpackages/2026-10-25-e3-fix4/attack-facts.jsonl --benign local/workpackages/2026-10-25-e3-fix4/benign-facts.jsonl --out /tmp/e2/summary.json`
    （实测与 canonical 逐字段相等）。
- 能力矩阵：[能力矩阵-同级方法](能力矩阵-同级方法.md)；应用层基线方案：
  [意见3-应用层基线](意见3-应用层基线-全球调研与落地方案.md) §9。
- E8 harness：`compare/tests/test_app_baseline.py`（**13 个测试函数**）已就绪；
  dry-run 产物 `local/e8-dryrun/summary.json` 为 `records=2, judged=0, labels={}`——
  **只证明流程可跑，不是结果**。

**今晚新增**：输入侧已冻结（FuxiBench 2.0 包；当前release身份见release.json），9 个检测排除场景已定；
为后续同输入比较提供冻结输入与分母。E8 仍需付费 judge。

**仍缺**：E8 实际结果（61–279 次 judge，约 USD 0.1–1）；同模型 text+tool 基线的 TPR/FPR；
论文 §5.2/表 6 文字。

**可对审稿人陈述**
> 我们主动报告了信息极少的活动量基线：仅用 PBC 体积做检测时 rank AUROC=0.5278，PBC≥1
> 时 FPR=27.8%，说明"活动多=攻击"的解释不成立。同输入应用层基线（text+工具日志）的
> 对照实验尚待运行，我们不会把当前结果表述为"方法更优"。

**不可陈述**：E8 已跑/已复现；0.836、0.960、logistic LOO；"1.000 vs 0.087–0.522"不带限定。

**风险/披露**：fix4 全单智能体；活动量基线弱不等于已排除任务复杂度混杂。

---

### 意见 3b：InjecAgent 外部基准协议

**状态：✅ 可答复（协议与披露）**。

**已具备证据**
- 协议事实：2,108 = 1,054×2；149 = 99 `benign_control` + 50 `benign_short_50`；
  运行方式为"规则+Observer"文本模式，**未在 OS 上执行**（`compare/Dataset/external/InjecAgent/README.md`）。
- canonical 结果：`compare/results/external/InjecAgent/results.json`
  TPR **0.8605**（TP 1814/FN 294，行 15）、benign FPR **0.0134**（2/149，行 67）、
  Observer calls 777（行 7）。
- 冲突口径：`compare/results/paper/injecagent_final.json` 的 **0.9934**（行 14，
  `fpr_short=null`）为非 canonical，不得引用（[数字修正](数字修正-2026-10-25.md) §2.1）。
- 可复算算术：Meta Prompt Guard 合并集 F1 = `2*2108/(2*2108+149)` = **0.9659**。
- 复现命令：`env/.venv/bin/python scripts/evaluation/verify_claims.py --manifest docs/paper/claims.json`
  （54/54）；协议审计与样本构造见 `docs/evaluation/experimental-design.md` §85 附近。

**今晚新增**：数据集重建（2.0 包）不改变 InjecAgent 协议事实；sanitizer/2.0 GT 与其无关。

**仍缺**：论文 §5.2 协议段、表 11 重算与限制文字；**OS 真执行从未做**（若审稿人坚持，
只能按 REUSE-AUDIT 的披露口径说明成本，不承诺重跑 2,108 次真实执行）。

**可对审稿人陈述**
> 该实验未在操作系统上执行 InjecAgent 样本，而是文本降级运行；2,108 与 149 的构造、
> 运行器和指标口径已如实披露。修正稿不再用它支撑"泛化优越"，只作为弱 OS 信号下的
> 退化边界。文中的 MtAgentLens 数字统一采用 canonical 的 TPR 0.8605。

**不可陈述**：0.9934；"已真实执行/OS 级复现"；Meta F1=1.000；"文本模式等价于执行"。

**风险/披露**：canonical artifact 无攻击侧逐样本预测，无法离线复算 TPR；不得挑选另一
artifact 的更高值。

---

### 意见 4：指标核对（F1/百分比）

**状态：✅ 可答复（算术与 claims 齐全，论文文字待改）**。

**已具备证据**
- claims：`docs/paper/claims.json`（**54 条**），`verify_claims.py` 实测 **54/54**。
- Meta Prompt Guard 合并集 F1 = **0.9659**（不是 1.000）；表 11 的 TPR/FPR/F1 口径已更正。
- 218 ns 占比：**0.0218%（1 ms 端）/ 0.0044%（5 ms 端）**，应写区间而不是"<0.005%"。
  依据 `compare/results/overhead/archives/legacy-218ns-performance.tar.gz::perf/perf_report.json`（218.2624 ns，2026-06-17 旧构建）。
- 复现命令：`env/.venv/bin/python scripts/evaluation/verify_claims.py --manifest docs/paper/claims.json`。
- 数字审计：[数字审计-2026-10-25.md](数字审计-2026-10-25.md)。

**今晚新增**：无（数据集重建不改变算术）；文案同步已完成 EVIDENCE-INDEX/REPRODUCE。

**仍缺**：论文表 11/12、摘要、§5.3 文字替换；若引用新数字先登记 claims。

**可对审稿人陈述**
> 表11的F1需按攻击+良性合并集核对（Meta算术值约0.966）；原稿218 ns的百分比算术已纠正。
> 218 ns来自旧报告三个程序均值相加，不能解释为单次探针/SSL操作成本；新稿不再与握手时长比较。
> 实际合入论文前不写“已修改完成”。

**不可陈述**：F1=1.000；"<0.005%"；把 218 ns 写成 fix4 全系统开销。

**风险/披露**：218 ns 与 36.9 MB 非当前构建；正文必须写明测量日期/构建。

---

### 意见 5：完整系统开销

> **2026-10-29 复核更新**：优先采用 [精简报告与审稿回复](../../results/overhead/README.md) 顶部的新结果和限定。固定本地负载下 Collector 稳态均值 0.661%（3×60 s），非完整系统／任意 Agent ≤5%。原高并发存在 evidence 关联覆盖不足；原恢复规则是否预先登记尚未核实，不能写成已证实的预注册重试。

**状态：✅ 可答复（有限定；2026-10-28 完成正式实测）**。

**可保留的历史线索**
- fix4 审计曾记录 Observer 调用与run级时间差；调用次数不等于费用，run级“最近证据到最终alert”不等于可验证事件级告警延迟。旧记录和原始artifact只供追溯，不与新矩阵合表。
- `docs/evaluation/overhead-baseline.json` 包含已移除的ONNX；`compare/results/overhead/archives/legacy-218ns-performance.tar.gz::perf/perf_report.json` 的218.2624是三个程序均值之和，不是单次探针或一次SSL读写成本；配套旧循环未建立连接，不能代表成功数据采集路径。

**已具备的新实测证据**
- 写作版报告：[Overhead 中文报告](../../results/overhead/README.md)（14 图 + 8 表）。
- 正式批次：`compare/results/overhead/raw/full-system/`，36/36 条件完成、15 配对区组、372 实例、每格 3 次；execution report `complete=true`，`overhead_report.py` 严格完整模式无 warning。正式 plan SHA-256 `1f8a02e5…`；T8.md `7bd95ce4…`、T8.csv `bbc538d5…`、F2.png `cce37005…`。
- 完整成本（并发 1）：C0/C1/C2/C3 条件墙钟 2.32/4.50/3.90/9.27 s；collector CPU 0/0.75/0.80/0.85 s；峰值 RSS 0/373.6/376.1/376.0 MiB；DB+WAL 峰值 —/—/2.98/3.59 MiB；C3 Observer 2.0 次/条件、12,706 输入+1,176 输出 token、$0.005232 上界。
- 并发扩展（C3 32）：条件墙钟 150.91 s [40.21–234.74]（C0 3.99 s），collector CPU 93.76 s、峰值 RSS 772.7 MiB [461.7–1036.5]，DB+WAL 169.45 MiB [84.92–232.27]，trace 47.86 MiB；责任图 823.7 节点/36,254.7 边，图更新 82,572 次/3.51 s；Observer 8.3 次/条件、144,922 输入+20,261 输出 token、$0.067886 上界。
- 捕获完整性：所有 C1–C3 条件受控操作 raw missing 与 persisted loss 均 0.0%（32 并发 0/1536、0/1536）；32 并发进程采集源 ring-buffer 丢弃 13,439–16,442 次、TLS 源 0；分层报告，不合并成单一 loss 率。
- 告警：32 并发可关联最终告警 23/1536（1.5% [0.4–2.3]），p50 144.10 s、p95 145.17 s；并发 1 为 1/16（6.25%），p50=p95=7.63 s。该良性 fixture 不支持攻击漏报率估计；复核显示 96 个成功凭据动作仅 23 个具有关联 evidence，存在关联覆盖缺口，不能仅以其他无关操作解释。145.17 s 为三次运行内 p95 的均值，并非 pooled p95；23 个样本共享 3 个 alert。
- 探针：TLS 3 次零丢弃，每成功 SSL 读写操作 873.4 ns [386.7–1133.5]；逐程序 186.7/564.6/808.7 ns。其他程序短诊断：syscall enter 66.3–70.9 ns、complete 85.6–90.7 ns、exec 3.74–4.15 μs、exit 1.47–1.75 μs（含过滤返回，不是每有效事件完整成本）。
- 费用：正式 36 条件 Agent+Observer 峰时上界合计 $0.6204（含归档失败 attempt $0.0309）；本实验全部 cohort 合计 $1.6465（观测/已知 usage 上界 $1.0265 + 未计量请求准备金 $0.62），预算 $5。
- 执行修正：`c32-r3-C3` 首次 attempt 在 300 s 关闭上限处被截断（workload 512/512 完成，phase-2 已运行 233 s）；按固定恢复规则归档并用 600 s 上限重跑同一冻结条件，恢复运行通过全部校验，矩阵与测量口径未改。失败 attempt 的费用与部分数据保留在 `formal-attempts/`。

**可对审稿人陈述**
> 我们接受局部探针时间与 RSS 不足以代表完整成本的意见。旧 218 ns 的单次操作解释已撤回。
> 在同一冻结构建和固定负载上，我们完成了 36 个正式条件（无监测、仅采集、本地责任分析+DB、
> 完整系统；并发 1/4/8/16/32，每格 3 次）和独立探针补充。完整系统在并发 1 的条件墙钟为
> 9.27 s，峰值 RSS 376 MiB，Observer 费用上界 $0.005232/条件；并发 32 时条件墙钟为
> 150.91 s [40.21–234.74]、峰值 RSS 772.7 MiB，phase-2 批量关联是主要成本。所有受控操作
> 在 raw 与持久化证据中均无缺失，但 32 并发进程采集源仍报告 13,439–16,442 次 ring-buffer
> 丢弃，二者分层报告。TLS 成功读写探针成本为 873.4 ns/操作。所有数字、失败尝试和费用上界
> 均可追溯到 T8/F2 与 execution report。

**不可陈述 / 限定**：不写“完整开销已测全”或跨主机/内核结论；该实验是单主机、单内核、单一固定负载、3 次描述性重复，不报 CI/p99，不推断长期无泄漏。不把负的任务耗时配对差值解释为加速，也不把受控操作零缺失写成全局零丢失；低告警覆盖率不是漏报率。218 ns 不是单次探针/SSL 操作成本；不以握手时长证明采集占比；Mock 不等于真实 Observer；缺 usage 不等于零费用；历史 ONNX/RSS 不是当前成本。

---

### 意见 6：测试独立性/参数敏感性

**状态：🟢 敏感性已正式完成；独立性只能如实披露（不再宣称独立开发集）。**

**已具备证据（`compare/results/sensitivity/`）**
- **S1 阈值**：gate=10 时 safe 0.08–0.30（111 值）× unsafe 0.50–0.86（73 值）= **8103 点**；部署点 `(0.15,0.70)` PP F1 **0.852**，全网格 **0.769–0.877**，最优 **+0.025**，**6980/8103 点差 ≤0.03（86.1%）**。
- **S2 六维权重/gate**：六维权重逐维 ×0.8/×1.2 → F1 **0.836–0.852**；gate center 5/7.5/10/15/20 → F1 **0.830–0.852**；部署点 93/93 score/verdict 精确重放。
- **S3 联合 LHS**：两阈值 + gate + 六维权重共 9 维、1000 次采样；F1 中位数 **0.852**，95% 区间 **[0.784, 0.877]**，**91.1% ≥0.80**。
- **开发/测试披露**：93 场景/85 family，回溯 split dev 43/41 family、test 50/44 family；描述性 F1 dev 0.880 / test 0.828。该 split **不是独立 held-out**，参数在同一基准开发阶段确定；不声称严格隔离。
- **方法边界**：固定已记录 Observer，重算 score/route；不是 live Observer 重跑；hard-rule runs 保留 production verdict。
- 图/表：`compare/results/sensitivity/figures/fig8_sensitivity.{png,pdf,svg}`、`tables/table8_{sensitivity,oat,lhs}.tex`。

**仍缺/不做**：独立开发集（客观不存在）；19 个 difficult benign 的新集（另立限制）；六维权重的人工先验来源无法追溯。旧 25 点/54 点/6 维 LOO 的数字已被本轮 8103+OAT+LHS 取代，不再引用旧翻转数。

**可对审稿人陈述**
> 我们没有独立开发集：参数是在同一 93 场景基准的开发阶段确定的；85-family 的 43/50 划分是回溯性分组，不能作为独立性证明。我们如实将其写为 limitation，并补充覆盖阈值、六维权重与门控的敏感性：8103 点阈值网格的 PP F1 为 0.769–0.877，部署点 0.852，距最优仅 0.025，6980/8103 点差 ≤0.03；逐维权重 ±20% 的 F1 为 0.836–0.852，门控中心 5–20 为 0.830–0.852；1000 次联合采样的 F1 中位数为 0.852（95% [0.784,0.877]，91.1% ≥0.80）。这些结果说明结论不依赖单点参数，但不等于独立验证。

**不可陈述**："已做独立开发/测试集验证"；"家族间无泄漏已证明"；"敏感性等价于独立性"；"六维权重由独立开发集学习"。

**风险/披露**：family split 是回溯性分组；固定 Observer；hard-rule 固定；gate 其他中心为公式外推（baseline 93/93 精确）。

---

### 意见 7：PBC 混杂/Type C 归因

**状态：🟡 单智能体部分已 canonical 落盘；Type C 部分 ⛔ 待付费/新数据**。

**已具备证据**（canonical：`datasets/FuxiBench/evidence/pbc/`）
- 数据：fix4 **61 条有效单智能体 run**（18 benign + 43 attack），冻结 checkpoint 的
  PBC 边数 + 重放最终裁决，零模型调用；生成时逐字段复现 `e3-fix4/e2-summary.json`
  （`reproduction.exact_match=true`）。
- 分布：benign n=18（PBC=0 **13/18**，mean 0.72，p50 0，p90 3，max 3）；
  attack n=43（PBC=0 **28/43**，mean 1.77，p50 0，p90 3，p95 5，max 32）。
- 纯活动量基线（alarm iff PBC≥阈值）：rank AUROC **0.5278**；
  PBC≥1：TPR 1.00（4/4 可行动）/ FPR 0.278（5/18，Wilson [12.5%, 50.9%]）；
  PBC≥10：TPR 0.50（2/4）/ FPR 0；PBC≥20：TPR 0.25（1/4）/ FPR 0。
  TPR 分母是 **4 条可行动（Unsafe）攻击 run**；全体攻击报警率另列（15/43、2/43、1/43）。
- 可行动攻击的 PBC 为 1/1/18/32；28/43 攻击 run PBC=0 仍被判 Safe/review；证据门控
  （PBC≥10）只在 2/43 攻击与 0/18 良性上激活 → 可行动裁决不能由活动量单独解释。
- fix4 攻击臂 **Type C = 0**（A=3/B=40）；93 场景中 63 单智能体 / 30 多智能体，
  多智能体有效 run = 0；E7 的 20 个新困难良性场景**已取消**。
- 复现命令（在仓库根，写入 /tmp，不动 canonical）：
  ```bash
  env/.venv/bin/python scripts/evaluation/pbc_distribution.py \
    --attack-facts local/workpackages/2026-10-25-e3-fix4/attack-facts.jsonl \
    --benign-facts local/workpackages/2026-10-25-e3-fix4/benign-facts.jsonl \
    --attack-results local/workpackages/2026-10-25-rc-fix4/attack/results.jsonl \
    --benign-results local/workpackages/2026-10-25-rc-fix4/benign/results.jsonl \
    --scenarios-dir local/workpackages/2026-10-25-sink-release2/FuxiBench/scenarios \
    --reference-summary local/workpackages/2026-10-25-e3-fix4/e2-summary.json \
    --void-attempts benign=local/workpackages/2026-10-25-rc-fix4/benign/void-attempts.jsonl \
    --void-attempts attack=local/workpackages/2026-10-25-rc-fix4/attack/void-attempts.jsonl \
    --out-dir /tmp/pbc-w4
  # 期望：reproduction_exact=true, valid_runs=61, type_c_runs=0, AUROC=0.5278
  ```
  另见 [REPRODUCE §3](../evaluation/REPRODUCE.md) 的 e2 命令（与 canonical 逐字段相等）。
- 允许/禁止措辞的 canonical 文本：`pbc/STRATIFIED-METRICS.md` §5。

**今晚新增**：W4 canonical 产物已落盘（分布/分位/直方图/attack_type 分解/排除项/
provenance/门控对照），零付费；数据集 2.0 不增加 run，也不改变 PBC 数字；PBC 产物早于
2.0 分类迁移的 9 项 detection_excluded，**不是** 2.0 的检测分母。canonical 采用 **28/43**
（历史 W4 记录曾写 26/43，canonical 为 28/43）。
**仍缺**：Type C vs A/B 的 PBC 分布与归因（需 23 个 Type C 的付费 run，RC-fix5 5–7 h）；
多智能体良性对照；论文 §6.1.2/§7.1 文字。

**可对审稿人陈述**
> 我们在 RC fix4 的 61 条有效单智能体 run（18 benign + 43 attack）上报告全量 PBC 分布与
> 分层 TPR/FPR：良性 13/18 PBC=0、攻击 28/43 PBC=0，纯活动量 AUROC 0.528（近随机）；
> PBC≥1 时良性误报 27.8%（5/18），PBC≥10 时只覆盖 50%（2/4）可行动攻击；4 条可行动攻击
> 的 PBC 为 1/1/18/32，28/43 攻击 PBC=0 仍被判 Safe/review。因此"检测只是区分活动量"
> 不成立。但 fix4 全是单智能体、Type C=0，**不能**回答 Type C 的 PBC/归因问题。

**不可陈述**：0.836/0.960/18/19/0.895；"已排除活动量混杂"；"Type C 定量/归因已完成"；
任何 E7 数字。

**风险/披露**：65%（28/43）攻击 run PBC=0，活动量基线弱不等于混杂已排除；Type C 未测
是硬缺口；PBC 61 run 早于 2.0 排除，不能当检测分母。`evidence/pbc/README.md`/`STRATIFIED-METRICS.md`
历史 W4 记录曾写 26/43，冻结口径为 **28/43**（per_run/`e2-summary`）。

---

### 意见 8：消融粒度/同级方法比较

**状态：🟡 部分（层级重建与指标 producer 已就绪；真实 93 消融待 C0 与授权）**。

**已具备证据**
- E4 逐维 LOO：D1–D6 route flips 1/1/1/1/0/0、verdict 0；门控中心 5/10/20
  （`local/workpackages/2026-10-25-e3-fix4/summary.json`）。
- 能力矩阵：`docs/paper/能力矩阵-同级方法.md`（8 个方法、可观测输入/分析单元/输出/可复现性
  分级；未复现方法禁止引用性能数字）。
- 复现命令见[REPRODUCE §4](../evaluation/REPRODUCE.md)；claims 已登记。
- 同级方法**未实跑**：L0.5（仅 eBPF vs +PBC）层级拆分未做；GUARDIAN/AgentSight 未复现。

**今晚新增**：FuxiBench 2.0 已冻结、9 个检测排除场景已定，为后续消融提供固定分母；家族划分
未重建（85/dev43/test50，label/type 变化不影响 family）；本轮不产生新消融或同级实跑数据。

**仍缺**：真实 C0 93 run 上的 L0–L3 导出与 ΔAUROC/Holm（代码与合成门禁已就绪，
见 `docs/archive/ai-2026-10-30/research/CODEX-FINAL-ROUND-2026-10-27.md` §5.2）；L0 固定阈值 TPR/FPR 尚未确定；
20-run live Observer 子样本预算；1 个最接近方法的复现或客观不可复现理由（E11，需适配+训练）；
论文 §6.2/表 9/10 文字。

**可对审稿人陈述**
> 逐维 LOO 与门控中心扰动已完成（裁决 0 翻转）；L0→L1 中 eBPF 与 PBC 的层级拆分尚未完成，
> 同级方法目前只有能力矩阵（不可直接同输入比较），我们不引用未复现方法的性能数字。

**不可陈述**："eBPF/PBC 贡献已拆分"；引用 GUARDIAN/AgentSight/BlindGuard 的性能数字；
"同级方法比较已完成"。

**风险/披露**：能力矩阵是文档级核查，不是复现实验；L0.5 需要重构分析层。

## 3. 统一红线（advisor 材料/回复信都适用）

- 不写"人工标注/人工复核/人工 κ/人机等效"；本轮只有 AI 标注，`human_annotations=0`。
- 不写无 artifact 数字：0.836、0.960、0.895、0.681、"86 场景 route / 0.399 复算"；
  清单与替换口径见[数字修正](数字修正-2026-10-25.md)。
- 不引用 InjecAgent 的非 canonical 0.9934；只用 canonical TPR 0.8605 / benign FPR 0.0134。
- 不把 218 ns/36.9 MB/6.24% CPU/1425 MiB 写成 fix4 当前值。
- 不把 fix4 的单智能体结论写成多智能体/Type C 结论；Type C=0 必须披露。
- 不把设计级因果提案写成运行根因真值；unknown 不当负例，不给"零误报"的统计充分性声明。
- 不声称独立开发/测试集、家族间无泄漏或审稿人已接受 AI 银标。

## 4. 证据路径速查

| 主题 | 主 artifact | 命令/说明 |
|---|---|---|
| 判定语义 | `docs/paper/decision-procedure.md`；`collector/src/responsibility/classification/final_decision.rs` | `cd collector && cargo test --lib final_decision` |
| 当前测量 | `docs/evaluation/rc-2026-10-25-fix4/README.md`；`local/workpackages/2026-10-25-rc-fix4/` | binary `de3a0894…`、release `aa364a45…` |
| claims | `docs/paper/claims.json`（54） | `env/.venv/bin/python scripts/evaluation/verify_claims.py --manifest docs/paper/claims.json` → 54/54 |
| E5 身份 | 旧 e5-identity 工作包已删除；见 REPRODUCE §3 可重算命令 | 见[REPRODUCE §2](../evaluation/REPRODUCE.md) |
| E2/PBC | `local/workpackages/2026-10-25-e3-fix4/e2-summary.json` | 见[REPRODUCE §3](../evaluation/REPRODUCE.md) |
| PBC 分布（canonical） | `datasets/FuxiBench/evidence/pbc/`（`pbc-distribution.json`、`STRATIFIED-METRICS.json`） | 61 run/28 of 43/AUROC 0.5278；旧 fix4 类型、非 2.0 检测分母；旧 Type C 有效 run=0 |
| E3/E4 | `datasets/FuxiBench/evidence/pbc/fix4/e3/summary.json`（E3 25 点 + LOO；旧 codex-revision 相对权重 artifact 已删除） | 见[REPRODUCE §4](../evaluation/REPRODUCE.md) |
| InjecAgent | `compare/results/external/InjecAgent/results.json` | canonical TPR 0.8605/FPR 0.0134 |
| 开销旧数字 | `docs/evaluation/overhead-baseline.json`；`compare/results/overhead/archives/legacy-218ns-performance.tar.gz::perf/perf_report.json` | 2026-10-18 / 2026-06-17，非 fix4 |
| FuxiBench 2.0 GT | `datasets/FuxiBench/ground_truth/`；`datasets/FuxiBench/evidence/v5/`（gt-v5、flip、FLIP-REPORT） | 93 GT=58 attack+35 benign；A47/B11；9 excluded；3 标注 279 sessions/17 diff/15 confirmed/2 retained |
| 运行层 | `local/workpackages/2026-10-26-final-dataset/runtime/PIPELINE-DESIGN.md`；`runtime/smoke/` | sanitizer 已跑；447×3 推迟 |
| 2.0 合并/排除 | `datasets/FuxiBench/evidence/merge/MERGE-REPORT.json`；GT 的 `legacy_v4_detection_contract` | merge/schema 校验通过；`detection_excluded=9`；`compare/tools/datasets.py` 过滤 excluded（adapter 26 测试） |
| 数据集 release（2.0） | `datasets/FuxiBench/release.json`；`checksums.sha256` | 当前身份以 `release.json` 为准；包 portability/clean-room 门（18/18） |
| W9 验证（post-fix） | `local/workpackages/2026-10-26-final-dataset/VALIDATION-2026-10-25.md`；`local/workpackages/2026-10-26-final-dataset/validation/post-fix/*` | binary `4372a153…`；3 run capture 0 丢帧/claims 0，不入分母；修复前 run3 只作诊断 |
| 无 artifact 数字 | [数字修正-2026-10-25.md](数字修正-2026-10-25.md) | file:line + 替换口径 |

以上是今晚 advisor 审阅可用的完整证据地图；**2b 与 7 的定量结论在付费 campaign 完成前
不能写，1 的 0.399/route 表述必须删除或标待重算**。
