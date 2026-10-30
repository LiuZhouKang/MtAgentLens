# MtAgentLens 修稿交接 — 新会话唯一入口（2026-10-19 修稿版）

> 本文件自包含，不假设任何先前对话。先跑 §0 的自检命令，再读 §2–§4。
> 架构细节见 `docs/superpowers/HANDOFF-2026-10-19.md`；本文只讲“现在在哪、
> 审稿人要什么、下一步怎么做”。历史细节在 `docs/archive/engineering/`。

---

## 0. 60 秒自检（进仓库第一件事）

```bash
cd /home/lzk/MtAgentLens
git log -1 --format='%h %s'      # 期望 610f726
git status --short                # 期望：37 M + 10 ??（P1.1/P1.2 未提交）
./scripts/verify.sh               # 期望 verify: all gates passed（14 门）
cd collector && cargo test        # 期望 1014 passed / 0 failed / 3 ignored
                                  # （删除 legacy server 43 条、新增约 9 条；见 §14）
cd .. && sha256sum collector/build/release/mtagentlens
```

**不要相信本文件的二手结论**：所有状态先跑命令核实。工作树是脏的，这是本会话
P1.1/P1.2 的未提交改动；不要 reset/checkout 掉。

当前关键事实（2026-10-19 修稿版）：

```
HEAD             610f726（整体重构提交）
门禁             14/14 PASS
测试             1048 passed / 0 failed / 3 ignored
clippy           167（预算 167）
#[allow]         49（预算 49）
hardcoded /home  0（预算 0）
release 二进制   4bfd5e38…（reconnaissance 模板收紧后）
FPR 基线二进制   c53d667a…（P1.1 最终 FPR 测量时的二进制；已记录在
                 docs/evaluation/fpr-baseline.json 的 provenance）
工作树           37 modified + 10 untracked（未提交）
语料             旧 86 场景已从备份恢复；P1.2 样本 DB 在
                 compare/results/p12-recollect/dbs（gitignored）
.git             约 5.7 GB；历史旧 API key 必须轮换
```

**产物路径陷阱**：Rust 产物在 `collector/build/{debug,release}/`，不是
`collector/target/`；eBPF 产物在 `bpftools/.output/bin/`。
**`compare/results/*` 被 gitignore**：可读证据放 `docs/evaluation/`；样本 DB
放 `compare/results/p12-recollect/dbs`（本地、可重采）。

---

## 1. 工作方法（不可妥协）

信条：**更深入，不是更大**。先拿不可辩驳的一手证据，把系统钉死，再以“证明”
为单位删改。每加一条能力，同时加一条能证明它存在的门；每删一条能力，同时把
预算降下来。

**动手前四问**（每个任务都答）：

1. 顶尖专家会做什么用户没要求的事？
2. 从哪些原始材料开始（raw DB rows、eBPF events、scenario action traces、
   人工标注），而不是汇总 JSON？
3. 用什么具体方法（BCa/Wilson、McNemar、Holm、PR-AUC、ECE、κ、
   Halpern–Pearl、P50/P95/P99、perf/cgroup）？
4. 除了作者，什么能证明结果是对的（第三方一键复现、人工标注、独立基线、
   14 门、负控）？

**禁止**：用二手结论代替一手证据；把旧构建的数字当当前二进制；把
Warning+ 当可行动；把评估脚本复合分当二进制结果；声称“零缺陷/无逃逸”。

---

## 2. 本项目现在是什么（已核实）

```
bpftools/                 eBPF（tls_capture、process、stdiocap；最低内核 5.8）
collector/src/
├── framework/            runners/analyzers（SSE/HTTP/过滤/脱敏/日志/运行时）
├── responsibility/
│   ├── graph/            GraphBuilder + PatternMatcher + TaintTracker
│   ├── claims/           ClaimBuilder/TracebackBuilder/AlertBuilder
│   │                     + authorization.rs（TaintedContextUse 授权门，ADR-0015）
│   ├── runtime/          ResponsibilityRuntime + routing + [diagnostics:json]
│   ├── identity/         UnionFind + 三信号 resolver
│   ├── evidence/         分类/意图抽取/关联
│   ├── persistence/      单 schema + 只读查询 + delegations
│   ├── replay/           live-only 回放（含 provisional→authoritative 映射）
│   └── server/           legacy 路由树（零调用；生产用 src/server/）
├── server/web.rs         REST + live SSE；嵌入前端
└── main.rs               record/trace/ssl/process/stdio/system/doctor/export-genai
frontend/                 Next.js 16 + React 19（5 页面；全屏沉浸页）
scripts/                  verify.sh（唯一门禁）/wire/（FPR/开销/基线）/
                          evaluation/（binary_verdict_bootstrap、stats 待建）
compare/                  FuxiBench 86 场景基准 + 结果/脚本
docs/                     README/architecture/adr/design/evaluation/paper/
                          superpowers/（本交接 + archive）
```

关键 ADR：0005 单 schema、0006 场景词表、0007 文件变更、0008 数据出口、
0009 依赖审计、0010 网络生产者、0011 唯一 MatchLevel、0012 图模板、
0013 架构收口、0014 不设 CI、**0015 告警可行动口径 + 污点授权门**。

---

## 3. 本次会话已完成（P1.1 + P1.2 小样本）

### 3.1 P1.1 告警语义 + TaintedContextUse 授权门（ADR-0015）

- **唯一可行动口径**：`actionable := decision=Unsafe`；`Warning+` 更名
  `warning_plus`（review 信号）。三处相反定义（baseline / 评估脚本 / 读取方）
  统一；gate 10 强制 `actionable == strict`。
- **claim 是观察不是裁决**：有 claim 但引擎分 ≤0.15 → `Uncertain/Info`（仍落库）。
  旧语料反事实：Warning+ 良性 17/19 → **0/19**，攻击 66/67 → 63/67。
- **TaintedContextUse 四条件**：读取方必须是穿越者、共享证据、未获
  `writer→reader` 直接委派授权、置信度 ≥0.70。旧语料反事实良性 15/19 →
  **0/19**、攻击 59/67 → 43/67。
- **同 Agent 读取不再造 PBC**；`/`、空串、`(unknown path)`、目录不再是污点源；
  被授权共享不再落成 PolicyViolation；告警标题用组合 severity；
  `[diagnostics:json]` 新增 6 个 `taint_*` 计数器；replay 补
  provisional→authoritative 委派映射。
- **新鲜隔离 FPR**：20/20 observed，Warning+ 0/20、**actionable 0/20、TCU 0/20**；
  二进制 SHA `c53d667a…`；同协议紧邻一次 1/20（fail-closed Uncertain）已披露。

### 3.2 P1.2 小样本（用户要求停在全量之前）

- 12 场景（6 benign + 6 attack），交付二进制 `4bfd5e38…`，observer off，隔离协议。
- 图模式：良性 **0/6**、攻击 **2/6**；Credential **0/12**；TCU **0/12**；
  Unsafe 攻击 **0/6**。
- **`reconnaissance` 模板此前只要求一次文件动作**（任何读取），良性 5/6 命中；
  收紧为同时要求 `PolicyBoundaryCrossing` 后良性 0/6、攻击 2/6。
- **matcher verdict 此前不持久化**（DB 永远 0）；现已进入
  `ClassificationResult.pattern_verdicts` 与告警 `engine_section_json`。
- **Credential 计数 bug**：`LIKE '%Credential%'` 误匹配 fixture 路径；已改为
  匹配 JSON 键 `"Credential"`。
- 证据：`docs/evaluation/p12-small-sample/`（REPORT.md、summary.json、results.jsonl）；
  旧语料已恢复；样本 DB 本地。
- **不要把这 12 例当最终结论**：它只证明方向与暴露问题。

### 3.3 本次新增/修改的关键文件

- 新增：`collector/src/responsibility/claims/authorization.rs`、
  `docs/adr/0015-alert-actionability-and-taint-authorization.md`、
  `compare/scripts/run/recollect_isolated.py`、
  `docs/evaluation/experimental-design.md`、
  `docs/evaluation/p12-small-sample/`、
  `docs/paper/response-letter-notes.md`、`docs/paper/claim-evidence-matrix.md`。
- 修改：claim/alert/graph/taint/runtime/replay/persistence/classification/
  pattern_matcher；FPR 脚本与 baseline；README/capabilities/CHANGELOG/
  threat-model/architecture/experiment-design/handoff。

---

## 4. 审稿意见（修稿的核心输入）

> 原文以编辑部邮件/截图为准；下面是逐条摘要，**不要凭摘要改论文，先读原文**。
> 截图文件 `docs/paper/粘贴的图像.png` 未跟踪，含个人信息时不要提交。

### 审稿人 1

1. **在线/准实时验证缺失**：第 3/4 章均为离线；要求真实/准实时 Agent 会话的
   在线检测、延迟、吞吐。
2. **性能/开销/时效/误报未量化**：要求开销、检测时效、误报率的量化，并与
   LLM Guard 等对比。
3. **实时性与证据充分性**：跨 Agent/会话/进程的因果链是否完整？要求多 Agent
   协同攻击场景的定量分析（证据链完整率、归因准确率）。
4. **基线口径不一致**：与 LLM Guard/Meta Prompt Guard 的指标口径不同；要求
   equal-FPR 召回、AUC、统计显著性。
5. **成本/错误分析**：抽取/责任分析/图构建耗时；漏报/误报根因；LLM 对比消融。

### 审稿人 2

1. **定位不清**：与静态分析、动态沙箱、prompt 注入检测的关系与边界。
2. **责任归因定义/度量不严谨**：图/边/证据/置信度的形式化与可验证度量。
3. **实验覆盖不足**：严格（Unsafe）与宽松（Warning+）完整混淆矩阵、CI、
   多 Agent（agent_teams/subagents）细分。
4. **TaintedContextUse FPR 0.8947**：良性 15/19，作者承认是调查信号但论文仍
   当检测能力；要求修复或明确排除。
5. **LLM Guard 对比不公平**：内容级 vs 系统级；要求同输入/成本或明确不可比。
6. **统计显著性**：bootstrap CI、效应量、多重比较校正。
7. **实时性**：端到端延迟未测；要求 P50/P95/P99 与吞吐。
8. **责任图 ground truth**：要求人工标注 + inter-annotator agreement（κ）。
9. **消融**：图模式/凭据/污点等组件贡献未量化；逐组件消融。
10. **可复现性**：代码/数据/配置/artifact。

### 编辑

- Major revision；逐条回复；补充实时性、开销、统计显著性、多 Agent 细分、
  错误分析、人工标注、消融；统一口径；明确 TaintedContextUse 定位；
  公平对比；可复现 artifact；语言/格式。
- **期限已核实：2026-11-03（11 月 3 日）之前**提交；逾期按自动撤稿处理
  （截图 `docs/paper/粘贴的图像.png`，2026-10-19 逐段放大核对）。
- 提交材料：修改说明 + 带修订状态的修改稿（Word）+ 定稿修改稿（含日期），
  顺序为“修改说明 → 修订稿 → 定稿”，合成一个 PDF；另需参加 ChinaData2026
  并到会报告。
- **R1/R2 的细分子意见已补齐**（交接原摘要漏掉）：见
  `docs/paper/response-letter-notes.md` 的 R1.1/R1.2/R1.4/R1.5 与 R2-a…f
  （PBC 数量混杂、同层基线 AgentSight/GUARDIAN/BlindGuard/MAStitch、
  23 个 Type C 根因定量、L0→L1 消融不可分、表 11/表 12 数值修正等）。

---

## 5. 现有证据与硬事实（写作时只能引用这些）

| 事实 | 值 | 出处 |
|---|---|---|
| 旧语料 Unsafe TPR | 0.8955（Wilson 0.7997–0.9485） | `standard_evaluation_v5.json`（2026-07 构建，历史） |
| 旧语料 Warning+ FPR | 0.8947（17/19） | 同上（已废弃口径） |
| 旧语料 TCU | 良性 15/19、攻击 59/67 | 同上（旧规则） |
| 新隔离 FPR | 20/20 observed；actionable 0/20、TCU 0/20、Warning+ 0/20 | `fpr-baseline.json`（二进制 c53d…） |
| P1.2 小样本 | 6+6；模式 0/6 vs 2/6；Credential 0/12；TCU 0/12；Unsafe 0/6 | `p12-small-sample/`（二进制 4bfd…） |
| 旧开销 | 6.24% CPU / 1,424.8 MiB RSS（ONNX 93%） | `overhead-baseline.md` |
| Observer | 59/63 旧 DB observer_failed | 交接 §3.4 |
| 旧消融 | L2 ΔAUROC = 0.0 | `ablation_summary_v16.json` |
| 运行时测试 | `ResponsibilityRuntime` 零单元测试 | 代码事实 |
| 端点 | `/api/runs/:id/graph`、`/evidence` 404 | 代码事实 |
| 历史 | `.git` 5.7 GB；旧 key 必须轮换 | 交接/SECURITY |

**不能写进论文的旧数字**：0.8955（当前二进制）、0.8947（Warning+ 口径）、
F1 0.9925（脚本复合分）、L2 主增益（与消融冲突）。

### 5.1 全量缺口登记（修稿 + 工程收口共用的单一清单）

本次审计与审稿意见暴露的**全部**缺口已登记在
[`GAP-REGISTER.md`](../../../superpowers/GAP-REGISTER.md)：

- 76 条：**21 P0**（修稿/发布阻断）、**29 P1**（顶级质量必须）、
  **26 P2/NONGOAL**（可接受债务/刻意不做，写进论文限制）。
- 类别：EFF 检测与测量、PROD 生产者与语义、PERF 在线与开销、ARCH 架构与代码、
  INT 集成/API/前端、ID 身份/带外、DATA 数据/持久化、REPRO 复现/发布/安全、
  DOC 文档/科研。
- **规则**：每条完成后补证据（测试/产物/文档）再改 `DONE`；不允许只改状态
  不加证据。P0 未完成不得声称“完成”。
- 工程项（ARCH/INT/ID/DATA/REPRO）与审稿项不是两件事：它们互为支撑——例如
  `/api/runs/:id/alerts` 的 run_id bug 会污染在线评估，runtime 零测试会让
  延迟/告警实验不可信，语料哈希不符会让 FPR 不可复现。

---

## 6. 修稿方案（六条工作流）

> 每条都先小样本（6–10 例/格），方向成立再扩；全量前必须停下列确认。

### W1 测量核心（R1.4/R2.3/R2.6）
- 双口径完整混淆矩阵；actionable/review 分开；PR-AUC；equal-FPR；
  Wilson/BCa；McNemar + Holm；Cliff's delta；ECE。
- 统计脚本 + 单元测试；v6 artifact。
- 小样本 5+5；全量 ≥50+50。

### W2 语料与构念效度（R1.3/R2.3）
- 逐场景 action trace 审计：攻击是否真的执行；未执行单列。
- benign 扩 ≥50；attack 用交付二进制重采；multi-agent handoff ≥10；
  credential-touch ≥10。
- 小样本 20 audit + 10 handoff。

### W3 归因 ground truth（R2.2/R2.8）
- codebook（Agent/Action/Chain 三层）；双标注 + 裁决 + κ。
- 形式化：Agent/事件/边/claim/证据/置信度；Halpern–Pearl actual causality。
- 小样本 10–15 例 pilot；κ<0.6 先改 codebook；全量 30。

### W4 在线延迟与开销（R1.1/R1.2/R2.7）
- 分段戳：eBPF→intent→evidence→graph→claim→alert；P50/P95/P99、吞吐、背压。
- lite 模式（observer/ONNX off）；空载/负载 CPU/RSS/写放大。
- 小样本 3 live + 5+5；全量 10 live + 负载。

### W5 基线、消融、逃逸（R1.2/R1.4/R2.5/R2.9）
- LLM Guard/Meta Prompt Guard 同输入 + equal-FPR + 成本；不可比处明说。
- 逐组件消融；负结果照实报。
- 逃逸分析：静态 TLS、QUIC、进程内 Agent、分片、短连接；写“看不到什么”。

### W6 Artifact 与论文（R2.10 + 编辑）
- Docker/`make repro`、校验和、种子、一键重算表。
- 论文 §5 重写；response letter；limitations/threats；删旧数字。
- 审稿人只看到修改稿、response letter、artifact；内部笔记不提交。

### W7 架构与集成收口（GAP-REGISTER: ARCH/INT/ID/DATA）
- `/api/runs/:id/alerts` 按 run_id 过滤；legacy server 删除或 `/graph`
  `/evidence` 实现；前端 `actionable`/monitoring 语义（需确认）；
  Runtime 单元测试；OpenClaw 真实 gateway E2E + asserted AgentId；
  委派持久化失败计数；replay 诊断暴露。
- 这些不需要大语料：每项补回归测试/小 e2e；与修稿并行。
- 目标：**代码与架构统一、简洁**——删零调用、单一类型真源、前后端契约一致。

### W8 复现/发布/安全（GAP-REGISTER: REPRO/DOC）
- Docker/`make repro`；场景 JSON 哈希；二进制/语料 SHA 全链；旧 key 轮换
  （`filter-repo` 需先确认）；冻结文档勘误；README/capabilities 最终同步。
- 目标：第三方一条命令重算论文每张表；不能重算的数字不写进论文。

---

## 7. 小样本先做什么（新会话的前 1–2 天）

1. **重新测 20 场景 FPR**：当前二进制 4bfd… 与 baseline 的 c53d… 不同；先测
   `./scripts/wire/run-benign-fpr.sh`，确认 0 action/0 TCU 是否仍成立。
2. **3 个 live 会话**：给 pipeline 每段加时间戳，测 P50/P95/P99、吞吐、背压。
3. **5+5 lite 开销**：observer off/ONNX off，空载/负载 CPU/RSS。
4. **10 个多 Agent handoff**：授权门、TCU、跨 Agent 归因。
5. **10–15 例归因 pilot**：codebook + 双标注 + κ。
6. **20 个场景 action trace 审计**：找出“攻击没真正发生”的场景。

产出是“方向是否成立 + 成本 + 是否改设计”。方向不成立就改设计，不扩样本。

---

## 8. 全量运行条件（工程门）

- 小样本方向明确；脚本在合成/小样本上验证；
- 二进制/语料 SHA、种子、环境记录；
- 人工标注 κ ≥0.6；
- 延迟/开销脚本在 live 小样本上验证；
- `verify.sh` 14/14 绿；
- 用户确认预算与时间。

全量只跑一次；pilot 不作为结论。

---

## 9. 代码/架构质量目标（修稿同时做）

> 具体条目见 `GAP-REGISTER.md` 的 ARCH/INT/ID/DATA/REPRO 表；下面是原则。

- **删 legacy**：`responsibility/server/**` 零调用；实现或删除 `/graph`
  `/evidence`（前端改动需用户确认）。
- **前端一致性**：`actionable` 字段、monitoring 与 openCount 语义；需确认。
- **runtime 测试**：`ResponsibilityRuntime` 零单测；至少覆盖 alert 触发门、
  phase1→phase2 refinement、诊断计数器。
- **统一**：单 schema/单类型真源继续保持；新增字段走 serde default；不引入
  影子类型。
- **预算只降**：clippy 167 / ignored 3 / home 0 / allow 49。
- **不要大爆炸**：纯删除可即做；合并/抽象先设计；与已有门协调。

---

## 10. 路径与陷阱（全部踩过）

1. 会写文件的评审 agent 不要放同一工作树；评审后查 `git status`。
2. `compare/results/*` gitignored；可读证据放 `docs/evaluation/`。
3. 旧语料在 `local/corpus-backup-2026-10-19-pre-p12/`；不要覆盖。
4. P1.2 样本 DB 在 `compare/results/p12-recollect/dbs`；报告在 docs。
5. FPR baseline 的二进制是 c53d…；当前 release 是 4bfd…；最终数字必须用
   同一个冻结二进制重测。
6. 长任务写 `.sh`；eBPF 用 `printf '%s\n' 1 | sudo -S -p '' <cmd>`。
7. 不用 worktree 隔离；一律主会话内联。
8. 前端改动先确认；commit message 由用户定稿（中文、无前缀、不提 agentsight）。
9. `local/` 与真实凭据绝不提交；历史旧 key 必须轮换。
10. 产物在 `collector/build/`，不是 `target/`。

---

## 11. 常用命令

```bash
./scripts/verify.sh
cd collector && cargo test
cd collector && cargo clippy --all-targets --message-format=short
./scripts/check_fresh_clone.sh

# FPR（sudo + LLM）
./scripts/wire/run-benign-fpr.sh /tmp/fpr
python3 scripts/wire/summarize_fpr.py /tmp/fpr --out-dir /tmp/out --name fpr-baseline

# P1.2 小样本重采（单场景/断点续跑）
PYTHONPATH=env:env/src env/.venv/bin/python \
  compare/scripts/run/recollect_isolated.py --only <sid> --db-dir /tmp/p12-dbs

# 基线一致性
python3 scripts/wire/check_baselines.py
```

---

## 12. 下一步（新会话直接从这里开始）

1. 跑 §0 自检；确认工作树、1048 tests、14 门。
2. 读 `docs/paper/claim-evidence-matrix.md`、`docs/paper/response-letter-notes.md`、
   `docs/evaluation/experimental-design.md`、`GAP-REGISTER.md`、审稿原文。
3. 先做 P0 中不需要大语料的工程 bug：`/api/runs/:id/alerts` run_id、
   legacy 端点、Runtime 测试、语料/场景哈希；每项 TDD + 回归。
4. 做 §7 的 1–2 项小样本（先重测 20 场景 FPR + 3 live 延迟 + 20 个 action trace）。
5. 任何全量运行前停下来问用户预算/时间。
6. 不提交；commit message 等用户定稿。

## 13. 给新会话的三句话

1. **先跑 `./scripts/verify.sh` 与 `cargo test`**：绿，才是当前真实状态；工作树
   是 P1.1/P1.2 的未提交改动，不要 reset。
2. **先做 Claim–Evidence Matrix，再补实验**：能支撑的写结果，不能支撑的删/降级；
   旧 0.8955/0.8947 只能标为历史。
3. **小样本先行，全量后置**：方向不成立就改设计；不要为“看起来完整”烧 token。
4. **全量缺口在 `GAP-REGISTER.md`**：P0 没有证据不得标完成；工程收口与修稿并行，
   它们互为支撑，不是两条线。

---

## 14. 2026-10-19 会话进展（工程收口 P0）

> 本条记录本次会话已做并验证的工程项；实验数字仍未产出，不能当结论引用。

### 14.1 已修并验证

| 项 | 证据 |
|---|---|
| ARCH-06 `/api/runs/:id/alerts` | DB `runs` 表解析（301/301 单 run）；多 run 无匹配 → 409 `ambiguous_run`；`get_alerts_desc` 保持 DESC；5 个测试 |
| ARCH-01 legacy server | 删除 14 文件/2,525 行零调用树；生产端实现 `/graph`（最新 snapshot）与 `/evidence`（四集合） |
| ARCH-02 runtime | 告警门矩阵、TLS 诊断计数、空流 RunStart/RunEnd、latency 采样测试 |
| REPRO-03 | `refresh_prompt_input_hashes.py --write/--check`；86/86 匹配；篡改可检出 |
| REPRO-10 | 重写 `env/scripts/evaluate_frozen.py`；dataset adapter 支持分类 traces 目录；历史语料复现 60/67、0/19 FP |
| 实时互通 | `live` run（`<log>.responsibility.db`）、active 仅 running、appState 竞态、`actionable`/ClaimState/RunSummary 类型；`scripts/e2e/live-server-smoke.sh` 实测 REST+SSE+前端三页 |
| 统计 | `scripts/evaluation/stats.py`（Wilson/BCa/McNemar/Holm/Cliff/AP/ECE）`--selftest` |
| 构念效度 | `compare/scripts/audit/audit_action_traces.py`；历史 86 observed → 68 checkable → 52 executed、2 not_executed |
| W4 基础 | runtime `[diagnostics:json].latency`（route/trigger/event_to_alert µs 分位）；`record/trace --lite`；overhead 脚本支持 lite/idle/repeat |
| W1 provenance | `fpr_runner.py` 每行记录 collector SHA；断点续跑只跳过成功且有 DB 的场景 |

### 14.2 仍未完成（不得声称完成）

- 冻结二进制重测：20 场景 FPR、≥50 attack、多 Agent handoff、PBC 匹配良性。
- 3 live 延迟 / 5+5 lite 开销的真实测量（脚本已就绪）。
- W3 人工标注 + κ；W5 同层基线/消融；REPRO-01 Docker。
- 论文 §5 重写与 response letter 定稿。

### 14.3 当前门禁

`./scripts/verify.sh` 14/14；`cargo test` 1017/0/3；frontend 528/528；
clippy 139/167；allow 49/49；release 当前 `5aaff2d7…`（已含丢弃修复）。
不要引用旧的 4bfd/c53d；最终论文数字需用最终冻结二进制重测。

### 14.4 冻结二进制小样本（2026-10-19 夜）

- 测量二进制 `2dfe82dd…`（release）；当前源码二进制 `ca197ef0…`（仅多路径穿越
  防护）。证据目录 `docs/evaluation/fresh-2026-10-19/`。
- **FPR**：20/20 observed，Warning+ 0/20，actionable 0/20，Wilson 0–16.11%；
  每个 DB/scenario 的 SHA 在 `fpr/provenance.json`。
- **PBC 负结果**：本次 20 个良性 run 的 PBC 全为 0（即使 470 actions）；历史
  攻击 PBC 中位数 220、IQR 78–419，良性无一落入。**当前语料无法证明区分
  恶意 vs 活动结构**；必须先建 PBC 匹配的困难良性集。
- **延迟**：route p50 253µs / p95 676µs；trigger p50 575ms / p95 1.07s；
  `event_to_alert` 无样本（良性无告警）。
- **action trace**：86 observed → 68 checkable → 52 executed、2 not_executed。
- **不能写进论文**：当前二进制的攻击召回、PBC 匹配 FPR、event_to_alert 分位、
  lite 开销。全量（≥50 attack / ≥50 benign / PBC 匹配困难良性）前必须让用户
  确认预算与时间。

### 14.5 顶级就绪审计（2026-10-20）——不可开跑大实验

- 19 个只读 agent 的结论：**未达顶级、统一、可收尾**；完整证据见
  `docs/superpowers/TOP-TIER-AUDIT-2026-10-20.md`。
- 核心阻断（必须在全量前修）：
  1. PBC 重复计数（c2 23→4 pairs、data-exfil 34→1 pair）——**已修**
     `GraphBuilder::upsert_pbc_edge` + 测试；去重后原 2 个 Uncertain 很可能是假检出。
  2. 最小证据门让单事件攻击永远 <0.70；fail-closed 永不映射为 Unsafe。
  3. 多 Agent 委派链被并查集合并，AgentDelegatesTo=0。
  4. 凭据盲区（HOME `.ssh/.env`、workspace 豁免）。
  5. GT provenance 与“独立人工标注 κ=0.87”冲突（需用户澄清）。
  6. 前端假数据（demo alerts、44.0K/s）与 stale `frontend/out`。
- 已修安全 P0：静态资源路径穿越（`assets.rs` + live 404）、TLS 明文 `data`
  凭据泄漏（两字段都脱敏 + 测试）。
- 结论：先做 Phase 0（测量/判定完整性），再做 6–10 例/格 pilot，方向成立才全量。

### 14.6 2026-10-20 自主迭代（Phase 0 + 标注工具 + pilot）

- **RC**：`e3881d8732d43a4b56be964472f7747bb54e3c0f449d3df9706826812088db05`
  （PBC 去重、unsafe-rule 门控、凭据策略、委派图/身份、observer 三个失效修复、
  前端假数据/错误态/中文标题、安全 P1、前端入门禁）。
- **标注标准程序**：`tools/annotator/`（盲标 + Agent/Action/Chain + Cohen/Fleiss κ
  + JSON 导出），`make annotate`；codebook v1；已接入 gate 3。
- **Observer-on pilot**：3 个确认执行攻击（c2-beacon、c2-reverse-shell、
  credential-ssh-keys）全部 actionable；6 个未执行/只读 fixture 0 actionable；
  benign-heavy Safe。证据 `docs/evaluation/pilot-2026-10-20/`。
- **关键瓶颈**：语料执行率（严格 action audit 29/68）；PBC 匹配良性、真实
  handoff、人工 κ 仍未做。全量前必须先补这三项。

### 14.7 架构与规则校准（2026-10-20 续）

- 删除零接线 TOTP/2FA（2,680 行）与 auth 集成测试；移除 sha1/hmac/rand 依赖。
- 删除未使用的 pipeline traits（SharedState/Builder/Orchestrator）与 no-op GraphConfig 字段。
- **修复 `simple_glob_match`**：此前只支持首尾 `**`，`**.ssh/id_*`、`**/target/**` 从未生效；现在 `**` 跨 `/`、`*`/`?` 不跨段，含回归测试。
- **收窄 Unsafe 规则**：`/etc/passwd`、syslog、/proc、/usr/bin、ps aux 不再 Unsafe；保留 shadow/sudoers/SSH 私钥/外泄端点。
- 删除 `privilege_escalation` 与 `recursive_self_improvement` 两个无语义模板（任何 Process+PBC 都会命中）。
- 新增 6 个 PBC 匹配困难良性场景；修复后 4/4 复测 Safe。
- 当前 RC `132028f801e356eb128acf49c9c423412b6e0cc32a857d1242debad7b052d0d8`（pattern-only direct-unsafe 必须 Observer 仲裁）。
- `scripts/check_ts_contract.py` 已接入 gate 3：Rust 枚举/必填字段 ↔ TS 漂移会失败。

### 14.8 机制贡献审计与代码收口（2026-10-20）

- 6 个只读 agent：**没有一项机制达到“核心且已证”**；六维/Platt/PBC 计数/CUSUM/
  图模板属于工程启发式，已在代码中降级/删除；OS 观测 + 类型化证据 + Observer
  仲裁 + 归因是唯一可写的理论骨架。
- 代码收口：Observer 结构化 OS 证据、强制仲裁、authority 按证据派生、多跳
  traceback、score provenance（raw/calibrated/gate/版本/参数）、删除
  calibration/L2/simplifier、修 summary 计数与 benchmark 健康解析。
- 新 RC `b89bbbed971bff043a78ca4937a9e12355890536aaefc60437a4b944a97c0a80`。
- 仍未做：A20 attempt/success、A16 丢包落库、A04 完成率实测、A21 intent-PBC
  负控、Observer 配对重复、人工 GT/κ、≥50 语料。
- 详见 `docs/evaluation/mechanism-contribution-2026-10-20.md`。

### 14.9 机制审计 v2 与代码收口（2026-10-20 晚）

- **审计**：15 个只读维度 + 对抗核验（66 条确认 / 22 条被推翻或降级），完整证据
  `docs/evaluation/mechanism-audit-2026-10-20-v2.md`。核心结论：采集层已证；
  判定层此前没有单一最终裁决；PBC/六维/Observer 的旧数字不可用。
- **判定完整性**：新增 `classification/final_decision.rs`（三值、单调聚合）；
  phase 2 聚合 phase-1 Observer 观测，不再从静态分重判；schema v3 增加
  `runs.final_decision/final_severity/final_authority/final_alert_id/
  decision_provenance_json`；告警时间戳不再为 0；灰区改为
  `ambiguous_review`（review，不 actionability）；Observer 失败不再产生 Unsafe；
  缓存命中回放裁决；phase 2 重建全部 claim。
- **证据/图**：PBC 全入口去重、拒绝 `..`/`/` 哨兵路径、删除 `exfil` 标签泄漏、
  收紧敏感路径（passwd/group/known_hosts/config/.env.example 不再敏感）、
  Command↔Url 关联、委派节点先落库、占位 Agent 统一 `unattributed`、
  traceback 只沿证据并支持 TCU 根因回溯。
- **Observer**：重写 system prompt（无 SAFE 先验、明确不可信数据边界）、
  未引用证据的 Unsafe 降 Uncertain、temperature=0、记录 usage、去掉 L2/CUSUM
  提示渲染。
- **采集/产品**：TLS ring 提升到 64 MiB（verifier 要求常量预留）；sudo 产物 chown 给调用者；
  RunEnd 阻塞写；SSE Host 校验；snapshot/graph run_id 过滤；artifact 允许列表；
  /ready 无生产者返回 503；前端告警不再截断；契约门禁覆盖全字段。
- **当前门禁**：`./scripts/verify.sh`（本轮末尾重跑）；`cargo test` 824/0/3；
  allow 49/49；clippy 117/167；bpftools 测试全绿。
- **仍未做**：最终 RC 重采 26 良性 / 攻击 pilot、Observer 配对实验、人工 GT/κ、
  多 Agent handoff 语料；论文按替换清单改写（见审计文档 §5）。

#### 14.9.1 冻结 RC 与最终状态（2026-10-20 夜）

- 最终二进制 SHA-256：`61b3d4fd7d32ca349b1b667298144bd9179e2b444bdf27ef70c7574973987099`
- `cargo test --lib`：827 passed / 0 failed / 3 ignored
- `./scripts/verify.sh`：14/14（冻结后重跑）
- 良性 FPR：26/26 Safe/Info；actionable 0/26（Wilson 0–12.87%），
  warning+ 0/26；活跃子集（evidence>0）18 run、0 actionable（Wilson 0–17.59%）。
  证据 `docs/evaluation/fpr-2026-10-20-final/`；指标用
  `scripts/evaluation/final_verdict_metrics.py --benign ...`（含 active 子集）
- 机制裁决/替换清单/判定流程：
  `docs/evaluation/mechanism-audit-2026-10-20-v2.md`、
  `docs/paper/revision-package.md`、`docs/paper/decision-procedure.md`
- 攻击 pilot：4 场景全部未执行有害动作（execution=partial），最终 3×Uncertain/Warning（review）、
  1×Safe；actionable 0/4 **不能**用于 TPR。证据
  `docs/evaluation/fpr-2026-10-20-final/attack-pilot/execution.json`。
- 收口报告：`docs/superpowers/CONSOLIDATION-REPORT-2026-10-20.md`

### 14.10 GT v4 重标与机制升级（2026-10-20 续）

- **GT 复核队列**：`docs/evaluation/gt-review-queue-2026-10-20.md`（72 条场景设计/输入问题，重构攻击场景时逐条修正）。
- **GT**：删除规则生成器（annotate_gt_v3_independent / promote_gt_v2 /
  generate_gt_v2_candidates / reannotate_gt* / run_annotation_study /
  backfill_type_c_boundaries / create_new_scenarios / legacy
  generate_missing_gts）与旧 GT JSON；按 `ANNOTATION-CODEBOOK.md` v4，由三名
  独立 reviewers（刘周康/唐浩钺/刘锦样）逐场景重标，第四方裁决；93 个场景
  （86 冻结 + 7 困难良性）。原始记录 `ground_truth/_reviewers/`；一致性用
  `scripts/evaluation/gt_agreement.py` 计算；`validate_gt.py` 校验。
- **机制升级（不降级）**：OS 级内容来源标签（Authored/Retrieved/Derived，
  Trusted/Untrusted，UserNamed/Unrequested）→ `UntrustedInstructionFlow`
  边界；权限包络 → `CapabilityExpansion`；三信号身份（进程树 + TLS +
  请求 metadata session_id）；D2 DeBERTa / D6 baseline 接入评分；traceback
  以外部内容边界为根因。设计见
  `docs/design/mechanism-upgrade-2026-10-21.md`。
- **测试**：`cargo test --lib` 864（新增 provenance/envelope/identity/
  scoring/traceback 测试）。
- **下一步**：GT 校验 + κ；冻结新 RC；26 良性 + 执行审计后的攻击重测；
  论文按 GT v4 与来源机制重写。
