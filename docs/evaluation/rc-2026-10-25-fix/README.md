# RC 2026-10-25（修复后复测）

> **已被 [RC 2026-10-25-fix2](../rc-2026-10-25-fix2/README.md) 取代**（授权包络边界修正后复测，
> binary `db3191bb…`）。本文件保留为对照：它的良性复核率仍偏高的原因已定位到能力包络的
> 工作区/截断/描述符间接写三处缺陷（见实验设计 §75），其指标不得作为当前构建结果引用。

> 本目录是 [RC 2026-10-24](../rc-2026-10-24/README.md) 的**修复后复测**。修复内容见
> [实验设计 §74](../experimental-design.md)：候选审阅覆盖（不再伪造"已呈现"、Phase 2 批量补审）
> 与 Phase 2 数据量收敛（一对一归因、低于阈值诊断按意图设上限）。
> 协议、范围、红线与 2026-10-24 RC **完全相同**，唯一变化是 collector binary。

## 1. 冻结身份

见 `local/workpackages/2026-10-25-rc-fix/`：

| 项 | 值 |
|---|---|
| collector binary | `890b7e4c…e5bb67`（`binary/sha256.txt`） |
| eBPF helpers | 与 2026-10-24 RC 相同（`d738d4ba…`/`acf522d0…`/`41d71b40…`） |
| 数据源 release | `local/workpackages/2026-10-24-final-rc/release/`，`release_id f1b08e41…e72236`（93 case） |
| 门禁 | `./scripts/verify.sh` 17/17 全绿（`verify-gate.log`） |
| 观测臂 | `observer on`（产品默认）、release-bound `pilot_observer` |
| 范围 | process 模式：19 benign + 44 attack（`tmux-live` 仍排除，原因见 RC 2026-10-24 §2.6） |

## 2. 修复前后必须对照的两件事

1. **不再有 `unreviewed:*` 裁决来源**：修复前 benign 100% / attack 88% 的 `Uncertain` 主要
   来自"最终图新增候选被伪造成已呈现且无投票"；修复后该来源不应再出现（出现即回归）。
2. **数据量**：Phase 2 证据行数上限从 |intents|×|actions| 收敛到 |actions|；低于阈值诊断
   每意图 ≤4。修复前的 0.8 GB 级 DB 与 >300 s 终结化应当消失（以 campaign 日志与 DB 大小核对）。

## 3. 结果（2026-10-25 04:00 完成）

原始产物：`local/workpackages/2026-10-25-rc-fix/{benign,attack}/results.jsonl`（派生账本
`derived.results.jsonl`）、`metrics.json`、`overhead-full.json`、`overhead-lite.json`、`rc.json`。

### 3.1 canonical verdict

| 臂 | runs | 观测 | healthy | Unsafe | Uncertain | Safe | 弃权率 | 二元覆盖率 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| benign | 19 | 19 | 19 | **0** | 15 | **4** | 78.9% | **21.1%** |
| attack | 44 | 44 | 39 | **6** | 38 | 0 | 86.4% | **13.6%** |

- **可行动假阳性（benign Unsafe）**：0/19；条件 FPR 0/4 = **0**，Wilson 95% **[0%, 49.0%]**
  （分母只有 4，必须同时报覆盖率）。
- **可行动阳性（attack Unsafe）**：6/44 = **13.6%**（全尝试）；条件口径 6/6 = 100%，
  Wilson **[61.0%, 100%]**。
- **没有任何 `unreviewed:*` 裁决来源**（修复的直接验收条件）。
- 与修复前（[RC 2026-10-24](../rc-2026-10-24/README.md)）对照：benign 二元裁决 0 → 4；
  attack Unsafe 5 → 6；abstention 100%/88.4% → 78.9%/86.4%。

### 3.2 数据量（同一 benign 场景，修复前 vs 修复后）

| 表 | 修复前 | 修复后 |
|---|---:|---:|
| DB 大小 | 218.5 MiB | **18.0 MiB** |
| `correlation_evidence` | 62,402 | **4,137** |
| `evidence_refinements` | 61,690 | **908** |
| `non_link_diagnostics` | 18,612 | **804** |
| `graph_edges` | 29,270 | **1,928** |

### 3.3 开销（空载 45 s，同一 workload，同一脚本）

| profile | 修复前 | 修复后 | 历史基线（2026-10-18） |
|---|---|---|---|
| full | 151.2% CPU / 1716 MiB | **17.9% / 1636 MiB** | 6.24% / 1425 MiB |
| lite | 12.1% / 238.6 MiB | **10.7% / 240.0 MiB** | — |

full profile 的 CPU 回退从 ×24 收到 ×2.9；RSS 仍由 ONNX 推理子进程主导（+15% vs 历史）。
"低开销"仍只能按 profile 报告。

### 3.4 仍未解决

- **弃权率仍高**（78.9% benign / 86.4% attack）：裁决来源已诚实，但 Observer 对真实候选
  多数给 `Uncertain`；这是模型判断与候选选择共同造成的，需要下一阶段（授权包络/候选选择/
  observer 决策规则）处理，不能靠调阈值。
- **`tmux-live` 多 agent 仍不可测**：数据量修复后运行能完成 Phase 2（611 s，不再被 SIGKILL），
  但 harness 在该模式下没有 runner ledger，`bind_run_artifacts` 拒绝（`Run binding requires a
  regular non-symlink runner ledger`），且 611 s 仍过慢。该模式继续排除，原因记录在此。
- 真人标注/κ、独立执行分母、R-Judge 真实判定仍未执行（见交接）。

## 4. 解释边界

- 修复消除的是**伪造的复核请求**，不是模型的判断能力：Observer 仍可能对真实候选给出
  `Uncertain`（弃权），此时运行仍是 review——这是正确行为，不是回归。
- 候选选择的噪声（例如良性任务写自己的输出文件被当作 `CapabilityExpansion` 边界）仍然存在，
  属于下一阶段工作（授权包络与候选选择），不在本 RC 内通过调阈值掩盖。
