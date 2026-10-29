# MtAgentLens 返修现状审计与行动计划（2026-10-25）

> 本文件是 2026-10-25 独立审计的**唯一入口与结论汇总**。三份原始审计报告在同目录：
> [`code-audit.md`](code-audit.md)、[`dataset-audit.md`](dataset-audit.md)、
> [`paper-audit.md`](paper-audit.md)。审计方式：只读核验（脚本/命令/源码），不采信文档自述；
> 未修改数据集、release、源码，未 commit/push。返修截止：**2026-11-03 之前**（剩约 9 天）。
>
> **2026-10-28 更新**：本文是历史审计快照。R1.5“完整开销未测”已不再成立：36/36 条件实测完成，见[实验总方案 §6.6](../../evaluation/EXPERIMENT-SUITE-7-REVISED-2026-10-26.md#66-实测结果2026-10-28-完成)。

## 0. 一句话结论

**代码不是主要问题，实验缺口和标注诚信才是。** 工程门禁 17/17 真实、判定内核与静默失败治理
达到专业水准；但论文的**核心能力（23 个多智能体 Type C 场景的归因与根因定位）在当前唯一
RC（fix4）里一个都没跑**——fix4 只覆盖了 63 个单智能体场景。因此 12 项审稿意见中
**0 项可判完全闭环、10 项部分闭环、2 项未闭环**；数据集 metadata 把 AI 评审写成了真人评审；
论文核心源码不在版本库、HEAD 不可构建。**当前材料不能提交。**

## 1. 核实过的事实基线（2026-10-25 19:14）

| 项 | 实测值 | 证据 |
|---|---|---|
| 工作区 | HEAD `610f726`，**1286 项未提交**（702 M / 190 D / 394 ??） | `git status --porcelain` |
| 门禁 | **17/17 全过，EXIT=0**；Rust lib 995 passed/3 ignored；compare 1092+16 subtests；env 365；clippy 99/99；`#[allow]` 49/49 | `/tmp/verify_gate_2.log` |
| claims | **56/56 hold** | `python3 scripts/evaluation/verify_claims.py --manifest docs/paper/claims.json` |
| 当前 RC | fix4：binary `de3a0894…`、release `aa364a45…`、config `6d44789c…`、61 条有效 run | `docs/evaluation/rc-2026-10-25-fix4/README.md` |
| 语料 | release2 共 **93 case = 63 单智能体（19 benign + 44 attack）+ 30 多智能体（7 benign + 23 attack）** | release2 `prompt_inputs/manifest.json` 的 `mode` |

## 2. 三个问题的直接回答

### 2.1 代码有没有“专业、顶级地彻底完美解决”？

**大部分是专业的，但没有彻底完美。** 统计：**P0 = 1、P1 = 4、P2 = 12**。

实测为真的专业部分（12 项）：单一裁决内核（live/replay 共用 `AnalysisCheckpoint::evaluate`）、
静默失败治理（`lost_count/closed_count/write_failures` 进入 `capture_quality`，非 healthy 把
Safe 降 Uncertain）、路径遍历/DNS rebinding 修复且有测试、DB/日志 0600、导出按 run prune、
`collector/src` 零 `unsafe`、1138 个测试仅 4 处弱断言、无 TODO/unimplemented、clippy 防缓存
低报、ignored 测试强制带理由。

必须修的问题：

| ID | 问题 | 影响 |
|---|---|---|
| **P0-1** | **论文核心源码与新交付物不在版本库**：HEAD 中 `collector/build.rs`、`classification/final_decision.rs`、`docs/paper/claims.json` 均为 0 文件；`checkpoint.rs` 的 `include!` 依赖 `build.rs` → **HEAD 无法编译**；`compare/tests` HEAD 33 vs 盘上 142、`env/tests` 23 vs 92 | 论文“代码可得/可复现”不成立；`git clean -fdx` 会抹掉核心实现 |
| **P1-1** | **subagent 边界记录的 `parent_agent`/`child_agent` 是传播中途算出的陈旧 id**（`union_find.rs:552/607` vs 最终 `:779`）；实测 `parent_exists=false`，前端 `identity-lineage.ts:137` 会画幽灵父节点 | **直接污染多智能体归因**（正是 E6 要测的东西） |
| **P1-2** | 门禁存在“0 测试也通过”：`cargo test -- --exact no_such` → 0 passed / EXIT=0；gate 11/12/13 不看计数，gate 5/6/7/9 无“存在且非空”断言 | 门禁可信度地基；删掉测试体不会被发现 |
| **P1-3/P1-4** | openSSF 自评高估（“verifier 真实加载”不在门禁；14/14 vs 17）；EVIDENCE-INDEX claims 19/19 vs 实测 56/56、E2 0.458 vs 0.528 | 对外材料写错数字比缺数字更伤 |
| **P2-02** | capability envelope 写路径用纯字符串前缀：用户点名的路径会授权同前缀兄弟路径；`/dev/` 整体放行（含 `/dev/shm`） | 给 Observer 错误的授权前提，影响“授权 vs 越权”判据 |
| **P2-04** | `runs` 表无任何外键；`UPDATE runs` 不校验 `rows_affected`；RunStart 丢弃时 run 无声从分母消失 | 残余静默丢样本路径 |

### 2.2 数据集有没有“帮我专业标注好”？

**没有达到“可发表的人工标注”标准。** 分三层：

| 层 | 现状 | 结论 |
|---|---|---|
| A. FuxiBench 场景级 GT | 内容接近专业：93 场景、243 条行为、Fleiss κ(label)=0.9823 / κ(type)=0.8754 可由原始记录复算 | **内容可用，署名不诚实**：3 个 **AI reviewer agents** 被写成 “three independent reviewers” + 真人姓名；3×93 份 reviewer 记录只有 1 份有原始 provenance；74 项 `legacy_needs_human_review=true` 被改名后摘要报 0；58/93 GT 含 `/home/lzk` 绝对路径（680 处） |
| B. 运行级人工标注 | 只有 2 个良性受控任务、6 条 `model_proposal`、**0 条 `method=human`、0 attestation**；`paper_metrics_available=false` | **完全没做**。2 < 15，不能算 κ；不能声称人工复核/签署 |
| C. 根因/入口/链 GT | 93 个 GT **0 个** `root_cause`、0 个 `chain`；9 个现行 C 中 7 个无任何入口/根因字段 | **缺失**。R1.2/R2.4 无法回答 |

另有发布阻断：`datasets/FuxiBench` 的 prompt_inputs **93/93 字节与 sha 和 manifest 不符**
（项目自带 `refresh_prompt_input_hashes.py --check` = 323 drift）、69/93 case/117 fixture sha 不符；
数据集工作树 2026-10-25 改了 373/768 文件但 metadata 仍写 10-22。**注意：fix4 实际使用的
release2 是自洽可复现的**（release_id 重算一致、prompt 93/93 与 fixture 1048/1048 自检通过），
坏的是准备公开的 `compare/Dataset/` 那一份。

### 2.3 审稿意见处理到什么程度？

**0 完全闭环 / 10 部分闭环 / 2 未闭环。** 逐条见 [`paper-audit.md`](paper-audit.md) §1。要点：

- **R1.1** 状态机与算法 3 已写，但 Word 在替换时**删掉了六维定义、表 4、图 3、0.15/0.70 三区**，
  导致“六维/门控”成了无定义词。
- **R1.2** E5 合成微基准很强（六拓扑 F1=1.000），但真实语料身份归属**未测**。
- **R1.3/R2.3** 同输入 text+tool 基线**未跑**；能力矩阵**未进正文**；2,108/149 的构造未在回复信回答。
- **R1.4** 旧表撤下了，但 F1≈0.966 的算术更正没写进正文/回复信；E7 困难良性**只有 spec**。
- **R1.5** 只做了 218 ns 的算术更正；完整开销/并发/丢失/时延/token 费用**未测**。
- **R2.1/R2.2** E2/E3 的离线分析有，但正文缺 PBC 分层表/FPR/TPR；PBC 匹配良性未做。
- **R2.4 未闭环**：根因是标题核心能力，但 `traceback.rs` 仍单 root、无排序；GT 无 root/entry/chain；E6 未跑。
- **R2.5** E3/E4 固定观测 replay 有，但 **L0.5（仅 eBPF）拆分未做**，无 ΔAUROC/TPR/FPR 与统计校正。
- **编辑部材料**：无修订痕迹 PDF、无定稿、无合并单一 PDF、上传栏文本未准备。

## 3. 六个必须先纠偏的事实（不纠偏会继续做错）

1. **被审稿版本是 `docs/paper/thy软件学报24最终稿.doc`，不是 `软件学报初稿`。** 审稿人引用的
   FPR 上界 0.176、2,108/149、表 11 Meta Prompt Guard=1.000 只在 thy 版存在；`数字审计-2026-10-25.md`
   按初稿（0.158、[0.788,0.961]）引用，认错了文件。修订痕迹以 thy 为基线是对的。

2. **“23 个 Type C” = 23 个多智能体攻击场景，不是 GT 的 `attack_type=C`（9 个）。** 论文自己的
   A/B/C 是“攻击方法”（10/34/23），GT 的 A/B/C 是“载荷到达通道”（3/55/9），两者同名不同义。
   release2 里 `mode=agent_teams` 的攻击场景正好 **23** 个（另有 7 个多智能体良性），
   审稿人要的就是这 23 个上的根因/入口/Top-k；**fix4 一个都没跑**。

3. **fix4 只测了 63 个单智能体场景。** 23 个多智能体攻击 + 7 个多智能体良性（高委派、高 PBC，
   正好是 R2.2 要的匹配良性）全部未进入当前指标。这是“检测弱/覆盖率低”之外更根本的问题：
   **论文的核心贡献没有被测量**。

4. **数据集的“三人独立标注”是 AI reviewer agents。** 被审稿版 §5.1 还写了“3 位安全研究人员 +
   Fleiss κ=0.87”；清稿删了但**回复信没有更正**。公开数据 metadata 必须改，回复信必须补
   “标注来源更正”，否则是诚信问题。

5. **修订痕迹时间倒签。** 553 ins + 3040 del 全部 `author=唐浩钺`、`date=2026-08-24`，早于审稿
   （2026-10-19）。必须重新生成并设为实际返修时间。

6. **论文核心源码不在 git。** 不解决 P0-1，任何“开源/可复现”声明都站不住；一次误删即可
   丢掉 `final_decision.rs`、`build.rs`、`claims.json`。

## 4. 能否按期“完美”返修：两条路线

### 路线 A（推荐，唯一能真正闭环 R1.2/R2.4 的路径）

做**一次干净的新 campaign（RC fix5）**，覆盖全部 93 个场景（含 30 个多智能体），再补
E7/E8/开销/人工标注，最后整体改写论文与回复信。

关键判断：**campaign 不需要等数据集发布件修好**。release3（`71e10e45…`）已经是 post-rename、
带 `sink_host_transform` 的 blinded release，fixtures/scenarios 无 `MtAgentLens` 指纹，
prompt/fixture 自检通过，可直接作为新 campaign 输入。`compare/Dataset/` 的 manifest 修复
可以并行做，只影响发布件，不阻塞实验。

工作量与日历（8.5 天，需并行）：

| 日期 | 工作 | 硬验收 |
|---|---|---|
| 10-26 | 代码前置修复 P1-1 / P1-2 / P2-02 / P2-04；构建 release 二进制；门禁 17/17 | 新 binary hash；多智能体单测（boundary id ∈ agents[]） |
| 10-26 晚 | 2 条链路验证 run（1 单智能体 + 1 多智能体，observer on）+ 模型预检 | capture healthy、loss=0、有 canonical verdict、多智能体有 runner ledger |
| 10-27 | 全量 campaign v2 分批跑（93 + 可选 E7 20） | 每批 capture/verdict/DB hash 检查；失败整批作废重跑 |
| 10-28 | E8 同输入基线（对同一批 run 做 A1/A2/A3）、开销实验 | 三臂结果 + 并发/丢失/延迟/RSS/token |
| 10-28~30 | 根因/身份 GT（23 多智能体 + 9 C）；运行级人工标注 ≥15~20 任务 × 3 人 | κ 可算；GT provenance 如实 |
| 10-30~31 | 冻结 RC fix5、claims 更新、统计（Wilson/BCa/McNemar/Holm）、数字审计 | 门禁 17/17 + claims 全 hold |
| 11-01~02 | 论文改写、Word 修订痕迹、逐点回复、合并单一 PDF | 修订时间真实；上传栏文本就绪 |

预计机器时间：单智能体 63 × ~50 s ≈ 1 h；多智能体 30 × ~5–10 min ≈ 2.5–5 h；加 20%–30%
重试，**约 5–7 小时**，可 overnight。模型用 DeepSeek v4-flash，量级为几十元人民币；
Observer 调用已有稀疏性证据。**真正瓶颈是 3 位作者的标注工时（约 15–25 人时）与
tmux-live 稳定性，不是钱。**

### 路线 B（fallback：按期提交“收窄版”）

不跑新实验，只做：数字/口径更正、数据集 metadata 诚信修复、回复信如实说明
“23 Type C 未测、根因定量未完成”，并把标题/贡献中的“根因定位”收窄为“责任链证据组织”。
优点是能按期交；缺点是审稿人点名的核心能力仍无数据，**复审通过概率低**。

### 路线 C（若编辑部允许）

向编辑部说明需要补核心实验，申请延期。当前通知写“逾期自动撤稿”，需先确认可行性。

**建议：A 为主，设 10-27 24:00 为 go/no-go 门；若多智能体链路或标注人力不成立，立即转 B。**

## 5. 需要在 10-26 前决定的事（阻塞项）

1. **P0-1 授权**：是否允许把当前工作区做一次本地 commit（不 push）？或者至少授权
   `sha256 清单 + 异地只读备份`。不解决，论文“代码可得/可复现”就是假的。
2. **预算与 sudo**：是否启动 campaign v2？需要用户在会话中提供 sudo 密码（不写入仓库/日志）。
3. **真人标注**：刘周康、唐浩钺、刘锦样三位能否在 10-28~10-30 各投入约 6–8 小时，
   做 ≥15 个任务的独立标注 + 23 个多智能体 GT 的根因/入口确认？不能的话，R1.2/R2.4 只能
   写成“AI 辅助、待人工复核”，按 B 路线处理。
4. **路线选择**：A / B / C。
5. **标题与贡献**：若 E6 因时间无法完成，是否接受把“根因定位”从标题/贡献中收窄。

## 6. 审计产物与复现

| 文件 | 内容 |
|---|---|
| [`code-audit.md`](code-audit.md) | 工程/代码 17 条问题（P0=1/P1=4/P2=12）+ 12 项实测已修 + 探针复现 |
| [`dataset-audit.md`](dataset-audit.md) | 数据集三层结论 + 9 fail/5 warn + metadata 修复建议 + 标注最小操作步骤 |
| [`paper-audit.md`](paper-audit.md) | 12 项意见状态 + 21 条数字不一致 + Word 逐节 + 提交合规清单 |
| `local/audit/2026-10-25/verify_dataset.py` / `verify-dataset.json` | 93 case 全量只读核验脚本与机器可读结果 |
| `local/audit/2026-10-25/code-audit.md` 等 | 原始报告（与 docs 副本一致） |

关键复现命令：

```bash
export PATH="$HOME/.cargo/bin:$PATH"
./scripts/verify.sh                                   # 期望 17/17
python3 scripts/evaluation/verify_claims.py --manifest docs/paper/claims.json   # 期望 56/56
python3 scripts/evaluation/refresh_prompt_input_hashes.py --check               # 当前 323 drift
```
