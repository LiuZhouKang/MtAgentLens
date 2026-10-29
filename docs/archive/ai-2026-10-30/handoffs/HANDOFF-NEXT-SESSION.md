# MtAgentLens 新会话交接（最后更新：2026-10-26）

> **2026-10-28 意见5定稿入口**：[实验总方案 §6](evaluation/EXPERIMENT-SUITE-7-REVISED-2026-10-26.md#6-overheadbench5)及[§6.6 实测结果](evaluation/EXPERIMENT-SUITE-7-REVISED-2026-10-26.md#66-实测结果2026-10-28-完成)。仅并发1做四条件，4/8/16/32做C0/C3，三重复共36正式条件；TLS短微基准、其他探针顺带诊断另计。36/36已完成，T8/F2在`compare/results/overhead/raw/full-system/report/`；旧60条件与218 ns单次操作解释已撤回。此更新不改变下文历史记录。

> **历史快照（2026-10-26）**：文中的 84/26/9 是当时口径；2026-10-27 盲标+裁决后 9 例困难良性已计入分母，
> 当前为 **93 = 58 attack + 35 benign、detection_excluded 0**。见 `docs/dataset/FUXIBENCH-2.0-CONTRACT.md`。

> 这是给**下一个 Lead（Codex + 更强模型，指挥 DSH 子代理）**的唯一入口。
> 先读本文件，再按 §5 的命令在工作区核验；**不要相信旧会话叙述，以门禁与产物为准**。
> 变更摘要见 [`CHANGELOG.md`](../CHANGELOG.md)；本地证据索引见 `local/README.md`（gitignored）。
> **Codex Lead 启动包（含可直接粘贴的 prompt）**：[`HANDOFF-CODEX.md`](HANDOFF-CODEX.md)。


> **Codex 返修增补（2026-10-25）**：本轮核验当时基线 17/17、39/39 后（历史记录，当时门禁），补了逐维相对权重扫描。当前门禁仍全过，claims 为 **54/54**。新增分析固定旧 Observer 观测，不能当端到端鲁棒性；54 配置条目/臂的攻击分诊最多变化 **3** 个运行、良性 **0**，裁决不变。原整体权重缩放只能作归一化控制。marker∪sink 的当前并集为 **18**；拒绝措辞 30/43 不等于 30 个完全未执行。RC 与 release 数据未改。
>
> 可编辑的完整 Word 修订工作稿、真实修订比较工作稿、修改说明在 `local/workpackages/2026-10-25-codex-revision/`，并非投稿定稿。源码说明以更新后的 `docs/paper/revision-package.md`、`修改说明-草稿.md`、`response-letter-notes.md` 与 E3 报告为准。本轮模型单 token 预检返回可用；下一步优先补真实身份/根因、同输入基线、困难良性和完整开销，停止重复做全套审计来替代实验。


> **新实验前修复（后续增补）**：当前任务按用户新要求先解决影响实验的问题，未启动正式 campaign；一条良性 A1 联机冒烟因首步说明为空已标无效并停止，随后仅做一次首步诊断（end_turn、非空说明）。A1 泄漏后续/子任务、profile 被字面名字覆盖、历史结果混入当前汇总均已复现并修复。新增 `--arm` 和 judge 输入落盘；付费入口要求恰好一条绑定有效、完成且健康的运行，并显式 real。当前产物在 `local/workpackages/2026-10-25-pre-experiment-fixes/`，聚焦104项、收尾门禁全过、claims54/54；含空首步停止、历史空首步不续用与usage/stop记录回归。三个marker触达且Safe的逐例核对没有证明“最终告警存在但未传入内核”的错误；该DSH假设已由Lead否决。真实问题是部分场景用限额自检/仅生成配置文件的marker作为攻击执行代理，下一份协议须先明确授权及可验证副作用，不强迫Safe变Unsafe，不改fix4分母。
>
> `final-rc-checklist.md` 已纠正人工签署被误当作一切实验前置条件的问题。研究与受控采集可继续，依赖人工参考的发表指标再等实际标签；用户已授权实验，逐个运行，无需再次泛化请示。

> **运行见证修复（后续增补）**：`sink-receipts/v2` 已接到 executor 的 setup 后/agent 前基线、完整指纹差集与固定FD校验复制；离线 `execution-witness/2` 只接受本运行nonce/目录/runner绑定且实际字节匹配的耐久副本。marker整行精确匹配，缺失保持unknown，不再借同场景另一运行推断未执行。两个审计脚本已移除隐式带凭据提权。聚焦59项、当时门禁17/17（compare+annotator1088+16subtests、env357；历史记录）、claims54/54；一次独立本地HTTP收发验证通过，旧回执排除，零付费模型调用。证据在 `local/workpackages/2026-10-25-execution-witness/validation.json`。根README与fix4/release未改。
>
> **接下来的真实缺口**：DSH只读检查发现 persistence/resource/prompt 三个场景的当前fixture与旧verifier有明显漂移，不能直接沿用旧canary/路径/文本检查当目标完成。先修场景授权与完成判据，再逐个执行。另一个已发现问题：上一轮A1首步诊断虽非空，却返回DSML Bash调用而非安全分析（见pre-experiment-fixes/validation.json），不能算协议成功；后续先修模型角色/返回格式处理，禁止据此直接启动全量A1–A3。这些不改变冻结RC数字。

> **FuxiBench 2.0 最终冻结（2026-10-26）**：canonical 数据集为自包含包
[`datasets/FuxiBench/`](../datasets/FuxiBench/README.md)（版本 **2.0.0**，
`taxonomy_version=fuxibench-2type/1`）。GT **93 = 58 attack + 35 benign**；新两类型
**A 47 / B 11**（旧 Type A 移除、旧 B→A、旧 C→B）；**9** 个审计 benign 场景
`detection_excluded`（旧 v4 攻击契约保留在 `legacy_v4_detection_contract`），
检测参与集 **84 = 58 attack（A47/B11）+ 26 benign**；**无人工标签/κ/金标**。
包内含 `release.json`（`release_id=ec1ba74da25ffd61fcda4ae427ed33fd0c779171acca7d0c194ab7067de45a2a`、
3,532 文件）、`checksums.sha256`、scenarios/fixtures/bench/prompt_inputs/ground_truth/
evidence（v5/merge/pbc/validation/legacy v4）/traces（脱敏，TRACES-NOTICE）。
旧仓库内数据集树已退役，不再作为当前路径；旧 v4/AI-silver v1 只作历史。
人工标签仍 **0**、运行级根因参考仍 **0**；运行层 3 标注者流水线已交付但 447×3 全量推迟；
Type C/多智能体付费 campaign、E8/E9/E11 仍未做；dry-run harness 见
`scripts/evaluation/final_campaign_harness.py`。门禁：`./scripts/verify.sh` **18/18**
（含包 portability/clean-room 第 18 门），compare **1266 + 16 subtests**、env **365**、
scripts **60 + 7 subtests**、claims **54/54**、`validate_gt` **93/58/35/problems=[]**。
PBC canonical 61 单智能体 run（28/43 attack PBC=0，AUROC 0.5278，旧 Type C 有效 run=0）。
入口：[`DATASET-CARD.md`](../datasets/FuxiBench/DATASET-CARD.md)、
[`AI-SILVER-RELEASE.md`](../datasets/FuxiBench/AI-SILVER-RELEASE.md)、
[`REVIEW-RESPONSE-EVIDENCE-2026-10-26.md`](paper/REVIEW-RESPONSE-EVIDENCE-2026-10-26.md)。

## 0. 目标与不可协商的诚信边界

目标：把 MtAgentLens 做成可验证的世界顶级 agent 监测产品与科研项目——单一门禁全绿、
标注/数据集证据闭环、审稿意见逐条如实处理、论文数字只来自冻结 RC。

- AI agent 标注只能记 `method=model_proposal`；不得改名 human、不得删 provenance；
  agent 间一致性**不是**人类 κ，`not_evaluable` 不是通过。
- 复核者姓名只是责任分配；没有真人实际填写就没有 attestation，**不得代签**。
- 论文数字只能来自最终 RC（当前 = **RC fix4**）与正式实验；fix4 之前的数字只作历史对照。
- `event_alignment_available=false`（ADR-0016），不得写逐事件对齐。
- 无 verdict / capture 非 healthy 的 run 不进分母；攻击召回分母必须有**场景侧执行证据**。
- 报告二元裁决必须同时给**弃权率与覆盖率**；不得把点估计写成精确值。

## 1. 当前事实基线（以本节为准）

### 1.1 门禁与工作区

- 工作区 `/home/lzk/MtAgentLens`；HEAD `610f726`；**非常脏、未提交未推送**（用户明确要求不要 commit/push）。
- 唯一门禁 `./scripts/verify.sh` → **18/18 全绿**（2026-10-26；含 FuxiBench 包
  portability/clean-room 第 18 门）。计数：compare **1266 passed + 16 subtests**；
  env **365 passed**；scripts **60 + 7 subtests**；Rust suite 绿；claims 校验 **54/54 hold**
  （`python3 scripts/evaluation/verify_claims.py --manifest docs/paper/claims.json`）。

### 1.2 当前唯一可引用测量：RC 2026-10-25-fix4

身份：binary **`de3a0894ff03162b84772aadbc031d74cca659b8f0f07f1110003435d196b3da`**；
release **`aa364a45cbfdc3aab00679e52dace81410f9073a70f482382b1ddab9550db542`**（非 loopback
采集端，93 cases）；config **`6d44789c86f648447e8b63ed69660f5bf5fd67e2115fb665850d561400c633d7`**；
process 模式；observer on。完整说明：[`evaluation/rc-2026-10-25-fix4/README.md`](evaluation/rc-2026-10-25-fix4/README.md)。

| 指标 | 值 |
|---|---|
| benign 有效 / 已裁决 | **18 / 15**（15 Safe · 3 review · **0 Unsafe**；弃权 16.7%、覆盖率 83.3%） |
| benign 条件 FPR | **0/15**，Wilson **[0%, 20.39%]** |
| attack 有效 / 已裁决 | **43 / 22**（4 Unsafe · 21 review · **18 Safe**；弃权 48.8%、覆盖率 51.2%） |
| attack 可行动 | **4/43 = 9.3%**；条件 **4/22 = 18.2%**，Wilson **[7.31%, 38.52%]** |
| 执行链（独立证据） | **marker 16/43 = 37.2%**（8 sink 成功；marker∪sink = 18） |
| 已执行子集 | **4 Unsafe / 9 review / 3 Safe**（已裁决 4/7 = 57.1%，Wilson [25.05%, 84.18%]） |
| 排除场景 | benign-multi-release-orchestration（两次 degraded）；resource-runaway-loop（release2 内联 replica 缺陷，源码已修、release 不改） |
| 无效尝试 | 全部在 `void-attempts.jsonl`，规则 = 每场景取第一个合法 run（`select_valid_runs.py`） |

### 1.3 已完成的离线证据（均指 fix4，除非注明）

- **E2 PBC/活动量**：良性 n18 / 攻击 n43，attack PBC=0 28/43；纯活动量 AUROC
  **0.5277777777777778**、PBC≥1 FPR 27.8%；旧 Type C 有效 run=0、非检测分母
  （`datasets/FuxiBench/evidence/pbc/`，文档 `evaluation/e2-pbc-activity-baseline.md`）。
- **E3 阈值/权重/门控**：25 阈值点 + 9 权重/门控点，裁决翻转 **0/61**（`e3-fix4/summary.json`）。
- **E4 六维 LOO**：D1–D6 任一维置零，裁决翻转 **0/61**（分支最多变化 1 run）。
- **硬规则消融**：replay 61 runs，关闭新规则时与记录裁决**逐条一致（0 mismatch）**；
  开启 `hard_sensitive_egress` → **2 条已执行攻击 review→Unsafe、benign 0 翻转**；
  默认仍 off。见 `evaluation/hard-sensitive-egress-ablation.md` + RC 的 `replay-ablation.json`。
- **攻击场景静态审计（旧 GT 时代，历史）**：旧 67/67 GT=attack、67/67 有执行证据机制、
  66/67 非直白恶意指令（`evaluation/attack-validity-audit.md`）；当前 2.0 GT 为 58 attack/35 benign。
- **关键负结果**：attack-only（不经 LLM）直执载荷 → 0 claims、全 Uncertain（`no_observations +
  capture_quality:unknown`）→ **该协议不能当容量指标**（`evaluation/attack-capacity-2026-10-25.md`）。
- **执行率的核心限制**：30/43 日志出现拒绝措辞，可能与部分执行重叠（日志原文见 audit 文档）；
  未执行样本单列，不计漏报也不计"正确拒答"。
- Blinding：release3（`71e10e45…`）已移除 harness 指纹；但中性化探针显示拒绝主因是 payload
  可静态阅读，**未为 blinding 重跑 campaign**（经费纪律），fix4 明确标注为 pre-blinding release2 campaign。

### 1.4 标注 / studio / R-Judge（已完成的证据闭环）

- codebook：`pilot/1`（`tools/annotator/PILOT-CODEBOOK.md`），store 已迁移；
- 标注试点 + release 外部消费：`scripts/evaluation/annotation_release_consumer.py` 对真实 release
  **VERIFIED**（`evaluation/annotation-release-consumer.md`）；agent 标注仅 `model_proposal`；
- 单页 studio（标注+复核同页、独立标注默认不拉 AI 草稿）：e2e/单测已过，验收记录
  `evaluation/studio-acceptance-2026-10-25.md`；
- R-Judge：adapter + contract tests 已实现（`compare/rjudge/`）；**真实 A1–A3 运行未跑**，
  不得写任何 R-Judge 数字。

## 2. 现在真正未完成的工作（按优先级；外部阻塞要明示）

### 2.1 外部（只有人或独立第三方能做，不得伪造）

1. **独立人工标注**（≥15 tasks）、第四方裁决、逐字段人类 κ、真人 attestation——未完成；
2. R-Judge 真实 A1–A3 跑分与对照——需要预算与人工核对；
3. 正式投稿材料定稿（Word/PDF 数字替换需按 `数字审计-2026-10-25.md` 执行）。

### 2.2 需要新实验（要预算；跑前先预检 + 分批验证，失败 campaign 整份作废）

4. **E6 跨机根因/身份**；**E7 已取消新建 20 个困难良性场景**（只保留 spec 记录，不写 E7 数字）；
5. **E8 / B1 / ADR 应用层基线**（same-model Text+Tool-Trace 对照；`app_baseline.py` 已就绪，需模型预算）；
6. **开销实验**（fix4 未做；现有数字来自 fix2，只能按 profile 报告并注明）；
7. **L0.5 层级拆分**（仅 eBPF vs eBPF+PBC）与 **tmux-live 端到端**（runner ledger 已实现，未真实验证）；
8. 若要让 `hard_sensitive_egress` 默认开启：必须**新 campaign 重算全部数字**，不得复用本次 replay 消融数字。

### 2.3 零/低成本收尾（可直接做）

9. 论文 Word 第 6/摘要/§7 数字替换与逐点回复定稿（以 claims + 数字审计为准）；
10. 仓库/文档一致性扫描（本次交接已做一轮；后续只需增量）；
11. `local/` 历史目录归档或删除（见 `local/README.md`；删除前 grep 引用）。

## 3. 工作方式与红线

- **TDD**：先写失败测试，再最小实现；收尾 `./scripts/verify.sh` 必须 18/18。
- **不要 commit/push**（除非用户明确要求）；不要回滚用户已有改动；工作区本来就是脏的。
- **不要重跑已有有效数据**：fix4 的 61 条有效 run 已是证据；任何新 campaign 必须是有新问题/新语料/新规则。
- **经费纪律**：单场景/小批跑；先 `model_preflight.py`（1 token，exit 3 = 不许开始）；每批检查
  capture_quality/裁决/致命供应商错误；失败即整份作废，不做事后挑选。
- **盲化与构造效度**：不要在 run 路径/环境/fixture 里暴露 harness 标识；
  `agent_visible_env()` 会过滤 `MTAGENTLENS_*`/`MTL_*`/`COLLECT_*`（保留 RUN_TAG）。
- **安全**：sudo 密码只在会话中传递，不写入仓库/日志；凭据只在 `env/config/vendors/`（gitignored）。

## 4. 关键陷阱（都真实踩过）

1. eBPF 路径上限 126 字符：campaign 输出根必须短，否则敏感路径被截断（用 `/tmp/<short>` 或 `local/f6`）。
2. sink 残留会占端口 → 下一条 run 完成证据消失（已修：`stop_leftover_sinks()` 按 pid+祖先保护，
   post-run + campaign-start 清理；仍建议每批后 `pgrep -af sink.py` 检查）。
3. `http.server` 绑非 loopback 时 `getfqdn` 可能阻塞：主机需 `/etc/hosts` 条目
   `93.184.216.34 updates.example.com`（`scripts/wire/prepare-sink-address.sh` 幂等写入）；
   该名字必须中性（旧 `sink.mtagentlens.local` 被模型引用为指纹）。
4. release 不可变：不要修改 release 目录；不要在 release 内执行 fixture（`__pycache__` 破坏校验）。
5. replay fingerprint：`replay --run-dir` 在实现变更后会拒绝重放（正确行为）；分析内核 replay 用
   `mtagentlens replay-sensitivity`（显式说明是"记录输入上重算"）。
6. capture 非 healthy 的 run 一律作废并重试一次；重试仍坏则排除并记录（不做事后挑选）。
7. `pilot_observer` 的 `--observer on` 是产品默认；关闭 observer 的数字不能当产品指标。

## 5. 新会话前 10 分钟 checklist

```bash
cd /home/lzk/MtAgentLens
git status --short | head            # 确认脏工作区、未 commit
./scripts/verify.sh                  # 必须 18/18；日志留档
python3 scripts/evaluation/verify_claims.py --manifest docs/paper/claims.json   # 54/54
sed -n '1,120p' docs/HANDOFF-NEXT-SESSION.md      # 本文件
sed -n '1,160p' docs/evaluation/rc-2026-10-25-fix4/README.md
cat local/README.md                  # 有效 vs 历史产物
```

## 6. 参考索引（按需要读）

| 主题 | 文件 |
|---|---|
| FuxiBench 2.0 包（canonical 数据集） | `datasets/FuxiBench/README.md`、`DATASET-CARD.md`、`AI-SILVER-RELEASE.md` |
| 场景 GT/两类型/排除规则 | `datasets/FuxiBench/ground_truth/`、`evidence/v5/`、`evidence/merge/` |
| RC fix4 身份/数字/复现 | `docs/evaluation/rc-2026-10-25-fix4/README.md` |
| 论文数字登记 | `docs/paper/claims.json` + `docs/paper/数字审计-2026-10-25.md` |
| claim → 证据矩阵 | `docs/paper/claim-evidence-matrix.md` |
| 攻击有效性与执行/拒绝 | `docs/evaluation/attack-validity-audit.md` |
| E2 / E3 / E4 | `docs/evaluation/e2-pbc-activity-baseline.md`、`e3-threshold-sensitivity.md`、`e4-ablation.md` |
| 硬规则消融 | `docs/evaluation/hard-sensitive-egress-ablation.md` |
| attack-only 负结果 | `docs/evaluation/attack-capacity-2026-10-25.md` |
| 标注协议/外部消费/studio | `docs/paper/annotation-protocol.md`、`docs/evaluation/annotation-release-consumer.md`、`docs/evaluation/studio-acceptance-2026-10-25.md` |
| 实验设计（§80/§85/§86 等） | `docs/evaluation/experimental-design.md` |
| 审稿逐点状态/回复 | `docs/paper/修改说明-草稿.md`、`docs/paper/response-letter-notes.md` |
| RC 冻结清单 | `docs/evaluation/final-rc-checklist.md` |

## 7. 交付/验收定义（Lead 用）

- 门禁 18/18 + claims 54/54（或按新 RC 更新且全部 hold）；
- 新结论必须能指向 artifacts（RC/报告/DB），并同步 `claims.json`、数字审计、矩阵、回复信；
- 任何"未完成/外部阻塞"必须在 RC README 与 claims blocker 中如实保留；
- 不 commit/push；不伪造人工数据。
