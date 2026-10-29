# FuxiBench 2.0 来源与迁移说明

本文件说明 FuxiBench 2.0 包的来源、标注/裁决链、两类型迁移、脱敏与许可。
当前版本 **2.0.0**，`taxonomy_version=fuxibench-2type/1`。当其他文档与
[metadata.json](metadata.json) / [release.json](release.json) 冲突时，以后两者与
[ground_truth/](ground_truth/) 为准。

## 1. 来源链

1. **场景输入**：`scenarios/`（93 个场景 JSON，含 30 个多 Agent 场景）、
   `fixtures/`、`bench/`、`prompt_inputs/`（93 case + manifest）。场景是受控设计，
   不是生产流量样本。
2. **场景 GT**：每场景 3 个独立 AI 标注者 + 无多数裁决 + 强制翻转裁决；结果写入
   `ground_truth/`。协议见 [AI-ANNOTATION-PROTOCOL.md](AI-ANNOTATION-PROTOCOL.md)，
   codebook 见 [ANNOTATION-CODEBOOK.md](ANNOTATION-CODEBOOK.md)。
3. **v5 原始证据**：`evidence/v5/gt-v5.json`（3 标注共识与逐字段证据）、
   `evidence/v5/flip-decisions.json` 与 `FLIP-REPORT.md`（v4-v5 差异裁决）、
   `blind_sources/`（脱敏盲源）、`rejected-sessions.json`（隔离的无效会话）。
4. **v4 检测契约**：included 场景沿用 v4 契约字段；excluded 场景的旧 v4 攻击契约
   只保存在 `legacy_v4_detection_contract`。旧 v4 GT/reviewer 树完整保存在
   `evidence/legacy/v4/ground_truth/`（283 个文件，内容保留（路径已中性化；原始哈希见 `original_sha256`））。
5. **traces**：历史 eBPF/collector 证据，脱敏后打包在 `traces/`（227 文件）；
   说明见 [traces/TRACES-NOTICE.md](traces/TRACES-NOTICE.md)。

## 2. 两类型分类迁移

2026-10-26 起当前标签为两类型：

| 当前 | 含义 | 旧分类 |
|---|---|---|
| A | 间接/不可信指令或被害化产物驱动 | 旧 B |
| B | 多 Agent / 交接 / 委派权限攻击 | 旧 C |
| benign | 审计无可证攻击 | benign |

- 旧 **Type A（显式恶意 prompt）从当前分类与论文移除**；相关场景按审计判 benign
  或按实际载荷通道归入新 A/B。
- 每条 GT 保留 `legacy_label`/`legacy_attack_type`；excluded 场景另保留
  `legacy_v4_detection_contract`。
- 旧计数（67/26、A3/B55/C9、旧 86 场景、旧 κ）只是历史；当前计数为
  **93 = 58 attack + 35 benign**、**A47/B11**、检测集 **93 = 58 + 35**、
  excluded **0**。

## 3. 标注与裁决（无人工）

- `method=ai_consensus_3`、`human_review=false`、`human_annotations=0`。
- 3 个 fresh、text-only 标注者；**279** 个接受会话；91 场景三票一致、2 场景无多数
  进入第 4 裁决。
- 17 个 v4-v5 差异（label 10 / attack_type 17）全部逐字段裁决：
  v5=24、v4=3、unknown=0；15 confirmed / 2 v4-retained / 0 unknown；
  19 个裁决会话，另 2 个额外标注者。
- 9 个场景在独立审计后判 **benign**；2026-10-27 经 3 路隔离盲标（27/27 票 benign）
  与独立裁决（`conflicts=[]`）确认其为困难负例，`quality.detection_excluded` 置 false
  并计入检测分母；旧 v4 攻击契约仍保留在 `legacy_v4_detection_contract` 作 provenance。
- **没有人工标签、人工 κ、人工复核或人工金标**。旧三 reviewer 一致性数字只描述
  旧 reviewer agents，不是人类 κ，也不是当前 GT 数字；旧 reviewer 姓名只作 AI
  过程标识，不是人类署名。

## 4. 脱敏与可移植性

- 包内路径一律相对（`scenarios/...`、`fixtures/...`、`evidence/...`、
  `ground_truth/...`）；不含主机绝对路径、工作区目录或仓库路径。
- `prompt_inputs/manifest.json`、`label_annotation.source`、v4 契约中的证据路径均
  改写为包内相对路径。
- 盲源中的设计说明是脱敏后的设计意图，不含声明 label/category 等元数据（剥离记录
  在 `evidence/v5/`）。
- `traces/` 的主机路径已替换为中性占位；fixture 凭据与 trace 中的凭据均为
  **decoy（非真实秘密）**；保留数据库 schema 以支持 replay。使用前应
  `sha256sum -c checksums.sha256`。
- `/tmp/...` 只作为场景运行语义出现（运行时路径），不是数据文件位置。

## 5. 许可与引用

- **数据**（scenarios、fixtures、GT、docs、evidence、sanitized traces）：
  **CC-BY-4.0**，见 [LICENSE-DATA](LICENSE-DATA)。
- **代码**（MtAgentLens 仓库，非本数据包）：Apache-2.0 OR MIT，见
  [LICENSE-APACHE](LICENSE-APACHE) / [LICENSE-MIT](LICENSE-MIT)。
- 引用与版本元数据见 [CITATION.cff](CITATION.cff)；decoy 凭据显式声明为非秘密测试
  数据。

## 6. 历史归档

- **旧 v4 GT / reviewer / _causal**：`evidence/legacy/v4/ground_truth/`（283 文件，
  内容保留（路径已中性化；原始哈希见 `original_sha256`），含 `_reviewers`、`_agreement.json`、`_validation.json`、
  `_v5-merge-report.*`、`_causal`）。仅作历史引用。
- **旧 AI-silver v1**：`ai-silver/1` 的双会话/旧发布只作历史映射，不是当前 label
  或论文指标；旧 AI-silver 内容不再作为当前文档主线。
- 旧 v4 过程文档 [ADJUDICATION-PROTOCOL.md](evidence/legacy/docs/ADJUDICATION-PROTOCOL.md)、
  [QUALITY_STANDARD.md](evidence/legacy/docs/QUALITY_STANDARD.md)、
  [BASELINE-ENGINES.md](evidence/legacy/docs/BASELINE-ENGINES.md)、
  [AI-SILVER-REVIEWER-MAP.md](evidence/legacy/docs/AI-SILVER-REVIEWER-MAP.md) 作为历史记录保留，
  不是当前分类/计数标准。

## 7. 维护

修改场景/fixture/GT 后必须重新构建包、重算 `release.json` 与
`checksums.sha256`、重跑数据卡/协议中的计数检查；不要手改哈希清单。历史证据不
删除、不改写；旧分类映射与计数以 [GT-MIGRATION-SUMMARY.json](GT-MIGRATION-SUMMARY.json)
和 `metadata.json` 为准。
