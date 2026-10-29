# Collector

MtAgentLens 的 Rust 核心引擎。编译为单个静态二进制 `mtagentlens`，内嵌 eBPF 程序和前端静态资源，无外部运行时依赖。

要观察完整的进程启动过程，请先启动采集，再启动目标程序。支持 task iterator 的内核会在挂载探针后
枚举已有用户进程，以独立 PROCESS_SNAPSHOT 元数据建立初始谱系；快照不是 EXEC，不补造命令参数或动作。
`-p` 接受采集器所在 PID namespace 的编号，经内核快照转换后跟踪；无法唯一对应时拒绝启动。
枚举并非原子快照；不支持、传输失败或过期事件跳过都会显式报告，不能视为无损采集。
EXEC 的 PGID 与原始 cgroup ID 直接来自内核；
未核验的 cgroup unit 名称保持未知。不会按裸内核 PID 读取 procfs 或把 proc ticks 换算成内核身份。
当前实验状态与证据边界见 [当前证据入口](../docs/evaluation/CURRENT-EVIDENCE.md)，测量方法见 [实验设计](../docs/evaluation/experimental-design.md)。

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
|  |FileLogger|  |  Output  |  | Additional Analyzers         |   |
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
| Live SSE                  .  /api/live/events                |
| Legacy log response       .  /api/events                     |
| REST API                  .  /api/runs/{id}/alerts          |
|                           .  /api/runs/{id}/evidence        |
|                           .  /api/runs/{id}/events          |
|                           .  /api/runs/{id}/snapshots       |
+---------------------------------------------------------------+
```

## 模块导航

```
src/
├── main.rs                     clap derive CLI，9 个子命令路由
├── framework/                  流式数据采集管线
│   ├── runners/                SSL、Process、Stdio、System、Agent、OpenClaw 与 Fake
│   │   ├── outofband.rs        OpenClaw 旁路轨迹读取
│   │   └── fake.rs             合成事件流（测试/性能基准）
│   ├── analyzers/              SSE、HTTP、时间戳、脱敏、检测与日志处理
│   ├── normalize/              事件规范化
│   ├── core/                   Event 类型与时间戳工具
│   └── binary_extractor.rs     include_bytes! 内嵌 BPF 二进制
├── responsibility/             因果分析引擎
│   ├── graph/                  图构建、污点传播、模式匹配与快照
│   ├── identity/               身份解析与并查集
│   ├── classification/         规则、评分与最终裁决（final_decision.rs）
│   ├── scoring/                风险因子与分数融合
│   ├── claims/                 声明、回溯与告警构建
│   ├── evidence/               证据关联、质量与留存
│   ├── provenance/             来源标签、权限包络与 span
│   ├── outofband/              OpenClaw asserted intent
│   ├── observer/               Observer LLM、缓存与提供者
│   ├── persistence/            SQLite schema、写入与只读查询
│   ├── replay/                 回放、消融、敏感性与跨 run 差异
│   ├── judgment/               策略评估与委派追踪
│   ├── runtime/                事件循环与批量终结
│   ├── signals/                实时信号图与快照
│   └── types/                  共享类型
└── server/                     HTTP API 与静态资源
    ├── web.rs                  路由处理与实时 SSE
    ├── assets.rs               rust-embed 前端内嵌
    └── api_types.rs            请求/响应类型
```

## 开发

以下命令从仓库根目录开始；构建前需已有 eBPF 与前端产物（根目录 `make build` 会按顺序生成）。

```bash
cd collector

cargo build              # 调试构建
cargo build --release    # 发布构建 → build/release/mtagentlens
cargo test               # 测试数量以本次输出为准
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
