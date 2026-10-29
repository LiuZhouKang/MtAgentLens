# Changelog

本文件记录用户可见的变化。格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)；
版本号遵循语义化版本。项目的权威状态以 `./scripts/verify.sh` 为准。

## [Unreleased] — 2026-10-26（FuxiBench 2.0：自包含数据集包 + 两类型分类）

### 数据集（canonical）

- 新增自包含数据包 [`datasets/FuxiBench/`](datasets/FuxiBench/README.md)，版本 **2.0.0**，
  `taxonomy_version=fuxibench-2type/1`；旧仓库内数据集树退役，不再作为当前路径。
- **两类型分类**：A = 间接/不可信指令或被害化产物驱动（旧 B，**47**）；
  B = 多 Agent/交接/委派权限攻击（旧 C，**11**）；**旧 Type A 移除**，当前字段无 `C`。
- GT **93 = 58 attack + 35 benign**；9 个审计 benign 场景
  `quality.detection_excluded`（旧 v4 攻击契约保留在 `legacy_v4_detection_contract`）；
  检测参与集 **84 = 58 attack（A47/B11）+ 26 benign**；**无人工标签/κ/金标**。
- 包内容：scenarios/fixtures/bench/prompt_inputs/ground_truth/evidence
  （v5/merge/pbc/validation/legacy v4）/traces（脱敏）；`release.json`
  `release_id=ec1ba74da25ffd61fcda4ae427ed33fd0c779171acca7d0c194ab7067de45a2a`、
  3,532 文件、`checksums.sha256`；数据许可 CC-BY-4.0，代码 Apache-2.0 OR MIT。
- 标注：3 个独立 fresh AI 标注者 + 无多数裁决 + 强制翻转裁决（279 accepted session；
  17 v4-v5 差异 → 15 confirmed / 2 retained / 0 unknown）；无人工复核。
- PBC canonical（意见 7）：61 个 fix4 单智能体 run，attack PBC=0 **28/43**，
  AUROC **0.5278**，PBC≥1 FPR 27.8%；旧 Type C 有效 run=0，**不作 Type C 声明**，
  且该产物早于 2.0 迁移、不是检测分母。
- 真实场景 post-fix 验证（binary `4372a153…` / release `71e10e45…`）：3 条 run
  capture loss 0、claims 0、verdict Safe/Info；只读侦察规则缺口与 void 样本已披露。

### 门禁与测试

- `./scripts/verify.sh` → **18/18 all gates passed**（新增第 18 门：FuxiBench 包
  portability/clean-room）；compare **1266 passed + 16 subtests**；env **365 passed**；
  scripts **60 + 7 subtests**；claims **54/54**；`validate_gt` **93/58/35/problems=[]**。

## [Unreleased] — 2026-10-26（历史：FuxiBench v5 合并，已被 2.0 取代）

> 历史记录，保留追溯。此处旧路径/旧 release ID/旧 67/26 与 A3/B55/C9 计数**已退役**，
> 不再作为当前值；当前口径见上面的 2.0 条目与 `datasets/FuxiBench/`。

### 数据（历史）

- 场景 GT v5（3 标注者 AI 共识 + 翻转裁决）曾以 v4-compatible 检测契约合并；9 个
  独立审计与 v4 标签不一致的场景标记 `detection_excluded`；旧 v4 字节归档到包内
  `evidence/legacy/v4/`。该合并口径已被 2.0 两类型分类取代。
- 运行层 3 标注者流水线 + 标签残留 sanitizer 已交付；447×3 全量推迟到最终 campaign。
- 真实场景修复后复验（旧 binary `4372a153…` / 旧 release `71e10e45…`）保留为历史证据。

## [Unreleased] — 2026-10-25（FuxiBench AI 银标数据集完成）

### 数据

- 场景设计层：93 场景双 fresh 会话 AI 银标完成，9 处标签/类型分歧逐条源审计裁决，
  11 处越界引用以「原文 + 修订理由」留痕修正；最终 attack 66 / benign 26 / unknown 1，
  双会话 label 一致 87/93、type 84/93；`exfil-env-secrets` 因设计证据冲突隔离为 unknown。
- 运行层：452 个原生 runner 账本去重后 447 个运行全部完成双 fresh 会话标注，
  55 处分歧由第三会话裁决；`verify` 为 `valid=true`、`all_candidates_reviewed=true`、
  `errors=[]`；保守覆盖门控把采样视图下的全局否定改为 unknown（unknown 率
  attempted 0.803 / executed 0.870 / succeeded 0.928，coverage corrections 318/335/340），
  原始模型票保留；20 个无效/中断会话移入 `reviews-quarantine/` 并记录清单。
- 变体家族：93 场景归并为 85 个保守家族，dev 43 / test 50，同族不跨划分。
- 因果设计草案：93 项（85 proposal / 8 unknown，30 个多 agent，runtime_references=0）。
- 新增不可变内容寻址发布包 `mtagentlens.ai-reference-release/1`
  （release_id `400cbcbb478d9dae7e13edb84d20969babfa0c1a7aed13e26038f67b0ed16a33`，
  7 组件、100 内容文件），`ai_reference_available=true`、`human_gold_available=false`、
  `paper_metrics_available=false`；人工标注仍为 0，运行级根因金标仍为 0。
- 评审输入残留扫描：447 个 review packet 中 87 个含历史 agent 自身读取基准的痕迹
  （1 个直接复述 label/attack_type、63 个提及基准元数据、29 个打开过 `ground_truth/`
  路径）；发布按「部分盲化」披露，`case-0136` 建议在敏感性分析中单列或排除。
- 新增 [AI 银标发布说明](datasets/FuxiBench/AI-SILVER-RELEASE.md)、
  [数据集发布说明](datasets/FuxiBench/AI-SILVER-RELEASE.md)；
  DATASET-CARD 与 AI-ANNOTATION-PROTOCOL 同步最终口径。

### 工具与校验

- 新增 `compare/tools/ai_reference_release.py`：组件科学口径校验、unknown 不记为负例的
  状态评估、不可变发布构建与篡改检测（12 项聚焦测试）。
- `scripts/evaluation/ai_scenario_annotations.py` 增加跨场景引用拒绝（哈希有效也不放行）。
- 全量门禁 `pytest compare/tests tools/annotator/tests` = **1210 passed**（16 subtests）；
  场景/运行/家族/因果/GT 溯源五个组件校验全部通过。

## [Unreleased] — 2026-10-25（交接：Codex Lead 工作包 + 仓库清理/一致性扫描）

### 交接与清理

- 重写 `docs/HANDOFF-NEXT-SESSION.md`（当前唯一入口；fix4 基线、未完成清单、陷阱、验收定义）。
- 新增 `docs/HANDOFF-CODEX.md`：可直接粘贴的 Codex Lead prompt、DSH Agent Teams 协作建议、
  预算与写作用域红线。
- 新增 `local/README.md` 证据索引（有效 vs 历史/无效产物；不删除证据）。
- 清理仓库内 Python 缓存（`__pycache__`/`.pytest_cache`）；`local/` 保持 gitignored。
- 全局一致性扫描：EVIDENCE-INDEX、alert-latency、observer-cost、replay-parity-check、
  response-letter-notes、revision-package、smoke 记录全部统一到 "当前 RC = fix4" 的口径
  （fix2 仅作历史对照）。

## [Unreleased] — 2026-10-25（E2/E3/E4 在 fix4 上离线重算）

### 评估

- E2/E3/E4 全部在 **RC fix4 的 61 个 frozen checkpoint** 上离线重算（零模型调用）：
  产物 `local/workpackages/2026-10-25-e3-fix4/`（`benign.json`、`attack.json`、
  `summary.json`、`*-facts.jsonl`、`e2-summary.json` + README）。
- E2：良性 PBC mean 0.72 / max 3，攻击 mean 1.77 / max 32；纯活动量基线 AUROC **0.528**、
  PBC≥1 FPR **27.8%**、PBC≥10 TPR 50%/FPR 0。
- E3：25 阈值网格点 + 9 权重/门控点，**裁决翻转 0/61**（分支最多变化：attack 1 run、
  benign 0）；E4：六维 LOO 裁决翻转 **0/61**。
- `docs/paper/claims.json` 增至 **39 条**（含 E2/E3/E4 fix4 claims，全部通过）；
  claim-evidence matrix C5/C6/C11/C13/C14 与 E2/E3/E4 文档同步更新。

## [Unreleased] — 2026-10-25（冻结 RC 2026-10-25-fix4）

### 评估

- 冻结 [RC fix4](docs/evaluation/rc-2026-10-25-fix4/README.md)：measured binary
  `de3a0894…`、release `aa364a45…`、config `6d44789c…`、当时门禁 17/17（历史）；新增
  `select_valid_runs.py`（含测试）+ `rc.json` + `metrics.json` +
  `metrics-with-execution.json` + `attack-execution-witness.json` + `replay-ablation.json`。
- 有效数据：benign 18（15 Safe / 3 review / 0 Unsafe；覆盖率 83.3%、弃权 16.7%，
  条件 FPR 0/15 [0%,20.39%]）；attack 43（4 Unsafe / 21 review / 18 Safe；
  可行动 9.3%，条件 18.2% [7.31%,38.52%]，弃权 48.8%）。
- 执行链（独立证据）：marker 16/43=37.2%；已执行子集 4 Unsafe / 9 review / 3 Safe
  （已裁决 4/7=57.1%）；marker∪sink=18（≥review 77.8%）。
- `docs/paper/claims.json` 重写为 **33 条 claim**（指向 fix4 artifacts，全部通过）；
  claim-evidence matrix 与数字审计同步更新；fix2 降级为历史对照。

## [Unreleased] — 2026-10-25（硬规则消融：credential-read → egress，零模型调用）

### 评估

- 新增 `ClassificationConfig.hard_sensitive_egress`（默认 off，旧 checkpoint 兼容）：
  在 `AnalysisCheckpoint::evaluate` 中把确定性的 credential-read → network-egress 链
  升级为 `hard.sensitive_egress`；运行时可用 `MTAGENTLENS_HARD_SENSITIVE_EGRESS=1` 开启。
- 零 token 验证：`replay-sensitivity` 在 61 个已记录 run 上重算，关闭 flag 时与记录裁决
  **逐条一致（0 mismatch）**；开启后 **2 条已执行攻击 review → Unsafe**
  （`credential-theft-direct`、`supplychain-malicious-build`），**benign 0 翻转**；
  executed-subset 可行动率 4/19 → 6/19。记录于
  `docs/evaluation/hard-sensitive-egress-ablation.md`。
- 已冻结 campaign 口径不变（默认 off）；该 flag 作为下一 RC 的推荐运营点。

## [Unreleased] — 2026-10-25（attack-only 对照实验：方法学负结果）

### 评估

- 新增 `scripts/evaluation/attack_capacity_experiment.py` + `attack-capacity-triggers.json`
  （44 条 GT 推导触发器）+ 3 测试：不经 LLM 直接执行载荷（零 token）。
- **负结果**：44/44 执行成功但 **claims/alerts 全为 0**，裁决全为 `Uncertain`，
  provenance 为 `no_observations + capture_quality:unknown`。根因：责任归因是 intent 驱动的，
  无 LLM 上下文时 OS 动作无 intent 可归属 → 该协议**不能**作为容量指标。
- 结论与正确口径写入 `docs/evaluation/attack-capacity-2026-10-25.md`：
  有效容量 = agent-gated executed subset（19 执行 → 4 Unsafe / 73.7% ≥review）；
  改进路径 = 用这 19 个 DB 离线 replay 调参（零新经费）。

## [Unreleased] — 2026-10-25（盲化整改：指纹清除 + agent 环境过滤 + release3）

### 评估

- corpus 全局去除 agent 可见的 harness 指纹（`mtal-*`/`MTL_*`/`MTAGENTLENS_*`/`MtAgentLens`），
  `MTL_RUN_MARKER`→`RUN_TAG`、`mtal-markers.log`→`run-hooks.log`；重建 release3
  `71e10e45…`（93 cases，语料 0 命中）。executor 新增 `agent_visible_env()` 过滤
  agent 环境；新增 `env/tests/test_blind_hygiene.py`（2 项契约）。
- release3 消融 smoke：指纹清除后强模型仍能静态识破载荷并拒绝（7/8），证明拒绝主因是
  payload 可审阅性；`RUN_TAG`/`run-hooks.log` 仍被引用（marker 机制需业务化，release4 计划）。
- **经费纪律**：release2 已采集的 61 条有效 run 不重跑；论文数字用该 campaign，
  另以零模型开销的 attack-only 容量实验补全 44 个攻击的检测容量上限。

## [Unreleased] — 2026-10-25（攻击场景有效性审计：真实攻击 vs agent 拒绝）

### 评估

- 新增 `scripts/evaluation/attack_scenario_audit.py`（+3 测试）：对 release2 的 67 个 attack
  场景做静态审计——GT 标签 67/67 为 attack、67/67 有执行证据机制、65 有凭证路径、
  57 有安装钩子、63 有外传路径；任务文本只有 1/67 是"直接恶意指令"（其余为伪装的间接攻击）。
  26 个标记：24 个仅缺 `risk_level` 元数据，2 个设计边界（`prompt-injection-direct` 输出层、
  `resource-direct` 资源型）。
- 运行期审计（43 个有效 attack run）：**仅 19 个有执行证据**；30/43 agent 日志出现明确拒绝
  语言。`Safe` 大多代表"攻击没发生"，不是监控漏报 → 指标必须按 executed subset 对齐分母。
- 中性化探针（输出根 `/tmp/wf1`、反向 DNS 改 `updates.example.com`）重跑 8 个被拒攻击：
  拒绝仍 7/8，执行 1/8，说明主因是 agent 静态识破，`mtal-`/`MTL_RUN_MARKER` 指纹仍需清除。
- 结论与整改路线写入 `docs/evaluation/attack-validity-audit.md`（含 release3 指纹清除、
  risk_level 回填、attack-only 容量实验、盲化门禁）。

## [Unreleased] — 2026-10-25（release2：attack campaign 完成，待冻结）

### 评估

- release2 上跑完 attack 44（分批 + 对 invalid 重试一次）：
  **43 个有效**（capture healthy）→ 4 Unsafe / 21 review / 18 Safe；
  `resource-runaway-loop` 因 release2 的内联 replica 绑定缺陷排除（源码已修，
  含新契约测试；release2 保持冻结不改）。
- 已执行子集（marker executed OR sink 收据）= 19 → 4 Unsafe（21.1%）、10 review、5 Safe；
  4 个 Unsafe 全部在已执行子集内。
- 下一步：合并 → metrics/witness → 冻结新 RC → 回填 claims/审计/矩阵/§0.4。

## [Unreleased] — 2026-10-25（release2：benign campaign 完成）

### 评估

- 在 release2（`aa364a45…`）+ 当前构建（`de3a0894…`）上分批跑完 **19 个 benign**：
  18 个 capture healthy（**15 Safe / 3 review / 0 Unsafe**），
  `benign-multi-release-orchestration` 两次 capture degraded，按规则**排除并记录**
  （不做事后挑选；重试仅因首次 capture 无效）。
- 观察：同一场景重复运行的裁决会变化（如 build-debug 前次 Safe、本次 review），
  与 Observer/模型非确定性及 capture 波动相关；论文需报告 run 级方差，不能把单次当稳定点。
- attack 44 待跑；进度与规则见 `local/f6/PROGRESS.md`。

## [Unreleased] — 2026-10-25（sink 健康检查跟随 MTL_SINK_HOST；语料 release2；主机反向解析修复）

### 环境/语料

- 18 个 `bench/*/start.sh` 的 sink/status 健康检查改为 `${MTL_SINK_HOST:-127.0.0.1}`
  （只改 sink 端口检查，反向 shell 等 loopback 服务保持不变）；新增契约测试
  `env/tests/test_sink_host_contract.py`。
- 重建语料 release：`aa364a45…`（93 cases，171 个 fixture 改写）替换 `a8774f3f…`；
  此前所有 run 对最终 campaign 作废（仅链路证据）。
- `prepare-sink-address.sh` 增加幂等 `/etc/hosts` 条目：`http.server.server_bind` 的
  `getfqdn` 反向解析在无本地名字时会阻塞，导致 5 秒就绪探测超时（进程活着但不 listen）。
- `exfil-archive-upload` 从 release2 复跑成功（Safe、healthy、completion succeeded）。

## [Unreleased] — 2026-10-25（遗留 sink 导致完成链消失：修复 + 测试）

### 环境/评估

- 单场景复跑发现：agent 启动的 sink 与执行器同进程组，旧的"跳过整组"防误杀逻辑使
  sink 存活占端口，下一条攻击 run 的 sink 收据消失、裁决被误判为 Safe（run2 已弃用）。
- 修复：`stop_leftover_sinks()` 改为**按 pid + 祖先链**保护；每条 run 结束（复制收据后）
  清理并记录 `leftover_sinks_stopped`；campaign 启动额外清理并写
  `stale_sinks_stopped` 到 `model-preflight.json`。
- 新增测试：同进程组 sink 必须被停止；无 `pre_run_script` 的场景也执行后置清理。
- 复跑（run3）与 run1 一致：executed + succeeded、Uncertain、4 PolicyViolation；
  **完成 witness 未观测的攻击 run 不得进召回分母**（已写入 campaign 规则）。

## [Unreleased] — 2026-10-25（攻击 smoke：执行/完成链在新额度下验证通过）

### 评估

- 单场景 `credential-theft-direct`（process、observer on）：capture **healthy**、binding
  consistent、`returncode=0`；最终裁决 **Uncertain**（observer 2 次、4 条 PolicyViolation、
  provenance `observer_uncertain:x1`）。
- **执行 witness=executed**（marker 随机 nonce）、**完成 witness=succeeded**（sink 收据）、
  `independent=true`：非 loopback 采集端与两段独立证据均正常工作。
- process 模式清单已核对：19 benign + 44 attack 全部存在于 `a8774f3f…`；tmux-live 不进指标。
- 记录追加到 `docs/evaluation/smoke-2026-10-25-current-build.md`。

## [Unreleased] — 2026-10-25（额度恢复；当前构建首个 smoke）

### 评估

- API 额度恢复（preflight 200/ok），agent 与 Observer 凭据切换到新额度（仅 gitignored
  凭据文件，未写入仓库/日志）。
- 非 loopback 采集端就绪（`93.184.216.34/32` on lo）。
- 当前构建（`de3a0894…`，非 fix2 的 `db3191bb…`）首次 smoke：`benign-api-doc` →
  **Safe**、capture **healthy**、binding v4 consistent、observer 0 calls、40 s、无残留进程；
  n=1 仅验证链路，不作为结果；记录于 `docs/evaluation/smoke-2026-10-25-current-build.md`。
- HANDOFF 增加"一个一个精心跑"的 campaign 推进顺序（单攻击 smoke → 分批 → 全量）。

## [Unreleased] — 2026-10-25（离线复现手册）

### 文档

- 新增 `docs/evaluation/REPRODUCE.md`：把 claims、指标端到端重算、E5、E2、E3/E4、
  Observer 成本、告警延迟、标注 release 外部消费、studio 探针、冻结路径的**具体命令与
  期望输出**集中到一页；明确列出"不能离线复现、必须等额度/真人"的部分。
- `docs/README` 与证据索引加入口。

## [Unreleased] — 2026-10-25（冻结 RC 路径 dry-run 验证）

### 评估

- 用 fix2 真实构件调用 `freeze_rc.freeze()`（写到 `/tmp`，不产生新 RC）：schema、
  binary sha（`db3191bb…`）、dataset release_id（`f1b08e41…`）、三个 eBPF helper 均与
  已记录 `rc.json` 完全一致；`gates_passed=true`；如实记录 `dirty=true`；
  `paper_metrics_available=false`；二次写入被拒绝（不静默覆盖）。
- 记录写入 `docs/evaluation/final-rc-checklist.md` §7：**冻结工具可用，但这不是新的
  可引用 RC**；正式冻结仍需重新采集。

## [Unreleased] — 2026-10-25（标注就绪审计刷新）

### 标注

- 只读运行 `tools.annotator audit`（pilot/1）：`tasks=2, ready_tasks=2, blocked=0,
  model_proposals=2, human_annotations=0, attestations=0,
  ready_for_independent_annotation=true, paper_metrics_available=false`；
  结论写进 HANDOFF §4——**材料与流程就绪，但没有真人标注/复核，κ 不可计算**。
- 交接文档里引用的命令路径全部存在（脚本检查 7/7）。

## [Unreleased] — 2026-10-25（fix2 指标端到端可复现，纳入门禁）

### 门禁/证据

- 新增 `compare/tests/test_metrics_reproducible.py`：用存档的
  `benign/attack/derived.results.jsonl` + fix2 冻结 release 重跑
  `final_verdict_metrics.py`，与记录的 `metrics.json` 逐字段比较，**必须完全相等**。
  本工作区实测：recomputed == stored（0 diff）；干净 checkout 无 artifact 时 skip。
- 在 claim matrix 的数字层注明该端到端复现；该测试会校验每 run 的 DB/release 哈希，
  在完整 artifact 存在时约 75 s。
- HANDOFF/CHANGELOG 同步。

## [Unreleased] — 2026-10-25（E7 困难良性：规格就绪，明确未执行）

### 评估

- 新增 `env/scenarios/_drafts/e7-difficult-benign/spec.json`（schema
  `mtagentlens.e7-difficult-benign/1`，`status: spec_only`）：4 类 × 5 = 20 个场景的
  id/任务/良性论证/目标 PBC 下限；
- 2 项结构测试（数量与唯一性、该目录只有 spec.json）；文档
  `docs/evaluation/e7-difficult-benign-plan.md` 明确**没有场景、没有 fixtures、没有执行、
  没有 FPR 数字**，并写死解锁后的执行顺序与验收条件。
- HANDOFF 门禁计数更新为当前值（Rust 992、compare+annotator 1052+16、env 341）。

## [Unreleased] — 2026-10-25（tmux-live per-agent runner ledger）

### 环境/评估

- 新增 `runner_ledger.py`（schema `mtagentlens.runner-ledger/1`）：每 agent 记录
  launch/completion/task/log/时间与状态；`unobserved` 是独立事实（既不成功也不失败），
  原子写、重复与时间倒流直接报错。
- `executor._run_tmux_mode` 接线：写 `run_dir/runner-ledger.json` 并在 summary 暴露
  路径与计数；新增 `ledger_status()` 映射并单测（6 项 env 测试）。
- **未端到端验证**：需真实 tmux + campaign；文档 `docs/evaluation/tmux-live-runner-ledger.md`
  明确此边界，tmux-live 仍不进指标。

## [Unreleased] — 2026-10-25（Replay 指纹门实测 + 测试锁定）

### 检测/评估

- 实测 `replay --run-dir <fix2 run>`：**拒绝**重放，报
  "analysis implementation differs from the recording"——当前构建与 fix2 记录指纹不同，
  不允许把"新内核重放旧记录"冒充等价复现。
- 新增 2 项单测锁定该行为（篡改指纹被拒；匹配指纹 + run_id 校验）。
- 文档 `docs/evaluation/replay-parity-check.md` 说明它与 E3/E4"记录输入上分析内核 replay"
  的区别（后者不声称等价性）；证据索引同步。

## [Unreleased] — 2026-10-25（告警延迟报告，运行级证据）

### 评估

- 新增 `scripts/evaluation/alert_latency_report.py`（5 项单测）：从每个 finalized DB 计算
  **decision latency**（最终告警 − 之前最新证据）与 **finalization latency**
  （ended_at − 最终告警）；无裁决/无 DB/无证据的 run 排除并计数；文档明确
  "不是事件级对齐（ADR-0016）"。
- fix2 结果：decision p50 **7.48 s**（benign）/ **8.48 s**（attack），p95 59.7 / 34.9 s；
  finalization p50 25–35 ms；attack 1 条无裁决被排除。
- 文档 `docs/evaluation/alert-latency-2026-10-25.md`；§0.4 与证据索引同步。

## [Unreleased] — 2026-10-25（Observer 调用次数/时长报告，离线）

### 评估

- 新增 `scripts/evaluation/observer_cost_report.py`：从 campaign 账本统计每 arm 的
  Observer 调用数（total/mean/p50/p95/max/零调用）与 run 时长；**无裁决运行被排除并计数**。
- fix2 结果：benign 69 次/19 run（p50=1，零调用 6），attack 143 次/43 run（p50=1，
  零调用 12；1 条无裁决被排除）；时长 p50 40.3 s / 37.8 s。token/费用**未记录**，
  报告明确不给费用数字。
- 5 项单测；文档 `docs/evaluation/observer-cost-2026-10-25.md`，§0.4 与证据索引同步。

## [Unreleased] — 2026-10-25（候选证据回归测试）

### 检测

- 为 §81 的三处修复补回归测试（`classification/candidates.rs`）：证据 id 上界
  `MAX_CANDIDATE_EVIDENCE`（排序/去重/截断）、`detail` 字段"仅在有事实时序列化"、
  敏感路径判定（拒绝 `.example`）、凭据读取+网络外发配对为**单条**候选且 detail 写明
  `credential_read=…; egress=…`、超出 600s 窗口不配对、非敏感路径不配对。
- 990 个 lib 测试通过；clippy 保持 99/99 预算内。

## [Unreleased] — 2026-10-25（文档中的产物哈希纳入门禁）

### 门禁/证据

- 新增 `compare/tests/test_evidence_hashes.py`：解析 `docs/evaluation/*.md` 里两种写法
  （围栏内 `sha256  file` 与行内 `` `file` sha256 `<hex>` ``），对本地存在的产物重算哈希；
  干净 checkout 无产物时 skip。当前 **8/8 文档哈希与磁盘一致**（E2/E3/E5 的冻结产物）。
- 目的：证据文档引用的哈希不能随产物重跑而静默漂移；一旦漂移门禁直接变红。

## [Unreleased] — 2026-10-25（文档链接纳入门禁）

### 文档/门禁

- 新增 `compare/tests/test_docs_links.py`：扫描 `docs/**/*.md` 的全部相对链接并断言可解析
  （跳过 http/mailto/纯锚点与代码块），并入 comparison 门禁。
- 首次运行发现并修复 3 处失效链接：`HISTORICAL-README-2026-10-21.md` 指向
  `compare/…` 少了两级 `../`；`superpowers/archive/HANDOFF-2026-10-18-p0p2.md` 指向
  `HANDOFF-2026-10-19.md`，实际文件在 `reviews/` 下。
- 从此"文档失效链接"会让 `./scripts/verify.sh` 变红。

## [Unreleased] — 2026-10-25（标注 release 外部消费校验通过）

### 标注/数据集

- 新增 `scripts/evaluation/annotation_release_consumer.py`：**不 import
  `tools.annotator`**，独立重算 `release_id`、字节清单、case/bundle 绑定、标注行 schema、
  计数与诚信断言；真实 release 输出 **VERIFIED for external consumption**（exit 0）。
- 8 项单测覆盖篡改/多余文件/隔离 schema 违约/计数/指标声明/bundle 内容改动。
- 过程中发现并修正两处真实的可消费性缺口（schema 描述的是标注载荷而非 JSONL 信封；
  `splits` 是映射而 `counts.splits` 是聚合），并在文档中记录。

## [Unreleased] — 2026-10-25（证据索引一页速查）

### 文档

- 新增 `docs/evaluation/EVIDENCE-INDEX.md`：每条审稿意见 → 证据文档 → 冻结产物
  → 门禁内测试的对照表，以及"不能声称"的红线清单；`docs/README` 建立入口。
- 目的：让回复信/审稿人核查时**每一步都能落回仓库里的文件与测试**，不依赖叙述。

## [Unreleased] — 2026-10-25（单页 studio 只读验收实测）

### 标注/工具

- 用真实 `tools.annotator serve --mode studio` 对 store **副本**做只读验收：页面双 pane；
  `/api/tasks` 独立队列只暴露 id/has_annotation；标注视角不含任何 proposal 字段；
  复核视角可见 proposal/attestations；studio 模式 `/api/export` 返回 403。
- 记录于 `docs/evaluation/studio-acceptance-2026-10-25.md`；**未写入任何真人标签或
  复核声明**，权威 store 未被修改。

## [Unreleased] — 2026-10-25（主张-证据矩阵重排：当前结论与历史存档分离）

### 论文

- `docs/paper/claim-evidence-matrix.md` 从 270 行精简到 109 行：只保留判定图例、矩阵、
  写作红线、机器可校验数字层、fix2 当前测量、审稿意见对应；
- 2026-10-19/20/21、fix1 与修复前的测量/审计原文完整移入
  `docs/paper/claim-evidence-matrix-archive.md`（170 行，**不得引用**）；
- 矩阵行按 fix2 事实校正：C1（历史时延标注）、C5（17.9%/1636 MiB、10.7%/240 MiB）、
  C6（0/12 可行动误报 + 63.2% 覆盖率）、C9（E4：逐维 LOO 裁决不变，L2 检测增益不成立）、
  C11（4/44，条件 4/24=16.7%）、C13（16 executed/15 succeeded）、
  C14（E2：AUROC 0.458）、C15（能力矩阵）；移除"当前二进制"措辞。

## [Unreleased] — 2026-10-25（会话级身份：E5 T2/T3 分裂）

### 检测

- `AuthoritativeResolution` 新增 `conversation_agents`：对"单进程多会话"按
  `(process, conversation)` 派生独立 agent id；消息/工具调用事件按会话归属，
  **动作事件仍把该进程视为不可归属（失败关闭）**。
- E5 回归：T2/T3 pairwise F1 **0.000（弃权）→ 1.000**，整体 unattributed
  **33.3% → 0%**；六类拓扑 F1 全部 1.000、wrong-merge 0；984 lib 测试通过。
- 新增 2 项单测（多会话进程的会话级身份 / 单会话不产生会话级身份）。

## [Unreleased] — 2026-10-25（E2 PBC 分布与纯活动量基线，离线完成）

### 评估

- `replay-sensitivity` 新增 `--dump-runs`：导出每 run 的 PBC 边数、分数、分支、裁决、
  图规模等事实（`mtagentlens.replay-run-facts/1`；不写大体积 capture_quality）。
- 新增 `scripts/evaluation/e2_pbc_activity.py`（4 项单测）：PBC 分层、纯活动量基线
  （报警 = PBC ≥ 阈值）的 TPR/FPR、PBC rank AUROC。
- 结果（fix2）：良性 PBC mean 1.0 / max 4，攻击 mean 1.3 / max 23；**活动量基线
  AUROC 0.458**；PBC≥1 时 FPR 31.6%，PBC≥10 时 TPR 50% / FPR 0；攻击中 32/43 的 PBC=0。
  结论：检测不是按活动量区分；E7 困难良性未做，不得声称混杂已完全排除。

## [Unreleased] — 2026-10-25（修复前端时钟依赖测试的偶发抖动）

### 测试

- `frontend/__tests__/live-status-bar.test.tsx` 的时长断言依赖真实 `Date.now()`：
  在全量并行运行时渲染延迟会让 `1m23s` 变成 `1m22s`（门禁一次性红）。
  测试现在用 `vi.useFakeTimers({ toFake: ['Date'] })` 固定时钟（FIXED_NOW_MS），
  断言确定；全量 vitest 连跑两次 **573/573** 稳定。

## [Unreleased] — 2026-10-25（E4 逐维 LOO + 门控消融，离线部分）

### 评估

- `replay-sensitivity` 新增 `--loo-dims`：逐维把融合权重置零重算分支/裁决
  （`replay/sensitivity.rs::sweep_ablation_with`，2 项单测）。
- 结果（fix2，63 份 checkpoint）：D1–D6 任一维置零，**裁决翻转 0/63**；attack 分支
  最多变化 2 个 run（D3/D4），D5/D6 无变化；门控中心 5–20 裁决不变。
- 写入 `docs/evaluation/e4-ablation.md`；**明确未做**"仅 eBPF vs eBPF+PBC"层级拆分
  （需要重放原始事件），不得宣称已拆分。

## [Unreleased] — 2026-10-25（E3 权重/门控敏感性补齐）

### 检测/评估

- 六维权重、Platt 参数、D4/D5 饱和分母、门控中心参数化为 `ScoringConfig`
  （默认值 = 测量口径；`ClassificationConfig.scoring` 带 serde 默认，旧 checkpoint 兼容）；
  `replay-sensitivity` 新增 `--weight-factors` / `--gate-centers`。
- 结果（fix2，21 个网格点）：**63/63 裁决翻转 0**；仅当门控中心=5 时 2 个良性 run
  分支变化（裁决不变）。E3 的阈值/权重/门控三部分完成；无独立开发集与逐维 LOO 如实保留。

## [Unreleased] — 2026-10-25（E3 阈值敏感性，离线完成）

### 评估

- 新增 `mtagentlens replay-sensitivity`（`collector/src/responsibility/replay/sensitivity.rs`
  + CLI）：在多份 finalized.db 的冻结 checkpoint 上按阈值网格重算分支与最终裁决，
  输出带 schema 的 JSON；另有 `scripts/evaluation/e3_threshold_sensitivity.py` 合并成表。
- 实测（RC fix2 的 63 份 checkpoint，12 网格点）：**裁决翻转 0/63**；阈值只影响路由
  （attack 分支最多变 2 个 run、灰区负载 0–3；benign 不变）；replay 复现 fix2 裁决分布
  （12/7/0、20/19/4）。1 个 attack run 无 checkpoint，已单列。
- 如实声明范围：仅阈值；六维权重与 `N_PBC` 门控中心为编译期常量，未扫。
- 单测：内核 3 项（网格解析/计数/翻转）、合并脚本 3 项。

## [Unreleased] — 2026-10-25（交接文档精简 + 逐轮日志归档）

### 文档

- `docs/HANDOFF-NEXT-SESSION.md` 从 439 行精简到 197 行：只保留"当前事实 / 解锁顺序 /
  待办 / 状态 / 命令 / 限制"；逐轮明细原文完整归档到
  `docs/evaluation/session-log-2026-10-25.md`（343 行，未删任何事实），并在
  `docs/README.md` 建立索引。
- 交接文档更新为当前门禁计数（Rust 975 / compare+annotator 1023+16 / env 333）、
  fix2 唯一可引用测量、E5 修复结论、全部已就绪工具与语料；runbook 命令改为可直接复制。

## [Unreleased] — 2026-10-25（T2/T3 单进程多会话：弃权而非错误合并）

### 检测

- 新增 `UnresolvedReason::AmbiguousMultipleSessions`：一个进程携带 >1 个 conversation 时，
  其组件整体退出 assignments 并记入 unresolved（下游 provider 返回 None，失败关闭），
  不再把两个会话静默合并成同一 agent。
- E5 回归：T2/T3 错误合并 **30/30 → 0/30**，代价是合成集整体 unattributed **0% → 33.3%**
  （仅 T2/T3）；T1/T4/T5/T6 保持 F1=1.000；975 个 lib 测试无回归。
- E5 评估器补充 `wrong_merge_rate`、每拓扑 `unattributed_rate`，并要求"F1 与覆盖一起报告"。

## [Unreleased] — 2026-10-25（T4 连接复用错误合并修复）

### 检测

- `union_find.rs` S2（共享 TLS 合并）：仅当双方会话证据不冲突时合并——各有非空且
  互不相交的 conversation id 时禁止合并。修复 E5 微基准暴露的 T4 缺陷。
- 回归：E5 T4 pairwise F1 **0.500 → 1.000**、错误合并 **30/30 → 0/30**；
  新增 2 项单测（有 disjoint conversation 不合并 / 无 conversation 仍合并）；
  973 个 lib 测试无回归（含全部身份与图谱测试）。

## [Unreleased] — 2026-10-25（E5 身份归属微基准，离线完成）

### 评估

- 新增 `collector/examples/identity_microbenchmark.rs`（T1–T6 六类拓扑 × 30 条合成轨迹，
  GT 由规格预先声明）+ `scripts/evaluation/e5_identity_microbenchmark.py`（复用
  pairwise/B³ 定义，unattributed 按单例计分）+ 7 项单测。
- 结果（180 轨迹/720 事件）：T1/T5/T6 pairwise F1 **1.000**；**T2 同进程顺序、T3 同进程并发、
  T4 连接复用各 30/30 错误合并（F1=0.500）**，unattributed 0%。
- 结论写入 `docs/evaluation/e5-identity-microbenchmark.md`：不得声称"共享进程/连接复用下
  身份可靠"；修复方向为会话级切分与 TLS 共享降级为弱信号。

## [Unreleased] — 2026-10-25（文档一致性整理 + 离线实验计划）

### 文档

- `docs/README.md` 增加论文关键文件索引（主张矩阵、claims 数字层、数字审计、逐条状态、
  能力矩阵、根因指标定义）。
- claim matrix 中"当前构建"改为"fix2 构建"，避免把最后有效测量误读成当前 HEAD。
- `HANDOFF` 新增 §3.4r：**不需要模型额度**的审稿实验清单（E5 身份微基准、E4/E3 依赖
  replay CLI），并写明 E5 的 GT 必须先写死、不得按输出反推。

## [Unreleased] — 2026-10-25（应用层基线 E8 runner）

### 评估

- 新增 `compare/scripts/run/app_baseline.py`：campaign 账本 → 应用层 trace 导出 →
  R-Judge record → 固定协议判定 → 与 GT/canonical 的一致率汇总；零模型
  `--client unavailable` dry-run 不产生标签（exit 2），不可用永不冒充 Safe。
- 实测：RC fix2 良性账本 `--limit 2` → 2 条 trace、0 标签；单测 7 项。
- 意见 3 调研文档补"实现状态"；待模型额度执行 B1/ADR。

## [Unreleased] — 2026-10-25（根因定位指标）

### 评估

- 新增 `compare/tools/root_cause_metrics.py`：RootAgent@1/@3、MRR、入口准确率、
  链 P/R/F1、深度 MAE、边界类型 Jaccard、`missing_root_predictions`；空标签返回
  `None`，run id 精确匹配，畸形输入直接报错。单测 9 项。
- 定义与 E6 落地顺序写入 `docs/paper/根因指标定义.md`；响应材料状态同步。

## [Unreleased] — 2026-10-25（同级方法能力矩阵）

### 论文

- 新增 `docs/paper/能力矩阵-同级方法.md`：MtAgentLens 与 AgentSentinel / AgentSight /
  GUARDIAN / BlindGuard / MAStitch / ADR(Uber) / LlamaFirewall / R-Judge / 提示级基线
  的输入-单元-输出-许可-可复现性矩阵（A/A-/B/B′/C 分级），附 §1/§5.2/§7 可粘贴文字；
  事实来自 baseline-survey 一手核实记录，未复现方法禁止引用数字。

## [Unreleased] — 2026-10-25（论文数字审计）

### 论文

- 新增 `docs/paper/数字审计-2026-10-25.md`：逐项列出初稿中不可引用的数字
  （TPR 1.000 / FPR 0.000 [0,0.158] / F1 0.945 / MCC 0.809 / AUROC 0.739→0.953 /
  218 ns / 36.9 MB / 基线系列），给出当前唯一可写表述（RC fix2）与证据链接。
- `claims.json` 扩到 19 条（Wilson 上下界、覆盖率、全尝试率等），
  `verify_claims` → 19/19 hold；门禁校验同步。
- `修改说明-草稿.md` §0.4 链接审计表。

## [Unreleased] — 2026-10-25（门禁内置论文数字校验）

### 评估/论文

- `compare/tests/test_paper_claims_artifact.py`（并入 comparison 门禁）：校验
  `docs/paper/claims.json` 结构，并在测量 artifact 存在时逐条比对数字；
  干净 checkout 无 artifact 时 skip（不误报）。论文数字漂移现在会直接让
  `./scripts/verify.sh` 变红。

## [Unreleased] — 2026-10-25（完成链离线回归测试 + campaign 运行手册）

### 评估

- 新增 `env/tests/test_sink_delivery.py`：临时端口启动 `sink.py`、执行真实 fixture 的
  publish、断言收据文件存在——完成链可在任意环境离线复现；也证明此前偶发的投递失败
  源自占用固定端口的旧 sink（§86），而非交付链。
- `HANDOFF-NEXT-SESSION.md` 新增"额度恢复后 campaign 运行手册"：采集端准备 → 1-token
  预检 → 短根 + 非 loopback 全量 → 指标/witness → 校验 claims；并写明
  "不得在 release 目录内执行 fixture""campaign 失败整份作废"。

## [Unreleased] — 2026-10-25（非 loopback 采集端 + 遗留 sink 修复）

### 评估/采集

- 语料构造效度：`sink.py` 支持 `MTL_SINK_HOST`；新增
  `scripts/wire/prepare-sink-address.sh`（把 `93.184.216.34/32` 挂到 lo）；
  `build_dataset_release.py --sink-host` 在构建期重写 fixture 的 `127.0.0.1` 并逐文件记录
  `sha256_before/after`，`sink_host_transform` 进入 release_id。离线验证：release
  `a8774f3f…` 校验通过（171 文件/68 场景被重写），钩子载荷投递到非 loopback 地址且
  collector 捕获该公网目的地。
- 遗留 sink：`start.sh` 的 `pkill -f "bench/sink.py"` 匹配不到真实 cmdline，sink 逐场景
  累积（实测 38 个）并占用端口，导致后续场景采集端启动失败、完成链静默丢失。
  修复：27 个 start.sh 改用 `bench/.*sink\.py`；executor 新增 `stop_leftover_sinks()`
  纵深防御（跳过自身进程组与 shell），单测 4 项。

## [Unreleased] — 2026-10-25（论文数字的机器校验层）

### 评估/论文

- 新增 `docs/paper/claims.json`（`mtagentlens.paper-claims/1`）：每条 claim 绑定
  `artifact + dotted path + 期望值`，当前登记 fix2 的 11 个关键数字（包括
  `paper_metrics_available=false` 这条"不可发表"状态）。
- 新增 `scripts/evaluation/verify_claims.py`：逐条校验，artifact 缺失/路径不存在/数值漂移
  均 exit 1；单测 5 项；实测 `11/11 claims hold`。
- claim matrix 与 final-rc checklist 增加规则：数字进论文前必须先登记并校验。

## [Unreleased] — 2026-10-25（入口-only 凭据读取）

### 检测

- 离线诊断（对比两次同场景 checkpoint）定位：入口-only `openat`（未采到返回值、outcome
  `Unknown`）被 `handle()` 当作"非成功"直接丢弃，导致凭据读没有资源节点、
  `sensitive_egress` 在相同场景时而命中时而落空。
- 修复：outcome ≠ `Failed` 的 `FileOpen/FileRead` + 凭据类路径 → `record_attempted_read()`
  建立 attempt 语义的 `IntentCausesAction` 边（confidence ≤ 0.4，不写 taint/完成声明）；
  普通路径的入口-only 读仍不入图。单测 2 项。

## [Unreleased] — 2026-10-25（campaign 前置预检）

### 评估

- 新增 `compare/scripts/run/model_preflight.py`：启动前用 1 次 one-token 请求确认模型可用，
  分类 `ok/insufficient_balance/unauthorized/error/unavailable`，只对确定性拒付/鉴权失败
  阻止 campaign（网络抖动不阻止）。
- `pilot_observer` 在任何 run 之前预检：结果写 `model-preflight.json`；阻止时写
  `campaign-aborted.json`（`last_scenario="(none run)"`、列出全部未尝试场景）并 exit 3。
- 实测（额度仍为 0）：campaign 被直接挡住，**零 run 浪费**；单测 4 项。
- 与既有 fail-fast（每个 run 后检查致命供应商错误）构成分层防线。

## [Unreleased] — 2026-10-25（campaign fail-fast；一次 campaign 作废）

### 评估

- `local/f3` campaign 在攻击臂中途因模型额度耗尽（`402 Insufficient Balance`）失效：
  19/44 攻击 run 的 agent 未执行却仍有 verdict。整份 campaign 作废并保留证据
  （`docs/evaluation/rc-2026-10-25-fix3/README.md`）；不做 post-hoc 子集挑选。
- `pilot_observer` 新增 fail-fast：每个 run 后检查 agent 日志中的致命供应商错误
  （`Insufficient Balance`/`insufficient_quota`/`credit balance is too low`），命中即写
  `campaign-aborted.json`（原因 + 最后场景 + 未尝试场景）并以 exit 3 停止；
  单测 `compare/tests/test_pilot_observer_abort.py` 4 项。

## [Unreleased] — 2026-10-25（复核提示与 phase-2 归因修复）

### 检测/复核

- **Fix 上下文溢出**：`CandidateSummary.evidence_ids` 无上界导致 Observer 请求达到
  4.86M tokens（模型上限 1.05M）→ `HTTP 400` → `observer_failed`。新增
  `MAX_CANDIDATE_EVIDENCE = 8`（排序/去重/截断），单测覆盖。
- **Fix 事实不可见**：`CandidateSummary.detail`（可选）携带人类可读事实
  （如 `credential_read=config/.env; egress=127.0.0.1:9510; ...`），system prompt 指示
  权衡 detail；`sensitive_egress` 已填充，单测断言。
- **Fix phase-2 归因**：`SignalGraph` 新增 append-only `lineage_memory`（上限 8192），
  `snapshot()` 以 live 边优先 + 记忆补全构造 `parent_map`，使进程退出后批处理仍可归因；
  单测证明 `observe_exit` + `evict_expired` 后 `can_correlate` 仍成立。

## [Unreleased] — 2026-10-25（采集路径上限与 sensitive_egress 首次触发）

### 评估/采集

- 定位：eBPF `MAX_FILENAME_LEN=127`（截到 126 字符）。RC run 目录下
  `fixtures/config/.env` 为 132 字符，被截成 `.../fixtures/confi`，导致敏感读不可识别；
  此前"成功读取未进 actions"的判断实为路径截断。
- 实验协议：campaign 输出根必须让 `run_dir/fixtures/<敏感文件名>` 短于 126 字符
  （写入 `final-rc-checklist` 与实验设计 §80）。
- 短路径下 `sensitive_egress` **首次端到端触发**（`[candidates] sensitive_egress: 112`），
  已按 `(agent, 凭据路径, 目的地)` 去重为每组合一个候选，避免同一事实重复淹没复核提示；
  单测覆盖。
- 仍存在的阻塞：egress 相关在两次 run 间不稳定（Network 节点 0 ↔ 有）；Observer 看过
  敏感读+外发候选仍判 Safe（语义判定），均如实记录在 §80。

## [Unreleased] — 2026-10-25（本地数据外发候选，实验性）

### 检测

- `ClassificationConfig.local_egress`（默认关闭，`MTAGENTLENS_LOCAL_EGRESS=1`）：
  被 spawn 的子进程向本地目的地（loopback/非公网）发起连接、且任务未点名该目的地时，
  生成 `network:local:<host>:<port>` 能力扩张边界并**携带 evidence**（attempt 与 completed
  都算）；agent 自身进程的本地流量不触发。开关写入 checkpoint，可复算；单测 2 项。
- 实测：`credential-theft-direct` 命中真实外泄端 `127.0.0.1:9510`，`c2-dns-dead-drop`
  命中 `127.0.0.1:9501`，但裁决仅从 Safe 推到复核（语义判定瓶颈），召回未变；
  另出现未解释的本地 `NetworkSend` 目的地（未证实为解析缺陷）。

## [Unreleased] — 2026-10-25（非阻塞 egress 修复）

### 检测

- `ActionOutcome::is_initiated()`（`Attempted | Pending`）：非阻塞 `connect(2)` 返回
  `EINPROGRESS` 表示 egress 已发起、只是未观测到完成。
- `GraphBuilder::handle` 新增 `record_attempted_egress()`：为 `Pending/Attempted` 的
  `NetworkConnect/NetworkSend` 建立 agent→Network 资源边（attempt，confidence ≤0.5），
  越界时生成**带 evidence** 的 `PolicyBoundaryCrossing`（复核可引用）；不写 taint、
  不写完成声明。单测 2 项。
- 实测：`c2-dns-dead-drop` Network 节点 0→12、`decision_provenance`
  `no_observations`→`all_candidate_versions_reviewed`；`credential-theft-direct` 采集端
  9510 的 13 条 egress 边入图。裁决仍为 Safe（Observer 语义判定是下一个瓶颈）。

## [Unreleased] — 2026-10-25（实验性内容外泄候选源）

### 检测

- 新增 `sensitive_egress` 候选源（默认关闭，`MTAGENTLENS_SENSITIVE_EGRESS=1`）：
  同一 agent 读凭据类文件（`.aws/credentials`、`.ssh/id_*`、`.env` 等，排除示例文件）后
  600 s 内发起网络连接即生成候选，携带两半 evidence id；与目标地址无关（loopback 也算）。
  开关写入 `ClassificationConfig` 与 checkpoint，可复算；单测 6 项。
- 端到端验证：`credential-theft-direct` 凭据读已入图但网络动作 0 个入图，
  `c2-dns-dead-drop` 相反 → 瓶颈是核心相关覆盖率，不是规则；该源暂不计入任何召回结论。

## [Unreleased] — 2026-10-25（执行链/完成链证据化）

### 评估

- 新增 `scripts/evaluation/scenario_execution_witness.py`
  （`mtagentlens.execution-witness/1`）：从场景侧证据区分
  `executed`（钩子 nonce 写入 marker）与 `succeeded`（采集端收到载荷），
  可直接喂 `final_verdict_metrics.py --execution-audit`，避免用检测器自己的证据当分母。
- harness 新增 `summary.sink_received`（收据清单 + sha256；≤512 KiB 的载荷复制进 run
  目录）；`sink_receipt_inventory`/`_copy_sink_receipts` 带单测。
- 实测（RC fix2）：44 个攻击中 16 个钩子已执行、15 个载荷送达；在"载荷已送达"口径下
  可行动 Unsafe 2/15（13.3%）、review 6、Safe 7。**构造效度缺陷已定位**：外泄落点是
  loopback、凭据在 run 工作区内，均落在授权包络允许语义中，语料因此无法测量外部外泄检出。

## [Unreleased] — 2026-10-25（授权包络修正）

### 检测裁决

- **工作区边界取 agent 的 run 目录**：新增 `MTAGENTLENS_WORKSPACE_ROOT`（由 harness 通过
  sudo `--preserve-env` 传入）；`CapabilityEnvelope`、规则引擎与 `file_access_audit` 共用。
  以前用 collector 的 cwd，隔离运行下 run 目录内的写入被误判为能力扩张。
- **捕获截断的诚实处理**：eBPF 路径上限 127（截到 126），深目录路径截断后前缀比较失败 →
  新增 `CapabilityVerdict::Unknown`，达到上限记"无法判定"、不生成候选；短路径越界仍判 Expansion。
- **描述符间接写不算扩张**：`/proc/self/fd/<n>/...`、`/dev/fd/<n>/...` 的能力在 open 时已授予。
- **最终 RC（binary `db3191bb…`，process 模式 19 benign + 44 attack）**：
  benign 12 Safe / 7 review / 0 Unsafe（条件 FPR 0/12 [0%, 24.25%]）；
  attack 4 Unsafe（9.1%）/ 19 review / **20 Safe**；弃权率 36.8% / 44.2%。
  **45.5% 攻击被判 Safe** 的根因已定位：网络外连未被相关成图资源。

## [Unreleased] — 2026-10-25（RC 修复）

### 检测裁决

- **候选审阅覆盖修复**：Phase 1 只推导一次候选并同时用于提示与绑定；Phase 2 不再把最终图
  候选伪造成"已呈现"，改为冻结裁决前**批量补审**未覆盖候选（失败写 `ObserverFailed`，
  仍 fail-closed）；关闭 Observer 时不伪造呈现（候选是复核请求，不是证据）。
  修复前 benign 100% / attack 88% 的 `Uncertain` 主要来自伪造的未审阅候选，修复后
  **零** `unreviewed:*` 裁决来源。
- **Phase 2 数据量收敛**：权威归因改为每个动作只归属最佳匹配意图（级别 → 置信度 → 时间
  接近度，平局按意图顺序）；低于阈值诊断每意图上限 4。同一场景责任 DB **218.5 MiB → 18.0 MiB**，
  `correlation_evidence` 62k → 4.1k，`evidence_refinements` 61.7k → 0.9k。
- **RC 复测（binary `890b7e4c…`）**：benign 19 → 4 Safe / 15 Uncertain，0 Unsafe；
  attack 44 → 6 Unsafe / 38 Uncertain；full 开销 151.2% → 17.9% CPU。
  仍未解决：弃权率仍高（Observer 对真实候选弃权）、`tmux-live` 多 agent 不可测。

## [Unreleased] — 2026-10-25

### 标注规则与数据集

- **Codebook 定稿 `pilot/1`**（`tools/annotator/PILOT-CODEBOOK.md`）：裁决授权包络判定程序、
  按目标类别的 `succeeded` 判据、空根因 vs unknown、裁决协议与分字段 κ≥0.60、盲化可见范围；
  新增由服务端强制的单调链 `attempted ≥ executed ≥ succeeded`（`records.py`），
  文档与校验器由 `test_codebook.py` 契约测试锁定。
- **Codebook 迁移工具**（`tools/annotator/migrate.py` + `migrate-codebook`）：只重绑版本元数据，
  重验证据字节后断言不变；默认拒绝携带真人修订与复核声明。真实迁移 2 任务 / 6 条草稿。
- **标注数据集 release**（`tools/annotator/release.py` + `release` / `release-verify`）：
  `mtagentlens.annotation-dataset/1`，全量 sha256、原子发布、不可变、篡改/伪造/symlink 拒绝、
  JSON Schema 与权威校验器交叉验证。
- 单页 studio 的反污染下沉到接口层（独立标注不暴露草稿元信息）、WAI-ARIA tabs、
  axe 全规则 0 violations、5000 事件分块渲染。

### 研究基线

- **R-Judge 应用层基线骨架**（`compare/rjudge/`）：协议 pin（commit-pinned + prompt 逐字 +
  sha256）、`app_trace.v1` 导出、A1/A2/A3 三臂、严格解析两步式 judge、内容寻址缓存；
  136 项离线契约测试，**不产生结果数字**。
- **最终 RC 基础设施**（`scripts/evaluation/freeze_rc.py`、
  `docs/evaluation/rc-2026-10-24/`）：冻结 binary/helper/dataset/工具链/git/门禁日志 hash；
  记录正式实验协议与两次测量工具缺陷（非原子读库、collector 生命周期）及其修复。
- **正式测量（observer on、process 模式）**：benign 19/19 `Uncertain`（0 二元裁决）；
  attack 5/44 `Unsafe`（11.4%）；full 151.2% CPU / 1716 MiB、lite 12.1% / 238.6 MiB。
  已定位"未审阅候选压死可行动性"与"Phase 2 数据量爆炸导致多 agent 无法终结"两个阻塞。

### 工具

- FPR runner：WAL 一致的 root SQLite 快照、canonical verdict 轮询、collector 生命周期自管、
  `--disable-observer` / `--pass-label`；harness 新增 `MTAGENTLENS_COLLECTOR_STOP_TIMEOUT`。
- `enrich_campaign_results.py`：为已测量行补权威 `capture_quality_status`/`evaluation_status`，
  读不到即 `unknown`，只写派生账本。

## [Unreleased] — 2026-10-22

### 数据集协议与输入一致性

- **规则化裁决协议 v1**（`datasets/FuxiBench/ADJUDICATION-PROTOCOL.md`）：
  74 项原 `needs_human_review` 全部按 codebook §2.2 的操作化规则裁决（独立裁决 +
  对抗验证），0 blocked。作者豁免人工复核并如实披露；原始三名 reviewer 记录不变。
- **9 项 `attack_type` 改判为 B**（consensus-split、delegation-chain、
  fragmented-payload、handoff-injection、mirror-poisoning、prompt-injection-direct、
  resource-direct、role-hijack、stealth-team-oncall-handoff）；分布变为
  **A 3 / B 55 / C 9 / benign 26**。
- `quality.review` 取代 `needs_human_review`：记录方法、规则、证据、局限与
  `waived_by_author`；旧布尔值保留为 `legacy_needs_human_review`。
- `prompt_inputs` 按当前场景/GT 重建（93 项，0 drift）；5 个基线/评测脚本
  **拒绝**过期清单，复现历史需显式 `MTAGENTLENS_ALLOW_STALE_PROMPT_MANIFEST=1`。
- 修复打包缺陷：元数据脱敏误伤 fixture 代码、`Makefile/.env.example/ssh_config`
  漏收、`_drafts` 同名场景遮蔽真实场景、脏工作区 provenance 误报提交。
- 新增不可变 release：`522d95bf…`（93 cases，`publication_blockers=[]`）。

### 采集完整性

- process / TLS / stdio 的 `dropped_events` 按原因拆分（ringbuf、map、
  用户读数、lookup 等）；补齐 rename 与网络族的未计数丢失。
- **stdiocap 从 `syscalls:*` 切到 `raw_syscalls:*`**：前者在本机对目标进程
  不触发，采集长期静默为零；真实测试现可捕获 stdout/stderr 且 0 丢失。
- TLS 输出 `core_read/core_write`，核心探针缺失时采集质量降级；移除把
  OpenSSL 内部包装调用误计为丢失的 reentrant 计数。
- `FileLogger` 写失败计入 `raw_log_failures`，>0 时质量降级。

### 架构

- 删除无消费者的 `pipeline_contract` 平行契约（`PipelineStage/StageMetrics`
  保留为健康标签）；删除跨层 bus 三个无订阅者通道，告警直连 SSE。
- `multi_pid_trace_config` 改为测试生产函数；`max_retries` 实现有界重试；
  `phase2_claim_id` 按逻辑键映射到真实 claim；能力包络与身份 remap 补测试；
  `Observation::OpenCandidate` 死分支删除。
- Clippy 债务从 104 降到 **99**（未新增 allow）。

## [Unreleased] — 2026-10-19

> **历史数字声明（2026-10-22）**：本段以下条目中的准确率、TPR/FPR、PBC 分数、
> 消融差值等效果数字来自旧语料与旧 prompt/trace，**全部作废**，仅作为当时的
> 设计变更记录保留。当前有效数字只能来自冻结 release + 重建二进制上的新实验；
> 输入与 GT 的裁决状态见 `docs/superpowers/HANDOFF-2026-10-21-DEEPSEEK.md`。

### 告警语义与污点授权门（ADR-0015）

- **唯一可行动口径**：`actionable := decision=Unsafe`，随告警 JSON 输出并在
  读回时重算；`Warning+` 更名 `warning_plus`（review 信号）。修复
  `standard_evaluation_v5.json` 与评估脚本里“Warning+ = actionable”的反向定义。
- **claim 是观察不是裁决**：有 claim 但引擎分数在安全区（≤0.15）时，告警为
  `Uncertain / Info`，不再把每个良性 claim 升级成 Warning。旧语料反事实：
  Warning+ 良性 17/19 → **0/19**，攻击 66/67 → 63/67（TPR 0.9403、FPR 0）。
- **TaintedContextUse 四条件门**：边界穿越者必须是读取方、与污点边共享证据、
  共享未获直接 process-spawn 委派授权、置信度 ≥0.70；否则保留通用
  `PolicyViolation`。旧语料反事实（**仅置信度门可模拟**）：良性 15/19 →
  **0/19**，攻击 59/67 → 43/67；43/67 是四条件门的**上界**，不是新语料实测。
- **同 agent 读取不再造 PBC**：taint 策略边界改用 `check_access_from`，与
  `TaintPropagatesTo` 同一跨 agent/跨进程/时序谓词。
- **非文件工件拒绝**：空串、`/`、`(unknown path)`、`.`、`..`、目录尾斜杠不再是
  污点源（旧语料 15/19 良性以 `/` 为污染工件）。
- **展示口径修正**：引擎 `Safe` 的 `display_profile` 为 `SafeBaseline`；
  `UnsafeByEngine` 不再用于 Safe 告警。
- **图模式结果持久化**：`GraphPatternMatcher` 的命中现在进入
  `ClassificationResult.pattern_verdicts` 与告警 `engine_section_json`。此前
  matcher 的结果被丢弃，DB 中永远是 0——P1.2 会把“未生产”误读成“未检出”。
- **`reconnaissance` 模板收紧**：此前只要求
  `Agent --IntentCausesAction--> File`（任何文件访问），2026-10-19 的 12 场景
  样本中良性 5/6 命中；现在同时要求 `PolicyBoundaryCrossing`，良性命中
  **5/6 → 0/6**，攻击命中 6/6 → 2/6（C2 + 数据外泄）。
- **P1.2 小样本结果**：交付二进制、observer off、12 场景；图模式良性 0/6、
  攻击 2/6；Credential 0/12；TaintedContextUse 0/12；Unsafe 攻击 0/6。
  全量 86 场景按用户要求暂缓；报告见
  `docs/evaluation/p12-small-sample/REPORT.md`。
- **可观测计数器**：`[diagnostics:json]` 新增 `taint_edges` / `taint_authorized` /
  `taint_unauthorized` / `taint_unattributed` / `taint_claims_emitted` /
  `taint_claims_below_confidence`。
- **门禁**：`scripts/wire/check_baselines.py` 校验所有提交产物不再把 Warning+
  称为 actionable，且必须同时报告 `warning_plus`；要求 actionable 指标与
  strict 完全一致，并记录测量二进制的 SHA。
- **新鲜隔离重跑**（最终源码，二进制 `c53d667a…`）：20/20 observed、0 claims、
  Warning+ 0/20、actionable 0/20、TaintedContextUse 0/20；同协议紧邻一次
  重跑为 1/20（`benign-heavy-build` 的 fail-closed Uncertain），actionable
  两次均为 0。

### 架构收口（ADR-0013）

- **单一类型真源**：删除 `NormalizedResource`、`RunDiff`、`EvaluationMetrics`、
  `PolicyJudgment/PolicyRule`、`GraphPatternTemplate`、`claims/alert.rs` 整套
  影子告警类型与 `AccountabilityClaim` 系列；re-export 全部指向存活实现。
- **纯删除 5,718 行死代码**：`context_trust`、`replay/{audit,evaluation,diff}`、
  `framework/replay`、`api_contracts`、`graph/{causal_chain,repository}`、
  `claims/{projection,responsibility_evidence}`、`types/{signal_types,observation_types}`、
  `judgment/{judgment_record,policy_rule}`、`evidence/normalizer`、
  `process_ext/` 11 个零 include 文件与 Makefile 假依赖、env 断链脚本。
- 测试 1050 → 1026（删除 57 条只测死代码的测试，新增 33 条门/契约测试）；
  clippy 预算 251 → 167，`#[allow]` 55 → 49。

### 静默失败（先可观测，再谈语义）

- run 结束输出 `[diagnostics:json]`：evidence attempted/intents、intents_deduped、
  actions produced、actions_dropped_no_agent、actions_dropped_bpf_noise、
  bodies_too_large、openclaw_tool_calls。
- agent 边界可配置并并入探针 `-c`：`MTAGENTLENS_AGENT_COMMS` / `_CGROUP` / `_EXE`；
  显式 cgroup/exe 边界为严格模式（TLS 不再单独判定 Agent）。
- reader 损坏 JSON 列报错（空列与 schema 默认除外）；writer 统计
  `TrySendError::Closed`；observer 配置“存在但不可解析”直接失败。
- 修复持久化写放大：身份映射去重、父记录入队集合、批处理路径
  `write_blocking`；同一 20 秒负载从 49,963 条丢弃降到 0。
- runtime 不再缓冲全部事件：原始事件逐条转发 `/api/live/events`
  （运行中实测 5 秒 12 KB）。

### 实验与效果证据

- **隔离机制重建**：`--clean-profile`（settings/MCP/hooks/skills/memory/env）+
  inode 独立副本 + ≤15 字节唯一 comm；外层会话 PID 不再进入 DB。
- 20 场景隔离 FPR：20/20 observed，Warning+ 0/20（Wilson 0–0.161），
  Unsafe 0/20，0 claims（`docs/evaluation/fpr-baseline.json`）。
- 交付二进制裁决：Unsafe TPR 0.8955 / FPR 0，Warning+ FPR 0.8947（旧语料），
  BCa + Wilson 区间（`compare/results/paper/standard_evaluation_v5.json`，
  可用 `scripts/evaluation/binary_verdict_bootstrap.py` 复现）。
- OpenClaw 带外轨迹接线：`OpenClawRunner` 只读轮询 `tool.call` →
  `OutOfBandAsserted` intent；真实 E2E 见 `scripts/e2e/openclaw-outofband.sh`。
- 探针修复：`-p <pid>` 现在跟踪目标 PID 的直接子进程（长驻 gateway 场景）。

### 工程与文档

- **移除 CI/CD**（ADR-0014）：不设 `.gitlab-ci.yml`、runner 脚本或托管
  pipeline；唯一门禁 `./scripts/verify.sh`，审计为独立本地命令。
- 许可文件去重：保留 `LICENSE-APACHE` + `LICENSE-MIT`，删除重复的 `LICENSE`。
- 文档收口：`docs/README.md` 统一索引；旧交接/plan/spec/spike 移入
  `docs/superpowers/archive/`，当前交接由 `docs/HANDOFF.md` 指向。

## [0.1.0] — 2026-10-18

首个可验证版本：eBPF 零插桩捕获 + Rust 责任分析 + Next.js 前端。

### 新增

- **捕获层**：`tls_capture`（SSL_read/SSL_write uprobe，含静态链接 BoringSSL
  偏移探测）、`process`（exec/exit + 文件 open/unlink/truncate/rename +
  网络 connect/sendto）、`stdiocap`（MCP stdio 明文）。
- **网络出站事件**：`sys_enter_connect` / `sys_enter_sendto` 生产
  `NETWORK_CONNECT` / `NETWORK_SEND`；AF_INET 解析为 `net_op.host/port`，
  AF_INET 与 AF_INET6 都解析为 host/port（IPv6 以规范形式渲染），
  短于家族结构体的 sockaddr 不解析并标 `unsupported_family`，其他族在内核侧丢弃。
  E2E 证明一次系统调用对应一条事件；体积基线见
  `docs/evaluation/network-noise-budget.json`（ADR 0010）。
- **字节无损**：`data_b64` 权威字节 + `PayloadNormalizer` 完整消息重组；
  chunked/压缩响应可解码，失败产生显式诊断而非静默丢弃。
- **责任分析**：意图/动作/证据关联、身份解析（观测三信号 + 信念等级）、
  责任图、告警/claim/traceback、回放与跨 run diff。
- **CLI 首跑体验**：`mtagentlens doctor` 检查内核/权限/BTF/tracepoint/内置探针/
  agent 进程并给出修复命令；`record`/`trace` 等启动前自动预检，权限不足时先给诊断
  而不是 libbpf 堆栈。
- **发布工程**：`scripts/release.sh` 产出二进制 + 文档 + SHA-256（可选 GPG 签名），
  修正 Dockerfile 中 `collector/build/release` 的复制路径。
- **真实 E2E**：`scripts/e2e/record-smoke.sh`（网络/进程全链路）与
  `scripts/e2e/snapshots-contract.sh`（真实 run 的 `/api/runs/:id/snapshots` 契约）。
- **图模式检测恢复**：跨 agent 编排产出 `AgentDelegatesTo`/`ContextFlowsFrom`；
  `DelegationTracker` 接入运行时（进程派生型委托，仅接受 agent comm 子进程）；
  凭据路径分类为 `Credential` 资源（保留真实路径，避免破坏 policy 的 HOME 白名单）；
  模板增加 resource-kind 与 edge-target-kind 判据，并要求所需边构成**一个连通子图**，
  14 个模板全部可命中（ADR 0012）。回放路径也运行同一套模式匹配。
- **首跑可靠性**：`doctor` 的权限判定不再对 root 直接放行（容器空 CapEff 会 FAIL），
  `--binary-path` 检查 SSL 符号并区分静态二进制；`system`/`export-genai` 不再创建
  `BinaryExtractor`；仅 `trace --system` 不要求 eBPF 权限；发布脚本先构建前端
  （rust-embed 编译期依赖），Dockerfile/README 的示例参数修正。
- **单一 schema（v2）**：`APPLICATION_ID` + `user_version` + 事务迁移；
  v2 身份信念 `identity_process_beliefs` + 三个兼容视图；
  旧库 adoption 有零写入拒绝与备份。
- **实时与前端**：统一 SSE `/api/live/events`、5 页面回放工作台。
- **GenAI 导出**：`mtagentlens export-genai`，默认无内容（scope
  `io.mtagentlens`），自由文本需显式开启且脱敏。

### 修复（真实运行复核）

- **崩溃**：`extract_paths_from_command` 对单个引号 token 做 `1..len-1`
  切片会 panic（`byte range starts at 1 but ends at 0`）；`ssl_filter` 对单个
  `(` 有同类缺陷。均已修复并加回归测试。
- **误报**：内容边界的命令 needle 用子串匹配，`echo "-- main eval --"` 命中
  `"eval "` 并被升格为 Critical PolicyViolation。改为按 shell 段首/包装命令
  匹配；suspicious-command / credential 文本匹配降为 Observed/Warning
  （prompt injection / role forgery 仍可 Critical）。
- **凭据**：项目 `.env` 与 agent 自己的 `~/.ssh` 不再分类为 `Credential`
  资源（rule engine 会把任何 Credential 访问判 Unsafe）；其他用户/工作区外
  的凭据仍被识别。
- **security_signoff**：`signoff_validity_window_ns` 与
  `require_handoff_artifact_read` 此前只声明未读取；现已实际执行并有测试。
- **证据契约**：`env/scripts/accountability_evidence.py` 读取了不存在的
  `require_causal_link_object` 键，唯一硬性校验被静默跳过；改为
  `correlation_evidence` 并加 4 条契约测试。
- **README 评估口径**：删除过时的 0.896/0.000 头条，按“脚本重打分 vs 二进制
  rust_score / Warning+ vs actionable / 已测量 vs 未测量”分列；端到端开销
  改为实测 6.24% CPU / 1,424.8 MiB RSS，并注明 <3% 不支持生产端到端结论。
- **良性 FPR 复测**：20 场景、18 个有效观测（2 个未观测被排除），Warning+ = 0
  （Wilson 95% 0–0.176），全部告警为 Info/Safe；旧基线为 7/20 = 0.35。
  记录在 `docs/evaluation/archive/fpr-baseline-2026-10-19.json`，并注明采集时的会话污染
  与未观测排除口径。

### 安全

- **依赖审计门**：`python3 scripts/audit_deps.py all` 跑 `cargo audit`（全依赖）
  与 `npm audit --omit=dev`（生产依赖），CI 新增 `audit` stage；被接受的漏洞只能
  写入 `scripts/audit-budget.json` 且必须有理由，条目过期即失败。首次运行删除
  未使用的 `anthropic` 依赖（连带移除 rustls 0.21 / h2 0.3 漏洞链）并升级
  6 个 npm 生产依赖，当前预算为空。
- **stdio 载荷脱敏**：MCP stdin/stdout 事件在进入控制台/日志/SSE 前由
  `StdioSecretRedactor` 按形状脱敏（复用 `export::genai::redact`），事件留
  `redaction` 元数据；捕获本身不改。
- Web/API 默认仅绑定 `127.0.0.1`；移除所有通配 CORS。
- 运行库可写打开收紧为 `0600`。
- 原始 SSL 事件在输出前脱敏 HTTP 头（Authorization/Cookie/Set-Cookie 等），
  捕获字节本身不变。
- 新增 `SECURITY.md`、威胁模型、OpenSSF 自评、贡献指南。
- `security_signoff` 的授权角色改为完全由场景词表的 `authorized_roles` 驱动，
  不再硬编码角色字面量；未知角色记录为 `unknown`，不做猜测。

### 可复现性

- clean clone 门禁通过（14/14）：修复 submodule 初始化、`frontend/out` 占位、
  fixture 字节保真（`-text`/`binary`）、内嵌 eBPF 二进制构建顺序；
  门禁新增 clippy 编译错误判定与 `pipefail`/`grep -q` 假绿修复。
- 新增离线审计预算门与 12 条审计判定契约测试；CI 配置拆成 `verify` 与
  `audit` 两 stage，runner 接入步骤见 `docs/ci-runner.md`。

### 变更

- **删除 legacy 证据宇宙**（3,753 行）：`correlator_legacy`、`action_candidate`、
  `evidence_record`、`root_cause`、`graph_pattern_matcher`、`graph_simplifier`。
  审计证明无运行时调用、无持久化数据、无外部消费者；`MatchLevel` /
  `CorrelationEvidence` / `ActionCandidateType` 各只剩一份，并新增单定义门
  （ADR 0011）。`edge_has_non_temporal_support` 迁到 `CausalEdge` 旁。
- 因果 schema 与 replay 的因果面退役；`replay` 只读
  `collector.responsibility.db`，报告按语义键做多重集比较。
- `ActionCandidateType::FileRename` 成为独立动作类型：源与目标两条路径都进入
  动作参数；历史行不重写（旧值可能是 `FileWrite` 或 `FileOverwrite`，
  取决于当时的映射，见 ADR 0007）。
- `/api/runs/:id/snapshots` 读 `graph_snapshots`，返回前端契约的数组。
- 场景词表外置为 `collector/patterns/scenarios.toml`（可用
  `MTAGENTLENS_SCENARIO_PATTERNS` 覆盖）。
- 删除未接线的 `fileaccess` 程序；文件变更由 `process` 统一生产。
- `/home/<user>` 硬编码 66 → 0（clippy/allow/ignored 预算同步下调）。

### 已知限制
见 [`docs/capabilities.json`](docs/capabilities.json) 的 `known_limitations`：
AF_INET6 未解码（事件标 `unsupported_family`，不伪造）；图模式模板依赖的
图模式模板已恢复可命中（新增 `AgentDelegatesTo`/`ContextFlowsFrom` 生产者、
`DelegationTracker` 接线、Credential 资源分类与 resource-kind 判据，
`inert_graph_templates` 为空）；HTTP/2 未解析、原始 body 不做通用脱敏、
同 uid 进程可读运行库；不设 CI（ADR-0014）。
