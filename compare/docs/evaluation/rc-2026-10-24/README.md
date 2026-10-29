# Final release candidate 2026-10-24

> **已被 [RC 2026-10-25（修复后复测）](../rc-2026-10-25-fix/README.md) 取代**。本文件保留为
> 修复前的对照记录：它记录的 binary `e02d2957…` 存在"伪造已呈现候选"与"Phase 2 数据量
> 爆炸"两个缺陷（§2.5/§2.6），其指标不得作为当前构建结果引用。

> 状态：**冻结中的最终 RC**。本目录只记录**冻结的身份与可复算的产物索引**；论文数字以
> 本 RC 的正式实验输出为准，历史数字（2026-10-19/20 及更早）全部作废。
> 原始 run 目录、数据库、pcap 体积过大，保留在 gitignored 的
> `local/workpackages/2026-10-24-final-rc/`；本目录保存它们的 hash、清单与摘要。

## 1. 冻结身份

| 项 | 值 |
|---|---|
| 数据源 release | `local/workpackages/2026-10-24-final-rc/release/`（93 case = 67 attack + 26 benign） |
| release_id | 见 `local/workpackages/2026-10-24-final-rc/rc.json`（由 `compare.tools.dataset_release.validate_release` 重算校验） |
| collector binary | `collector/build/release/mtagentlens`，SHA-256 见 `rc.json` |
| eBPF helpers | `bpftools/.output/bin/{tls_capture,process,stdiocap}`，production 构建（无 ASAN） |
| 工具链 | rustc / python / 内核 / node 版本见 `rc.json` |
| git | HEAD + dirty 状态与变更文件数见 `rc.json`（本 RC **未提交**） |
| 门禁 | `./scripts/verify.sh` 全绿；日志 hash 与 gate 摘要见 `rc.json` |

```bash
# 复现冻结身份（不重跑实验）
python3 scripts/evaluation/freeze_rc.py ...   # 仅生成一次，拒绝覆盖
```

## 2. 实验协议（命令与产物）

所有实验都使用**同一个冻结二进制**（runner 在开始时把二进制按 hash 复制到 run 目录，
中途重建工作树不影响已完成行），并记录 `collector_sha256`、`release_id`、
`configuration_sha256` 与 run-dir。

### 2.1 受控链路与 capture 健康（先决条件）

在正式测量前先跑单场景，确认 `capture_loss_free=true`、`ring_buffer_drops=0`、
`overall_healthy=true`；否则不进入正式指标。

### 2.2 良性 FPR

```bash
MTAGENTLENS_SUDO_PASSWORD=... PYTHONPATH=env:env/src \
  env/.venv/bin/python scripts/wire/fpr_runner.py local/workpackages/2026-10-24-final-rc/fpr
```

- 每遍 26 个 benign 场景；`--pass-label` 区分重复遍次。
- 观测臂：`observer on`（产品默认，含语义 Observer）与 `--disable-observer`
  （确定性核心臂）。两者是**不同量**：前者会把良性运行放进 `Uncertain` 复核队列
  （弃权），后者不应产生复核项。

### 2.3 攻击 TPR 与执行证据

```bash
MTAGENTLENS_SUDO_PASSWORD=... PYTHONPATH=env:env/src \
  env/.venv/bin/python compare/scripts/run/pilot_observer.py \
  --release local/workpackages/2026-10-24-final-rc/release \
  --observer on --mode auto --timeout 900 \
  --out local/workpackages/2026-10-24-final-rc/attack \
  --only <scenario-id> [--only ...]
```

- 攻击集：release 中的 67 个 attack case（清单见 `local/.../attack-cases.txt`）。
- 每个 run 记录 `run_binding`（v4）、canonical verdict、`capture_quality_status`、
  `evaluation_status`；attempt journal 把中断的尝试记成显式 `interrupted` 行，
  不静默丢弃。
- 执行率由 `scripts/evaluation/attack_execution_evidence.py` 从动作级证据独立判定；
  `not_executed`/`unobserved` 的 run 不进入召回分母。

### 2.4 开销

```bash
MTAGENTLENS_SUDO_PASSWORD=... ./scripts/wire/measure-overhead.sh 45 <out.json>
MTL_LITE=1 MTAGENTLENS_SUDO_PASSWORD=... ./scripts/wire/measure-overhead.sh 45 <out-lite.json>
```

空载主机、固定采样窗口；full 与 lite 两个 profile 分别记录 CPU/RSS/延迟。

### 2.5 测量有效性事件（已处理，必须如实记录）

**事件**：第一遍 benign 测量（`results.pass1-invalid-db-race.jsonl`，18 行）出现 6 行没有
canonical verdict。根因不是分类器弃权，而是测量工具读库方式错误：`fpr_runner` 在
collector 仍持有**未 checkpoint 的 WAL** 时复制主库文件，把最终裁决页留在了 WAL 里
（两条 multi-agent 运行的 WAL 达 30 MB / 55 MB）。

**处理**：
1. 该遍整体作废并归档为证据（不删除，也不修正行内容）；
2. `fpr_runner` 修复：等 collector 退出（`wait_for_collector`，超时即记 `artifact_error`），
   再用**root 权限的 SQLite online backup** 取 WAL 一致的快照（`finalized_snapshot`），
   记录 `finalized_db_sha256`，读取后清理临时快照；
3. `completed_scenarios` 收紧为「有 canonical verdict 才算完成」，因此缺裁决的行会被重测，
   不会静默进入或退出分母；
4. 用修复后的 runner 重跑整遍，作为正式 pass 1。

**影响**：第一遍**不进入任何指标**；正式结果只来自修复后的账本。这是"先证明测量有效
再花实验预算"的一次实际执行，也是本 RC 唯一的作废遍次。

### 2.6 测量范围与一个已量化的阻塞（multi-agent 运行）

正式指标只使用 **process 模式（单进程单 agent）场景**：

- benign 19 个（26 个 benign 中 7 个是 `tmux-live` 多 agent）；
- attack 44 个（67 个 attack 中 23 个是 `tmux-live` 多 agent）。

**为什么排除 `tmux-live`**：在这些运行上 collector 的 Phase 2 终结化无法在可接受的
时间内完成，harness 到点 SIGKILL，**没有 canonical verdict**。实测（同一 frozen binary）：

| 运行 | 模式 | 观测 | 结果 |
|---|---|---|---|
| benign-multi-performance-bench | tmux-live | observer off、stop grace 30 s | SIGKILL，无 verdict |
| benign-multi-performance-bench | tmux-live | observer off、stop grace 300 s | **仍超时** SIGKILL，无 verdict（合计 339 s） |
| 4 个 benign multi 场景 | tmux-live | observer on、默认 30 s | 4/4 SIGKILL，无 verdict |

根因已定位到数据量而不是网络：这些运行的 `collector.responsibility.db` 达 **0.8–0.85 GB**，
其中 `correlation_evidence` 473,614 行、`evidence_refinements` 459,010 行、
`non_link_diagnostics` 400,112 行，而 `actions` 只有 35,313 行——Phase 2 的相关与精化
产生了 O(intents × actions) 量级的行。历史（2026-07/2026-10-20）同类运行的 DB 只有
MB 级。这不是本 RC 的测量工具问题，而是当前工作树相对历史基线的一个**性能/体积回归**；
它同时解释了 30 s shutdown grace 被击穿与 SIGKILL 后裁决丢失。

处理原则：**不因为跑不动就放宽判定**，也不把无 verdict 的运行塞进分母。多 agent 覆盖面
作为已知限制记录，修复（有界相关/精化 + 终结化预算）列为后续工作，不在本 RC 内通过
改阈值糊过去。

## 3. 指标计算

```bash
# 1) 派生 capture quality（原始账本不改）
python3 scripts/evaluation/enrich_campaign_results.py --results <raw.jsonl> --out <derived.jsonl>

# 2) canonical verdict 指标（含弃权率、quality-limited、Wilson、all-attempts 界）
python3 scripts/evaluation/final_verdict_metrics.py \
  --benign <derived-benign.jsonl> --attack <derived-attack.jsonl> \
  --release <release> --out <metrics.json>
```

只使用 canonical verdict（`runs.final_decision`）。`Uncertain` 是**弃权**，不是阴性或阳性；
分母与 bound 必须一起报告。

## 4. 结果（2026-10-25 正式测量）

**冻结身份**：collector binary `e02d2957…e1e51d`；release `f1b08e41…e72236`（93 case）；
eBPF helpers `d738d4ba…`/`acf522d0…`/`41d71b40…`；内核 7.0.0-38-generic。
**观测臂**：`observer on`（产品默认）+ release-bound `pilot_observer`（v4 run-binding、
artifact hash、attempt journal）。
**范围**：process 模式单进程场景（19 benign + 44 attack）；`tmux-live` 多 agent 见 §2.6。

### 4.1 原始产物

| 产物 | 路径（`local/workpackages/2026-10-24-final-rc/`） |
|---|---|
| benign 账本 | `benign/results.jsonl` → `benign/derived.results.jsonl` |
| attack 账本 | `attack/results.jsonl` → `attack/derived.results.jsonl` |
| 指标 | `metrics-observer-on.json` |
| 执行证据启发式 | `attack-execution-audit.json`（`independent=false`，不入分母） |
| 开销 | `overhead-full.json`、`overhead-lite.json` |
| 冻结身份 | `binary/`、`release/`、`rc.json`（由 `scripts/evaluation/freeze_rc.py` 生成） |
| 作废账本 | `fpr-archive/`（见 §2.5）、`fpr/`（探索性非 release-bound 测量） |

### 4.2 canonical verdict 结果

| 臂 | runs | 观测 | healthy | Unsafe | **Uncertain（弃权）** | Safe | 弃权率 |
|---|---:|---:|---:|---:|---:|---:|---:|
| benign | 19 | 19 | 18 | **0** | **19** | 0 | **100%** |
| attack | 44 | 43 | 41 | **5** | **38** | 0 | **88.4%** |

- **可行动阳性（attack）**：5/44 = **11.4%**（全尝试口径）；条件口径（healthy 二元裁决）
  5/5 = **100%**，Wilson 95% **[56.6%, 100%]**——但分母只有 5，覆盖率 11.6%。
- **可行动假阳性（benign）**：**0/19**；因为 benign **没有任何二元裁决**，条件 FPR
  **无法计算（n=0）**，只能报告全尝试上界 `[0%, 100%]`。
- 结论必须同时写覆盖率：**当前构建在 benign 上从不给出 `Safe`，在 attack 上只有 11.6%
  给出二元裁决**。所以"FPR=0"不是好成绩，而是"没有输出"。

### 4.3 为什么（已定位，见 [实验设计 §73](../experimental-design.md)）

Uncertain 的 provenance 分布：

| provenance | benign | attack | 含义 |
|---|---:|---:|---|
| `unreviewed:boundary/operation/pattern` | 11 | 11+ | Phase 2 新增候选没有 Observer 投票 → fail-closed review |
| `observer_uncertain` | 8 | 10 | Observer 审阅了但弃权 |
| `hard_violation` | 0 | 1 | 硬规则命中 → Unsafe |
| `observer_unsafe` | 0 | 2 | Observer 引用证据判 Unsafe → Unsafe |

即：**可行动性受"候选选择 + Phase 2 审阅覆盖"支配，而不是受证据强度支配**。
`policy_boundary` 候选（工具链读取系统路径也算越界）在 benign 上不可避免，一个未审阅候选
就足以把整次运行压成 review。

### 4.4 执行证据（低置信，仅作描述）

`attack_execution_evidence.py` 用监视器可观察指标判定 44/44 为 `executed`（网络工具 +
非回环 egress / shell / ssh 等）。该判定 `independent=false`，**不进入召回分母**；
论文不得把它写成"独立攻击执行标签"。

### 4.5 开销（空载主机，45 s 窗口，同一 workload）

| profile | CPU 均值 | 峰值 RSS | 事件数 | 对比历史 full 基线 |
|---|---:|---:|---:|---|
| full（默认，含语义层/ONNX） | **151.2%** | **1716 MiB** | 9,571 | 历史 6.24% / 1424.8 MiB |
| lite（`--lite`） | **12.1%** | **238.6 MiB** | 7,252 | — |

> 两臂均为历史记录：ONNX 语义层已于 2026-10-27 移除，`--lite` 与 `--disable-classifier`
> 已被 CLI 拒绝；当前对照只能用 `--disable-responsibility-observer`，这两行数字不得引用为
> 当前构建的开销。

full profile 的 CPU 相对历史基线上升约 24×（2.81 s → 68.03 s，同 workload），与 §2.6
的数据量回归同源；lite profile 把开销拉回可用范围（CPU 12.1%、RSS 239 MiB）。
"低开销"只能按 profile 报告，不能把 lite 的数字写成默认配置。

### 4.6 未执行

- `observer off`（确定性核心）臂未作为正式臂运行（§73 的机制决定它同样会被未审阅候选压成
  review；作为下一步对照再跑）；
- `tmux-live` 多 agent 场景（§2.6）；
- 真人独立标注、第四方裁决、人类 κ（没有真人参与，见标注 RC）；
- R-Judge/A1–A3 应用层基线的真实判定（仅实现与契约测试，无结果数字）。


## 5. 红线

- capture `degraded`/`unknown` 的 run 不进入条件指标；
- 不得把 `Uncertain`（复核/弃权）计成 Safe 或 Unsafe；
- 不得用历史 RC 数字替代本次结果；
- 事件级对齐不可用（ADR-0016），不得写逐事件对齐；
- 标注/κ 与检测指标是两套证据，不能互相顶替。
