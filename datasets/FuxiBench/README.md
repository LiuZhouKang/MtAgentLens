# FuxiBench 2.0（伏羲基准）

自包含的 LLM Agent 安全场景与场景级 Ground Truth（GT）数据集包。
**版本 2.0.0**，`taxonomy_version=fuxibench-2type/1`。

- **数据许可**：CC-BY-4.0（场景、fixture、GT、证据、脱敏 traces）。
- **代码许可**：Apache-2.0 OR MIT（MtAgentLens 代码仓库；本包为数据包）。
- **标注标准**：3 个独立 AI 标注者 + 裁决；**没有人工标签、人工 κ、人工复核或人工金标**。
- **性质**：场景设计级参考 + 历史运行证据；不是检测性能结论，也不自动认证任何论文指标。

## 1. 数据规模

| 项目 | 数量 |
|---|---|
| 场景输入 | **93**（30 个多 Agent 场景） |
| 场景级 GT | **93 条 = 58 attack + 35 benign** |
| 攻击类型（新分类） | **A 47 / B 11**（良性 35） |
| 参与检测计分的场景 | **93 = 58 attack（A 47 / B 11）+ 35 benign** |
| 审计后从检测分母排除 | **9**（审计判 benign；旧 v4 攻击契约保存在 `legacy_v4_detection_contract`） |

**两类型分类（2026-10-26 起）**：

| 类型 | 含义 | 对应旧分类 |
|---|---|---|
| **A** | 间接/不可信指令或被害化产物驱动恶意动作（prompt injection、被害化依赖、恶意 fixture） | 旧 **B** |
| **B** | 多 Agent / 交接 / 委派权限攻击 | 旧 **C** |
| benign | 审计无可证攻击 | benign |

旧 **Type A（显式恶意 prompt）已从当前分类与论文中移除**。审计判 benign 的 9 个场景
（包括两个旧 Type A 场景）不再属于当前攻击类；它们的旧 v4 攻击契约只作为历史
provenance 保留，**不得重新计入检测分母**。

## 2. 快速开始

```bash
# 1) 校验整包完整性（在包根目录；checksums.sha256 覆盖包内文件）
sha256sum -c checksums.sha256

# 2) 查看版本/分类/计数
python3 - <<'PY'
import json
meta = json.load(open('metadata.json'))
rel = json.load(open('release.json'))
print(meta['dataset_version'], meta['taxonomy_version'], rel['release_id'])
print(meta['detection_included_counts'], meta['detection_included_attack_types'])
PY
```

读取 GT：`ground_truth/<category>/<scenario_id>.json`，schema 为
`mtagentlens.ground_truth.v4`，但**分类字段是 2.0 两类型**；旧 v4 字段在
`legacy_label`/`legacy_attack_type`/`legacy_v4_detection_contract` 中。
检测指标必须先过滤 `quality.detection_excluded=true` 的记录（见
[DATASET-CARD](DATASET-CARD.md) §4）。

`traces/` 是脱敏后的历史 eBPF/collector 证据，仅用于 MtAgentLens replay 与
敏感性分析；先读 [traces/TRACES-NOTICE.md](traces/TRACES-NOTICE.md)。

## 3. 包内布局

```

  README.md  DATASET-CARD.md  AI-ANNOTATION-PROTOCOL.md  PROVENANCE.md
  SCENARIOS.md  ANNOTATION-CODEBOOK.md  AI-SILVER-RELEASE.md
  evidence/legacy/docs/ADJUDICATION-PROTOCOL.md  evidence/legacy/docs/AI-SILVER-REVIEWER-MAP.md
  evidence/legacy/docs/BASELINE-ENGINES.md  evidence/legacy/docs/QUALITY_STANDARD.md      # 历史 v4 过程文档，见 §5
  metadata.json  release.json  checksums.sha256  GT-MIGRATION-SUMMARY.json
  CITATION.cff  LICENSE-DATA  LICENSE-APACHE  LICENSE-MIT
  scenarios/       # 93 个场景 JSON
  fixtures/        # 场景引用的 fixture 文件
  bench/           # setup / sink 支撑脚本
  prompt_inputs/   # 93 个 case + manifest（fixture 相对路径）
  ground_truth/    # 93 条 GT + _reviewers/_agreement/_validation/_causal
  evidence/
    v5/            # 3 标注者原票、gt-v5、flip-decisions、FLIP-REPORT、blind_sources
    merge/         # MERGE-REPORT.{json,md}
    pbc/           # 61 单 Agent run 的 PBC 分布与分层指标
    validation/    # 诊断与 post-fix 真实验证证据
    legacy/v4/     # 旧 v4 GT / reviewer 记录（内容保留（路径已中性化；原始哈希见 `original_sha256`），仅历史）
  traces/          # 脱敏历史 traces + TRACES-NOTICE.md
```

所有包内引用使用相对路径（`scenarios/...`、`fixtures/...`、`evidence/...`、
`ground_truth/...`）。`/tmp` 只作为场景运行语义出现，不是数据包路径。

## 4. 文档

| 文档 | 内容 |
|---|---|
| [DATASET-CARD.md](DATASET-CARD.md) | 组成、分类、准入规则、验证、能力边界与红线 |
| [AI-ANNOTATION-PROTOCOL.md](AI-ANNOTATION-PROTOCOL.md) | 3 标注者 + 无多数裁决 + 强制翻转裁决协议 |
| [ANNOTATION-CODEBOOK.md](ANNOTATION-CODEBOOK.md) | 两类型 codebook 与字段定义 |
| [SCENARIOS.md](SCENARIOS.md) | 93 个场景的完整清单（含新类型/检测状态） |
| [PROVENANCE.md](PROVENANCE.md) | 来源、迁移、脱敏、许可与历史归档 |
| [AI-SILVER-RELEASE.md](AI-SILVER-RELEASE.md) | 发布身份、计数、证据与校验 |
| [CITATION.cff](CITATION.cff) | 引用信息与许可 |

## 5. 历史文档（不是当前标签标准）

以下文件保留为 v4/v5 迁移过程的历史记录，**不得作为当前分类或当前计数引用**：
[ADJUDICATION-PROTOCOL.md](evidence/legacy/docs/ADJUDICATION-PROTOCOL.md)（旧 v4 规则裁决）、
[AI-SILVER-REVIEWER-MAP.md](evidence/legacy/docs/AI-SILVER-REVIEWER-MAP.md)（旧 AI-silver 映射）、
[BASELINE-ENGINES.md](evidence/legacy/docs/BASELINE-ENGINES.md)、
[QUALITY_STANDARD.md](evidence/legacy/docs/QUALITY_STANDARD.md)。其中出现的旧
`67/26`、`A3/B55/C9`、旧 86 场景与旧 κ 仅供追溯；当前口径以
[metadata.json](metadata.json)、[release.json](release.json) 与
[ground_truth/](ground_truth/) 为准。

## 6. 边界与引用

- 不声称人工标注/人工复核/人工 κ/人机等效；AI 标注是唯一标准。
- 不把本包当检测效果证明；检测指标须使用 detection-included 93 场景并按预注册
  口径报告 void/capture/Uncertain 与排除项。
- 多 Agent/Type B 的运行级检测与因果定量仍待最终付费 campaign；本包不提供
  运行级根因金标，PBC 产物中 **Type C（旧分类）有效 run = 0**，不得作 Type C 声明。
- 引用请使用 `CITATION.cff`；数据集许可见 `LICENSE-DATA`，代码许可见
  `LICENSE-APACHE` 与 `LICENSE-MIT`。
