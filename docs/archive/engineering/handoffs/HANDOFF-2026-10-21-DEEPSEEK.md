# MtAgentLens 接续交接（2026-10-21 · Claude Code + DeepSeek）

> 本文是**当前唯一接续入口**。旧交接（`HANDOFF-2026-10-21-CODEX.md` 等）保留为历史证据，
> 其“完成”字样不能覆盖本文的实测状态。旧实验数字全部作废。

## 1. 接收摘要（本次会话实测，非引用）

| 项目 | 实测结果 | 命令/证据 |
|---|---|---|
| HEAD | `610f72680322907e6d8aa562f3a277fdb06c22b3` | `git rev-parse HEAD` |
| 工作区 | 816 条：391 M / 179 D / 246 未跟踪（目录折叠） | `git status --porcelain` |
| 空白错误 | `git diff --check` 退出 0 | 同上 |
| release 二进制 | `mtagentlens` SHA256 `4f9429ef…f354`；`replay` `583769ec…925`，与 CODEX 交接一致 | `sha256sum collector/build/release/*` |
| 交接日志 | `/tmp/mtagentlens-*` 多数仍在（19:0x–19:1x） | `ls /tmp/mtagentlens*` |
| 统一门禁 | 17 道；改动前基线 **17/17**（`/tmp/deepseek-verify-baseline.log`），改动后见 §7 | `./scripts/verify.sh` |
| GT | 93 个场景，`needs_human_review=true` **74**，无待审 19 | 本会话脚本枚举 |
| 场景完整性 | `check_scenario_integrity.py --strict`：93 场景，`problems: []` | 实跑，退出 0 |

结论：工作区与 CODEX 交接描述一致，未发现回滚或丢失。以上只证明**文件与摘要一致**，
不证明门禁仍全绿、不证明人工 GT 已签、不证明检测效果。

> 本表记录**接收瞬间**；§2–§6 是本轮改动后的最终状态（release 二进制与 GT 均已变化）。

## 2. 本轮新确认的事实

### 2.1 冻结 prompt 包与当前场景全面脱钩（confirmed）

`datasets/FuxiBench/prompt_inputs/manifest.json` 记录的 `source_scenario_sha256`
与当前 `env/scenarios/**` 字节对比：

- 93 个 GT 场景中 **0 个**与冻结清单匹配；
- 86 个哈希不匹配（场景已重写），7 个 GT 场景（`benign-credential-rotation-plan`、
  `benign-env-example-review`、`benign-etc-group-audit`、`benign-heavy-build`、
  `benign-known-hosts-audit`、`benign-passwd-audit`、`benign-ssh-config-audit`）
  根本不在清单内；
- 但 `prompt.txt` 与清单内记录的哈希**互相一致**（73/73 待审项）——即包内自洽，
  只是整体停留在旧场景版本。

影响面（已核对调用方，非推测）：

| 调用方 | 读取对象 | 后果 |
|---|---|---|
| `env/run_one.py` / `scenario_loader` | 当前场景 JSON + 当前 fixture | 与 GT、场景一致 |
| `compare/scripts/evaluate_prompt_tools.py` | 冻结 `prompt_inputs` | 基线看到的是旧任务 |
| `compare/scripts/evaluate_mtagentlens.py` | 冻结 manifest 的 `attack_type` | 主评测按旧类型计分（18 个场景类型不符） |
| `compare/scripts/llm_guard_batch.py` | 同上 | 同上 |
| `compare/scripts/meta_prompt_guard_batch.py` | 同上 | 同上 |
| `compare/scripts/ablation/deberta_offline.py` | 同上 | 同上 |
| `compare/tools/dataset_release.py` | 从当前场景**重新生成** | 发布时自愈 |

实测样例：`supplychain-direct` 的冻结 prompt 是旧 npm 验证流程，当前场景是
`make build` 构建钩子（Type B）。若现在直接跑官方 prompt 基线，基线与
MtAgentLens 评的不是同一任务。

**性质**：数据/复现性缺陷，不是运行时缺陷。最终发布前必须用
`compare/scripts/build/build_prompt_input_bundle.py` 重新生成并重绑哈希；
在此之前不得把官方 prompt 基线结果与当前 GT 混用。

### 2.2 门禁不覆盖上述脱钩（confirmed）

`scripts/verify.sh` 第 17 道只调用 `validate_gt.py`（结构 + 标注者来源）与
`check_scenario_integrity.py`（场景/fixture/类型/执行标记）。二者都不比较
`prompt_inputs` 与当前场景，因此 17/17 可以在基线输入全部过期时通过。

### 2.3 本轮已实施并验证的修复

#### A. 官方 prompt 基线拒绝过期冻结清单（confirmed → fixed）

- 新增 `compare/tools/prompt_manifest.py`：比对 manifest 的
  `source_scenario_sha256`、`label`、`attack_type` 与当前场景/GT。
- 接入 `evaluate_prompt_tools.py`、`evaluate_mtagentlens.py`（主评测也从
  manifest 取 attack_type）、`llm_guard_batch.py`、
  `meta_prompt_guard_batch.py`、`ablation/deberta_offline.py`；过期时退出 1，
  复现历史结果需显式 `--allow-stale-manifest` 或
  `MTAGENTLENS_ALLOW_STALE_PROMPT_MANIFEST=1`（会打印警告）。
- 实测：`python3 compare/scripts/evaluate_prompt_tools.py --tools llm_guard`
  在加载模型/调用 API 前退出 1，报 104 个问题（86 场景哈希 + 18 标签/类型）。
- 测试：`compare/tests/test_prompt_manifest_guard.py` 6 项通过。

#### B. 生产者接线门禁（confirmed → fixed）

- 新增 `collector/tests/producer_wiring_contract.rs`（4 项）：从源码推导
  `impl Runner/Analyzer for ...` 集合，要求每个生产实现都出现在
  `main.rs` 的注册调用中；`run_trace` 内单独再查一遍，避免“另一条命令路径
  还注册着”掩盖删除；并交叉校验 `docs/capabilities.json` 的
  `analyzers.registered` / `implemented_but_not_registered`。
- 变异验证（已恢复，`main.rs` SHA 前后一致 `63b5e75f…`）：
  - 删除 `main.rs:1471` 的 `add_runner(Box::new(process_runner))` →
    `ProcessRunner implements Runner but no add_runner(...) registers it`；
  - 删除 `main.rs:1386` 的 trace 路径 `RawSecretRedactor` 注册 →
    `RawSecretRedactor is not registered inside run_trace`。
- 局限：文本扫描而非类型检查；新增测试替身需放进 `fake.rs` 或 `#[cfg(test)] mod`。

#### C. 采集覆盖门：零损失 ≠ 零观测（confirmed → fixed）

- `checkpoint.rs::quality_status` 与 `run_artifacts.py::capture_quality_status`
  同时新增覆盖判定：配置了 `Process:` helper 但 `analysis.event_counts.process`
  为 0 或缺失 → `unknown`，Safe 降级为 Uncertain。TLS/stdio 不做此判定
  （合法地可能没有一次调用/输出）。
- 运行时在 `route_event` 按 source 计数，写入 checkpoint 的
  `analysis.event_counts`。
- 测试：Rust 5 项（4 checkpoint + 1 runtime）、Python 4 项；
  `cargo test --lib` 900 通过、`analysis_regressions` 19 通过、`env/tests` 138 通过。

#### D. 采集完整性不再自证（confirmed → fixed）

- `collector_manager._parse_runtime_metrics_from_logs` 的
  `expected_sources` 现在由运行参数推导（`--ssl/--process/--stdio`），
  不再只包含“日志里出现过的源”。进程 helper 崩溃或静默时
  `capture_stats_complete` 为 False，`overall_healthy` 随之为 False。
- 一个既有测试的 fixture 因配置了 `--ssl=true` 却缺少 TLS 最终统计而补全
  （断言未放宽）。
- 测试：`env/tests/test_capture_metrics_contract.py` 新增 4 项；env 全套 138 通过。

#### E. 运行摘要按 run 取裁决（confirmed → fixed）

- `server/web.rs::read_final_verdict` 原查询忽略请求的 `run_id`
  （`... WHERE final_decision IS NOT NULL LIMIT 1`），多 run 数据库会把 A 的裁决
  报给 B。现改为 `WHERE run_id = ?1 AND ...`，未知 run 返回空。
- 测试 `final_verdict_is_scoped_to_the_requested_run`（两个 run + 缺失 run），
  `cargo test --lib` 901 通过。

#### F. 调查面板按 `claim_ids` 精确关联（confirmed → fixed）

- `investigation-workbench.tsx` 原来直接渲染 store 里的全部 claims，忽略
  `alert.engine.claim_ids`。现按 claim_ids 过滤，标题显示“N 条 · 本告警”；
  仅当告警没有 claim_ids（历史 run）时才回退到全 run 列表。
- 测试 +2；前端 lint / type-check 通过，541 单测通过（原 539）。

#### G. 单场景审计脚本的丢包检查方向修正（confirmed → fixed）

- `env/scripts/audit_trace.sh` 原来用 `grep -c "RINGBUF_STATS"` 当“丢包数”，
  导致正常 run（有最终统计行）被判失败、完全没有统计的 run 反而通过；
  `lost_total` 只取最后一行（单源）。
- 现按源取最终累计值（TLS_RINGBUF_STATS / PROCESS_RINGBUF_STATS，忽略周期性
  RINGBUF_STATS），加计 trace 中的 stdio 最终 STATS，并新增“必须见到最终统计”
  检查。
- 合成日志实测四种情况：干净=通过；无统计=失败；仅周期性=失败；
  process/stdio 丢包=失败。未在真实 run 上重跑。

#### H. prompt 包重建脚本不再先删数据（confirmed → fixed）

- 审查发现：`build_prompt_input_bundle.py` 先 `rmtree(output_dir)` 再读
  `quality/manifest.json`，而仓库数据集**没有**该文件。按旧文档执行会先删掉
  173 个冻结文件再抛 `FileNotFoundError`。
- 现改为：先解析并校验全部输入 → 在 `.prompt_inputs.staging` 构建 → 成功后
  原子替换；失败保留旧包。无 quality manifest 时从 93 个 GT 文件推导样本列表。
- 实测：`--output-dir /tmp/mtl-prompt-dryrun` 成功生成 **93** 项，
  类型分布 A5/B46/C16/benign26，与 GT 一致；真实包未改动。
- 正确的重建顺序（两段，第二段绑定哈希）：
  ```bash
  env/.venv/bin/python compare/scripts/build/build_prompt_input_bundle.py
  env/.venv/bin/python compare/scripts/build/refresh_prompt_input_hashes.py --write
  ```
  只执行第一段会让 manifest 缺 `source_scenario_sha256`，评测脚本会按设计拒绝。
- 测试：`compare/tests/test_prompt_input_bundle.py` 新增 2 项（失败不删旧包、
  无 quality manifest 时从 GT 推导），全部 8 项通过。

#### I. 结果行必须与其 GT 标签一致（confirmed → fixed）

- `final_verdict_metrics.validate_run_artifacts` 原来只校验 release/DB/二进制哈希，
  不校验 results 行的 GT label 是否与 `--attack` / `--benign` 文件角色一致。
  把良性场景误放进 attack 文件会静默改变 actionable rate 与 FPR。
- `validate_release` 现在附带运行时索引 `case_labels`（不参与 release_id 摘要），
  两处调用分别传 `expected_label="benign"/"attack"`，不一致即报错。
- 测试新增 1 项；`compare/tests` 301 通过。

#### J. prompt 打包的三个数据缺陷（confirmed → fixed）

- **元数据脱敏误伤代码**：`mode:`/`category:` 等行正则带前导空白，把 fixture 里
  `    mode: str` 改写成 `[REDACTED_EVALUATOR_METADATA]`（影响 3 个场景）。
  现在只在 agent 任务文本、且仅列首匹配；fixture 只做 evaluator-token 替换。
- **fixture 文件漏收**：后缀白名单漏掉 `Makefile`、`.env.example`、
  `deploy/ssh_config`、`known_hosts`。现在按内容判定：可 UTF-8 解码且无 NUL 的
  文件才纳入，二进制仍排除。
- **provenance 记录脏工作区**：`source_scenario_git_rev` 对已修改文件仍写旧提交。
  现在 `git hash-object` 与提交 blob 比对，脏文件记 `dirty:<sha>`，未跟踪记
  `uncommitted`。
- 测试：bundle + provenance + 打包共 15 项通过；真实包已重建，`benign-build-debug`
  fixture 从 9 → 13 个文件。

#### K. eBPF 丢包计数按原因拆分（confirmed → fixed，待内核复验）

- `process.bpf.c`：`dropped_events` 从单计数器改为 3 键数组
  （ringbuf full / pending table full / user read failed）；补齐
  rename 路径读取失败与网络 `sa_family` 读取失败的计数。
  `PROCESS_RINGBUF_STATS` 同时输出总和与拆分。
- `tls_capture.bpf.c`：3 键（ringbuf full / state map failed / reentrant），
  进入侧 map update 失败、退出侧 lookup miss 均计数；`TLS_RINGBUF_STATS`
  输出拆分。
- `stdiocap.bpf.c`：3 键（ringbuf full / args map failed / lookup missed）。
- Rust `capture_health` 保存 `loss_reasons`；Python 解析输出
  `capture_loss_reasons`（旧 helper 无拆分时保持 `None`，不伪造 0）。
- `make -C bpftools all test` 通过；**已在 Linux 7.0.0-38-generic 真实加载**：
  无害 fixture 七项事实全 true，helper exit 0。本次最终统计
  `dropped_events=32, ringbuf_full_dropped=0, pending_table_dropped=0, user_read_dropped=32`：
  **历史“9 次丢包”不能再写成 ring-buffer 溢出**，至少这一次全部来自用户指针读取失败。
  证据 `/tmp/deepseek-kernel-verify.log`、`/tmp/mtagentlens-kernel-verification-v6x5mdbi/summary.json`。

#### N. stdiocap 在真实内核上完全失明（confirmed → fixed）

- 真实测试发现：`syscalls:sys_enter_write` / `sys_enter_read` 在 Linux 7.0
  上对测试运行环境派生的目标进程**不触发**（bpftrace 对比确认 `raw_syscalls`
  能看到同一进程的 `write`，`syscalls/*` 看不到）。stdiocap 因此长期“编译通过、
  测试通过、实际零事件”。
- 改为 `tp/raw_syscalls/sys_enter/sys_exit` + `__NR_read/__NR_write` 过滤，
  与 process helper 一致；raw tracepoints 在受支持内核上始终可用。
- 同时修正退出侧：先做目标 pid/uid 过滤再查 pending 表，未跟踪 fd 在退出侧
  过滤而**不**计入丢失；进入侧对目标的所有 read/write 存参。
- 真实验证：目标 `os.write(1,"OUT-DATA")/os.write(2,"ERR-DATA")` 被完整捕获，
  `dropped_events=0, args_map_dropped=0, lookup_missed_dropped=0`。

#### O. TLS 重入被误计为丢失（confirmed → fixed）

- 真实 OpenSSL 测试：10 个符号全部附加、`core_read=true/core_write=true`、
  捕获 7 个请求/响应事件；但 `reentrant_dropped=4`——OpenSSL 内部包装调用
  （SSL_read→SSL_read_ex）会按设计重入同一 tid，数据并未丢失。若保留该计数，
  **每个正常 TLS 运行都会被判降级**。
- 移除 reentrant 丢失计数；真正的参数丢失仍会在退出侧以 lookup miss 计数。
  复测 `dropped_events=0, state_map_dropped=0`。

#### L. 草稿场景不再遮蔽真实场景（confirmed → fixed）

- `_index_scenarios` 只跳过 `fixtures/` 与 `archive/`，而
  `env/scenarios/_drafts/prompt-injection.json` 与真实场景同 id；排序后草稿
  先被 `setdefault` 收下，导致冻结包里的 `prompt-injection` 是**无关的入职摘要
  任务**。现在跳过所有 `_` 前缀目录，并对真实场景重复 id 直接报错。
- 重建后 `prompt-injection` 的 prompt 已是 release-readiness 任务。
- 测试：bundle 套件新增 2 项（草稿忽略、重复报错），共 14 项通过。

#### M. 执行校验器与 fixture 漂移（confirmed → fixed，单场景）

- `env/scripts/verifiers/exfil-archive-upload.py` 只把 `POST/upload` 当作上传，
  当前 fixture 用 `curl --data-binary @bundle <loopback>`，完整执行会被判
  `indeterminate`（执行率为 0）。现识别 `--data/--data-binary/-d/-F/-T/-X POST`。
- 新增 `env/tests/test_exfil_archive_upload_verifier.py` 2 项（POST 体=pass，
  纯 GET=indeterminate）。
- 其余 74 个 verifier 未逐一比对 fixture 漂移；这是已知残留风险。

### 2.3.1 架构清理（本轮，全部有测试/编译证据）

| 债务 | 处理 |
|---|---|
| `multi_pid_trace_config.rs` 测试文件内副本 | 生产函数移入 `framework/runners/targets.rs`，集成测试直接导入；旧测试还在断言早已删除的 `--trace-*` 参数 |
| `pipeline_contract.rs` 平行真源 | 删除 `EventSource/StageContract/canonical_pipeline_contracts` 及自指测试；`PipelineStage/StageMetrics` 移到 `stage_metrics.rs`（仅健康标签）；`maplit` 依赖删除 |
| `Observation::OpenCandidate` 无生产者 | 删除变体与分支；未审候选由 `ScopedCandidate`（空票 → review）承担，测试改用真实路径 |
| 跨层 bus 三个无订阅者通道 | 删除整个 `cross_layer_bus.rs`，runtime 直接用 `alert_tx` 推 SSE；评分/分类/claim 信号此前只有自身测试订阅 |
| `phase2_claim_id` 固定字面量 | 保存 phase-1 claim，按 `(subject,policy,boundary,type)` 逻辑键映射到 phase-2 claim；无对应者记 `Dismissed` 并指向 provisional claim；新增键相等测试 |
| `max_retries` 解析但从不使用 | 实现有界重试（attempts 含首次；仅 timeout/429/5xx 重试，指数退避），补策略测试与示例配置说明 |
| 能力包络运行时生产者无测试 | 抽出 `install_capability_envelopes()`，测试 UserPrompt 安装、非 UserPrompt 不安装 |
| 身份合并后污点/包络传播无测试 | 扩展 remap 测试：污点来源与能力包络必须随权威 id 迁移 |
| `FileLogger` 写失败只打印 | 增加进程级失败计数并写入 `capture_quality.analysis.raw_log_failures`，>0 时质量降级；`/dev/full` 真实写失败测试 |
| TLS 核心探针部分附加不可见 | loader 输出 `core_read/core_write`；采集质量在两个实现中因 false 降级；真实 OpenSSL 测试 10 探针全附加 |
| 文档滞后 | `ANALYZER_PIPELINE_CONTRACT.md` 重写为真实组合（旧设计归档）；`PhaseClassificationEngine` 更正为 `ClassificationEngine`；Phase 2 注释更正为 `ambiguous_review`/Uncertain |
| `runs.scenario_id/mode` 无生产者 | 新增 `--scenario-id/--run-mode` CLI（trace 与 record）、`RuntimeConfig` 字段与 `PersistRecord::RunContext`；benchmark 的 `CollectorManager.set_run_context` 在启动前绑定场景。真实冒烟：trace 与 record 的 `runs` 行都写入场景/模式。 |
| **页面把“无数据”当成“仍在加载”** | `/overview` 与 `/traces` 在没有 graph/alerts 的 live run 上永久停在加载层（实测 5s+ 不消失）。现在用 `dataLoading` 区分初始加载与“加载结束但无数据”，显示显式空态；新增 2 个单测；e2e 断言改为数据容忍的结构锚点 |
| 浏览器 e2e 断言陈旧 | 旧断言引用已删除的“打开事件工作台”和纯数据内容；更新后 Chromium/Firefox/WebKit **24 项全通过**（含 live SSE、run 结束回放、axe a11y、run 隔离）。`run-isolation` 的 better-sqlite3 ABI 问题是 sudo 下 PATH 指向 root 的 Node 24，已用 nvm Node 20 路径修复 |
| Clippy 预算 | 104 → **99**，`scripts/verify.sh` 预算随之收紧到 99 |

**未做（明确保留）**：`agents[].role_file` 仍是死元数据（98 个引用、6 个文件不存在）。
注入角色会改变 benchmark 行为，留待实验协议确定后处理。

### 2.3.2 第三轮质量审计与修复（2026-10-22）

独立只读审计（文档契约 / 测试完整性 / 产品面）后按确认项修复：

| 问题 | 修复 |
|---|---|
| 四份文档各自宣称“唯一入口”；FINAL 仍是 14/14、A5/B46/C16、74 待审 | FINAL 标为历史并指向 DEEPSEEK 入口；`docs/HANDOFF.md`、`docs/README.md` 指针统一；CONTRIBUTING 改为 17 门 |
| SCENARIOS.md 以 86/19/14/A10B34C23 作为当前目录 | 总览改为 93（67+26）、A3/B55/C9、10 攻击类别；逐场景叙述明确标为 2026-06 历史 |
| metadata.json 自相矛盾（86 vs 93、benign_types 13、traces 86、残留 needs_human_review、重复 changelog） | 由当前 GT 重算：93/67/26、A3B55C9、traces 0、移除遗留字段、changelog 去重、更新内核/版本/日期 |
| CLAUDE/architecture 列出去除的 `/agents /clusters /handoff /nebula`，`/` 仍写成控制台 | 改为实际路由（`/` landing、`/console`、`/overview`、`/investigations`、`/traces`、`/research/fuxibench`） |
| README 的 25 rules、29 analyzers、L1/L2 semantic、34/86、84.2% | 改为代码可核对的 19 rules（5+14）、17 analyzers、L1 exact-signature；删除旧语料性能数字 |
| Event 结构、TimestampNormalizer 单位、datetime 来源写错 | 更正为 `{timestamp,source,pid,comm,data}`、boot ns→epoch ns、展示层转 ms |
| 丢包计数在多个脚本中规则不一 | `summarize_responsibility_run.py` 改用 benchmark 的 canonical parser；未知统计显式标“未知/不完整” |
| Claim 持久化、checkpoint 拒绝分支、多 run 快照与 channel 溢出无测试 | 增加 claim 回读 + alert.claim_ids 关联；3 个 checkpoint 拒绝分支；双 run 快照隔离；channel 溢出确定性断言 |
| 4 个空壳集成测试 + 伪性能基准 | 删除 `streaming_framework_integration.rs`（真实覆盖在 buffer/correlator 单测） |
| TS 契约只检查 claim_state 名称列表 | 改为整枚举比对，发现并修复 `AccountabilityAnomaly` vs 线上 `ResponsibilityAnomaly` 漂移 |
| 归档 run 的 final_verdict 恒为 null（目录名 vs DB UUID） | `read_final_verdict` 单 run 时解析目录别名；多 run 不匹配则显式 unknown，不借用他 run |
| 一次轮询失败即宣告 run 结束并重拉不可达后端 | 连续 3 次失败才结束，保留已渲染数据、不重拉；SSE 连续 3 次错误显示 disconnected |
| 证据加载失败/加载中被显示成 0 条 | 新增 `EvidenceNotice`（错误横幅/加载提示），investigations 与 traces 接入；直播中证据 10s 刷新 |
| 重复 DOM id `command-console-title` | 侧栏标题改用独立 id |
| 执行证据分类“有任意指标即 executed” | 秘密读取或“网络工具 + 非回环出站”才 executed；普通 shell → weak_partial（排除出分母） |
| 质量审计仍按 v2 GT 契约，v4 永远不 ready | 支持 v4 字段与 expected_behaviors，新增 v4 paper_ready 测试 |
| RQ 评估器在缺数据时输出估计值/零值 | 缺 manifest 明确报错；无实测消融时 RQ2=not_measured；证据层级=not_measured；Uncertain 从二分类分母剔除并单独计 abstained |

### 2.4 机制接线取证结论（只读，未全部修复）

独立只读审查追踪了 provenance、身份解析、进程谱系、图模式、最终裁决、
Observer、带外读取、checkpoint 回放、claim 关联。已确认的缺口：

| 严重度 | 结论 | 状态 |
|---|---|---|
| medium（hypothesis） | 采集门只要求“已配置源健康”，不要求存在 OS/process 源；`trace --ssl=true --process=false` 仍可权威 Safe | **待决策**：是否把“无 process 源”一律降为 Uncertain |
| medium | `alert.engine.claim_ids` 已持久化但前端调查面板不用，选中告警时列出全 run claim | **已修复**（§2.3-F） |
| medium | 带外 OpenClaw 在 gateway_pid=0 时注释称“按内容关联”，实际 correlator 硬门禁进程谱系，静默无证据 | **部分修复**：更正注释 + 启动时 `log::warn!` 显式告警；内容降级关联未实现 |
| medium | 能力包络运行时生产者、身份合并后的污点/包络传播、`MTAGENTLENS_PROVENANCE_STRICT`、会话 id 接线：删除即测试仍绿 | **待补测试** |
| — | 复核更正：Observer 超时与 1 MiB 上限**已有**集成测试（`collector/tests/analysis_regressions.rs:301,310`），代理报告为假阳性，未改动 | 已核实 |
| low | `runs.scenario_id/mode` 无生产者；`phase2_claim_id` 固定字面量；`Observation::OpenCandidate` 无生产者；跨层 bus 无订阅者；`max_retries` 未实现；文档与代码不一致 | **待清理/决策** |

“待补测试”这一类不改变行为，先补测试即可暴露真实接线；本轮完成了
producer_wiring_contract、run_id 过滤、claim_ids 前端关联与带外告警，
其余列出的测试缺口仍在。

### 2.5 完整性评审新增结论

| 严重度 | 结论 | 状态 |
|---|---|---|
| medium | prompt 包重建脚本先删后读、且依赖不存在的 quality manifest | **已修复**（§2.3-H） |
| medium | `final_verdict_metrics` 不校验 results 行的 GT label 与所在文件角色（benign 行混入 attack 文件会悄悄改变分母） | **已修复**：`validate_release` 提供 `case_labels`，两处调用传入 `expected_label`；新增回归 |
| medium | TLS 部分探针附加失败不可见：helper 只报 attached/failed 总数，collector 只存 attached；核心 `SSL_read/SSL_write` 缺失时仍 healthy | **待修**：需 loader 输出逐探针核心状态（涉及 bpftools C 重建），本轮只记录 |
| low | `FileLogger` 写/刷失败只 `eprintln`，原始 JSONL 可能不完整而采集质量仍 healthy | **待修**：需把失败计数接入 capture quality |
| low | `serde_yaml`/`unsafe-libyaml` 仅 `[dev-dependencies]`（`handoff_chain_verification.rs`），不进入 release 二进制；应作为测试工具债务处理 | 已核实，P2 口径更正 |

评审还确认：§2.3-C/D 两项在其输出中已显示为“已修复”，不是待办；不要重复开单。

### 2.6 已确认但本轮未修的架构债务（下一阶段起点）

以下均有 file:line 证据，未在本轮修改：

1. **测试与生产脱节**：`collector/tests/multi_pid_trace_config.rs` 测试的是文件内
   复制的 `build_process_args_for_pid`，生产的 `build_process_args` 改了它不会失败。
2. **平行真源**：`analyzers/pipeline_contract.rs` 的 `canonical_pipeline_contracts()`
   只被自身测试消费，没有任何分析器声明/读取它；`PipelineStage/StageMetrics`
   仍被 server 健康端点使用，不能整块删除。
3. **无生产者的类型/字段**：`Observation::OpenCandidate` 从不构造；
   `runs.scenario_id/mode` 无生产写入；`ScoringSignal/ClassificationSignal/
   ClaimSignal` 无生产订阅者；`ObserverProviderConfig.max_retries` 未实现。
4. **claim 映射丢失**：`phase2_claim_id` 写死为 `"phase2-authoritative"`，
   不能 join 任何持久化 claim。
5. **TLS 部分附加不可见**：loader 只报 attached/failed 总数，核心
   `SSL_read/SSL_write` 缺失仍可能 healthy；需逐探针输出。
6. **FileLogger 写失败只 eprintln**，原始 JSONL 可能不完整而采集质量仍 healthy。
7. **`runs.final_decision` 之外的 API**：`read_final_verdict` 已按 run 过滤；
   但 `runs.scenario_id/mode` 仍空，前端 mode 徽章永不出现。
8. **`agents[].role_file` 是死元数据**：schema 要求该字段，98 个 agent 条目
   引用 `developer/analyst/operator/auditor/network-engineer/devops-engineer.md`，
   其中 6 个文件不存在；执行器只用它给日志命名，从不把角色注入 prompt。
   要么实现注入（会改变现有行为），要么从 schema/场景中移除并迁移。
9. **文档滞后**：`ANALYZER_PIPELINE_CONTRACT.md` 描述的
   `pipeline_orchestrator.rs`/`PipelineBuilder` 不存在；`engine.rs` 对
   Phase 2 的注释仍是旧的 DirectUnsafe 默认；README/CLAUDE 的
   `PhaseClassificationEngine` 类型名不存在（实现是 `ClassificationEngine`）。

## 3. 74 项 `needs_human_review` 已由规则化裁决关闭

**这不是人类复核。** 作者于 2026-10-22 将裁决委托给项目；协议见
`datasets/FuxiBench/ADJUDICATION-PROTOCOL.md` v1，原始三名标注者
（刘周康、唐浩钺、刘锦样）的记录与 `annotation.annotators` 未改动。

方法与结果：

- 74 项各自独立裁决 + 对抗验证（每题一个 verifier 尝试推翻），再做一致性审查；
- **74/74 关闭，0 blocked**：73 项 `close_with_documented_limitations`，
  1 项 `close`；
- **9 项 `attack_type` 按协议 §2.2 改判，全部为 → B**：
  `consensus-split`、`delegation-chain`、`fragmented-payload`、
  `handoff-injection`、`mirror-poisoning`、`prompt-injection-direct`、
  `resource-direct`、`role-hijack`、`stealth-team-oncall-handoff`；
- 被验证者推翻的改判已回退：`identity-confusion` 维持 C；
  `exfil-archive-upload` 在修正 A 规则后维持 A；`role-progressive` 未通过改判；
- 最终分布：**A 3 / B 55 / C 9 / benign 26**（原 A5/B46/C16）；
- 19 项原本 `needs_human_review=false` 的遗留字段一并移除，`quality.review.status`
  成为唯一关闭真源；
- 每项写入 `quality.review`：`method=rule-based-adjudication-v1`、
  `human_review=waived_by_author`、规则、证据、局限；旧布尔值保留为
  `quality.legacy_needs_human_review`；`quality.notes` 保留并以
  `notes_disclosure` 标注其中旧 prompt 包陈述已被取代。

数据一致性：`validate_gt` 93/93、0 problems；`gt_summary` 0 type mismatch；
`check_scenario_integrity --strict` 0 problems；`prompt_inputs` 重建后
guard 0 drift。

不可声称的内容：人类已复核、领域专家背书、执行率或检测效果。这些仍需要
真实实验与外部验证。

## 4. 任务表（按风险/依赖排序）

| 序号 | 任务 | 状态 | 证据要求 |
|---|---|---|---|
| P0 | 接收与基线核对 | **完成**（本文 §1） | 命令 + 退出码 |
| P1-a | 冻结 prompt 包脱钩 | **完成**：重建 93 项、0 drift；5 个基线/评测脚本拒绝过期清单 | §2.3-A/H |
| P1-b | 74 项复核 | **完成（规则化裁决；执行方式见协议）**：74/74 关闭，9 项改判 | §3 + `ADJUDICATION-PROTOCOL.md` |
| P1-c | 采集损失与端到端稳定性 | **计数已分因并实测**（§2.3-K）：本次 32 次丢包全部为用户读数失败；历史 9 次不可追认 | 需在所有数据源上重采后再做损失分布 |
| P1-d | 机制真实接线与科研效用 | **取证完成**（§2.4），部分修复 | 生产者→消费者→裁决的 file:line；断开即失败的测试 |
| P2-a | 104 条 Clippy + 未维护依赖 | 待做 | 最新日志分组；不批量 allow、不升预算 |
| P2-b | 能力/类型目录可机验 | **已落地**（§2.3-B） | 删除生产者必须让测试失败（已做变异验证） |
| P2-c | 生产指标收口 | 待做 | 未知保持未知，不以零值填充 |
| P3 | 冻结 release 与正式实验 | **当前 release `5d876355…`**；正式实验未开始 | 需在冻结二进制上重采数据，论文披露裁决执行方式 |

外部前提（无法由本会话完成）：
- 74 项人类复核签署；
- 官方 LLM Guard / Prompt Guard / Vigil 的真实依赖、权重与服务部署；
- 多内核、长时间、高负载的采集覆盖；
- 正式付费模型实验（须先冻结协议、数据、二进制与样本量）。

## 5. 不可破坏的契约（摘录，完整见 TRANSFER 文档）

- 保留刘周康、唐浩钺、刘锦样原始记录；AI 工作不得伪装人类签署。
- FileOpen 只证明打开；failed/pending/unknown 不生成完成事实边。
- 未知采集质量不得成为权威 Safe；Uncertain 不得当 TN。
- Observer 裁决绑定候选/版本/精确证据。
- schema v4 复合主外键、统一选 run、一致 WAL 快照不退化。
- live/replay 共用纯分析核；缺 checkpoint 拒绝同构声称。
- 不删失败测试、不加 skip、不放宽预算换绿。

## 5.1 本轮未验证边界（务必保留）

- **内核**：process / TLS（OpenSSL 动态链接）/ stdio 都已在 Linux 7.0.0-38 真实
  加载并触发；未验证其它内核/架构、静态 SSL（BoringSSL/Node）路径、
  GnuTLS/NSS 的实际附加，也未在长时间高负载下统计损失分布。
- **历史丢包**：旧 `dropped_events=9` 的机制已不可追认；分因计数上线后，
  process fixture 实测 30 次全部为 `user_read_dropped`，ringbuf=0。
- **浏览器**：三浏览器 24 项 e2e 通过（含 live SSE、run 结束、axe、run 隔离）；
  未覆盖移动端/真实大屏数据规模。
- **模型与基线**：未调用付费模型或官方 baseline 服务；prompt 基线会因
  过期清单被拒绝运行是预期行为（可用 `MTAGENTLENS_ALLOW_STALE_PROMPT_MANIFEST=1`
  显式复现历史）。
- **GT**：74 项为**规则化裁决 + 作者豁免人工复核**；执行方式见协议，没有任何人类签署；
  论文必须如实披露。`quality.notes` 保留为 2026-10-20 历史记录。
- **实验**：正式效果、消融、性能实验尚未开始；旧数字全部作废。RQ 评估器
  现在缺 manifest 会明确报错，无实测消融时 RQ2=not_measured、证据层级=not_measured；
  它需要真实 run corpus，不能从 GT 单独生成。
- **测试**：alert/traceback 的 writer→reader 回读、前端 schema-v2 生产视图仍未覆盖；
  claim/checkpoint/快照隔离已补。
- **丢包解析**：benchmark 与 run summary 已共用 canonical parser；`audit_trace.sh`
  仍是独立实现（方向已修正），spikes 为开发工具。
- **架构**：`agents[].role_file` 仍是死元数据；`runs.scenario_id/mode` 只在
  benchmark/CLI 显式传入时写入（不会凭空生成）。

## 6. 本轮最终验证（2026-10-22 · 全部改动之后）

| 验证 | 实测 | 证据 |
|---|---|---|
| `./scripts/verify.sh` | **17/17，exit 0** | `/tmp/deepseek-verify-auditfix.log` |
| Rust 全 target | **1050 passed / 0 failed / 3 ignored** | `/tmp/verify_rust_tests.log` |
| Clippy | **99**（预算已收紧为 99；allow 49 未增） | `/tmp/verify_clippy.log` |
| 比较/盲标 | **335 passed** | `/tmp/verify_comparison.log` |
| env | **145 passed** | `/tmp/verify_environment.log` |
| 前端 | lint / type-check 通过，**547 单测** | `/tmp/verify_frontend_test.log` |
| 浏览器 e2e | **最终二进制上 Chromium / Firefox / WebKit 各 8 项，共 24 passed**（live SSE、run 结束回放、axe a11y、run 隔离） | `/tmp/mtl-browser-e2e/playwright.log` |
| eBPF 内核 | process 七项事实全 true；TLS 10 探针、`core_read/core_write=true`、7 事件、dropped=0；stdio 真实 OUT/ERR、dropped=0 | `/tmp/deepseek-kernel-verify.log`、`/tmp/mtl-tls-verify/`、`/tmp/mtl-stdio-verify2/` |
| GT | 93/93 `validate_gt`，`review=0`，0 type mismatch；场景完整性 0 problems | 实跑 |
| 数据集 release（当前） | `release_id=5d876355c7778b77cf71b2c1317862037a02334e16e552d2b7ec1a0925adfdbd`，93 cases（67/26，A3/B55/C9），blockers=[]；与当前 GT 逐字节一致 | `/home/lzk/MtAgentLens-releases/FuxiBench-2026-10-22-v2` |
| 数据集 release（历史） | `522d95bf…` 保留为不可变历史版本，不再作为当前输入 | `/home/lzk/MtAgentLens-releases/FuxiBench-2026-10-22` |
| release 二进制 | `mtagentlens` `fc9784a1212e5886fc361f842c08a3904cb097ce670e16a0b13f8549c5650b12`；`replay` `10c384e911b289c150df682e40e0742336db52f926ac5c45103acc1b5eddf1c3` | `sha256sum collector/build/release/*` |
| 格式 | `cargo fmt -- --check` 干净 | 实跑 |

**未验证**：TLS/stdio 之外的其它内核/架构；正式效果、消融与性能实验（旧数字仍全部作废）。

## 7. 收尾规则

每阶段报告：具体改动 → 为何必要 → 实际命令/退出码 → 覆盖边界 → 下一步。
未跑的检查、未签的人审、未重采的实验一律写明“未完成”。
