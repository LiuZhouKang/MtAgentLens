# Codex 最终一轮：完整交接与执行 prompt（2026-10-27）

> 当前只差“代码/运行基础设施收口 + 真实 smoke + 实验编排”。数据 G0 已机械完成；不要重做 G0，不要重标标签。
> 权威上下文：`docs/research/FINAL-CODEX-HANDOFF-2026-10-26.md`、`local/audit-2026-10-27/G0-VERIFICATION.md`、`local/audit-2026-10-26/` 审计、`docs/evaluation/EXPERIMENT-SUITE-7-REVISED-2026-10-26.md`。

## 0. 当前事实（先读）
- 包 `datasets/FuxiBench` release `9c7099c1d36d21af025f5050d462b273cc04aef0c69d3ff72e902779372c6ea3`；93 输入=58 attack（A47/B11）+35 benign；**0 excluded**；93/93 `paper_ready`（机械）。
- multi 30=22 attack+8 benign；`handoff-unicode` 已是第 8 个 multi benign；旧 23 Type C=22 attack+handoff 良性，不得写 23 attack。
- 独立验收：A1 verify 18/18；A2 health 18/18；A3 package 93/58/35/0；A4 GT/scenario 0 problems；A7 code P1 通过；A8 reverse 通过。A5 env 声明 491 vs 实际 298（全量 870），是验收表/文件集不一致，不是测试坏。
- A6 `/tmp` blind prepare 已由 DSH 修复（`repo_rel` 支持 external work + 回归测试），当前 `prepare --smoke 3 --work /tmp/dsh-gt` exit 0；重复验证，不要回滚。
- 代码已有：tmux v4 runner-events/binding、IdentityView 5 条件、AttributionResult、system_score/run_metrics、AppBench A1/A2/A3、replay validate-recording；ONNX 已移除；旧 v1 traces review-only。
- 仍缺：真实 30 multi smoke、C0 93×1、Overhead 四条件 driver、Ablation L0–L3 重建、trace packager/发布准入、@3/MRR 多候选排序、真实参考对齐。
- 工作区 1340+ dirty 条目、765 files changed、94k deletions，未 commit；先做只读快照/明确提交策略，不批量回滚。

## 1. 本轮目标（Definition of Done）
不是“再写设计”，是**可运行、可验收、可进入最终实验**：
1. 所有代码 P1 闭环；18/18；新增测试红→绿。
2. 真实 30 multi smoke 通过（若 sudo/付费被限制，只产出精确命令与 blocker；不伪造通过）。
3. Overhead 四条件、Ablation L0–L3、trace packager 至少达到“可跑 plan + 单样本验收”。
4. E0 4-scenario pilot 通过；给出 C0 93×1 的精确命令、预算、预计时长；**未得到新的明确付费授权不得启动 93 付费 campaign**。
5. 输出最终 readiness matrix：PASS/FAIL/PENDING，不写实验结果。

## 2. 任务包（按依赖）
### C1 复验当前基线（不重复 G0）
- 跑 `PATH=/home/lzk/.cargo/bin:$PATH ./scripts/verify.sh`；确认 A1/A2/A3/A4/A7/A8；A5 记录差异。
- 确认 `repo_rel` 修复/test 在树里；不重新修。
- 若发现 G0 标签/notes 问题（identity-confusion、recon-user-enumeration），只做披露/报告，不改标签。

### C2 真实 30 multi smoke
- 用当前 tmux v4 runner；先 2-pane fake，再 1 个真实 multi（不跑付费模型则用现有 mock/recorded；若必须付费，先停下给命令/预算）。
- 验证：runner-events/input_reference、RUN_TAG/FIXTURE_DIR、pane→pid、agent logs、DB v4/checkpoint、binding、no failed。
- 不做 93；输出 smoke 日志/命令/结果。

### C3 Overhead 四条件 driver
- C0 agent-alone / C1 `trace --capture-only` / C2 `--disable-responsibility-observer` / C3 default Observer；ONNX 无。
- 并发 1/4/8/16/32；15 blocks（四条件展开/并发实例另计）；loss 分母=受控操作数；alert 拆分 event→claim/claim→alert；p95/p99 样本不足报 p95/max；token/cost 记录。
- 不拿 process-only/synthetic parser 冒充全系统。

### C4 Ablation L0–L3
- 从**原始 facts**重建 L0 raw activity、L0.5 +PBC、L1 +graph/taint、L2 +Observer（缓存+20-run live 子样本）、L3 full；每层冻结评分规则/gate/hard_egress off。
- 六维 LOO+gate 只在本实验；不得直接读 checkpoint authoritative identities 冒充低层。
- 输出可复现 plan + 单样本验收；不写入结果。

### C5 Trace packager / replay
- 新 93 v4 DB 的 packager/validator：checkpoint/implementation/verdict/claims/tracebacks/attribution/run_metrics、hash、content-free；旧 v1 review-only。
- `metadata.traces_replayable=false`、`traces_collected=87` 旧包；新 trace 不迁移 v1。
- 跑 sample replay；失败退出非零。

### C6 @3/MRR 与 reference adapter
- 当前 `ranked_agents` 只在单 actor 时给值；要么实现多候选确定性排序（证据/时序，不称概率），要么输出/文档明确只报 @1+RootSet，禁止 @3/MRR。
- 3-AI reference → prediction 的对齐 adapter：只读参考、不补预测；unknown 不当负例；先 synthetic 单测，不付费。
- 这是 2b/8 的硬缺口；不能靠单根冒充 Top-k。

### C7 E0/C0 编排
- E0 4-scenario pilot：1 A、1 B/multi、1 single benign、1 multi benign；capture/DB/checkpoint/export/replay/annotation prepare 全通过。
- C0 93×1：只生成命令/预算/预计时长（5–7h）；**没有新的 `MTL_ALLOW_PAID=1` 和预算确认不启动**。
- 记录 release/build/config/kernel/dirty/seed；void/failed 不静默。
- 不跑安全/E11/ONNX。

## 3. 约束
- 不改数据标签/分母；不改 G0 机械包；不重标；不删除旧证据。
- 不新增 `#[allow]`/`#[ignore]`/预算；不跨包顺手重构；TDD。
- 不 commit/checkout/reset/stash；先快照。
- 不 sudo/付费，除非用户在本轮明确给出；没有则输出精确命令与 blocker，不伪造。
- 所有结论附 file:line/命令/日志；不写“效果理想/实验完成”。

## 4. 交付格式
1. 一张 readiness matrix：包/状态/证据/剩余 blocker。
2. 每包：改动文件、失败→通过测试、命令、日志路径。
3. A5/A6 状态；A6 已修，不重复。
4. 真实 30 multi smoke 结果或精确 blocker。
5. C0 命令/预算/时长；未授权则明确“未启动”。
6. ≤10 条给 Lead 的下一步；不写实验结论。

## 5. Codex 本轮执行与验收（2026-10-27）

本节记录本轮证据，不覆盖 G0。未 commit、reset、stash、sudo，未启动场景、Observer 或标注的付费实验调用；工程协作使用了 DSH，不代表本轮零模型成本。
修改前的 HEAD、status、tracked staged/unstaged binary patch 在
`local/readiness-2026-10-26/team/final-round-snapshot/`；这不是 untracked 文件备份。

### 已实现的接线与复验

| 包 | 状态 | 实现与证据 | 尚未证明 |
|---|---|---|---|
| 基线 / G0 | PASS（机械） | `final-round-baseline-verify.log`：18/18，compare 1449 passed/1 skipped，env 870 passed；`final-g0-health.json` all_ok；`final-g0-package.json` clean-room | `paper_ready` 不是独立科学裁决；既有两项标签 caveat 保留 |
| A5 / A6 | A5 声明差异；A6 PASS | A5 四文件 298 vs 旧声明 491；全量 env 见实际门禁。`final-a6-prepare.log`：外部 `/tmp/mtl-final-round-a6-20261027` prepare 3 场景退出 0，无评审调用 | 不重修 A6、不重标 G0 |
| tmux / canonical multi | PASS（限定范围） | `env/tests/test_tmux_run_binding.py`：真实 tmux、canonical `benign-multi-handoff` 三 pane + mock agent/collector；`final-canonical-multi-mock-green.log`；原双 pane/超时/失败接线测试也复验；`final-30-multi-input-audit.json` 验证全部 30 场输入与 fixture 物化（不执行 setup） | 不是模型执行、真实 eBPF、完整 checkpoint/replay 验收；不是 30 场真实采集 |
| E0 / C0 编排 | PASS（plan/测试） | `final_campaign_harness.py e0` 固定 A / B-multi / single benign / multi benign 四层；共用 `campaign/runs`，已验证完成的 E0 不被 C0 重跑；`final-e0-parent.log` | E0 真实采集、导出、replay、annotation prepare 尚未端到端通过 |
| Overhead driver | PASS（plan/受控假进程测试） | `scripts/evaluation/overhead_driver.py`；15 blocks / 60 condition runs / 732 workload instances；TLS-on，C0/C1/C2/C3，私有配置与执行文件哈希；`final-overhead-config-green.log` | attach-ready、真实 Observer、CPU/RSS/内核内存、真实 operation↔capture/claim/alert 对齐均未验收；stdout 回执缺失不是 eBPF 丢失率；capture loss 在真实适配器完成前固定为未测 |
| Trace packager | PASS（私有包合同/假 replay 测试） | `scripts/evaluation/trace_packager.py`；复算原 run-row 的 v4 binding，单 run、schema 5、严格 replay、文件哈希、拒绝篡改；`final-trace-correction.json` / 聚焦测试日志 | 当前 DB 含捕获内容，**不能称为脱敏公开包**；只有 manifest 不含内容；新 93 DB 尚不存在 |
| AI reference adapter | PASS（synthetic） | `compare/tools/attribution_reference.py`；逐 run+target_action 对齐，逐字段 unknown，已知参考下错误/缺失/歧义预测保留在评分分母；`final-reference-correction.json` | 独立参考与 alignment 尚未生产；文件哈希验证不证明 AI 参考正确，也不证明人工签字 |
| @3 / MRR | 不报告 | 新 adapter 只报 Root@1 + RootSet，@3/MRR 为 null；chain 为有向 occurrence DAG 的**无类型边**比较，不声称 typed-edge F1 | 没有多候选排序，不把候选集合伪装成排序 |
| Ablation L0–L3 | **PENDING，代码阻塞** | `checkpoint.rs::build_graph/evaluate` 仍直接读取冻结后的 graph；本轮未把现有过滤器包装成 raw-fact rebuild | 原始事实重建入口、每层固定评分/门控、Observer 缓存适用性与 20-run live 子样本尚未闭环；不得生成消融表 |

完整执行日志均位于 `local/readiness-2026-10-26/team/`。新包的 fake executable
测试只证明 driver/拒绝路径；实际 Rust replay 单测与真实旧 v1 拒绝分别记录于
`final-replay-parent.log`、`final-legacy-replay-rejection.json`。

**版本区分**：runner-events/run-binding 使用 v4；当前 SQLite schema 是 **5**。
已重新构建 `replay` 与 `mtagentlens` release 二进制（`final-release-build.log`）；
构建前磁盘上的旧 `replay --help` 没有 `--validate-recording`，不能用旧二进制做新包准入。

### 可复现命令与付费边界

以下仅产生 plan，**本轮已验证未启动 C0**：

```bash
PYTHONPATH=env:env/src env/.venv/bin/python scripts/evaluation/final_campaign_harness.py \
  --release datasets/FuxiBench --out local/final-experiments e0
PYTHONPATH=env:env/src env/.venv/bin/python scripts/evaluation/final_campaign_harness.py \
  --release datasets/FuxiBench --out local/final-experiments campaign
```

固定 E0：`exfil-env-secrets`、`consensus-split`、`benign-api-doc`、
`benign-multi-handoff`。先修完 Ablation 与真实参考接线，再进行有授权的 E0；
C0 只在 E0/采集/导出/严格 replay 准入后执行。

将来明确授权 root/模型预算后，执行模板是在上述命令的子命令前加入 `--execute`，
并由操作者显式设置 `MTL_ALLOW_PAID=1`；例如：

```bash
# TEMPLATE ONLY: 本轮未执行，预算确认与运行权限尚缺。
MTL_ALLOW_PAID=1 PYTHONPATH=env:env/src env/.venv/bin/python \
  scripts/evaluation/final_campaign_harness.py --release datasets/FuxiBench \
  --out local/final-experiments --execute e0
# E0 通过后，将末尾 e0 换为 campaign，复用同一目录中的已验证运行。
```

C0 计划为 93×1，5–7h 是既有计划估算，**不是本机实测承诺**；E0 属于这 93 条，
不是额外四条。模型调用次数不等于场景数，Agent/Observer 的调用与 token 未知。
财务预算须按 Agent 输入/输出 token + Observer 输入/输出 token 各自乘实际供应商费率，
另计三路参考与 AppBench（93×3 arms×2 calls =558 calls 上限）。目前没有足够的
冻结 token 账本/供应商费率来填一个可靠美元数；不得把未知预算写成 0。
Overhead 的 confirmed budget 只是批准额度，不是供应商侧硬限额；费用未知时
驱动停止后续付费条件。warmup 与 20-run live 子样本不包含在 15 blocks 中。

**当前不能宣称“只剩跑实验”**：Ablation 原始事实重建、独立运行参考生产/对齐、
真实多智能体/eBPF E0 以及公开 trace 脱敏与重放一致性仍未验收。当前进程 uid=1000，
检查的 collector/BPF 可执行文件无 capabilities；本轮禁止 sudo/付费，故没有把
mock 结果写成真实采集通过。最终表/claims 仍不得填实验结果。

### 独立审查后的补修

`final-new-packages-review-result.json` 中 5 项 P1 以回归测试收口：
执行绝对 workload 路径；未核实 capture observation 不产出 loss 数字；
仅 C3 在独立 collector cwd 中复制私有配置（同 UID 并非安全沙箱）；
父进程正常退出后也清理本任务进程组；打包前后拒绝未登记的 replay 旁文件。
对应日志：`final-overhead-review-fixes.json`、`final-trace-review-fixes.json`。
实际最终复验以 `final-round-accepted-verify.log` 和
`final-accepted-packages-parent-latest.log` 为准；更早的全量绿灯是中间快照。

下一步仅有以下依赖顺序，不代表这些工作已经完成：
1. 完成 Ablation 从 raw facts 重建与评分规则冻结，禁止以现有 graph 过滤代替。
2. 完成独立 runtime reference 的生产合同与 alignment 证据；adapter 的 synthetic 通过不能替代参考本身。
3. 在明确运行权限/预算后跑 E0，核验真实采集、当前 schema/checkpoint、导出、严格 replay、标注 prepare。
4. E0 准入后复用同目录执行 C0；另行验收 Overhead 的采集损失/时延/资源数据接线和公开 trace 的可移植脱敏合同。

真实数据包接线还补了 GT 辅助目录排除：`_reviewers`/`_causal` 的同名 JSON
不得作为 canonical GT。`final-trace-real-layout-red.log` 复现拒绝，
`final-trace-real-layout-green.log` 30 passed（包括真实 canonical release 绑定）。
`final-overhead-real-cli.json` 核验实际新构建二进制接受三个采集 profile 的参数
（仅 `--help`，没有加载 eBPF/调用模型）。

最终验证：`PATH=/home/lzk/.cargo/bin:$PATH ./scripts/verify.sh` 退出 0、18/18；
comparison/annotation/scripts 1540 passed、1 skipped、23 subtests；env 871 passed；
clippy 98/99。`final-accepted-packages-parent-latest.log` 为 128 passed；
`final-replay-parent.log` 为 51 passed；release 构建退出 0。
本轮代码/二进制哈希在 `final-source-manifest.json`，权限证据在
`final-permissions.json`。以上均不是检测效果、身份一致率或根因实验结果。

## 6. 可直接转交 DSH Lead 的完整执行 prompt

```text
你接手 /home/lzk/MtAgentLens，担任 DSH Lead，负责接完剩余工程与实验准备，并在授权和准入条件满足后执行正式实验。不要重做已经验收的模块，不要把本 prompt 当成源码事实；先读文件和日志。目标是可复核、科学有效，不承诺理想数字或零缺陷。

一、先读与事实核实
1. AGENTS.md、CLAUDE.md；docs/research/FINAL-CODEX-HANDOFF-2026-10-26.md §11–12；docs/research/CODEX-FINAL-ROUND-2026-10-27.md §5–6；docs/evaluation/EXPERIMENT-SUITE-7-REVISED-2026-10-26.md；local/audit-2026-10-27/G0-VERIFICATION.md 及 2026-10-26 方法学/可行性/身份归因审计。
2. 当前 canonical datasets/FuxiBench；release_id=9c7099c1d36d21af025f5050d462b273cc04aef0c69d3ff72e902779372c6ea3；93=58 attack(A47/B11)+35 benign，0 excluded；multi30=22 attack+8 benign。paper_ready 93 是机械判据。旧84/9与Type C只作历史；禁止混fix4与旧v1 trace。保留 identity-confusion 与 recon-user-enumeration 的科学 caveat，不能默称全部标签已无争议；这些仍归数据 Lead 处理，任何必要变更先说明证据和所有绑定影响，不能静默改GT/分母或原地改冻结包。
3. 最新已验收：18/18；comparison/scripts/annotation1540 passed、1 skipped、23 subtests；env871；本轮聚焦128；Rust replay51。日志在 local/readiness-2026-10-26/team/final-round-accepted-verify.log、final-accepted-packages-parent-latest.log、final-replay-parent.log。A5四文件298不是旧声明491；A6外部/tmp prepare已经修复并复验，不重修。
4. 已有工具：scripts/evaluation/{final_campaign_harness,overhead_driver,trace_packager}.py；compare/tools/attribution_reference.py；已有 tmux v4、五条件 IdentityView、AttributionResult、run_metrics、AppBench三臂。runner-events/binding v4不等于SQLite schema：当前SQLite是5。release二进制已重建，仍需核对实际源码/构建hash。旧87个v1 DB review-only；traces_replayable=false、traces_collected=87，不能拿它们充新93条。
5. 工作区大量既有改动；新阶段commit只含本轮工程/测试/交接，其他暂存删除和本地数据未混入，不能认为HEAD是完整阶段快照。先看git status/log和交接实际提交，保留用户修改。日志/local与大体积数据不会自动随git传输。

二、工作方式与团队
加载适用 skills：karpathy-guidelines、ponytail、executing-plans、systematic-debugging、test-driven-development、requesting-code-review、verification-before-completion；只有新接口/方法确需设计时使用brainstorming/writing-plans并更新现有ADR，不重新全仓设计。
默认Lead+1实现者+独立Verifier；仅对真正独立的文件范围并行。每包先列allowed paths、输入/输出合同、失败复现、DONE WHEN精确命令、停止条件；同文件禁止并行写。Lead负责接口与科学口径、最终验收，不能只转述worker成功。一次brief限时、返回<=20行；出现两轮同类纠错先改spec，长侦查超时先返回已得证据，禁止无界工具循环。只更新现有交接/ADR，不再堆多套计划、发布或同功能脚本。

三、剩余工作，按依赖执行
P0 基线保护：核实上述基线与release，保存只读状态/必要差异；不重复无理由的大门禁。定位需要改动的确切producer/consumer，先失败测试再最小实现。保持纯eBPF产品；应用转录只作独立参考/基线，不成为隐蔽检测输入。ONNX已移除，不恢复、不重清；不做安全/E11/前端扩展。

P1 AblationBench真实重建（当前代码硬缺口）：
从PRE-resolver事实、原始intent/action/捕获上下文重建L0 raw activity、L0.5+PBC、L1+graph/taint、L2+Observer、L3 full RuleCatalog/identity；不得读取checkpoint.authoritative identities/graph作为低层输入，也不得过滤最终图冒充重建。
先明确每层可见事实、缺失输入、评分输出与共同固定阈值/权重/门控；原始输入缺少授权或provenance时补生产者和版本化录制合同，历史缺失必须拒绝/unknown，不补造。Observer改变verdict不一定改变连续system_score，必须逐项核查，不能凭层名预设提升。缓存只在候选事实/签名确实适用时复用；20-run live子样本单独列预算，未授权不跑。六维LOO、none/fixed/sigmoid gate、hard_sensitive_egress off只在本实验；不能在同一93标签上调阈值制造提升。先synthetic单样本，证明变动被冻结最终graph/identity不会影响低层重建，关模块真正切断依赖；完整层可用性、缺失率、差异和作用域均可解释。

P2 独立Identity/Attribution参考生产与接线（adapter已有，不重写）：
建立detector-blind的原始证据包、版本化codebook、3个fresh AI+独立裁决、原始响应/session/hash与拒绝/隔离规则；先synthetic prepare/validate，不先花钱标全量。
unit=run+message/intent/action或target_action；证据锚点以独立原始捕获为准。runner role/pane PID只是声明/物理关联，执行者不自动等于根因；不读detector verdict/claims/actor答案来造参考或crosswalk。unknown逐字段保留。跨run的actor/node映射必须有命名空间和独立锚点；无法对齐报告coverage，不能硬猜。
现有 ai_runtime.py 强制logical root_actor=null，不能把它当作根因GT；设计级causal proposal也不是运行真值。复用现有三评审工程壳，不另起重复协议。新adapter只报Root@1+RootSet；@3/MRR固定不报，不为填表随意排序；当前chain为有向DAG无类型边比较，不声称typed-edge F1。
known reference下错误/缺失/歧义prediction必须保留分母计miss；不能因预测缺节点/多出actor就排除。验证RootStep、chain-node、Boundary Jaccard、benign false-root、identity pairwise/B³/merge/split等尚缺producer/adapter的接线；复用已有metrics，做五条件同unit cohort与full重复解析确定性、基线/消融及拓扑分层。对未实现/不可识别项明确不报告，不造预测。

P3 Overhead真实计量接线（driver已有，测量未完成）：
保留C0 agent-alone、C1 trace --capture-only、C2 trace --disable-responsibility-observer、C3 default；TLS采集参数一致；核验C3实际provider不是Mock。并发1/4/8/16/32×3=15blocks、60conditionruns、732workloadinstances；warmup另计，单campaign C0与Overhead C0要区分。
接受控操作账本与实际eBPF采集/持久化事实的独立匹配，以已知操作数作loss分母；普通真实agent无已知操作数只报counts。当前capture_loss固定未测/null，不能把stdout回执缺失或自报source字符串当eBPF损失。补真实attach-ready、process/TLS/persist健康与末尾flush、CPU/RSS/进程树/DB大小及内核内存的可测范围，event→claim与claim→alert的同钟/对齐/缺失处理；p99样本不足不报。token/cost来自真实账本，unknown不写0。confirmed budget不是供应商硬限额，费用未知或到限停止后续付费条件。
保留已修：绝对workload执行路径、hash绑定、私有collector cwd配置、正常退出后也清理自有进程组；同UID目录不是沙箱。源码测试不证明真实采集健康。

P4 Trace准入与可交付包：
已有packager复算原始run-row binding，单run/schema5、严格replay、hash、私有权限、拒绝WAL/journal/未登记旁文件。保留canonical GT排除_reviewers/_causal的回归。
补一条真实当前build recording的package→validate→export→严格replay闭环，再补新93条完整性/唯一scenario绑定/覆盖汇总。原始DB含敏感内容，当前manifest content-free不等于DB脱敏；原始包验证仍依赖source DB/run目录，不能谎称自包含公开包。
解决可移植依赖闭包和公开/私有边界：不改原始证据；公开内容若中性化/脱敏改变分析输入，必须使用独立derived身份和映射，重新验证裁决一致性或明确不可重放/仅审阅。禁止把路径重写后旧hash或旧结果当作原样证据，也不能把旧v1迁成新trace。

P5 E0与C0准入：
现有E0固定 exfil-env-secrets / consensus-split / benign-api-doc / benign-multi-handoff；canonical真实三pane+mock通过、30输入物化通过，都不是真实eBPF或30模型运行通过。
无付费/特权执行时先把全部30输入/启动/绑定做到可验证mock/recorded检查，明确范围；真实E0须验证RUN_TAG/RUN_TAG_FILE、pane-pid/role/input_reference、agent logs、capture、schema5 checkpoint、当前实现hash、claim/attribution/run_metrics、export、strictreplay、annotation prepare。任一失败停止，void/failed/degraded全部留痕，不能称检测成功。
E0与C0共用campaign/runs，只有release/build/config及准入一致的已完成运行可复用；旧suite里“pilot不入分母”与当前复用合同要在首次执行前统一，不能看完结果再改。工程调试样本若实现已变要标明，不混最终93分母；每场景只取一条按预注册规则确认的canonical run，不挑最佳。
C0为93×1；5–7h只是原计划估算，费用应按真实token/供应商费率估计，不编一个美元数。先列Agent、Observer、三路参考、AppBench、Overhead、live子样本分别的预算/停止策略。用户本次交接不是新的付费campaign或sudo授权：没有明确预算与执行权限，输出精确命令/blocker，绝不自行设置MTL_ALLOW_PAID=1或启动93。

P6 正式实验/统计/材料（准备与运行分开）：
逐项审查并接通七项实验的实际producer→export→reference→metrics→表，不把有函数或测试等同于harness完整。复用所有合格C0录制，不另跑一套基准。
AppBench A1 task-only/A2含tool results/A3不含；同run每record reason→label两次调用，不是两位judge，558只是93×3×2的上限。审计各臂是否真有可用于AUROC/AUPRC的连续分数；只有二值标签就披露其退化口径，不能伪造概率/可比分数。报告转录覆盖、截断、void、all/decided/executed。
Sensitivity仅threshold×gate-center预注册15点与scenario bootstrap；Observer冻结观测结论有限，live差异须独立预算。PBC分布、activity-only原始计数、tie-aware指标、A/B与benign single/multi/difficult分层，不拿detector-derived PBC当activity baseline；旧fix4数字不替代新93结果。
Identity/Attribution拓扑普查、参考coverage/conflict、基线/消融、scenario级CI/paired bootstrap/permutation、Holm family、参考误差敏感性按suite核实实现；1 run不推断运行方差，不将units当独立重复。缺失数据输出不可估，不补数；同步对应审稿意见和5张结果表，不新增重复总检测表。只有正式有证据的结果才能进入claims/回复材料。

四、验收与交付
每包：先失败→最小修复→窄测试→Lead验收；安全/复杂接线再fresh Verifier反证。新增验证接入已有门禁，不新增allow/ignore、不涨lint/测试预算、不篡改测试换绿。遇到科学不可识别、缺模型/权限/预算，区分代码缺口与外部阻塞；在授权范围继续独立工程工作，不用mock写真实结果。
收尾运行 PATH=/home/lzk/.cargo/bin:$PATH ./scripts/verify.sh（18/18；实际数量以新日志为准）；按改动重建真正将使用的release二进制，检查源码/构建/录制实现hash。不得只测源码却执行旧bin。
在现有CODEX-FINAL-ROUND文档更新唯一readiness matrix：每项PASS/PARTIAL/FAIL/PENDING、文件:行、精确命令/退出码/日志、参考与统计边界、剩余blocker；列清已运行/未运行/需要授权的阶段与下一步<=10条。
不删旧证据，不批量reset/checkout/stash，不自动commit/push，不把dataset/models/DB/log/凭据无审查加入git；新增当前产物保持统一命名，不另建平行GT/release/流程。必要的第三方方法/API只引用可核对的一手来源，记录实际采用部分，不因“顶级”字样扩展架构。
完成标准：代码准备项有真实可执行路径及独立复验；外部阻塞准确列明；只有真实E0准入与明确预算均通过才允许建议C0执行。禁止“全部完美/效果理想”替代验收证据。
```
