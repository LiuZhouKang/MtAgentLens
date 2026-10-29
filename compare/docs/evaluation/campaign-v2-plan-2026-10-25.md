# Campaign v2 实验与标注执行方案（2026-10-25 草案）

> 目的：用**一次干净、可复现的 campaign** 补齐《软件学报》返修真正缺的四类证据，并让
> 全部论文数字来自同一 binary + 同一 release。本文件是待讨论的执行草案，**未运行任何命令**。
> 与 [`../audit/2026-10-25/README.md`](../audit/2026-10-25/README.md) 配套。

## 0. 为什么必须重跑一次，而不是继续补 fix4

fix4 的 61 条有效 run 全部来自 63 个**单智能体**场景（19 benign + 44 attack）。release2/release3
里还有 **30 个多智能体场景（23 attack + 7 benign）**，它们才是论文标题里的“多智能体”与审稿人
点名的“23 个 Type C”，也是 R1.2/R2.4 的对象。当前没有任何一条多智能体 run 进入指标。
继续在 fix4 上做离线分析，回答不了审稿人的核心质疑。

## 1. 实验矩阵

| 实验 | 回答 | 审稿意见 | 输入 | 新模型调用 | 产物 |
|---|---|---|---|---|---|
| **C2** 全量 campaign | A/B/C 三类攻击、执行证据、多智能体归因 | R1.2/R2.2/R2.4 | release3 93 case | 93 run × agent + Observer | results.jsonl、DB、witness v2、metrics |
| **E7** 困难良性扩展 | PBC/委派匹配的良性 FPR | R1.4/R2.1/R2.2 | 新增 20 场景 | 20 run | FPR + PBC 分层表 |
| **E8** 同输入基线 | 文本 vs 文本+工具轨迹 | R1.3/R2.3 | C2 的 run 转录 | A1/A2/A3 × run 的 judge 调用 | 三臂 TPR/FPR/覆盖/成本 |
| **OH** 完整开销 | 图维护/Observer/并发/丢失/时延/RSS/token | R1.5 | 同一 binary | 并发梯度下的 agent 调用 | 分项开销表 |
| **AN** 人工标注 | 人工 κ、根因/入口/链参考 | R1.2/R2.4 | C2 的证据包 | 0 | κ、RootAgent@1/@3/MRR、Entry/Chain |
| **A5** 层级消融 | eBPF vs +PBC | R2.5 | C2 的原始事件/DB | 0 | L0.5/L1 对照 |

可选：**R-Judge 外部基准**（E8 的 judge 协议）与 **tmux-live 端到端**已在 C2 内覆盖。

## 2. 冻结身份（开跑前确定，之后不改）

| 项 | 取值 |
|---|---|
| 输入 release | `local/workpackages/2026-10-25-sink-release3/FuxiBench`，release_id `71e10e45…`（post-rename blinded；fixtures/scenarios 无 MtAgentLens 指纹；含 `sink_host_transform`） |
| 采集二进制 | 修完 §3 前置缺陷后重新构建的 release binary；记录 SHA-256 |
| 配置 | `observer on`、release-bound `pilot_observer`、`--mode auto`（多智能体自动 tmux-live） |
| Agent 模型 | `env/config/claude.env` → DeepSeek `deepseek-v4-flash`（当前配置） |
| Observer 模型 | `env/config/observer.toml` → `deepseek-flash` |
| 主机前提 | `scripts/wire/prepare-sink-address.sh` 把 `93.184.216.34/32` 挂到 `lo`；`/etc/hosts` 有 `93.184.216.34 updates.example.com`；输出根目录短路径（eBPF 126 字符上限） |
| 运行模式 | 串行（parallel=1），每批结束后检查，不并发抢 host 噪声 |

## 3. 开跑前的代码前置修复（否则新数据同样不可用）

| 优先级 | 修复 | 验收 |
|---|---|---|
| P1-1 | subagent boundary 的 agent id 改为 Step 4b 之后统一重算 | 新测试断言每条 boundary 的 parent/child agent id ∈ `agents[]`；E5 六拓扑无回归 |
| P1-2 | verify.sh gate 11/12/13 断言 `passed >= 基线`；gate 5 断言 `allrs > 100`；gate 6 用 build.rs 源清单 | `--exact no_such` 必须 FAIL；门禁 17/17 |
| P2-02 | envelope 路径前缀：非目录前缀要求精确相等或 `prefix/`；`/dev/` 白名单收窄 | 新增 sibling-prefix 反例测试；现有 envelope 测试全过 |
| P2-04 | `UPDATE runs` 检查 `rows_affected`，失败计数进 capture_quality | 新测试：模拟 RunStart 缺失→degraded |
| P0-1 | 工作区快照进版本库（需用户授权）或 sha256+异地备份 | `git ls-tree HEAD` 含 `final_decision.rs`/`build.rs`/`claims.json`；或备份校验通过 |

> 排序理由：P1-1 直接污染多智能体归因（C2 的核心产出）；P2-02 直接影响“授权 vs 越权”判定；
> P1-2 决定后续所有数字是否有门禁背书。代码修复走 TDD，改完先跑 `./scripts/verify.sh` 与
> `cargo test`，再构建 campaign binary。

## 4. 分阶段执行

### 4.1 链路验证（2 条 run，约 30–45 分钟）

```bash
export PATH="$HOME/.cargo/bin:$PATH"
sudo ./scripts/wire/prepare-sink-address.sh

PYTHONPATH=env:env/src env/.venv/bin/python compare/scripts/run/pilot_observer.py \
  --release local/workpackages/2026-10-25-sink-release3/FuxiBench \
  --only benign-api-doc --only identity-confusion \
  --mode auto --observer on --timeout 1200 \
  --out local/f10/validate
```

验收（缺一不可，否则先修链路再跑全量）：

- `capture_quality_status=healthy`，SSL/process loss=0；
- `state=completed` 且有 canonical `final_decision`；
- 多智能体 run 有 `runner-ledger.json`（每 agent 一个终态）；
- 多智能体 run 的 `collector.responsibility.db` < 100 MiB，Phase 2 < 150 s；
- `marker.log` / `sink-receipts` 有本次 nonce 证据；
- 无遗留 `sink.py` 进程。

### 4.2 全量 campaign（93 case，约 5–7 小时）

分批（每批 5–10 个场景，以便失败只作废一批），示例：

```bash
PYTHONPATH=env:env/src env/.venv/bin/python compare/scripts/run/pilot_observer.py \
  --release local/workpackages/2026-10-25-sink-release3/FuxiBench \
  --only <sid1> --only <sid2> ... \
  --mode auto --observer on --timeout 1200 \
  --out local/f10/c2-b1
```

批次建议：

| 批 | 场景 | 预计 |
|---|---|---|
| b1–b4 | 26 benign（含 7 多智能体） | 1.5–2 h |
| a1–a5 | 44 单智能体 attack | 1 h |
| c1–c3 | 23 多智能体 attack | 2.5–4 h |
| retry | 所有 capture degraded / no-verdict 的首次失败（只重试一次） | 0.5–1 h |

每批后检查：`results.jsonl` 行数、`capture_quality_status`、`final_decision`、DB hash、
`runner_ledger`、`pgrep -af sink.py`。**失败 campaign 整份作废，不做事后挑选。**

### 4.3 冻结与汇总

```bash
RC=local/workpackages/2026-10-25-rc-fix5
python3 scripts/evaluation/select_valid_runs.py ...   # 每场景首个合法 run
python3 scripts/evaluation/enrich_campaign_results.py --results ... --out ...
python3 scripts/evaluation/scenario_execution_witness.py --results ... --out ...
python3 scripts/evaluation/final_verdict_metrics.py --benign ... --attack ... \
  --release local/workpackages/2026-10-25-sink-release3/FuxiBench --out $RC/metrics.json
python3 scripts/evaluation/freeze_rc.py ...
```

验收：每场景恰好一条合法 run；void-attempts 全保留；execution witness 只认本 run nonce；
`capture != healthy` 不算分母；多智能体按 arm 单独给 coverage/abstention。

### 4.4 E8 同输入基线（约 1 小时）

- 对 C2 的 93 条 run 导出 `mtagentlens.app_trace.v1`（S1 转录优先，缺失回退 S2 TLS）；
- 三臂：A1 仅任务、A2 完整轨迹、A3 去 tool_result；
- judge 两次调用、temperature=0、严格解析；**judge 模型必须 pin 并写入报告**；
- 报告：每臂 TPR/FPR/coverage、与 MtAgentLens 的输入/成本/调用次数对照；
- 不可判定的 run 单列 `unavailable`，不记 Safe。

```bash
env/.venv/bin/python compare/scripts/run/app_baseline.py \
  --results local/workpackages/2026-10-25-rc-fix5/attack/results.jsonl \
  --out local/workpackages/2026-10-25-e8-baseline --client <pinned>
```

### 4.5 完整开销（2026-10-28 精简协议替代）

执行口径只看[实验总方案 §6](EXPERIMENT-SUITE-7-REVISED-2026-10-26.md#6-overheadbench5)，旧“1天开发+1小时运行”估算撤下，校准后估时/预算。已有 `overhead_driver.py` / `overhead_metrics.py` 复用并适配，不扩展历史 `measure-overhead.sh` 成第二套框架。

- 一套固定Agent负载；并发1做C0/C1/C2/C3分账，4/8/16/32仅C0/C3；每格3次，共36正式条件，15配对区组、372并发实例，预热/校准/诊断另计。
- C2含runtime+DB，不是graph-only；图维护轻量计时。完整系统报耗时增幅/完成率、CPU/RSS/存储、成功操作缺失及分层丢弃、可关联端到端告警p50/p95、真实Observer调用/usage/费用。
- TLS成功读写微基准3重复；其他启用进程/syscall程序只随C1短诊断统计。按program ID及窗口差值计时，正式性能测量关闭诊断统计。
- 历史218 ns是三个程序均值之和，不能称一次探针/SSL操作；不再用握手时长作开销分母。T8分账表与F2并发图已于2026-10-28生成（`compare/results/overhead/raw/full-system/report/`，36/36条件）；正式测量在独立窗口执行。

### 4.6 人工标注与根因 GT（10-28~10-30，与 C2 并行）

**运行级标注**（`tools/annotator`，codebook `pilot/1`）：

1. 从 C2 选 ≥15（建议 20）个任务：覆盖 benign/attack、单/多智能体、executed/not-executed；
2. `python3 -m tools.annotator pilot --store ... --release ... --codebook-version pilot/1 --ledger ...`
   生成盲化证据包；
3. 刘周康、唐浩钺、刘锦样**各自独立**在单页 studio 填写 `attempted/executed/succeeded/roots/identities`，
   不共享修订、不看检测器输出；第四方只在全部提交后裁决；
4. `audit` → `annotation_agreement.py`：每字段 κ≥0.60；`unknown` 是类别不是缺失；
   **agent 之间的 κ 不能写成人类 κ**，报告里按实际 `method=human` 统计。

**根因/入口/链 GT**（R1.2/R2.4 的核心）：

1. 扩展 codebook：`root_cause_actor(s)`、`entry_point`、`delegation_chain (from,to)`、
   `boundary_types`；证据只取场景设计 + 本次 run 的证据包，不看 detector 输出；
2. 范围：23 个多智能体 attack（+ 9 个 `attack_type=C` 子集单独报）；
3. 流程：先由 AI 生成 `model_proposal` 草案（标注来源如实），再由三位作者**独立确认/修正**；
   报告用 “author-adjudicated reference” 而不是 “independent human annotation”；
4. 输出给 `compare/tools/root_cause_metrics.py`：RootAgent@1/@3、MRR、EntryPoint Acc、
   Chain P/R/F1、Depth MAE、Boundary-type Acc、`missing_root_predictions`。

> 实现前提：`claims/traceback.rs` 目前只返回**单个** root，没有有序候选。要报 @3/MRR，
> 需先实现确定性的 root 候选排序（按证据强度/距离/严重度，平局规则固定），并补测试。
> 若时间不够，只能报 @1 与链 P/R，并在 §7 明写 “@3/MRR 因实现限制未报告”。

## 5. 统计与数字登记

- 二元裁决必须同时给 **弃权率与覆盖率**；FPR 给 Wilson 区间；
- 攻击侧先给执行证据，再给 Unsafe/Safe/review；`marker`、`sink`、`marker∪sink` 分列；
- 分类比较用 McNemar exact + Holm；Effect size 用 Cliff's delta；报告分母与缺失；
- 每个进入论文的数字先写 `claims.json`，跑 `verify_claims.py` 必须全 hold；
- 旧 RC 数字（fix4 及更早）只作历史对照，不与 fix5 混表。

## 6. 成本与时间（量级）

| 项 | 量级 | 说明 |
|---|---|---|
| C2 机器时间 | 5–7 h（可 overnight） | 单智能体 ~50 s/run；多智能体 ~5–10 min/run |
| C2 模型成本 | 几十元人民币量级 | DeepSeek v4-flash；以首批 run 的实测 token 校准 |
| E8 judge | 93 × 3 臂 ≈ 279 次 | flash 档，约 0.5–1 h |
| OH 开发+运行 | 1 天 + 1 h | 主要是并发梯度脚本 |
| 人工标注 | **15–25 人时** | 20 任务 × 3 人 × 15 min + 根因 GT 确认 |
| 论文改写 | 2 天 | Word 修订痕迹重新生成、回复信、合并 PDF |

**关键路径不是算力，是：① tmux-live 多智能体的稳定性；② 三位作者的标注工时；③ 根因排序实现。**

## 7. 风险与回退

| 风险 | 触发信号 | 回退 |
|---|---|---|
| 多智能体 run 失败/无裁决 | 验证 run capture 非 healthy 或 Phase 2 超时 | 先修链路（DB 体积/停止超时）；仍不行则把 23 Type C 写成限制，转路线 B |
| campaign 中途供应商错误 | `Insufficient Balance` 等 | fail-fast 停整批，不挑选；修好预算重跑该批 |
| 标注人力不足 | 10-29 仍 <15 任务 | 只报告 AI 草案 + 0 人工 κ，明确写未完成 |
| 根因排序实现超时 | 10-29 未通过测试 | 只报 @1/链 P/R，删 @3/MRR，标题收窄“根因定位” |
| 时间不足 | 10-31 未冻结 RC fix5 | 转路线 B：按期提交诚实的收窄版 |

## 8. 开跑前 checklist

- [ ] 用户确认路线 A + 预算 + sudo 密码（只在会话中传递，不落盘）
- [ ] 三位作者确认标注时间
- [ ] P1-1/P1-2/P2-02/P2-04 修复并通过门禁 17/17
- [ ] P0-1 快照/版本库问题处理（授权 commit 或 sha256+异地备份）
- [ ] `prepare-sink-address.sh`、`/etc/hosts`、短输出根目录
- [ ] `model_preflight.py` 1-token 预检通过（exit 3 = 不许开始）
- [ ] `pgrep -af sink.py` 无遗留；release3 校验通过
- [ ] 记录 binary/release/config/命令/开始时间到 campaign 目录
