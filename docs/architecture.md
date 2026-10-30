# 系统架构

MtAgentLens 从 Linux 进程、TLS 与 stdio 采集事实，在 Rust Collector 内关联身份、行为和证据，将运行结果保存到 SQLite，并通过内嵌 Web 界面展示。它提供观测与分析，不拦截 Agent 的操作，也不把未观测到的行为视为已证明不存在。

## 数据流与职责

| 层 | 输入与职责 | 实现入口 |
|---|---|---|
| 采集 | TLS 明文、进程生命周期、文件与网络操作、指定进程 stdio | [bpftools](../bpftools/README.md) |
| Runner | 启动采集子进程，将行式 JSON 转为事件流；SystemRunner 从 `/proc` 采样 | [runners](../collector/src/framework/runners/mod.rs) |
| Analyzer | 重组、解码、HTTP/SSE 解析、时间归一化、脱敏与日志输出；各来源的链由 CLI 组装 | [main.rs](../collector/src/main.rs)、[analyzers](../collector/src/framework/analyzers/mod.rs) |
| 责任分析 | 摄入事实，维护信号、身份、责任图、证据和候选；必要时请求 Observer | [runtime.rs](../collector/src/responsibility/runtime/runtime.rs) |
| 持久化 | 单写入通路保存运行、图、证据、裁决与回放 checkpoint | [writer.rs](../collector/src/responsibility/persistence/writer.rs) |
| 服务与界面 | REST 查询、实时 SSE、内嵌静态页面 | [server](../collector/src/server/web.rs)、[frontend](../frontend/README.md) |

采集程序源码分别为 `bpftools/tls_capture/tls_capture.bpf.c`、
`bpftools/process/process.bpf.c`、`bpftools/stdiocap/stdiocap.bpf.c`；同目录 C 程序负责用户态加载和输出。
[BinaryExtractor](../collector/src/framework/binary_extractor.rs) 在编译时嵌入三个 helper；前端从 `frontend/out/` 嵌入。因此构建顺序是 BPF、前端、Rust，主二进制位于 `collector/build/release/mtagentlens`。

事件原始时间由采集来源决定。[TimestampNormalizer](../collector/src/framework/analyzers/timestamp_normalizer.rs) 将内核启动时间转换为 epoch 纳秒；面向展示的时间字段可另转毫秒。解码状态、截断、丢事件和来源健康状态属于证据质量，不能用空事件或零计数替代失败。

## 一次正常运行

按[入门步骤](../README.md#快速开始)完成构建后，用 `record` 启动采集，在浏览器查看实时事件，正常停止后读取结束阶段的分析结果并查询或导出。日志路径对应 `<log>.responsibility.db`；`trace --capture-only` 仅采集，不创建责任数据库。CLI 参数和产物说明见 [Collector 指南](../collector/README.md)。

## 责任分析的两个阶段

流式阶段由 `ResponsibilityRuntime::route_event()` 接收事件，更新信号快照、临时身份和图。`trigger()` 执行阶段性分析，供实时观察；临时结果不等于运行结束后的权威结果。

结束阶段由 `finalize()` 在冻结信号上重新解析身份、关联证据和构建分析输入。运行保存版本化的 [AnalysisCheckpoint](../collector/src/responsibility/replay/checkpoint.rs)，并由 `checkpoint.evaluate()` 产生分析结果。在线结束阶段与离线敏感性分析共享该评价入口，避免各自维护一套裁决算法。回放要求 checkpoint 契约与实现指纹匹配，不承诺任意旧库都能按当前算法重放。

身份以 `(pid, start_time)` 区分进程代际。[IdentityResolver](../collector/src/responsibility/identity/resolver.rs) 处理进程与消息绑定；权威解析还受冻结的 [IdentityBoundaryPolicy](../collector/src/responsibility/identity/policy.rs) 约束。进程、TLS 会话和 conversation 相关信号用于关联，不能等同于用户认证。显式消息身份优先于进程回退；模型文本中的自称角色不能提供同等权威。

[GraphBuilder](../collector/src/responsibility/graph/graph_builder.rs) 维护行为关系和委派记录；证据关联与污点传播分别位于 `responsibility/evidence/`、`responsibility/graph/taint.rs`。上下文共享授权只覆盖观测到的直接进程委派，完整条件见 [ADR-0015](adr/0015-alert-actionability-and-taint-authorization.md)。它不是通用权限系统。

## 分诊与最终裁决

[评分链](design/scoring-engine.md) 汇总六维信号，用于排序和路由。`DirectUnsafe` 是代码中的路由名称，高分本身不构成违规证明。

[final_decision.rs](../collector/src/responsibility/classification/final_decision.rs) 聚合规则证据与按候选作用域绑定的 Observer 意见：已确认动作上的硬违规可形成权威 Unsafe；有引用的语义判断可支持 Unsafe；未审候选、失败或冲突保留复核状态。高分但无充分裁决证据不能单独升级为 Unsafe。`Safe` 仅表示本次输入和裁决流程未建立违规结论。

Observer 是可配置的语义仲裁通路。启用远端 provider 会发送候选及有界任务上下文；缺少真实 provider 时的 Mock 不能记为真实语义实验。数据出口和敏感信息边界见 [威胁模型](security/threat-model.md)。

## SQLite 与 Web 边界

[Schema 入口](../collector/src/responsibility/persistence/schema.rs) 在任何写入配置前分类数据库，拒绝不支持的类型和未来版本；迁移通过事务完成。可写数据库收紧为 Unix `0600`。当前 schema 版本由 `LATEST_SCHEMA_VERSION` 定义，设计约束见 [单一 schema ADR](adr/0005-single-schema.md)。版本号不能替代库类型检查。

Web 服务默认绑定 `127.0.0.1:7395`，可由 `MTAGENTLENS_BIND` 改变。`/api/live/events` 提供实时 SSE，`/api/runs/*` 查询运行及其持久化结果。服务没有内置用户鉴权；回环绑定不构成同机用户隔离。

CLI 主命令为 `record`、`trace`、`ssl`、`process`、`stdio`、`system`、`export-genai`、`replay-sensitivity`、`doctor`。`collector/src/bin/replay.rs` 是独立 Cargo 辅助二进制，不是主 CLI 子命令。

## 扩展位置与验证边界

- 增加来源：扩展 Runner 和 CLI 组装，明确事件时间、来源健康、结束与丢失语义，再接入规范化链。
- 增加检测：区分事实、候选、分诊分数和最终证据，修改对应 classification/evidence 模块；补充 live/checkpoint/replay 一致性验证。
- 修改身份或持久化：先检查冻结策略、run 作用域与迁移契约，避免只修改流式分支。
- 新增外发或 HTTP 接口：同步更新安全边界与敏感字段说明。

Linux 5.8 是 ring buffer 的最低内核条件之一，还需 BTF、权限和可用 hook；不代表所有 5.8+ 平台已验收。eBPF 更改须在目标内核实际加载并检查 verifier。冻结实验只证明其指定二进制、输入和运行环境，当前实验入口见 [证据导航](evaluation/CURRENT-EVIDENCE.md)。
