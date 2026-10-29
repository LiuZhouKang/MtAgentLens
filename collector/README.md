# Collector

MtAgentLens 的 Rust 核心引擎。编译为单个静态二进制 `mtagentlens`，内嵌 eBPF 程序和前端静态资源，无外部运行时依赖。

要观察完整的进程启动过程，请先启动采集，再启动目标程序。支持 task iterator 的内核会在挂载探针后
枚举已有用户进程，以独立 PROCESS_SNAPSHOT 元数据建立初始谱系；快照不是 EXEC，不补造命令参数或动作。
`-p` 接受采集器所在 PID namespace 的编号，经内核快照转换后跟踪；无法唯一对应时拒绝启动。
枚举并非原子快照；不支持、传输失败或过期事件跳过都会显式报告，不能视为无损采集。
EXEC 的 PGID 与原始 cgroup ID 直接来自内核；
未核验的 cgroup unit 名称保持未知。不会按裸内核 PID 读取 procfs 或把 proc ticks 换算成内核身份。
当前进展与验收边界见 [实验设计](../docs/evaluation/experimental-design.md)。

## 流水线

```
  eBPF programs (include_bytes! embedded at compile time)
    |
    v
+---------------------------------------------------------------+
| Runner Layer                                                  |
|                                                               |
| SslRunner . ProcessRunner . StdioRunner . SystemRunner        |
| AgentRunner (composes multiple runners + global analyzer)     |
|                                                               |
| -> Pin<Box<dyn Stream<Item = Event> + Send>>                 |
+-------------------------------+-------------------------------+
                                | EventStream { id, timestamp, source, event_type, data }
                                v
+---------------------------------------------------------------+
| Analyzer Pipeline (add_analyzer() chain)                      |
|                                                               |
|  +-------------+  +-----------+  +------------------------+   |
|  | SSEProcessor|->| HTTPParser|->| TimestampNormalizer   |   |
|  | (chunk reass)|  | (req/resp)|  | (btime -> epoch ms)   |   |
|  +------+------+  +-----+-----+  +-----------+------------+   |
|         +---------------+---------------------+                |
|                         v                                      |
|  +------------------------------------------+                 |
|  | ResponsibilityRuntime                    |                 |
|  |                                          |                 |
|  |  Phase 1 (streaming, per-event)          |                 |
|  |  +------------------------------------+  |                 |
|  |  | ClassificationEngine          |  |                 |
|  |  | GraphBuilder                       |  |                 |
|  |  | IdentityResolver (UnionFind)       |  |                 |
|  |  | TaintTracker                       |  |                 |
|  |  | Observer (on conflict signals)     |  |                 |
|  |  +------------------------------------+  |                 |
|  |                                          |                 |
|  |  Phase 2 (on SIGINT, batch finalize)     |                 |
|  |  +------------------------------------+  |                 |
|  |  | TracebackBuilder                   |  |                 |
|  |  | ClaimBuilder                       |  |                 |
|  |  | AlertBuilder                       |  |                 |
|  |  +------------------------------------+  |                 |
|  +------------------------------------------+                 |
|                                                               |
|  +----------+  +----------+  +----------------------------+   |
|  |FileLogger|  |  Output  |  | 26 other Analyzers         |   |
|  |(rotation)|  |(console) |  | HandoffInjection .         |   |
|  +----------+  +----------+  | MultiAgentAnomaly .        |   |
|                              | AuthHeaderRemover .        |   |
|                              | HTTPFilter . SSLFilter ... |   |
|                              +----------------------------+   |
+-------------------------------+-------------------------------+
                                |
                 +--------------+--------------+
                 v                             v
  SQLite (responsibility.db)        JSONL (streaming push)
  structured query . replay         SSE -> frontend real-time
                 |                             |
                 +--------------+--------------+
                                v
+---------------------------------------------------------------+
| Output Layer                                                  |
|                                                               |
| HTTP Server (Hyper 1.0)  .  :7395                            |
| Embedded frontend (rust-embed) . Next.js 16 static export     |
| Legacy event stream       .  /api/events                     |
| REST API                  .  /api/runs/{id}/alerts          |
|                           .  /api/runs/{id}/evidence        |
|                           .  /api/runs/{id}/events          |
|                           .  /api/runs/{id}/snapshots       |
+---------------------------------------------------------------+
```

## 模块导航

```
src/
├── main.rs                     clap derive CLI，8 个子命令路由
│
├── framework/                  流式数据采集管线
│   ├── runners/                7 个 Runner
│   │   ├── ssl.rs              TLS 明文捕获 (uprobe)
│   │   ├── process.rs          进程生命周期 (tracepoint/kprobe)
│   │   ├── stdio.rs            MCP stdio 捕获
│   │   ├── system.rs           CPU/内存 (/proc 轮询)
│   │   ├── agent.rs            组合 Runner + 全局 analyzer 管理
│   │   └── fake.rs             合成事件流 (测试/性能基准)
│   │
│   ├── analyzers/              30+ 个 Analyzer (管道-过滤器模式)
│   │   ├── sse_processor.rs    SSE 分片重组 (Content-Type: text/event-stream)
│   │   ├── http_parser.rs      HTTP 请求行/头/体解析
│   │   ├── http_filter.rs      基于正则的 HTTP 事件过滤
│   │   ├── ssl_filter.rs       基于正则的 SSL 事件过滤
│   │   ├── timestamp_normalizer.rs  btime → epoch ms 转换
│   │   ├── auth_header_remover.rs   Authorization 头剥离
│   │   ├── handoff_injection.rs     交接文档注入检测
│   │   ├── multi_agent_anomaly.rs   多 Agent 异常检测
│   │   ├── file_access_audit.rs     文件访问审计 (工作区边界)
│   │   ├── connection_buffer.rs     连接缓冲管理
│   │   ├── security_signoff.rs      安全签核追踪
│   │   ├── file_logger.rs           文件日志 (轮转)
│   │   ├── output.rs                控制台输出
│   │   └── ...
│   │
│   ├── core/                   Event 类型 · 时间戳工具
│   └── binary_extractor.rs     include_bytes! 内嵌 BPF 二进制
│
├── responsibility/             因果分析引擎 (17 个子模块)
│   ├── graph/                  ResponsibilityGraph
│   │   ├── graph.rs            核心图结构 (节点·边)
│   │   ├── graph_builder.rs    事件→图构建器
│   │   ├── taint.rs            TaintTracker 跨进程污点传播
│   │   ├── pattern_matcher.rs  攻击模式匹配（模板要求见 capabilities 的 unbacked_graph_types）
│   │   ├── snapshot.rs         图快照
│   │   └── delta.rs            增量差异
│   │
│   ├── identity/               身份解析
│   │   ├── union_find.rs       UnionFind 数据结构
│   │   └── resolver.rs         三信号融合解析器
│   │
│   ├── classification/         分类引擎
│   │   ├── engine.rs           ClassificationEngine
│   │   ├── scoring.rs          6 维组合评分
│   │   ├── rule_engine.rs      规则引擎 (25 条：5 safe + 20 unsafe)
│   │   ├── rules.rs            规则定义
│   │   ├── pattern_matcher.rs  GraphPatternMatcher
│   │   └── types.rs            分类类型
│   │
│   ├── claims/                 声明构建
│   │   ├── claim_builder.rs    ClaimBuilder
│   │   ├── traceback.rs        TracebackBuilder
│   │   ├── alert_builder.rs    AlertBuilder
│   │   └── accountability_evidence.rs 证据契约
│   │
│   ├── outofband/              Agent 自落盘轨迹（只读）
│   │   └── openclaw.rs         OpenClaw tool.call → asserted intent
│   │
│   ├── observer/               Observer LLM
│   │   ├── cache.rs            CachePrior (L1 精确/L2 语义)
│   │   ├── llm.rs              LLM 调用客户端
│   │   ├── provider.rs         Observer 提供者接口
│   │   ├── system_prompt.rs    系统提示词
│   │   └── types.rs            Observer 类型定义
│   │
│   ├── persistence/            持久化
│   │   ├── writer.rs           PersistenceWriter（SQLite 批次 + 丢弃/失败计数）
│   │   ├── schema.rs           单一 schema 与版本门
│   │   └── reader.rs           只读查询（损坏 JSON 报错，不返回空结构）
│   │
│   ├── replay/                 回放与跨 run 差异
│   │   ├── executor.rs          权威回放执行器
│   │   └── run_diff.rs          跨 run 操作差异
│   │
│   ├── judgment/               策略评估
│   │   ├── policy.rs           策略定义与评估
│   │   ├── delegation.rs       委派追踪
│   │
│   ├── runtime/                事件循环编排
│   │   ├── runtime.rs          ResponsibilityRuntime (阶段 1+2)
│   │   ├── routing.rs          事件路由
│   │   └── buffer.rs           事件缓冲
│   │
│   ├── server/                 HTTP API (Hyper 1.0)
│   │   └── routes/             graph · alerts · evidence · diff · runs · SSE
│   │
│   ├── signals/                实时信号
│   │   ├── signal_graph.rs     SignalGraph
│   │   └── signal_snapshot.rs  信号快照
│   │
│   └── types/                  共享类型 (40+ 结构体)
│
├── server/                     HTTP 服务器
│   ├── web.rs                  路由处理
│   ├── assets.rs               rust-embed 前端内嵌
│   └── api_types.rs            请求/响应类型
│
└── auth/                       TOTP 2FA 认证
```

## 开发

```bash
cd collector

cargo build              # 调试构建
cargo build --release    # 发布构建 → build/release/mtagentlens
cargo test               # 1223 lib tests / 3 ignored（当前）
cargo fmt -- --check     # 格式检查 (edition 2024)
cargo clippy             # Lint
```

`.cargo/config.toml` 将构建目录重定向为 `build/` 以保持项目根目录整洁。

## 附加二进制（Cargo 自动发现）

| 文件 | 用途 |
|------|------|
| `src/bin/replay.rs` | 对已落盘的 `collector.responsibility.db` 重跑权威回放并输出报告 |
| `src/bin/perf_throughput.rs` | 用 FakeRunner 注入合成 SSL 事件测量管道吞吐 |
| `src/bin/covert-channel.rs` | 文件元数据隐写信道演示（协议见 `env/scenarios/fixtures/covert-channel/comm-protocol.md`） |

## 配置

- `../env/config/observer.toml.example` — Observer LLM 配置模板（flat schema）；
  真实配置在 `env/config/vendors/*/observer.toml`（本地、gitignored）

## 相关文档

- [系统架构](../docs/architecture.md)
- [评分引擎设计](../docs/design/scoring-engine.md)
- [SAFI 攻击框架](../docs/safi/README.md)
- [文档索引](../docs/README.md)
