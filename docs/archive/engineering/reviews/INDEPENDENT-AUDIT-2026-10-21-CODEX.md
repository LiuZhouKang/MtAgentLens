# MtAgentLens 独立审计：当前工作树仍存在关键正确性与科研有效性问题

审计基准：本次会话环境日期 2026-10-21；HEAD `610f726`，审查对象包含 staged、unstaged 和未跟踪的新代码及 GT。开始审计时，已跟踪差异涉及 459 个文件、12,571 行新增、27,708 行删除；另有大量未跟踪产物。本文是对当前工作树的审查，不是对 HEAD 提交的验收。

## 结论

**不能认定项目已经没有问题、完成架构统一或达到成熟生产产品及已证实的顶级科研成果标准。** 当前已有能运行、具备测试和若干数据契约的研究原型，但采集事实、身份与授权、判定证据、run 隔离、GT 来源和评估输入之间仍有实质断裂。

本轮归并为 **25 组确认问题**。其中部分是新修复没有覆盖完整调用链，部分是当前仍存在的旧缺陷；不将所有问题归因于某一次模型修改。已有交接文档的“已完成”“全绿”不作为正确性证据。

本轮没有修改产品源码、GT、场景或实验数字，没有提交。唯一仓库新增文件为本报告。临时复现脚本、临时 SQLite 和验证日志写在 `/tmp`。

## 方法和覆盖边界

- 主审负责服务端、前端、跨模块契约和结论复核；三个独立只读审查分别覆盖 eBPF/C、Rust 核心、GT/评估。依照仓库规范拆分上下文。
- 比较当前工作树与 HEAD，阅读生产调用链和相关现有测试；对关键问题直接调用当前 Rust 库、Vite 加载的前端源码、实际 Python 函数或 CLI 复现。
- 分清静态证明、临时数据复现、实际数据检查和历史交接材料。临时合成数据仅用于暴露代码缺陷，不作为项目效果或新实验数据。
- 没有加载内核探针、执行攻击场景、调用付费模型或重跑最终实验；没有验证不同内核、真实高负载丢失率、全浏览器视觉表现与长时间稳定性。本轮不是逐行覆盖所有第三方代码的穷尽审计，也不提供“零缺陷”保证。
- 没有做系统性的相关工作新颖性检索或产品市场横评，不能据此确认或否定“奠基性”“世界领先”。这些主张需要可复现实验和独立比较支撑。

## 实际执行的验证

| 检查 | 本轮实际结果 | 解释 |
|---|---|---|
| `cd collector && cargo test --lib --quiet` | 881 passed / 3 ignored | 库测试通过 |
| `cd collector && cargo test --quiet` | 累计 1004 passed / 0 failed / 3 ignored | 全 target 退出 0；日志有 27 条测试结果汇总 |
| `./scripts/verify.sh` | 14/14，通过 | rustfmt、全 target 测试、前端、C 测试、契约等现有门禁通过 |
| verify 内 clippy | 128 warnings，预算 167 | 通过预算门，不等于零警告 |
| 前端 `npm test` | 87 files / 530 tests passed | 主审另行执行 |
| 前端 `npm run lint` / `npm run type-check` | 均退出 0 | 主审另行执行 |
| `make -C bpftools test` | 退出 0 | BoringSSL offset 测试缺少指定 ELF，实际跳过；未执行 verifier 加载 |
| `make -C bpftools all` | Nothing to be done | 不能写成一次干净重建 |
| GT 校验 | 93/93，74 needs_human_review，0 problems | 没有发现下文原始标注缺失等问题，说明门禁覆盖不足 |
| 场景完整性校验 | 93，0 problems | 没有检查下文旧 prompt 与当前 GT 的同源性 |
| 相关 compare Python 测试 | 75 passed / 9 failed | 三个指定测试文件；没有把历史交接里的失败数当成本轮实测 |
| annotator Python 测试 | 6 passed | 未覆盖下文盲标 API 泄露 |
| `python3 scripts/audit_deps.py all` | 退出 0 | cargo 报告 0 live vulnerabilities、1 个未维护警告不设门；npm 生产依赖无达到失败级别的 advisory |

相关 Python 测试命令：

```bash
env/.venv/bin/python -m pytest -q compare/tests/test_evaluate_frozen.py compare/tests/test_evaluator.py --disable-warnings --tb=short
# 55 passed, 6 failed
env/.venv/bin/python -m pytest -q compare/tests/test_dataset_adapter.py --disable-warnings --tb=short
# 20 passed, 3 failed
env/.venv/bin/python -m pytest -q tools/annotator/tests/test_annotator.py --disable-warnings --tb=short
# 6 passed
```

这些失败分别涉及测试 DB 缺少 `timestamp_ns`、claim matching 断言、sanitizer 约束。需要分类修正，不能未经核实全部删除或全部归为“过时测试”。当前统一门禁没有运行上述 compare 测试。

现有 release 文件 SHA256：`40f6e8386f0ce1c01de42e6ad6cbc573b0e8e17d4f5cbde3f9496ab11a4b6e68`。此处只验证文件哈希与交接一致；没有将已有二进制冒充本轮对完整工作树的可复现重建。

## A. GT 和评估证据

### 01 [P1] 原始 reviewer 为空，最终 GT 却声称三方一致

- 位置：`datasets/FuxiBench/ground_truth/_reviewers/data-exfil-direct.json:143` 的唐浩钺记录为 `review: {}`；最终 `ground_truth/data_exfiltration/data-exfil-direct.json:195` 起却记录三票 attack、三票 B、行为数 `[2,3,3]`，并在第 226 行引用其具体判断。
- 实证：93 份 sidecar 中有这一份缺少一名 reviewer 的 label/type；`validate_gt.py` 仍返回 93/93、0 problems。
- 影响：不能声称已留存 93 个完整三方标注证据。`gt_agreement.py:82` 将缺失值作为 `?` 类别进入 κ，而不是拒绝不完整数据。格式校验通过不证明标注真实完整。
- 修复与验收：恢复可追溯原始记录，或重新独立补标及裁决；严格交叉校验 sidecar、最终 GT、人数和来源，缺失即失败。这里确认的是材料缺失和矛盾，**不是对造假的判断**。

当前 `PROVENANCE.md:34` 已明确标注执行者是 reviewer agents；固定姓名保持了，但不能据此声称真实同名人类参与。AI reviewer 本身不违背用户要求 agent 团队审阅的授权；其独立性和专业可靠性仍须证据。74/93 份 `needs_human_review` 也不能当作全部专业复核完成。

另：`gt_agreement.py:95` 起的 Jaccard 实际只比较 `attack_pattern` 类别词集合，不能证明命令、目标资源或执行证据一致。应准确命名为类别集合一致性，并另行验证行为内容。

### 02 [P1] 当前 243 条预期行为被评估加载器全部漏掉

- 位置：`compare/tools/datasets.py:407`，`_expected_claims` 仅对 dict 型 `expected_behaviors` 做转换；当前 v4 使用 list。
- 实证：直接读取 67 个攻击 GT，共 243 条行为，转换后共 **0 ExpectedClaims**；实际 adapter 得到的 67 个攻击 case 也全部为空。
- 影响：下游 attribution/claim coverage 的真实分母丢失，不能用输出的 0.0 评价责任归因能力。
- 修复与验收：按 schema 显式转换 v4 行为、boundary、should_alert、资源及命令语义；输入有预期行为而结果为空应报错。用真实 v4 fixture 验证数量与内容保真。

### 03 [P1] 重新运行 baseline 仍会将新 GT 与旧任务混用

- 位置：`compare/tools/datasets.py:100` 起读取当前 GT，`:345` 起读取旧 `prompt_inputs`，没有版本绑定；`compare/tools/runner.py:65` 起实际使用这组 case 运行并评分。
- 实证：冻结 manifest 的 **86/86** 个场景 SHA 与当前源文件不匹配，当前任务文本也全部不再原样存在于旧 prompt。实际 adapter 同时返回当前 v4 GT 和 86 份旧 prompt，且没有纳入新增 7 个困难良性。
- 例子：`prompt_inputs/cases/exfil-env-secrets/prompt.txt:8` 仍要求 env 导出；当前 `env/scenarios/credential_theft/exfil-env-secrets.json:8` 已改为 `make test` 和另一组 fixture。
- 修复与验收：将 scenario、fixture、prompt、GT、trace 与构建哈希绑定为不可变数据集发布版本；历史复现与新 RC 入口分开，hash 不一致时拒绝运行。保留历史数据本身合理，混合版本不合理。

### 04 [P1] conformant FPR 按场景名合并，污染重复运行分母

- 位置：`scripts/evaluation/final_verdict_metrics.py:143` 起按 `scenario_id` 收集合格集合；条件 `conformant is not False` 还接受未知值。源审计 `audit_benign_conformance.py:93` 起没有保留 run 唯一键。
- 实证：同场景 r1 合格/Safe、r2 不合格/Unsafe，加 r3 未知/Unsafe，实际 CLI 将三条都纳入 conformant，给出 2/3；只有 r1 有明确合格证据。
- 修复与验收：用 run 唯一 ID 和产物 hash 关联，只接受明确 `True`，unknown 单列；覆盖同场景至少三次重复的混合结果。

`final_verdict_metrics.py:52` 的 attack 汇总也没有接入独立执行审计。它的 `actionable_rate` 作为无条件处置率并非算术错误，**但不是执行条件召回**；不得把检测器自己的 `evidence>0` 当作攻击真实执行的唯一分母。

### 05 [P1] 评估入口仍用中间 Unsafe 告警代替运行最终裁决

- 位置：`env/scripts/evaluate_frozen.py:80` 起查询任意 `responsibility_alerts.decision='Unsafe'`；`compare/scripts/eval/standard_evaluation.py:167` 起保留同类逻辑。
- 实证：临时 DB 的 `runs.final_decision=Safe`，同时存在历史 Unsafe 告警，真实 `_alert_rows(..., True)` 仍返回检测命中。
- 影响：与 canonical `runs.final_decision` 口径分裂，最终推翻的中间结果仍可计 TP/FP；查询也缺少明确 run 限制。
- 修复与验收：运行级指标统一读取指定 run 的最终裁决，事件级告警另列；缺少最终裁决单列未知。历史告警指标只能放在明确历史协议下。

### 06 [P2；正式盲标前必须修] 盲标 API 返回模型答案和他人记录

- 位置：`tools/annotator/server.py:171` 原样返回 runs，`:192` 原样返回 claims；`:494` 起返回同 run 的全部 annotations。
- 实证：`reveal=False`、`model_revealed=False` 时，payload 仍包含 `runs.final_decision=Unsafe`、claims 严重度/置信度及告警标题摘要。现有 6 个测试仍通过。
- 边界：UI 隐藏部分正文不等于服务器没有发送；这不证明当前 93 份场景级 AI GT 使用过该工具或受其污染。
- 修复与验收：服务器按盲标白名单返回事实；本人标注、他人标注、裁决通道分离；保存记录绑定 DB SHA 和修订历史，防止覆盖原始记录。

## B. 采集事实和完整性

### 07 [P1] 子进程自愈逻辑依赖生产者永远不填的父 PID

- 位置：`bpftools/process/process.c:721`、`:747` 根据 `e->ppid` 认领；但 `process.bpf.c:317`、`:379`、`:436`、`:520`、`:645` 把对应文件/网络事件的 ppid 设为 0。
- 影响：EXEC 丢失或 fork 后不 exec 的子进程，文件/网络事件继续被 FILTER/PROC 模式过滤。交接所谓自愈尚未真正成立。
- 修复与验收：生产事件写入真实父进程身份，并以生产事件结构覆盖“缺 EXEC、父进程已知”的过滤链路。

### 08 [P1] 文件去重把后续写入/截断压缩成首次读取

- 位置：`process.c:254` 的聚合键只含 pid+path；`:321` 重复即返回，`:356` 保留首次 flags。Rust `evidence/action_classifier.rs:69` 恰好依赖 flags 区分读写。
- 触发：同一进程先只读打开，再用 `O_WRONLY|O_TRUNC` 打开同一路径。
- 影响：写入/截断事实消失或被记录成读，安全语义错误。
- 修复与验收：聚合区分进程生命周期、操作 flags 和时间；不能把不同操作合并为同一类。另表满分支 `process.c:368` 与 `:775` 会重复输出同一事件，应一并覆盖。

### 09 [P1] 噪声过滤器无条件删除安全相关路径

- 位置：`process.bpf.c:47` 起过滤 `/proc`、`/sys`、`/dev`、`/lib` 等前缀；mutation/rename 同样调用（`:425`、`:509`）。
- 影响：进程环境/内存、`/dev/shm`、系统库变更等事实无法进入分析链；不计作丢失。没有目录边界还误吞 `/library` 一类普通路径。
- 修复与验收：按事件语义和目标范围过滤，保留安全相关元数据与过滤计数；对读取和变更使用不同策略，测试目录边界。

### 10 [P1] 系统调用尝试被写成已确认成功行为

- 位置：`process.bpf.c:281`、`:458`、`:660` 等仅采 sys_enter，没有返回值；`runtime/routing.rs:774` 起转为动作，`classification/final_decision.rs:133` 称为 confirmed OS action。
- 影响：EACCES 的 open、失败的 unlink/connect 与成功事件无法区分，影响检测解释和攻击执行分母。成功 open 本身也不证明读过文件内容。
- 修复与验收：关联 syscall exit，建模 attempted/succeeded/failed 和返回值；保持“可疑尝试值得告警”与“已发生读取/删除/外传”的事实层次。

### 11 [P1] helper 关闭、丢失统计与裁决质量没有闭环

- 位置：`framework/runners/common.rs:180` 使用 kill_on_drop，`:314` 的 TERM/等待只在 stdout EOF 后执行；runtime 丢弃输入流可绕过它。`tls_capture.c:735` 只注册 SIGINT，不处理新清理代码发送的 SIGTERM。
- 影响：结束时可能跳过聚合 flush 和最终统计；stderr 的探针状态/丢失计数主要转日志，没有变成运行质量状态。`final_decision.rs:203` 在无观察时仍可给出 Authoritative Safe，无法区分“观察正常且无违规”与“没采到”。
- 修复与验收：统一 supervisor 执行停止、排空、wait 和超时强杀；按来源记录末次计数与缺失状态，最终结果必须带可观测性质量，不能把缺统计当零丢失。

### 12 [P2] benchmark 的健康统计解析会覆盖、漏计或重复累计

- 位置：`env/src/mtagentlens_benchmark/engine/collector_manager.py:406` 起新解析被 `:430` 起旧解析覆盖。
- 直接执行当前解析函数：rate_limit_drops=123、kernel dropped=0 得到总丢失 0；带空格的 dropped=17 得到 0；TLS 累计 10、15、最终 15 得到 40。
- 修复与验收：区分来源和损失类型，对累计计数取最终值或正确增量，缺少最终样本显式 unknown；用真实日志格式覆盖空格及周期/最终统计。

## C. 核心分析、身份与证据

### 13 [P1] 无关 Agent 的正常边可使已有攻击图模式消失

- 位置：`graph/pattern_matcher.rs:76` 收集全图所有模板类型边，`:105` 起要求它们全部处于同一连通分量。
- 当前库直接复现：有效 data_exfiltration 子图命中 1 次；另一无关 Agent 增加一条普通 IntentCausesAction 边，命中变为 0。
- 修复与验收：在候选连通分量/子图中寻找满足模板的匹配；无关上下文扩张不应撤销已有局部证据，同时保留反误报的连接要求。

### 14 [P1] Observer 超时只覆盖响应头，响应体可无限阻塞采集

- 位置：`observer/provider.rs:174` 的 timeout 包住 request，`:183` 的 body.collect 没有同一截止时间；主事件循环同步 await 分类。
- 直接调用当前库：本地服务器立刻返回 200 头后不发 body，配置 timeout_ms=50，300ms 外层截止仍然触发。
- 影响：主循环停止消费采集事件，结束/finalize 也可能一直等待。
- 修复与验收：一个端到端截止时间覆盖连接、头、限长响应体和解析，并明确取消；超时回到 Uncertain/错误状态。覆盖慢体、无 EOF 和过大 body。

### 15 [P1] 身份合并没有同步委派记录，合法共享变成未授权

- 位置：`graph/graph_builder.rs:246` 的 remap 只改图 Agent 身份，未同步 delegation_records；runtime 随后将旧记录传给 ClaimBuilder，授权判断按身份字符串匹配。
- 当前 GraphBuilder/ClaimBuilder 直接复现：合并前 authorized=1、unauthorized=0、claim=PolicyViolation；合并后 authorized=0、unauthorized=1、claim=TaintedContextUse。
- 影响：身份精化改变了授权事实，产生错误污点归因。不能把“精化后变化”都当成更权威的结论。
- 修复与验收：图、委派、污点和后续引用共用 canonical 身份映射；验证 remap 前后同一授权关系不变，并与 replay 一致。

### 16 [P1，有条件] Observer 的旧 Safe 缺少候选范围，可覆盖后来未审候选

- 位置：`classification/final_decision.rs:40` 的 ObserverSafe 没有候选/证据身份，OpenCandidate 仅有 kind；`:181` 在 open candidate 分支前返回 Safe。`runtime/runtime.rs:1965` 起按整个图追加泛化候选。
- 当前库 API 复现：ObserverSafe 与另一未审候选聚合为 Safe；生产触发链为静态确认，未运行外部模型端到端复现。现有测试只覆盖“Safe 清除它确实审过的候选”，无法证明它审过后来的候选。
- 修复与验收：将裁决绑定 agent、候选 ID、证据集合/版本和观察时间，仅清理已覆盖候选；未审候选保持 review。单纯交换分支会把已审 Safe 候选也永远留作 review，并非完整修复。

### 17 [P2；科研评分冻结前必须修] 第二阶段只拿新增证据，live/replay 评分不一致

- 位置：`evidence/correlator.rs:482` 跳过 linked_pairs；live finalize 重用该 correlator，而 `runtime.rs:1923` 起把返回 slice 当权威评分输入。
- 当前库复现：同一 exact match，stream evidence=1，live batch evidence=0 且产生 non-link；fresh replay batch evidence=1。固定相同图，完整与空证据的 D5 从 .93 变 0，D3 和总分也变化。
- 边界：图中已有证据仍可保护 hard-rule/claims，不能声称该例直接把 hard violation 变成 Safe。
- 修复与验收：区分新增写入 delta 与完整评分视图；权威评分使用不可变完整证据集。回放不能只靠默认 ScoreSignals 和数量近似重建 live 状态，须定义并验证等价契约。

### 18 [P2；机制有效性待修] 行为基线既采错对象，又反复归一化

- 位置：`runtime.rs:1095` 起用基线自己的 keys 计算偏离；`:1116` 起把全图旧边重复喂给每个 Agent，syscall/port 为 None，网络 host:port 被当文件路径。`scoring/baseline.rs:182` 起将上次已归一化的频率加新计数，再除累计样本数。
- 直接复现：两个完全相同的 50 次 openat 批，频率从 1.0 变成 .51；输入行为分布未变。
- 影响：D6 不能按声明检测新 syscall/端口/目录，且跨 Agent 污染、统计失真。
- 修复与验收：仅按所属 Agent 记录新增观测；分开原始计数与派生频率，用当前窗口对稳定基线比较。验证重复相同分布不漂移、不同 Agent 隔离、新行为能改变偏离。

## D. 持久化、服务端与前端

### 19 [P1] 持久化唯一约束缺 run_id，第二次运行可静默丢记录

- 位置：`persistence/schema.rs:163` 的 process_events 唯一键为 (pid,start_time_ns)，不含 run_id；多个其他 run 级实体也使用全局键，需逐表审查。
- 当前 PersistenceWriter 直接复现：同一进程写 run-a/run-b，run-a 有 1 行、run-b 为 0，dropped=0、write_failures=0。
- 影响：同一路径数据库累积多个 run 时，新的观测被去重，run 证据不完整且计数没有报告损失。
- 修复与验收：定义跨 run 与 run 内实体的主键语义，迁移约束及关联；必须验证跨 run 不互相覆盖、同 run 重复仍幂等。

### 20 [P1] API 对同一请求分别选择不同 run

- 位置：`server/web.rs:1309` 的 resolve_db_run_id 不接收 requested ID，总取最新；evidence 在 `:1332` 使用它。alerts 的 `:1495` 却按请求精确选择或返回 409。
- 触发：数据库有旧 A、新 B，请求 A evidence 得到 B；graph 已有 A snapshot 时正确，但 A 无 snapshot 的 `:1286` 回退也可能取 B。
- 修复与验收：所有资源共用显式 run resolver，单 run 兼容、精确匹配、歧义 409、live 语义一致；用同一多 run fixture 验证 graph/alerts/evidence/snapshots/DB 导入。

### 21 [P1] live DB 下载忽略 WAL 已提交事务

- 位置：`persistence/schema.rs:1054` 启用 WAL；`server/web.rs:854` 的 live 下载进入 `:1605`，只读主文件。
- 临时数据库复现：保持 writer 打开，提交 1 行而未 checkpoint；实际 live 查询为 1，按端点方式复制主文件后的查询为 0，WAL 4152 字节。
- 边界：不是所有 WAL 时刻都漏；当存在未 checkpoint 的已提交事务时，下载不完整，裸文件读取也没有一致快照保证。
- 修复与验收：使用 SQLite Backup API 或等价一致快照导出，避免仅复制主文件。覆盖活跃 WAL、并发写入和 checkpoint。依据：[SQLite WAL §2](https://www.sqlite.org/wal.html)、[Online Backup API](https://www.sqlite.org/backup.html)。

### 22 [P1] 前端并发加载能显示另一个 run 的证据

- 位置：`frontend/lib/store.ts:330` 的 loadRun 在各次 await 后没有验证当前请求或 run；生产调用来自初始化、选择器和 LiveSession。
- Vite 直接加载当前源码复现：先加载 A，再加载 B，B 先返回；A 后返回后，activeRunId=B，但图节点为 A-node。
- 修复与验收：请求序号/AbortController 和提交时 ID 检查覆盖 DB、API、失败路径；快速切换或自动初始化不能提交陈旧结果。

### 23 [P2] live 首载成功后实际切回 replay

- 位置：`store.ts:335` 的 keepLive 只保护开头；`:354` 调 applyDbImport，而后者 `:237` 无条件 appState=replay；`live-session.ts:287` 对同一 run 早退。
- 直接复现：调用 loadRun(live) 的同步阶段为 live，await DB 成功后变 replay，同时 live.run.isActive=true。
- 修复与验收：将加载模式作为提交契约传入，测试 await 完成后的状态和同 run 轮询，而不只断言开头同步状态。

### 24 [P2] 告警精简与调查交互契约冲突，回溯缺证据时还会高亮无关边

- 位置：`store.ts:167` 删除 graph_snapshot 后，生产 `live-alert-feed.tsx:113` 仍将该告警交给 focusAlert；`investigation-context.ts:55` 因缺 snapshot 返回默认上下文。
- 直接复现：store 中已有 1 个告警，点击对应 focus 后 activeIncidentId=null。
- 同类缺口：`lib/traceback.ts:26` 匹配不到证据就退回全部 PBC 边，不能将它们呈现为该告警的证据路径；engineRef 仅测试注册，生产没有赋值，相关桥方法静默无效。
- 修复与验收：告警身份和调查上下文不依赖随意裁剪的完整快照，按需取图/证据；无关联证据时明确未知。验证从真实 SSE 精简告警点击进入调查，并核对高亮边确有证据关系。

### 25 [P2] 主入口文档、能力清单和现有门禁仍非同一事实来源

- 位置：`README.md:450` 起仍有旧通道分布和评估表，首个 TPR/F1 行未统一作废；`docs/capabilities.json`、`CLAUDE.md` 仍描述已删除的 legacy server/缺失路由等过期状态。当前路由确已增加，不能把这些旧文档直接当缺陷依据。
- 实证：verify 14/14 可以在本报告 01–24 存在时通过；compare 的 9 个聚焦失败也不在统一门禁内。128 个 clippy 警告中存在 unused/dead-code，不能称已彻底简洁统一。
- 修复与验收：当前结果页只指向冻结版本证据，旧数字显式归档；能力清单由生产注册表和契约生成/校验；门禁覆盖真实 v4 GT、数据集 hash、多 run 和跨阶段不变量，保留测试数量与失败状态。

## 修复顺序与验收要求

1. **先锁定证据协议。** 将本报告逐项转成可复现回归；区分尝试/成功、未知/无违规、候选/已审、当前/历史。修复不应只删除告警、放宽规则或让门禁忽略失败。
2. **优先修运行事实与隔离。** 07–15、19–23：采集语义、停止排空、Observer 截止时间、身份授权、多 run 与一致快照；对每个修复验证生产入口，不能只测新 helper。
3. **再修 GT 与评估发布协议。** 01–06、16–18：补全原始记录，处理待复核项，升级 v4 加载器，锁定数据版本和执行分母，统一最终裁决。缺失记录不能由裁决者回填为“原始三人一致”。
4. **冻结后做小样本端到端验收，再开展全量实验。** 同时记录 ASR、所有 run 结局、独立执行条件召回、采集质量、最终裁决、归因与链路准确性；Observer 配对重复、组件消融、开销与延迟均绑定相同协议和版本。
5. **最后收口公开主张。** 基于最终证据更新论文、README、能力清单和产品流程；完成浏览器功能/视觉、干净构建、内核矩阵与长时负载验证。

科研上的关键是证明机制测量的对象、输入和分母确实对应其主张；更多模块、更长设计文档或更高 κ 都不能替代这一步。可参考 [NIST AI 测量与评估](https://www.nist.gov/ai-measurement-and-evaluation) 对指标、数据集和可靠性等属性的要求；该参考不构成任何认证或符合性结论。

## 本地证据索引

- `/tmp/mtagentlens-audit-gt-eval.md`：GT/评估独立审查及 CLI 实证。
- `/tmp/mtagentlens-audit-core.md`：Rust 核心独立审查及当前库直接复现。
- `/tmp/mtagentlens-core-audit-probe.rs`：图模式、候选范围、阶段证据、基线和身份授权复现。
- `/tmp/mtagentlens-core-observer-timeout.rs`：响应头完成、body 不结束的本地 Observer 复现。
- `/tmp/mtagentlens-core-cross-run.rs`：真实 PersistenceWriter 跨 run 复现。
- `/tmp/mtagentlens-frontend-audit-probe.mjs`：真实 Vite SSR 模块的 live、告警 focus、A/B 加载竞态复现。
- `/tmp/mtagentlens-core-audit-cargo-test.log`、`/tmp/mtagentlens-core-audit-verify.log`：Rust 全 target 和完整门禁日志。
- `/tmp/mtagentlens-audit-frontend-{test,type,lint}.log`、`/tmp/mtagentlens-audit-deps-online.log`：前端和在线依赖审计日志。

`/tmp` 证据仅是本次本地复现材料，后续可能被系统清理。本报告保留了核心输入、输出、位置和验收要求；实施阶段应将相应最小复现转为仓库持久回归测试。
