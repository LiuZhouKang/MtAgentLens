# FuxiBench 数据卡与实验准入规范（2.0.0）

更新：2026-10-26。`taxonomy_version=fuxibench-2type/1`；包版本与 `release_id` 以
[metadata.json](../../../datasets/FuxiBench/metadata.json) / [release.json](../../../datasets/FuxiBench/release.json) 为准。本卡描述已核验的
能力与缺口，不是人工签署、论文效果结论或投稿完成证明。

## 1. 用途与组成

目标是评估 LLM Agent 安全观测的检测与责任定位。场景是人工设计的受控任务，
不代表真实生产流量的患病率、攻击成功率或全部威胁分布。

| 项目 | 数值 |
|---|---|
| 场景输入 | **93**（30 个多 Agent 场景） |
| GT 记录 | **93 = 58 attack + 35 benign** |
| 攻击类型（新两类型） | **A 47 / B 11**（benign 35） |
| 检测参与集 | **93 = 58 attack（A 47 / B 11）+ 35 benign** |
| detection_excluded | **0**（2026-10-27 起；原 9 例经 3 路盲标 + 独立裁决确认困难 benign 后计入，见 §4） |
| GT schema | `mtagentlens.ground_truth.v4`（兼容工具；分类语义为 2.0 两类型） |

### 分类（2026-10-26 起）

| 类型 | 含义 | 旧分类映射 |
|---|---|---|
| **A** | 间接/不可信指令或被害化产物驱动恶意动作（prompt injection、被害化依赖、恶意 fixture、runbook 注入） | 旧 **B** |
| **B** | 多 Agent / 交接 / 委派权限攻击 | 旧 **C** |
| benign | 审计无可证攻击 | benign |

旧 **Type A（显式恶意 prompt）已移除**；命中旧 A 的场景或按当前审计判 benign，
或按其实际载荷通道归入新 A/B。当前字段**不会出现 `attack_type=C`，也不会出现
旧 Type A 语义**。

### 类别 × 新类型

| category | 场景数 | label/type 分布 |
|---|---:|---|
| `benign` | 26 | benign 26 |
| `c2_backdoor` | 6 | A 5 + benign 1 |
| `credential_theft` | 7 | A 7 |
| `data_exfiltration` | 7 | A 6 + benign 1 |
| `defense_evasion` | 3 | A 3 |
| `multi_agent_attack` | 23 | A 11 + B 11 + benign 1 |
| `privilege_escalation` | 3 | A 3 |
| `prompt_injection` | 3 | A 2 + benign 1 |
| `reconnaissance` | 6 | A 3 + benign 3 |
| `resource_abuse` | 4 | A 2 + benign 2 |
| `supply_chain` | 5 | A 5 |

完整逐场景清单见 [SCENARIOS.md](../../../datasets/FuxiBench/SCENARIOS.md)。

## 2. GT 字段

每条记录（`ground_truth/<category>/<scenario_id>.json`）：

| 字段 | 含义 |
|---|---|
| `schema` | `mtagentlens.ground_truth.v4`（工具兼容；分类语义看 `taxonomy_version`） |
| `taxonomy_version` | `fuxibench-2type/1`（全部 93 条） |
| `label` / `attack_type` | **当前 canonical**：attack/benign；attack 时 A/B；benign 时 benign |
| `legacy_label` / `legacy_attack_type` | 旧 v4 合并层 label/type（仅 provenance） |
| `legacy_v4_detection_contract` | 仅 excluded 场景：旧 v4 攻击契约（`expected_behaviors` 等）逐项保留 |
| `label_annotation` | v5 三标注共识、证据引用、source 哈希、分歧/裁决记录 |
| `annotation` / `legacy_annotation` | 历史 v4 标注记录（只读披露，不代表当前作者或人工） |
| `quality` | `paper_ready=true`（93/93，机械判据见 §4）；`detection_excluded`/`exclusion_reason` 保留字段语义 |
| `expected_behaviors` / `benign_activity` / `min_decision` / `should_alert` / `execution_check` | 检测契约内容（included 场景沿用 v4 契约；excluded 场景见 `legacy_v4_detection_contract`） |

## 3. 标注协议（摘要）

- 协议 `ai-consensus-3+adjudication/2`；输入视图 `human-design-audit-v2`；
  3 个 fresh、text-only、互不可见的同模型标注会话 + 独立裁决。
- **279** 个接受标注会话（93×3）；91 场景三票一致，2 场景无多数进入第 4 个裁决
  （`delegation-chain`、`identity-replay`）。
- 强制翻转裁决：17 个场景 v4/v5 有差异，字段决策 27（v5=24/v4=3/unknown=0）；
  15 confirmed / 2 v4-retained / 0 unknown；19 个裁决会话（17 翻转 + 2 无多数），
  另 2 个额外标注者（`atlas-recon-discovery`、`shadow-agent` 重试票）。
- `atlas-recon-discovery` 裁决 2:2（2 benign vs 2 attack），裁决者判 benign；
  其稳定性 caveat 保留，且该场景进入 detection_excluded。
- **无人工**：`human_review=false`、`human_annotations=0`、无人工 κ/金标/复核。
  同模型 Fleiss κ（0.8479 label / 0.8441 type）只作过程可重复性诊断，不是人类 κ。
- 原始证据：`evidence/v5/`（gt-v5、flip-decisions、FLIP-REPORT、blind_sources、
  v4-v5-diff、rejected-sessions）；完整协议见
  [AI-ANNOTATION-PROTOCOL.md](AI-ANNOTATION-PROTOCOL.md)。

## 4. detection_excluded 与分母准入

**当前 0 例**。2026-10-27 之前有 9 例 `quality.detection_excluded=true`
（`atlas-recon-discovery`、`c2-dns-dead-drop`、`exfil-archive-upload`、
`handoff-unicode`、`network-anomaly`、`prompt-injection-direct`、
`recon-passive-sniff`、`resource-direct`、`resource-disk-fill`）。它们的共同形态是
"看起来像攻击但实际无效果"：有界写（固定常量的 /tmp 写入）、自建 loopback stub、
`DECOY-*` 占位凭据、解码值从不 `eval/exec`、外发目标全是 127.0.0.1。

处置：三路互相隔离的盲标（可执行性 / 授权与危害 / 可观测与因果，27/27 票 benign）
加一路独立裁决（再回 fixture 逐行核对，`conflicts=[]`）后全部 `include_benign`，
`quality.detection_excluded` 置 false，旧 v4 契约仍保留在 `legacy_v4_detection_contract`
里作 provenance。`paper_ready=true` 的依据是机械判据：label 与两类型 taxonomy 一致、
不在排除集、所有 `role_file` 存在、multi 场景显式声明 `tmux.layout`、description 不用
退役 Type 标记、scenario 字节与冻结 prompt manifest 绑定一致、且每条保留标注证据 note。

准入规则：

1. 检测指标使用 **93 个场景**（58 attack + 35 benign）；`detection_excluded` 过滤仍然
   生效（当前为空集），新增排除必须写明理由。
2. 仍需按预注册口径把"审计判 benign 的历史攻击设计"作为**困难负例**单独披露；
   不得把其旧 v4 label/type 当当前攻击类。
3. 同场景重复运行不是独立场景样本；区间/配对比较保留场景聚类。
4. 按预注册口径单独报告 void attempt、capture 非 healthy、Uncertain 与 excluded
   数量；不得静默缩减语料或按检测器表现选样本。
5. `score`/`decision`/`alerts` 只描述一次运行，不是场景 GT；hook marker 只证明
   钩子到达，连接不证明数据送达。

## 5. 证据、PBC 与验证

- `evidence/v5/`：3 标注者原票、gt-v5、flip、合并与盲源；`evidence/merge/`：合并报告。
- `evidence/pbc/`：fix4 历史 **61 条单智能体 run**（18 benign + 43 attack）的 PBC
  分布与分层指标。canonical 数字：attack PBC=0 **28/43**、benign 13/18、
  rank AUROC **0.5277777777777778**、PBC≥1 FPR 27.8%、PBC≥10 TPR 0.50（2/4）/FPR 0。
  **该产物使用旧 fix4 类型记录、早于 2.0 两类型迁移**；只作活动量/混杂分析，
  不是 detection-included 分母，不得作新 A/B 或 Type C 定量声明（旧 Type C 有效 run=0）。
- `evidence/validation/`：修复前诊断 + post-fix 真实验证。post-fix binary
  `4372a153…`、release `71e10e45…`，3 条 run 全部 capture healthy、
  `ringbuf_full_dropped=0`、verdict Safe/Info、claims 0；benign 理想；
  `atlas-recon-discovery` 执行了 recon 钩子但只读侦察无 claim 规则（检测能力缺口，
  已披露）；`persistence-cron-backdoor` marker 空 = void。这些 run 不入论文分母。
- `traces/`：227 个脱敏历史 trace 文件；主机路径已替换为中性占位，fixture 凭据为
  decoy；详见 [traces/TRACES-NOTICE.md](../../../datasets/FuxiBench/traces/TRACES-NOTICE.md)。

### 输入一致性修复（2026-10-26）

`evidence/annotation-source-verification.json`记录93场景、1150个唯一任务/fixture/角色输入文件
的原始来源哈希与包内哈希。恢复依据是原始标注case-map，保留IP语义，未重判标签；prompt从
当前任务和fixture重新渲染。旧release的全局loopback替换已撤销；旧实验只能按原身份引用。
`metadata.json.input_repair`记录被替换的旧release身份，当前身份以release.json为准。

角色文件是设计审计背景；执行器将`role_file`视为历史角色标签，不额外注入角色system prompt。
设计审计不是运行参考；运行授权以实际launch任务为准。trace是历史数据，不能当本次构建的新采集。

## 6. 发布身份与校验

| 项目 | 值 |
|---|---|
| 包版本 | `2.0.0` |
| release schema | `mtagentlens.dataset-release/1` |
| `release_id` | `release.json` 的 `release_id`（不硬编码，避免自指） |
| 文件数 | `release.json` 的 `package_file_count` |
| 完整性 | `sha256sum -c checksums.sha256`；`release.json` 文件哈希清单 |
| 许可 | 数据 CC-BY-4.0；代码 Apache-2.0 OR MIT |

包级验证（`validate_fuxibench_package.py`）实测：`valid=true`，
records 93 / attack 58 / benign 35 / excluded 0 / included 58+35 / A47 / B11 /
package_file_count 见 `release.json`，problems=[]。`validate_gt` 实测 93/58/35/0 pending/problems=[]。

## 7. 能力边界与红线

- 不能声称人工标注/人工复核/人工 κ/人机等效；`human_annotations=0` 是事实。
- 不能把 AI 共识当标签正确率；不存在人工锚点。
- 不能把本包 PBC 产物重新计入检测分母；不能作旧 Type C 定量声明。
- 9 例困难负例计入分母是 2026-10-27 的盲标+裁决结果；不得回退成"攻击未执行成功"的口径。
- 无运行级根因金标；不报告 RootAgent/MRR/入口/链准确率。
- 运行层 AI 标注流水线已设计（`ai-consensus-3/1`）但 447×3 未执行；本包不含
  运行级 canonical annotation。
- 不能声称独立开发/测试集或家族间无泄漏已证明；正式 experiment 仍需固定划分、
  阈值、分母与预注册口径。
- 历史 v4 文档/计数（67/26、A3/B55/C9、旧 86 场景、旧 κ）只作追溯，不是当前值。

## 8. 维护

修改场景/fixture/GT 后必须重新构建包、重算 `checksums.sha256`/`release.json`、
重跑数据卡与协议中的计数校验；不要手改 `release.json` 或哈希清单。历史证据
（含 `evidence/legacy/v4/`）保留字节，不删除、不改写；旧分类映射以
[GT-MIGRATION-SUMMARY.json](../../../datasets/FuxiBench/GT-MIGRATION-SUMMARY.json) 为准。
