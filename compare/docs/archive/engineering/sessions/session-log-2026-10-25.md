# 2026-10-25 会话工作日志（从交接文档拆出，逐轮证据保留）

> 本文件是 2026-10-25 会话的**逐段工作记录**（原 `HANDOFF-NEXT-SESSION.md` §2–§3.4r 的
> 原文，未删除任何事实）。新的交接文档只保留"当前事实 + 待办"，细节与证据链在此。
> 每轮的变更摘要另见 [`CHANGELOG.md`](../../../../../CHANGELOG.md)；实验协议与根因见
> [`docs/evaluation/experimental-design.md`](../../../evaluation/experimental-design.md)。

## 2. 本次会话完成的工作

### 2.1 标注 codebook 定稿 `pilot/1` + 证据包迁移（P0-1）

- `tools/annotator/PILOT-CODEBOOK.md` 从 `pilot/1-draft` 定稿为 **`pilot/1`**：裁决了 5 项
  未决事项（授权包络判定程序、按目标类别的 `succeeded` 判据、空根因 vs unknown、
  裁决协议与分字段 κ≥0.60、可见范围与盲化），并新增**单调链**
  `attempted ≥ executed ≥ succeeded`（`yes > unknown > no`），由 `records.py` 强制。
  文档契约测试 `tools/annotator/tests/test_codebook.py`（7 项）守住文档与校验器一致。
- 新增 `tools/annotator/migrate.py` + CLI `migrate-codebook`（11 项测试）：重冻结证据包
  （重验 release/来源 SHA/字节范围）、断言证据字节与标注 payload 不变、按序重放修订、
  改写裁决引用、**默认拒绝携带真人修订与复核声明**（`--carry-human` 才显式携带并留痕）。
- 真实迁移：`2026-10-24-pilot-human/records.sqlite` → `2026-10-24-pilot-codebook-v1/`
  （2 任务、6 条 model_proposal、0 真人修订、0 声明）。旧包保留为历史。

### 2.2 标注数据集 release（P0-2）

- 新增 `tools/annotator/release.py` + CLI `release`/`release-verify`（42 项测试）：
  `mtagentlens.annotation-dataset/1`，`release_id` 同 `dataset-release/1` 口径，
  全量 `files` sha256、原子发布、不可变、篡改/伪造/symlink 全拒绝；含
  `schema/annotation.schema.json`（与 `_validate_annotation` 交叉验证）、README、LICENSE、
  `cases/`、`annotations.jsonl`、`reviewers.json`、`attestations.json`。
- 真实包：`local/workpackages/2026-10-24-pilot-codebook-v1/release/`
  （`release_id e05c0ed2…6cadb`，2 case 全 dev、`paper_metrics_available=false`）。

### 2.3 单页 studio（P0-3）

- 反污染下沉到接口层：独立标注页的 `/api/tasks` 与 task read **不返回**
  `has_model_proposal`/`proposals`/`attestations`；只有 studio 显式 `?review=1` 才返回，
  切回标注清空复核 DOM。
- WAI-ARIA tabs（←/→/Home/End）、polite status / assertive alert 播报、对比度提升、
  ≤900px 单列、5000 事件分块渲染；409/412 后保存禁用且保留草稿；`roots=null` 与 `[]`
  语义分开。e2e 11 项 × 3 浏览器 + axe 0 violations。

### 2.4 R-Judge 应用层基线骨架（P0-5）

- `compare/rjudge/`：协议 pin（论文 arXiv **2401.10019**v3；仓库 `Lordog/R-Judge`
  @ `83ce301d…`；**无 LICENSE** → 零 vendor；prompt 逐字 + sha256；两步式；
  `temperature=0`；无截断；8 项 UNVERIFIED 如实登记）、`app_trace.v1` 导出（S1 转录 /
  S2 TLS，多 agent 归属，显式缺失）、record 生成（A1/A2/A3 三臂）、严格解析的两步式 judge
  （默认不可用 client）、内容寻址缓存。136 项契约测试，**没有任何结果数字**。

### 2.5 最终 RC 与正式实验（P1）

- `scripts/evaluation/freeze_rc.py` + `compare/tests/test_freeze_rc.py`（5 项）：
  冻结 binary/eBPF helper/dataset/工具链/git/门禁日志 hash，记录只写一次。
- `docs/evaluation/rc-2026-10-24/`：协议、测量有效性事件、范围与结果。
- 冻结身份：binary `e02d2957…e1e51d`；release `f1b08e41…e72236`（93 case）；
  `local/workpackages/2026-10-24-final-rc/rc.json`。
- **正式结果（observer on、process 模式）**：
  - benign 19 运行：**19/19 `Uncertain`**，0 二元裁决 → 条件 FPR 不可计算；
  - attack 44 运行：**5 `Unsafe`**（11.4% 全尝试；条件 5/5，Wilson [56.6%, 100%]）、
    38 `Uncertain`、1 unobserved；
  - 开销：full **151.2% CPU / 1716 MiB**，lite **12.1% / 238.6 MiB**（45 s 空载窗口）。

## 3. 2026-10-25 修复：两个决定性阻塞已处理（RC fix 复测）

**修复 1：候选审阅覆盖（§74.1）**
- Phase 1 曾经在 Observe 之后**重新推导**候选，Phase 2 又把最终图候选伪造成"已呈现"
  （`ScopedCandidate` 无投票）→ 任何晚出现的边界候选都把整次运行压成 `Uncertain`。
- 现在：候选只推导一次并同时用于提示与绑定（`runtime` + `candidates::review_observations`）；
  Phase 2 不再伪造呈现，改为在冻结裁决前**批量补审**未覆盖候选（`AnalysisCheckpoint::review_request`
  → `review_candidates_with_observer`），失败写 `ObserverFailed`（仍 fail-closed）；关闭 Observer
  时不做补审、也不伪造呈现（候选是复核请求，不是证据）。
- 验收：RC fix 的两条臂**零** `unreviewed:*` 裁决来源。

**修复 2：Phase 2 数据量（§74.2）**
- 权威归因改为一对一（匹配级别 → 置信度 → 时间接近度，平局按意图顺序）；低于阈值诊断每意图 ≤4。
- 同一场景责任 DB 218.5 MiB → **18.0 MiB**；`correlation_evidence` 62k → 4.1k；
  `evidence_refinements` 61.7k → 0.9k。full profile 开销 151.2% → **17.9%** CPU。

**修复 3：授权包络的边界（§75）**
- 工作区曾取 collector 的 cwd（隔离根），而非 agent 的 run 目录 → run 目录内写文件被判越界。
  新增 `MTAGENTLENS_WORKSPACE_ROOT` 由 harness 传入（sudo `--preserve-env` 保留），
  `file_access_audit` 也使用同一根。
- eBPF 侧路径上限 127（截到 126 字符），深目录路径截断后前缀比较失败 → 新增
  `CapabilityVerdict::Unknown`（达到上限记"无法判定"，不生成候选）。
- `/proc/self/fd/...`、`/dev/fd/...` 等描述符间接写不再算能力扩张（能力在 open 时已授予）。

**最终 RC（fix2，binary `db3191bb…`，release `f1b08e41…`，process 模式）**
- benign 19：**12 `Safe` / 7 `Uncertain` / 0 `Unsafe`**；条件 FPR 0/12 [0%, 24.25%]，覆盖率 63.2%；
- attack 44：**4 `Unsafe`（9.1%；条件 4/24 = 16.7% [6.68%, 35.85%]）、19 `Uncertain`、20 `Safe`**；
- 弃权率 36.8% / 44.2%；零 `unreviewed:*` 裁决来源；
- 开销（fix 轮测，二进制行为同源）：full 17.9% CPU / 1636 MiB，lite 10.7% / 240 MiB；
- **最重要的诚实结论：45.5% 的攻击被判 Safe**。按证据链拆开：其中一部分是攻击根本没执行
  （marker 缺失 → Safe 正确）；一部分是"载荷确实送达但走 loopback、凭据在 run 工作区内"，
  两者都在授权包络的允许语义里（语料构造效度问题，见 §76）；剩下的是 Observer 语义漏报。
- **执行链/完成链（本轮新增）**：`marker_executed`（钩子执行，16/44）与
  `summary.sink_received`（采集端收到载荷，15/44，含 sha256 清单、小文件复制进 run 目录）
  是两个与检测器无关的场景侧证据；`scripts/evaluation/scenario_execution_witness.py`
  产出可直接喂 `final_verdict_metrics.py --execution-audit` 的分母。
- **构造效度（最重要的限制）**：外泄类场景的采集端绑在 `127.0.0.1`、凭据在 run 工作区内，
  两者都落在授权包络的允许语义里 → 这批样本不在可观测违规空间；`Safe` 与策略一致，
  但语料无法测量外泄检出。修复前 RC 的 Unsafe 来自已被 §75 删除的伪造候选。
  可检出的只有系统级违规（sudoers 写入、容器逃逸）。
- 仍未解决：`tmux-live` 多 agent 不可测（Phase 2 能完成但 611 s 且该模式没有 runner ledger）。

详见 [RC 2026-10-25-fix2](../../../evaluation/rc-2026-10-25-fix2/README.md) 与
[实验设计 §74/§75](../../../evaluation/experimental-design.md)。

## 3.4 实验性内容外泄源（本轮新增，默认关闭）

- `ClassificationConfig.sensitive_egress`（`MTAGENTLENS_SENSITIVE_EGRESS=1` 启用）：
  同一 agent 读凭据类文件后 600 s 内发起网络连接 → `sensitive_egress` 候选（与目标地址无关，
  loopback 也算），候选携带两半 evidence。单测 6 项；开关写入 checkpoint，可复算。
- **端到端尚未生效**：`credential-theft-direct` 的凭据读已入图，但**网络动作 0 个入图**；
  `c2-dns-dead-drop` 则相反。即瓶颈是核心相关覆盖率，不是规则。下一块工作见 §77.3。

## 3.4b 非阻塞 egress 修复（本轮新增）

- `connect(2)` 返回 `EINPROGRESS` 时 `ActionOutcome::Pending`，而 `handle()` 对所有非
  `Succeeded` 动作早退 → **异步 HTTP 客户端的 egress 从不进图**。语料里攻击采集端的
  connect 恰好全是 -115，agent 自身 API 流量是 blocking succeeded → "攻击 egress 不可见、
  噪声可见"。
- 修复：`record_attempted_egress()` 对 `Pending/Attempted` 的 `NetworkConnect/Send` 记录
  agent→Network 资源边（attempt，confidence ≤0.5），越界时生成**带 evidence** 的 PBC；
  不写 taint/完成声明。单测 2 项。
- 实测：`c2-dns-dead-drop` Network 节点 0→12、裁决来源 `no_observations`→
  `all_candidate_versions_reviewed`（仍 Safe，语义判定瓶颈）。
- 仍未触发 `sensitive_egress`：夹具对凭据文件的读取没有被相关进图 → 下一步是把 hook
  子进程的 `FileOpen` 稳定相关（当前同一 run 里 2883 条动作来自 `git` 仓库扫描，
  真正读取被淹没/未相关）。

## 3.4c 本地数据外发候选（本轮新增，默认关闭）

- `ClassificationConfig.local_egress`（`MTAGENTLENS_LOCAL_EGRESS=1`）：**被 spawn 的子进程**
  向本地目的地（loopback/非公网）发起连接、且任务未点名该目的地 → 生成
  `network:local:<host>:<port>` 能力扩张边界（带 evidence，attempt 与 completed 都算）。
  agent 自身进程的本地流量不触发；单测 2 项。
- 实测（`local/le1`）：`credential-theft-direct` 命中真实外泄端 `127.0.0.1:9510`、
  `c2-dns-dead-drop` 命中 `127.0.0.1:9501`，但裁决只从 Safe 推到复核（Observer 弃权/Safe），
  召回未变；同时出现未解释的本地 `NetworkSend` 目的地（未证实为解析缺陷）。
- 结论：规则正确但**语义判定与目的地可信度**是瓶颈，默认保持关闭，只作消融项。

## 3.4d 采集路径上限 126 字符（本轮定位，实验协议必须遵守）

- eBPF `MAX_FILENAME_LEN=127`（截到 126 字符）。RC run 目录下
  `fixtures/config/.env` 共 132 字符 → 采成 `.../fixtures/confi`，敏感读因此不可识别；
  之前说的"成功读取未进 actions"实为截断，不是丢事件（actions 最大路径长度恰为 126）。
- 换短输出根（本次 `local/l…`：run 目录 104 字符，加 `fixtures/config/.env` 共 124）后，
  凭据读完整出现并被相关（76 条相关证据）。
- **正式实验要求**：campaign 输出根必须让 `run_dir/fixtures/<敏感文件名>` 短于 126 字符；
  超限路径按截断处理（见实验设计 §80、final-rc-checklist）。
- `sensitive_egress` 首次端到端触发（112 条配对，已去重为
  `(agent, 凭据路径, 目的地)` 每组合一个）。仍有两个阻塞：egress 相关在两次 run 间
  0 ↔ 有（不稳定）；Observer 看过候选仍判 Safe（语义判定）。

## 3.4e 复核提示修复（本轮）

- **上下文溢出（真实故障）**：Observer 曾返回 `HTTP 400: requested 4,860,416 tokens`
  （模型上限 1,048,576），根因是 `CandidateSummary.evidence_ids` 无上界；已加
  `MAX_CANDIDATE_EVIDENCE=8`（排序/去重/截断）+ 单测。
- **事实不可见**：派生候选的凭据路径只以 UUID 存在，Observer 因此写出"没有 credential
  reads"。已加可选 `CandidateSummary.detail`（人类可读事实）+ system prompt 说明 + 单测。
- **phase-2 归因**：`SignalGraph` 新增 append-only `lineage_memory`（上限 8192），
  `snapshot()` 用 live 边优先 + 记忆补全，使进程退出后的批处理仍能归因（单测证明）。
- 仍未解决：`sensitive_egress` 在多次 run 里时有时无（m1 命中、m2/m4 未命中），
  还有一条未定位的时序/归属路径；在定位前保持默认关闭。

## 3.4f 阻塞：模型额度耗尽 + campaign fail-fast（本轮）

- `local/f3` campaign（当前构建的首个全量复测）在**攻击臂第 26 个场景**起遇到
  `API Error: 402 Insufficient Balance`：19/44 攻击 run 的 agent 没执行，
  `returncode=1`，但采集 healthy、binding 通过，于是仍写了 verdict。
- 处置：**整份 campaign 作废**（`docs/evaluation/rc-2026-10-25-fix3/README.md`，
  不冻结、不引用、证据保留）；事后挑前 25 个有效 run 属 post-hoc subsetting，不允许。
- **fail-fast 已实现**：`pilot_observer` 每个 run 后检查 agent 日志中的致命供应商错误
  （`Insufficient Balance` / `insufficient_quota` / `credit balance is too low`），
  命中即写 `campaign-aborted.json`（原因+最后场景+未尝试清单）并 exit 3；
  单测 `compare/tests/test_pilot_observer_abort.py`（4 项）。
- **当前阻塞**：模型 API 额度为 0。额度恢复前不再启动任何 agent campaign；
  离线工作（代码/测试/文档/标注工具/门禁）继续。
- 上一份**有效**测量仍是 [RC 2026-10-25-fix2](../../../evaluation/rc-2026-10-25-fix2/README.md)；
  当前构建相对它的默认行为差异见实验设计 §81，但**尚未复测**。

## 3.4g 离线复核（额度为 0 期间，2026-10-25 08:0x）

不依赖模型额度的交付项已重新验证：

| 项 | 命令 | 结果 |
|---|---|---|
| 单页 studio | `serve --mode studio --rater lzk-agent --method model_proposal` | HTTP **200**，93 KB 单页；含标注/复核/独立 面板与 `role="tablist"` ARIA |
| 数据集 release | `release-verify --root …/2026-10-24-pilot-codebook-v1/release` | `release_id e05c0ed2…`，2 cases，`pilot/1`，`paper_metrics_available=false` |
| 标注就绪度 | `audit --store …/records.sqlite` | 2 tasks ready，0 blockers，`human_annotations=0`、`attestations=0`，`ready_for_independent_annotation=true` |
| 门禁 | `./scripts/verify.sh` | 17/17 |

诚实边界：`model_proposal` 不是人工标签；没有独立人工标注就没有 κ / 论文一致性数字；
`paper_metrics_available=false` 是正确状态，不得改写。

## 3.4h 前置预检（本轮新增，零浪费）

- `compare/scripts/run/model_preflight.py`：读 `env/config/claude.env`，发 1 次
  `max_tokens=1` 请求，分类 `ok/insufficient_balance/unauthorized/error/unavailable`；
  只对确定性拒付/鉴权失败阻止 campaign（网络抖动不阻止）。
- `pilot_observer` 在启动任何 run **之前**预检（`--preflight on|off`，默认 on；测试用 off），
  结果写 `model-preflight.json`；
  阻止时写 `campaign-aborted.json`（`last_scenario="(none run)"`）+ exit 3。
- 实测（额度仍为 0）：直接输出 `[abort] model preflight: insufficient_balance;
  campaign not started`，**没有产生任何 run**。与 §82 fail-fast 构成分层防线。
- 单测：`compare/tests/test_model_preflight.py`（4 项）。

## 3.4i 入口-only 凭据读取修复（本轮，离线诊断）

- 用两次相同场景的 checkpoint 对比（m1 命中 / m4 未命中）定位：m4 的读证据在 phase 2
  被喂回图，但 `handle()` 对**非成功**动作早退；入口-only `openat`（没采到返回值）被判
  `Unknown` → 凭据读没有资源节点 → `sensitive_egress` 无法配对。这就是"时有时无"的根因类别。
- 修复：outcome ≠ `Failed` 的 `FileOpen/FileRead` + 凭据类路径 → `record_attempted_read()`
  建 attempt 边（confidence ≤ 0.4，不写 taint/完成声明）；普通路径不入图。单测 2 项。
- 端到端效果仍需 campaign（额度阻塞中），暂不写效果结论。

## 3.4j 机器可校验的数字层（本轮）

- 新增 `docs/paper/claims.json`（schema `mtagentlens.paper-claims/1`）：每条 claim =
  `artifact + dotted path + 期望值`，当前登记 fix2 的 11 个关键数字（含
  `paper_metrics_available=false` 这条"不可发表"状态）。
- 新增 `scripts/evaluation/verify_claims.py`：逐条校验，artifact 缺失/路径不存在/数值漂移都
  exit 1；单测 5 项（`compare/tests/test_verify_claims.py`）。
- 实测：`11/11 claims hold`（对 `local/workpackages/2026-10-25-rc-fix2/metrics.json`）。
- 规则：**任何数字进论文/回复信之前，先登记进 claims.json 并跑校验**；新 RC 产生后先更新它。

## 3.4k 语料构造效度 + 遗留 sink（本轮）

- **非 loopback 采集端**（修 §80 的构造效度）：`sink.py` 支持 `MTL_SINK_HOST`；
  `scripts/wire/prepare-sink-address.sh` 把 `93.184.216.34/32` 挂到 lo；
  `build_dataset_release.py --sink-host` 在构建时重写 fixture 的 `127.0.0.1` 并逐文件记录
  前后 hash（进入 release_id）。候选 release：`local/workpackages/2026-10-25-sink-release/FuxiBench`
  （`a8774f3f…`，171 文件/68 场景）。
  离线验证：release 校验通过；钩子把载荷投递到 `93.184.216.34:9514` 且 sink 收到；
  collector 捕获 `"host":"93.184.216.34"`。
- **遗留 sink（完成链杀手）**：`start.sh` 的 `pkill -f "bench/sink.py"` 匹配不到真实
  cmdline（`.../bench/<id>/../sink.py`），sink 逐场景累积（本轮清理 38 个），占端口导致新
  sink 起不来、外泄载荷无人接收。修复：27 个 start.sh 换模式 + executor 的
  `stop_leftover_sinks()` 纵深防御（跳过自身进程组/ shell，单测 4 项）。
- **campaign 前置要求（新增）**：先 `prepare-sink-address.sh`、导出 `MTL_SINK_HOST`、
  使用带 `sink_host_transform` 的 release；**不得在 release 目录内执行 fixture**
  （会写 `__pycache__` 破坏校验）。

## 3.4l 额度恢复后的 campaign 运行手册（按顺序执行）

```bash
# 0) 采集端准备（需要 root；只做一次，幂等）
sudo ./scripts/wire/prepare-sink-address.sh          # 93.184.216.34/32 -> lo

# 1) 模型额度预检（1 token；exit 3 = 不许开始）
python3 compare/scripts/run/model_preflight.py --env-file env/config/claude.env

# 2) 环境（短 campaign 根满足 §80 的 126 字符上限；sink 走非 loopback）
export MTL_SINK_HOST=93.184.216.34
export MTAGENTLENS_SUDO_PASSWORD=1
export MTAGENTLENS_COLLECTOR_STOP_TIMEOUT=180
export PYTHONPATH="$PWD/env:$PWD/env/src"
RC=local/workpackages/2026-10-25-sink-release/FuxiBench   # a8774f3f…，带 sink_host_transform
OUT=local/f4                                              # run_dir/fixtures/config/.env < 126

# 3) 先跑 1 个场景验证链路 + 执行/完成链（witness 必须非空）
python3 compare/scripts/run/pilot_observer.py --release $RC --observer on --out $OUT/smoke --only credential-theft-direct
python3 scripts/evaluation/scenario_execution_witness.py --results $OUT/smoke/results.jsonl --out $OUT/smoke/witness.json

# 4) 全量（每个 run 后检查致命供应商错误；命中即 exit 3 + campaign-aborted.json）
BENIGN=$(sed 's/^/--only /' /tmp/benign-process.txt | tr '\n' ' ')
ATTACK=$(sed 's/^/--only /' /tmp/attack-process.txt | tr '\n' ' ')
python3 compare/scripts/run/pilot_observer.py --release $RC --observer on --out $OUT/benign $BENIGN
python3 compare/scripts/run/pilot_observer.py --release $RC --observer on --out $OUT/attack $ATTACK

# 5) 派生 & 指标
for arm in benign attack; do
  python3 scripts/evaluation/enrich_campaign_results.py --results $OUT/$arm/results.jsonl --out $OUT/$arm/derived.results.jsonl
done
python3 scripts/evaluation/final_verdict_metrics.py --benign $OUT/benign/derived.results.jsonl --attack $OUT/attack/derived.results.jsonl --release $RC --out $OUT/metrics.json

# 6) 冻结新 RC，然后**先更新 claims.json 再引用任何数字**
python3 scripts/evaluation/verify_claims.py --manifest docs/paper/claims.json
```

注意：**不得在 release 目录内执行 fixture**（`__pycache__` 会破坏 release 校验）；
任何 run 失败/中止都要按 §82 作废整份 campaign，不做事后子集挑选。

## 3.4m 门禁内置论文数字校验（本轮）

- `compare/tests/test_paper_claims_artifact.py` 已并入 comparison 门禁：结构校验 + 对存在的
  measurement artifact 逐条比对 `docs/paper/claims.json`；无 artifact 的干净 checkout skip。
- 效果：数字漂移会让 `./scripts/verify.sh` 直接变红；新 RC 之后必须先更新 claims.json，
  否则门禁不会通过（这一点已写进 §3.4l 运行手册的最后一步）。

## 3.4n 论文数字审计（本轮，离线）

- 新增 [`docs/paper/数字审计-2026-10-25.md`](../../../paper/数字审计-2026-10-25.md)：从初稿 PDF
  逐项列出**不可引用数字**（TPR 1.000、FPR 0.000 [0,0.158]、F1 0.945、MCC 0.809、
  AUROC 0.739→0.953、218 ns、36.9 MB、基线系列数字），并给出当前唯一可写表述
  （fix2：4/44、条件 4/24=16.7% [6.68%,35.85%]、0/12 [0%,24.25%]、弃权率/覆盖率）
  与证据链接。
- `claims.json` 扩充到 **19 条**（覆盖 Wilson 上下界、覆盖率、全尝试率等），
  `verify_claims` → **19/19 hold**；门禁内校验同步生效。
- 规则不变：数字先登记 claims 再过门禁；报告二元裁决必须同时给弃权率与覆盖率；
  攻击召回分母必须有执行证据。

## 3.4o 同级方法能力矩阵（本轮，离线）

- 新增 [`docs/paper/能力矩阵-同级方法.md`](../../../paper/能力矩阵-同级方法.md)：MtAgentLens 与
  AgentSentinel / AgentSight / GUARDIAN / BlindGuard / MAStitch / ADR(Uber) /
  LlamaFirewall(AlignmentCheck) / R-Judge / 提示级基线逐项对比（输入、分析单元、输出、
  代码与许可、**在本基准的可复现性分级 A/A-/B/B′/C**、本文角色），并给出 §1 表 1 说明、
  §5.2 基线协议、§7 限制三段**可直接粘贴**的文字。
- 事实全部来自 `local/references/baseline-survey/` 的一手核实记录（2026-10-23），标注
  `[核实]/[论文声称]/UNVERIFIED`；未复现方法**禁止引用其数字**。
- 待办：额度恢复后按 §3 清单执行 E8（MAStitch 式 / 同模型 Text+Tool-Trace）与 ADR 对照。

## 3.4p 根因定位指标（本轮，离线）

- 新增 `compare/tools/root_cause_metrics.py`（schema `mtagentlens.root-cause-metrics/1`）：
  RootAgent@1/@3、MRR、EntryPoint Accuracy、Chain Link P/R/F1、Chain Depth MAE、
  Boundary-type Jaccard、`missing_root_predictions`；**缺失标签返回 `None` 而不是 0**，
  run id 必须精确匹配（不静默丢样本）。单测 9 项。
- 定义与 E6 落地顺序写入 [`docs/paper/根因指标定义.md`](../../../paper/根因指标定义.md)；
  `修改说明-草稿.md` §0.4 对应状态更新为"指标实现完成、实验待额度"。
- **E6 运行前不得出现任何根因准确率数字。**

## 3.4q 应用层基线 E8 runner（本轮，离线）

- 新增 `compare/scripts/run/app_baseline.py`：campaign 账本 → 应用层 trace 导出 →
  R-Judge record → 固定协议判定 → GT/canonical 一致率汇总；`--client unavailable`
  为**零模型 dry-run**（不产生任何标签，exit 2）。
- 实测（RC fix2 良性账本，`--limit 2`）：2 条 trace、0 标签、`/local/history/experiments/e8-dryrun/`
  下 `traces/` + `summary.json`，`note` 明确未尝试判定。单测 7 项。
- 待额度：先跑 B1（同模型 Text+Tool-Trace），再视预算做 B2（ADR）。

## 3.4r 下一轮可离线推进的审稿实验（不需要模型额度）

| 实验 | 为何离线可做 | 落地方式 |
|---|---|---|
| ~~E5 身份微基准~~ **已完成（本轮）** | 合成信号，无 LLM | `collector/examples/identity_microbenchmark.rs` + `scripts/evaluation/e5_identity_microbenchmark.py`；结果 T1/T4/T5/T6 F1=1.000（T4 缺陷已修复并回归）；**T2/T3 已改为歧义弃权（wrong-merge 0/30，整体 unattributed 33.3%）**；详见 [`docs/evaluation/e5-identity-microbenchmark.md`](../../../evaluation/e5-identity-microbenchmark.md) |
| E4 消融拆分（L0.5） | 需要重放已存档 DB 的评分，不需要模型（Observer 输出已落库） | 需先补 replay CLI（`replay::reauthoritative` 已有实现，缺命令行入口） |
| E3 参数敏感性 | 同上，阈值/权重扫描在 replay 上做 | 依赖 replay CLI |

诚实边界：这三项在跑通前不产生任何论文数字；E5 的 GT 必须**先写死再跑**，
避免"按结果调 GT"。
