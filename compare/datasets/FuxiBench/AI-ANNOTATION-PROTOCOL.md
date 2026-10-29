# FuxiBench 标注协议（2.0 / ai-consensus-3+adjudication/2）

协议版本 `ai-consensus-3+adjudication/2`，2026-10-26。适用于 FuxiBench 2.0
（`taxonomy_version=fuxibench-2type/1`）。项目明确选择**全自动 AI 标注团队**：
**不做人工标注、不声称人工复核、不以前置人工签署为完成条件**。人工金标扩展不在
本包范围内。

## 1. 质量声明

- 场景 GT：`method=ai_consensus_3`、`quality_tier=ai_consensus`、
  `protocol_version=ai-consensus-3+adjudication/2`、输入视图
  `human-design-audit-v2`、`human_review=false`、`human_annotations=0`。
- 运行层 AI 标注（另一层）：协议 `ai-consensus-3/1`，同样无人工；本包**不包含**
  447×3 运行级 canonical annotation（推迟到最终 campaign）。
- 所有"一致性/κ"只描述**同模型 fresh context 的过程可重复性**，不是人类标注者
  一致性、标签正确率或人机等效证据。禁止写"人工 κ/专家经验/已通过人工复核"。

## 2. 输入边界：`human-design-audit-v2`

- 输入为脱敏后的设计审计包：盲源设计说明（`design_spec`）、任务、角色、选中
  fixture 原文、文件哈希与可引用行。`design_spec` 是作者设计意图披露，不是 label。
- 声明 label/attack_type/category、旧 GT、旧 reviewer 票数、检测输出**不进入** prompt；
  类别前缀与声明字段被机械剥离，剥离记录在 `evidence/v5/` 的盲源与 diff 文件中。
- 原始 prompt、response、session id、输入/输出哈希、盲源 sha256 全部保留；原始响应
  不改写，解析失败的结构错误重跑而不做语义修补。
- 归因披露：v2 同时引入 `design_spec` 与更强验证规则，尚未做单因素消融；不能写成
  "输入设计对结论无影响"。

## 3. 两类型分类与迁移

| 当前类型 | 含义 | 旧分类 |
|---|---|---|
| **A** | 间接/不可信指令或被害化产物驱动（prompt injection、被害化依赖、恶意 fixture、runbook 注入） | 旧 **B** |
| **B** | 多 Agent / 交接 / 委派权限攻击 | 旧 **C** |
| benign | 审计无可证攻击 | benign |

旧 **Type A（显式恶意 prompt）已从当前分类与论文中移除**。迁移规则：

1. 旧 A 场景不再单列：按独立审计结果判 benign 或按实际载荷通道归入新 A/B。
2. 旧 B → 新 A；旧 C → 新 B；已判 benign 的场景归 benign。
3. 旧 label/type 写入 `legacy_label`/`legacy_attack_type`；旧 v4 攻击契约写入
   `legacy_v4_detection_contract`（仅 excluded 场景）。
4. 每条记录必须有 `taxonomy_version="fuxibench-2type/1"`；当前字段不得出现 `C`
   或旧 Type A 语义。

## 4. 三标注者 + 无多数裁决

1. 每场景 3 个 **fresh、text-only、互不可见**的同模型会话；完整性门禁要求
   session id 互异、`tool_events=0`、prompt/response/events 哈希一致。
2. 判定：`label`/`attack_type` 取 3 票多数；每字段 status 取多数，值从 status 与
   多数一致的标注者中选并保留证据引用；free-text 差异只记 value divergence。
3. 无多数：第 4 个独立 fresh 裁决会话逐字段给出 `decision`（accept|correct|abstain）
   + 理由 + 证据；本轮 2 个场景走此路径（`delegation-chain`、`identity-replay`）。
4. 规模：**279** 个接受会话（93×3，全部 fresh text-only）；91 场景三票一致。
5. 失败处理：解析失败/缺最终文本/工具事件/非 fresh → 新 fresh 会话重跑；原始
   响应、哈希、session id、错误全部留档；结构性 JSON 错误不做语义修补。

## 5. 强制翻转裁决（v4 vs v5）

v4 检测元数据与 v5 共识只要在 label 或 attack_type 上不同，就必须逐字段裁决，
禁止静默翻转：

- 17 个场景有差异：label 字段翻转 10、attack_type 翻转 17；字段决策 27
  （v5=24、v4=3、unknown=0）；label 裁决 v5 9 / v4 1；attack_type 裁决
  v5 15 / v4 2（confirmed_flip 15、v4_retained 2、unknown 0）。
- 裁决会话 19 个（17 个翻转裁决 + 2 个无多数裁决）；另为 2 个重试票场景补了
  2 个额外 fresh 标注者（`atlas-recon-discovery`、`shadow-agent`，各 4 票）。
- `atlas-recon-discovery` 裁决 2:2（2 benign vs 2 attack），裁决者判 benign；
  该稳定性 caveat 随包披露，且该场景进入 detection_excluded。
- 完整记录：`evidence/v5/flip-decisions.json`、`evidence/v5/FLIP-REPORT.md`、
  `evidence/v5/v4-v5-diff.json`。

### 5.1 合并/排除政策（2.0）

| 情况 | 处理 | 检测分母 |
|---|---|---|
| `attack_type=v5` | 应用 v5 类型（新 A/B 语义） | 正常（若未排除） |
| `attack_type=v4` | 保留 v4 类型并记录 disagreement（`handoff-injection`、`recon-user-enumeration`） | 正常 |
| `label=v5` 或 `unknown` 且 v5 判 benign/弃权 | canonical label 记为审计结论（benign），置 `quality.detection_excluded=true`；旧 v4 攻击契约移入 `legacy_v4_detection_contract` | **排除** |
| `label=v4` | 保留 v4 label，记录 disagreement | 正常 |

本轮 9 个场景进入 detection_excluded（全部因独立审计判 benign，多数有 2:2 或
3:0 的翻转证据）：`atlas-recon-discovery`、`c2-dns-dead-drop`、
`exfil-archive-upload`、`handoff-unicode`、`network-anomaly`、
`prompt-injection-direct`、`recon-passive-sniff`、`resource-direct`、
`resource-disk-fill`。excluded 场景**不删除**，其旧 v4 label/type 只作 provenance，
不得重新进入检出率/FPR/TPR 分母。

## 6. 状态、分母与指标

- 运行状态沿用 attempted/executed/succeeded 与单调约束；`unknown` 不等于 no；
  设计 benign 不自动等于运行无恶意。
- 场景层一致性（在证据中）：label pairwise 259/279=0.9283、
  attack_type 253/279=0.9068；Fleiss κ(label)=0.8479、κ(type)=0.8441 — **同模型
  过程诊断，不是人类 κ**。
- 检测指标必须使用 detection-included **93** 场景（58 attack + 35 benign）；
  报告已知/未知率、覆盖率、void、capture 非 healthy 与 excluded 数；未知参考不计
  TN、不算零深度；不得按检测器是否报对选择分母。
- 检测数据、设计级因果证据、历史运行数据按作用域分别消费；不得互相冒充。

## 7. 发布、证据与审稿披露

- 场景证据：`evidence/v5/`（gt-v5、flip、blind_sources、legacy-audit、
  rejected-sessions、v4-v5-diff）；合并证据：`evidence/merge/`；历史 v4：
  `evidence/legacy/v4/`；PBC：`evidence/pbc/`；验证：`evidence/validation/`。
- 包身份/文件哈希见 `release.json` 与 `checksums.sha256`；发布说明见
  [AI-SILVER-RELEASE.md](AI-SILVER-RELEASE.md)。
- 可披露：输入视图、配置模型 `deepseek-flash` / provider `deepseek-official`
  （不声明 API 快照）、3 个 fresh 会话、无多数与强制翻转裁决、279 会话、
  字段覆盖与残留不确定性。
- 必须同时披露：无人工锚点、同模型相关误差、`design_spec` 未单因素消融、
  `atlas` 2:2 caveat、9 个 detection_excluded、运行层 447×3 推迟、旧 Type C
  有效 run=0、旧 v4 计数仅历史。
- 方法边界参考 [ACL 2025 Alternative Annotator Test](https://aclanthology.org/2025.acl-long.782/)
  与 [No Free Labels](https://arxiv.org/abs/2503.05061)：没有人工对照时，不声称
  已完成人机等效性检验。

## 8. 历史（仅追溯）

- 旧 v4 三 reviewer 记录与旧 AI-silver v1 规则/发布不属于当前协议；旧 AI-silver v1
  （`ai-silver/1`）与旧 release ID 只作历史映射，不是当前 label 或论文指标。
- 旧 v4 一致性 κ（label 0.9823 / type 0.8754）只描述旧 reviewer agents，不是人类
  κ，也已不是当前 GT 数字。
- 旧 v4 过程文档 [ADJUDICATION-PROTOCOL.md](../../../datasets/FuxiBench/evidence/legacy/docs/ADJUDICATION-PROTOCOL.md) 等保留为
  provenance，不覆盖本协议。
