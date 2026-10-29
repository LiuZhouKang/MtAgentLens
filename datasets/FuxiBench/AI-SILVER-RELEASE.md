# FuxiBench 2.0 数据集发布说明

更新：2026-10-26。版本 **2.0.0**，`taxonomy_version=fuxibench-2type/1`。
本文是 [DATASET-CARD.md](DATASET-CARD.md) 与
[AI-ANNOTATION-PROTOCOL.md](AI-ANNOTATION-PROTOCOL.md) 的发布/身份说明；
不构成人工金标、论文效果结论或投稿完成证明。

## 1. 一句话结论

FuxiBench 2.0 是**自包含、可移植的 93 场景数据包**：GT 为 3 个独立 AI 标注者 +
无多数裁决 + 强制翻转裁决的共识（无人工标签），采用 **A/B 两类型**
（旧 Type A 移除；旧 B→A、旧 C→B）。93 条 GT = **58 attack + 35 benign**；
原先 `detection_excluded` 的 9 个审计判 benign 场景，于 2026-10-27 经 3 路隔离盲标
（27/27 票 benign）+ 独立裁决（`conflicts=[]`）确认为困难负例后计入分母（旧 v4 攻击
契约保留为 provenance）；检测参与集 **93 = 58 attack（A47/B11）+ 35 benign**。

## 2. 包与发布身份

| 项目 | 值 |
|---|---|
| 包路径 | ``（自包含数据包；代码在 MtAgentLens 仓库） |
| 版本 | `2.0.0` |
| release schema | `mtagentlens.dataset-release/1` |
| `release_id` | `release.json` 的 `release_id`（不硬编码，避免自指） |
| 文件数 | `release.json` 的 `package_file_count`（`checksums.sha256` 提供逐文件哈希） |
| 许可 | 数据 **CC-BY-4.0**；代码 **Apache-2.0 OR MIT** |
| 引用 | [CITATION.cff](CITATION.cff) |

包内文件清单/哈希以 `release.json` 与 `checksums.sha256` 为权威；任何文件变化都
必须重算这两者，不能手改。

## 3. 组成与计数

| 项目 | 数值 |
|---|---|
| 场景输入 | 93（30 个多 Agent） |
| GT | 93 = 58 attack + 35 benign |
| 新类型 | A **47** / B **11**（benign 35） |
| detection-included | 93 = 58 attack（A47/B11）+ 35 benign |
| detection_excluded | 0（原 9 例已于 2026-10-27 盲标+裁决后计入） |

GT 字段：`schema`（v4 兼容）、`taxonomy_version`、`label`/`attack_type`（当前
两类型）、`legacy_label`/`legacy_attack_type`、`legacy_v4_detection_contract`
（仅 excluded）、`label_annotation`、`annotation`/`legacy_annotation`、`quality`。
完整定义见 [DATASET-CARD.md](DATASET-CARD.md) §2；逐场景见
[SCENARIOS.md](SCENARIOS.md)。

### 3.1 标注规模

- 3 个 fresh、text-only、互不可见标注者；**279** 个接受会话（93×3）。
- 91 场景三票一致；2 场景无多数进入第 4 裁决（`delegation-chain`、`identity-replay`）。
- v4-v5 差异 17 个场景（label 10 / attack_type 17）；字段决策 v5=24/v4=3/unknown=0；
  15 confirmed / 2 v4-retained / 0 unknown；19 个裁决会话，另 2 个额外标注者。
- `atlas-recon-discovery` 裁决 2:2（2 benign vs 2 attack），裁决者判 benign；
  该 caveat 已披露。2026-10-27 的盲标复核（3 路一致 benign + 独立裁决）后该场景
  与其余 8 例一并计入检测分母。
- **无人工标签、人工 κ、人工复核或人工金标**。

## 4. 证据与目录

| 路径 | 内容 |
|---|---|
| `ground_truth/` | 93 条 GT（当前两类型）+ `_reviewers`（旧 v4 历史记录）+ `_agreement.json`/`_validation.json` + `_causal/`（设计级因果提案） |
| `scenarios/` `fixtures/` `bench/` `prompt_inputs/` | 场景输入、fixture、支撑脚本与 93 case 的 prompt manifest |
| `evidence/v5/` | `gt-v5.json`、`flip-decisions.json`、`FLIP-REPORT.md`、`v4-v5-diff.json`、`blind_sources/`、`rejected-sessions.json` |
| `evidence/merge/` | 合并报告（`MERGE-REPORT.json`/`.md`） |
| `evidence/pbc/` | 61 单 Agent run 的 PBC 分布与分层指标（历史 fix4 证据，非检测分母） |
| `evidence/validation/` | 修复前诊断与 post-fix 真实验证证据 |
| `evidence/legacy/v4/` | 旧 v4 GT/reviewer 树（283 文件，内容保留（路径已中性化；原始哈希见 `original_sha256`），仅历史） |
| `traces/` | 脱敏历史 eBPF/collector traces（227 文件；见 [TRACES-NOTICE](traces/TRACES-NOTICE.md)） |

## 5. 校验

```bash
# 整包哈希（在包根执行）
sha256sum -c checksums.sha256

# release/版本/计数
python3 - <<'PY'
import json
meta = json.load(open('metadata.json'))
rel = json.load(open('release.json'))
print(meta['dataset_version'], meta['taxonomy_version'], rel['release_id'])
print(meta['detection_included_counts'], meta['detection_included_attack_types'])
PY
```

包级验证（`validate_fuxibench_package.py`）实测：`valid=true`，records 93 /
attack 58 / benign 35 / excluded 0 / included 58+35 / A47 / B11，
package_file_count 见 `release.json`，problems=[]。`validate_gt` 实测 93/58/35/0 pending/problems=[]。

PBC 历史证据：61 条单智能体 run（18 benign + 43 attack）；attack PBC=0 **28/43**；
benign 13/18；rank AUROC **0.5277777777777778**；PBC≥1 FPR 27.8%；
PBC≥10 TPR 0.50（2/4）/FPR 0。**该产物使用旧 fix4 类型记录、早于 2.0 两类型迁移**，
不是 detection-included 分母；旧 Type C 有效 run=0，不得作 Type C 声明。

Post-fix 验证（`evidence/validation/`）：binary `4372a153…`、
release `71e10e45…`，3 条 run capture healthy、`ringbuf_full_dropped=0`、
verdict Safe/Info、claims 0；`atlas-recon-discovery` 的只读侦察无 claim 规则
（能力缺口，已披露）；`persistence-cron-backdoor` 为 void。这些 run 不入论文分母。

## 6. 能力边界与残留限制

1. 不能声称人工标注/人工复核/人工 κ/人机等效；AI 标注是唯一标准。
2. 不能把 AI 共识 κ（0.8479/0.8441，同模型过程诊断）写成人类 κ 或标签正确率。
3. 不能把本包 PBC 当新 A/B 或 Type C 定量；9 例困难负例计入分母是 2026-10-27 的
   盲标+裁决结果，不得回退成"攻击未执行成功"的口径。
4. 无运行级根因金标；运行层 447×3 AI 标注推迟到最终 campaign，本包不含运行级
   canonical annotation。
5. 场景是受控设计，不代表真实生产流量患病率、攻击成功率或全部威胁分布。
6. 多 Agent/Type B 的运行级检测与因果定量仍待最终付费 campaign。
7. 历史 v4 计数（67/26、A3/B55/C9、旧 86 场景、旧 κ）只作追溯，不是当前值。
8. 包内引用均为相对路径；`/tmp` 仅是场景运行语义，不是数据路径。traces 中主机
   路径已脱敏，凭据为 decoy。

## 7. 历史映射（仅一段）

旧 v4/v5 与 AI-silver v1 只作历史：旧 AI-silver v1 是 `ai-silver/1` 的双会话参考
（旧发布 ID `400cbcbb…`），旧 v4 为三 reviewer 规则裁决记录；二者都不是当前 label、
不是论文指标，也不代表人工。旧证据内容保留（路径已中性化；原始哈希见 `original_sha256`）在
[evidence/legacy/v4/](evidence/legacy/v4/)；旧过程文档
[ADJUDICATION-PROTOCOL.md](evidence/legacy/docs/ADJUDICATION-PROTOCOL.md)、
[AI-SILVER-REVIEWER-MAP.md](evidence/legacy/docs/AI-SILVER-REVIEWER-MAP.md)、
[QUALITY_STANDARD.md](evidence/legacy/docs/QUALITY_STANDARD.md)、
[BASELINE-ENGINES.md](evidence/legacy/docs/BASELINE-ENGINES.md) 保留为历史记录，不覆盖当前两类型口径。
