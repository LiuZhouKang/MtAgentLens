> **2026-10-27 状态：G0 已机械完成**：release `9c7099c1…c6ea3`；93/58/35、0 excluded、93 paper_ready；A6 `/tmp` blind prepare 失败是当前唯一验收阻断；`identity-confusion` notes 仍科学未决。本文件保留为修复记录，不再代表“未修”状态。

# FuxiBench 93 场景修复与冻结计划（2026-10-26）

> 目标：把当前 93 输入中 9 个 `detection_excluded` 与被标记 `paper_ready=false`/输入漂移的场景修复到**全部 93 可计分**，为 93×1 最终 campaign 和新 v4 trace 数据集提供干净输入。  
> 原则：不是“把 flag 翻 false”，而是 **task ↔ fixture ↔ description ↔ prompt_inputs ↔ GT ↔ oracle** 六方一致；证据不足则保持 unknown 并继续修，不用检测结果决定标签。  
> 本轮先做审计与修复设计；实际改数据在 W7 执行，改完重新冻结 release。

---

## 0. 当前问题面

> **2026-10-27 只读盘点**：93 例 scenario/prompt/bundle/text fixture **0 hash drift**；实际分类 **38 valid / 30 review_only / 16 source_change / 9 excluded**。`paper_ready` 44 true /21 false /28 missing =49 个 flag，但**不是 49 个坏输入**；核心源修约 **25 例**（16 source+9 excluded），其余 30 为审阅/flag 对齐。3 组 staged patch（resource-disk-fill、c2-dns-dead-drop、network-anomaly）dry-run OK、未应用；72 例 role_file 缺失（运行仅作日志键）；identity-confusion fail-open、handoff-unicode ASCII 仍开。以 `local/history/validation/readiness-2026-10-26/team/scenario-repair/inventory.json` 为准。

- 93 输入：58 attack + 35 benign；9 `detection_excluded`；84 scored。
- 9 excluded：`atlas-recon-discovery`、`c2-dns-dead-drop`、`exfil-archive-upload`、`handoff-unicode`、`network-anomaly`、`prompt-injection-direct`、`recon-passive-sniff`、`resource-direct`、`resource-disk-fill`。
- **不止一个其他场景**：当前 GT 中有 **49 个**需要裁决/补齐：`paper_ready=false` 21 + 字段缺失 28；30 multi 只有 9 个 `paper_ready=true`（5 attack+4 benign）；22 attack multi 中 17 个未 ready。另有 `needs_human_review`/输入漂移（见 §2）。其中 `handoff-unicode` 与 **`identity-confusion`** 是最典型的两个：前者被 excluded，后者仍在 58 attack 计分集、但场景/package hash、actor 命名空间、单进程回退等存在问题。
- **不预设目标计数**：当前顶层 label 是 58 attack+35 benign（9 excluded 的顶层 label=benign），但 9 个中 7 个的 `quality.notes` 记录 3/3 裁决为 attack；盲重标后可能是 **65 attack+28 benign（multi 23+7）**，也可能维持 **58+35（multi 22+8）**，或中间结果。**以盲标证据冻结为准**；禁止为了 58/35 或 22/8 调整标签。

---

## 1. 修复总原则

1. **一个权威输入链**：`scenario.json` → `fixtures/<fixture_dir>` → `bench/<id>/` → `prompt_inputs/cases/<id>` → `ground_truth/<id>.json`；六方 hash/内容一致。
2. **task 与 fixture 一致**：任务要求执行的动作必须在 fixture/docs 中存在；fixture 不得有未声明的隐藏 payload；description 不得声称不存在的命令/文件。
3. **角色一致性**：`agents[].role_file` 必须存在；`agents` 数量与 `mode`、`tmux_layout` 一致；多 Agent 场景必须有真实的跨进程/会话行为，不是单进程导入钩子伪装。
4. **prompt_inputs 重新生成**：禁止沿用旧 `ccbos/team`、旧路径、旧任务；每个 scenario 从当前 scenario+fixture 重建 prompt/bundle，重新计算 source hashes。
5. **GT 与 oracle 一致**：expected_behaviors、expected_commands、resources、min_agents、boundary、severity、oracle 必须可从当前 fixture 复现；不能从旧版本复制。
6. **重标**：3 个 fresh AI 标注 + 独立裁决；只看 task/fixture/轨迹，不看 detector 输出；AI 参考 `method=model_proposal`，无人工 κ。
7. **重跑**：修复后每个场景至少跑 1 次验证；验证 run 只作链路/执行证据，不作为最终论文分母；最终 93×1 在代码/数据冻结后统一跑。
8. **禁止**：为通过而删证据、改标签、隐藏 excluded、全局替换 loopback、把旧 v1 trace 冒充新 trace。

---

## 2. 场景清单与处置

### 2.1 9 个 excluded（全部要回到 93）

| 场景 | 当前问题（摘要） | 目标 | 最小修复 |
|---|---|---|---|
| `handoff-unicode` | 公开 token/明确允许规范化/relay；旧 C 语义弱；当前判 benign/excluded | **困难 benign（第 8 个 multi benign）** | 统一 task/docs 允许 relay 的表述；删除“未授权外传”形状；oracle 只验证规范化/selfcheck/relay 合法产物；重标 |
| `atlas-recon-discovery` | GT 2:1 vs v5_disagreement 2:2；fixture/description 与输入版本不一致；recon 只读无 claim | **困难 benign 或真实 recon attack**（二选一，按 task 授权） | 明确 task 是否授权公共基线/intake；未授权采集→真实 attack；授权→clean benign；重建 prompt/oracle |
| `c2-dns-dead-drop` | fixture 是普通运维清单、无 DNS 活动；载荷只在旧 prompt | **干净 benign 或真实 DNS attack** | 若 attack：fixture 真含 DNS 查询/编码；若 benign：删 dead-drop 叙事；重建 prompt |
| `exfil-archive-upload` | 旧归档外传形状/输入漂移 | 困难 benign 或真实外传 attack | 校验上传端点/载荷/授权；clean benign 则删攻击形状 |
| `network-anomaly` | 网络异常场景与 fixture/描述不一致 | 困难 benign | 明确正常网络行为；删异常叙事；oracle 按正常 |
| `prompt-injection-direct` | 直接注入文本/benign 审计冲突 | 困难 benign | 保留“看似注入但已披露/无越权”的良性链；删未授权动作 |
| `recon-passive-sniff` | prompt 内嵌旧 docs、fixture 良性清单；实际无 sniff | 干净 benign | 统一 docs/task；删 tcpdump/nmap 叙事 |
| `resource-direct` | 资源滥用形状/旧标签冲突 | 困难 benign | 明确资源使用上限；删恶意资源耗尽 |
| `resource-disk-fill` | 磁盘填充形状/旧标签冲突 | 困难 benign | 明确磁盘预算/cleanup；删填充攻击 |

### 2.2 另一个必须修的场景：`identity-confusion`（当前在 58 attack 内）

- 已知问题：当前 scenario sha 与 manifest 不一致；`prompt_inputs`/GT/reviewers 仍是重构前 ccbos/team 版本；actor 命名空间不一致（`role-analyst-01` vs `analyst`）；`_delegated_actor` 缺失时静默回退 analyst，单进程也能触发冒用；auditor 验收条件无法严格满足。
- 处置：
  1. 统一 actor id 命名空间（`analyst/operator/auditor` 或 canonical `role-analyst-01`，二选一，所有 task/fixture/GT 一致）；
  2. 删除静默回退；缺 plan 时 fail-closed，不冒用；
  3. auditor 验收条件按实际 fixture 生成；
  4. 重建 prompt_inputs/bundle；重算 scenario/fixture hash；
  5. 重标 3 AI + 裁决；确认真实 Type B 还是 benign；若攻击，要求跨进程/会话证据。
- 验收：scenario sha=manifest、prompt 与当前 fixture 字节一致、actor 一致、单进程不能完成攻击、GT `paper_ready=true`。

### 2.3 其他 `paper_ready=false`/输入漂移场景

当前 49 个需要裁决/补齐（21 false+28 missing；含上面两个与 9 个中的部分）。30 multi 只有 9 个 ready（5 attack+4 benign）。scenario/prompt/manifest 哈希目前基本一致；`identity-confusion` 包已重生成（sha 5657dadd，与 manifest 一致），但其 GT review notes 仍旧、fixture 仍有“缺 plan 静默回退 analyst”的 fail-open，需要修。9 个 excluded 顶层 label=benign，但 scenario description 仍写 Type A/C attack（如 handoff-unicode 仍“Type C”），必须改描述/oracle 再解禁。其余包括：
`benign-multi-distributed-test`、`benign-multi-incident-response`、`benign-log-analysis`、`stealth-slow-burn-exfil`、`network-c2-beacon`、`persistence-cron-backdoor`、`supplychain-dependency-confusion`、`leader-betrayal`、`consensus-attack`、`stealth-team-oncall-handoff`、`stealth-team-build-coordination`、`stealth-team-sprint-planning`、`role-progressive`、`mirror-poisoning`、`credential-ssh-direct`、`exfil-env-secrets`、`stealth-credential-leak`。

共同问题：
- prompt_inputs 仍指向旧 fixture/旧任务/旧路径；
- scenario/fixture 与 GT reviewers 版本不一致；
- role_file 不存在；
- description 声称的载荷/文件/命令不存在；
- needs_human_review 未消解。

处置：统一走 §1 六方一致 + 重标；**不是只改 `paper_ready` flag**。

---

## 3. 修复流水线（W7）

### Step 1 盘点与哈希
```bash
# 只读
env/.venv/bin/python scripts/evaluation/validate_fuxibench_package.py
env/.venv/bin/python - <<'PY'
# 逐场景打印 scenario/prompt/fixture/GT 的 sha256 与 manifest 对比
PY
```
输出：`local/dataset-repair/inventory.json`（scenario_id → hashes → problems）。

### Step 2 逐场景修复
- 修改 `datasets/FuxiBench/scenarios/<id>.json`、`fixtures/<fixture_dir>`、`bench/<id>/`；
- 从当前 scenario 重建 `prompt_inputs/cases/<id>/{prompt.txt,bundle.json}`；
- 修 role_file/agents/mode/task；
- 更新 `ground_truth/<id>.json` 的 behaviors/commands/resources/oracle/quality；
- 删除/重写旧路径与旧叙事，禁止全局 IP 替换。

### Step 3 重标
- 3 fresh text-only AI reviewers + adjudicator；
- 输入：task + fixture 原文 + 角色；不读 detector；
- 输出 label/attack_type/behaviors/severity/min_agents/min_decision/should_alert；
- 无多数 → unknown/裁决；记录分歧。

### Step 4 重建包
```bash
env/.venv/bin/python scripts/evaluation/package_fuxibench.py \
  --package datasets/FuxiBench --source-release <release> --force
env/.venv/bin/python scripts/evaluation/validate_fuxibench_package.py --clean-room
```
- 所有 93 `detection_excluded=false`；`metadata` 不再写 9 excluded；
- release id/checksums/manifest；
- 若修了 9 个，必须删除旧 excluded flag 和旧 legacy 契约的误导性文案。

### Step 5 静态/运行验证
- `validate_gt` 0 problems；
- 每个修复场景至少 1 个 run：capture healthy、DB v4/checkpoint；
- 执行证据（marker/文件/网络）与 task/fixture 一致；
- 不通过则回到 Step 2，不进入最终 campaign。

### Step 6 冻结
- 93 全 scored；`paper_ready=true`；`needs_human_review` 消解或明确 unknown；
- 30 multi（attack/benign 数由盲标冻结；当前顶层 22 attack+8 benign，但 7 个 excluded notes 为 attack，不能预设）；
- 生成 `FuxiBench 2.1`/新 release；更新 dataset card/provenance/claims；
- 冻结后不再改标签/分母。

---

## 4. 验收门（G0 数据）

- [ ] 93 个 scenario，0 `detection_excluded`；
- [ ] 93 个 `ground_truth`，`validate_gt` problems=[]；
- [ ] 每个 scenario 的 prompt_inputs 与当前 fixture/task hash 一致；
- [ ] role_file 存在、agents/mode 一致；
- [ ] 9 个原 excluded + identity-confusion 全部 `paper_ready=true`；
- [ ] 30 multi；handoff-unicode 归 benign 后仍 30；
- [ ] 包 clean-room 通过；release/checksums；
- [ ] 每个修复场景至少一次 capture-healthy 验证 run；
- [ ] 无旧路径/旧 IP/旧 prompt 残留（静态扫描）。

---

## 5. 与实验的衔接

- **必须先 G0 数据冻结，再 E1 93×1**；否则所有检测/身份/归因数字会变。
- 新 trace 数据集在 E1 后生成；旧 v1 包 traces 只读，不声称 replay。
- fix4 历史 run 只作回归；不与 93 主表混。
- 若某个场景最终无法修到一致：**不进入 93**，但必须在论文中单独列“无法构造有效场景”的清单与原因；不能悄悄改分母。
