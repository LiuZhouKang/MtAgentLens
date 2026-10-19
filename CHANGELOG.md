# Changelog

本文件记录用户可见的变化。格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)；
版本号遵循语义化版本。项目的权威状态以 `./scripts/verify.sh` 为准。

## [Unreleased] — 2026-10-19

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
