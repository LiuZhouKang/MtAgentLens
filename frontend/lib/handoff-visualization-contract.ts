// ═══════════════════════════════════════════════════════════════
// 第三局 — 宫室仪象所呈之型制约法
// 阅  shared_handoff.md（首局递送文书全本，计五十二目）
// 及 第二局所呈营缮要略（三阶递验章程 · 器具接口与流转程式）
//
// 拟定宫室仪象所呈之型制：仪象类型、展示规格、组件型范
// 与第二局之器相合，与首局文书之机锋密令相通
//
// 术法符文之形（TypeScript 类型契约）
// 对齐现有模式: lib/data-contract.ts · lib/types.ts
//               lib/handoff-contract.ts · lib/handoff-dashboard-contract.ts
//               lib/handoff-observability-contract.ts
//
// v3.1 增补:
//   §50 — TOCTOU 页缓存夹缝取证（第一阶文契递验之补充）
//   §51 — 意图-执行双流一致性校验（第三阶因果递验之深化）
//   §52 — 环境指纹防重放与会话克隆检测（第一阶+第三阶交叉约束）
// ═══════════════════════════════════════════════════════════════

import type { AnomalySeverity } from "./data-contract";
import type { AlertSeverity, MatchLevel } from "./types";

// ═══════════════════════════════════════════════════════════════
// 一、仪象类型总目 — 与 shared_handoff.md 四十九目逐一对应
//    每目一事，每事一型，型有规格，格有组件
// ═══════════════════════════════════════════════════════════════

/**
 * 仪象类型鉴别符 — 前端渲染分发依据
 * 对应第二局器具接口的 source + sub_type 二元组
 */
export type InstrumentType =
  // ── 实时流式（已有，复用） ──
  | "tls_decrypted_stream"       // §1: SSL/TLS 明文捕获流
  | "process_lifecycle"          // §4: 进程生命周期追踪
  | "sse_event_timeline"         // §3: SSE 推送实时事件流

  // ── 图层仪象 ──
  | "responsibility_graph"       // §2: 因果图与责任追溯
  | "identity_resolution"        // §12: 并查集身份解析
  | "taint_propagation"          // §13: 跨子代理污点传播追踪
  | "handoff_version_dag"        // §23: Handoff 文档版本 DAG

  // ── 文件与通信仪象 ──
  | "file_access_audit"          // §5: 文件访问审计链路
  | "stdio_mcp_protocol"         // §6: Stdio 透传与 MCP 协议解析
  | "credential_leak"            // §22: 子代理凭据泄露实时检测

  // ── 时序与取证仪象 ──
  | "forensic_timeline"          // §15: 微秒级时序取证
  | "hash_chain_integrity"       // §16: Handoff 文档完整性校验链
  | "cross_session_drift"        // §20: 跨会话慢速漂移检测
  | "cross_channel_correlation"  // §24: 跨通道时间窗口关联

  // ── 告警与检测仪象 ──
  | "pattern_match_alert"        // §8: 图模式匹配与告警
  | "signal_aggregation"         // §10: 实时信号聚合与异常评分
  | "baseline_deviation"         // §17: 跨代理行为基线建模
  | "canary_injection"           // §25: 金丝雀指令注入与盲读检测
  | "trust_decay"                // §26: 子代理信任衰减模型
  | "campaign_correlation"       // §27: 多会话注入战役关联

  // ── 夹缝与深层取证仪象（v3.1 增补） ──
  | "toctou_window_detection"    // §50: TOCTOU 页缓存夹缝取证
  | "intent_execution_drift"     // §51: 意图-执行双流一致性校验
  | "environment_fingerprint";   // §52: 上下文环境指纹防重放

// ═══════════════════════════════════════════════════════════════
// 二、深层仪象 — 非实时，事后回放与审计
// ═══════════════════════════════════════════════════════════════

export type DeepInstrumentType =
  | "session_replay"             // §7: 持久化双写与事后回放
  | "diff_replay_comparison"     // §14: 差分回放与注入影响量化
  | "ioc_extraction"             // §18: 注入载荷自动逆向与 IOC 提取
  | "forensics_dashboard"        // §21: 交互式取证仪表板
  | "force_graph_3d"             // §19: 3D 力导向拓扑可视化
  | "semantic_confidence"        // §9: LLM 观察者语义增强
  | "smart_sampling"             // §11: 智能采样与资源自适应

  // ── v3.1 增补深层仪象 ──
  | "toctou_page_cache_audit"    // §50: 页缓存写监视与 VFS 夹缝取证回放
  | "intent_embedding_compare"   // §51: 意图嵌入与执行嵌入双流对比
  | "env_fingerprint_replay";    // §52: 环境指纹碰撞检测与跨会话克隆分析

// ═══════════════════════════════════════════════════════════════
// 三、实时流式事件类型（与第二局器具接口之流对应）
//    source: "handoff_observability" → sub_type 驱动前端渲染
// ═══════════════════════════════════════════════════════════════

// ── 3.1 文件访问审计（§5 + PLATFORM-0042） ──

export interface FileAccessAuditEvent {
  sub_type: "FILE_ACCESS_AUDIT";
  access_id: string;
  timestamp_ns: number;
  syscall: "open" | "openat" | "read" | "write" | "close";
  target_path: string;

  // PLATFORM-0042 工作区边界
  workspace_tag: "inside" | "outside" | "boundary-ambiguous";
  workspace_root: string;

  // §1 关联
  agent_id: string;
  tool_call_id: string | null;
  chain_depth: number;
  handoff_phase: "dispatch" | "context_inject" | "execute" | "collect";

  // §16 哈希链
  content_hash: string | null;          // 写入时计算的 SHA-256
  hash_verified: boolean | null;        // 读取时比对结果
  hash_mismatch: boolean;               // 链上记录不一致则为 true

  severity: "info" | "warn" | "error";
}

// ── 3.2 Stdio/MCP 协议事件（§6） ──

export type McpMessageDirection = "stdin" | "stdout";

export interface MCPProtocolEvent {
  sub_type: "MCP_PROTOCOL";
  event_id: string;
  timestamp_ns: number;
  pid: number;
  agent_id: string;
  direction: McpMessageDirection;

  // JSON-RPC 解析结果
  jsonrpc_version: string;
  method: string | null;                // e.g. "tools/call", "initialize"
  params_summary: string | null;        // 参数摘要（截断至 500 字符）
  result_summary: string | null;        // 响应摘要
  error_code: number | null;
  error_message: string | null;

  // §22 凭据检测
  credential_detected: boolean;
  credential_pattern: string | null;    // 匹配到的凭据前缀模式
  credential_masked: boolean;           // 是否已脱敏

  raw_bytes: number;
}

// ── 3.3 污点传播事件（§13） ──

export interface TaintPropagationEvent {
  sub_type: "TAINT_PROPAGATION";
  taint_id: string;
  timestamp_ns: number;

  // 污点源（handoff 文档被注入点）
  source_type: "handoff_document" | "tool_call" | "file_content" | "network_payload";
  source_identifier: string;            // 文档路径 / tool_call_id / URL
  source_hash: string;

  // 传播链
  from_agent_id: string;
  to_agent_id: string;
  propagation_depth: number;            // 距离原始污点源的跳数
  propagation_path: string[];           // 经过的 agent_id 序列

  // 被污染的操作
  contaminated_operation: string;       // syscall / API call / tool name
  contaminated_target: string;          // 文件路径 / URL / tool 参数
  contamination_confidence: number;     // 0.0–1.0

  severity: "info" | "warn" | "critical";
}

// ── 3.4 哈希链完整性事件（§16） ──

export interface HashChainIntegrityEvent {
  sub_type: "HASH_CHAIN_INTEGRITY";
  chain_id: string;
  timestamp_ns: number;

  document_path: string;
  expected_hash: string;                // 链上记录的哈希
  observed_hash: string;                // 读取时实际计算的哈希
  hash_match: boolean;

  // 写入方与读取方
  writer_agent_id: string;
  reader_agent_id: string;
  writer_chain_depth: number;
  reader_chain_depth: number;

  // 关联的责任图边
  graph_edge_ids: string[];

  severity: "info" | "error";           // mismatch → error
}

// ── 3.5 凭据泄露检测事件（§22） ──

export interface CredentialLeakEvent {
  sub_type: "CREDENTIAL_LEAK";
  alert_id: string;
  timestamp_ns: number;

  // 泄露源
  leaking_agent_id: string;
  leak_source: "handoff_document" | "stdio_stream" | "ssl_stream" | "file_write";
  leak_channel: string;                // 具体文件路径/网络连接

  // 凭据信息
  credential_pattern: string;          // e.g. "sk-ant-", "ghp_", "Bearer"
  credential_entropy_bits: number;     // 熵值，用于判断是否为随机密钥
  credential_preview: string;          // 脱敏后的前 8 字符 + "***" + 后 4 字符
  credential_masked: boolean;

  // 阻断
  transmission_blocked: boolean;       // eBPF 层面是否阻止了传输
  block_reason: string | null;

  severity: "critical";                // 凭据泄露始终为 critical
}

// ── 3.6 跨通道关联事件（§24） ──

export interface CrossChannelCorrelationEvent {
  sub_type: "CROSS_CHANNEL_CORRELATION";
  correlation_id: string;
  timestamp_ns: number;

  // 锚点事件（handoff 读取时间点）
  anchor_event_type: string;
  anchor_agent_id: string;
  anchor_timestamp_ns: number;
  anchor_description: string;

  // 关联窗口内的其他通道事件
  window_ms: number;                    // 默认 ±500ms
  correlated_events: CrossChannelCorrelatedItem[];

  // 是否触发告警
  anomaly_detected: boolean;
  anomaly_reason: string | null;
  anomaly_score: number;                // 0.0–1.0 综合异常评分

  severity: "info" | "warn" | "critical";
}

export interface CrossChannelCorrelatedItem {
  channel: "ssl_new_connection" | "dns_query" | "file_write" | "process_spawn" | "network_send";
  timestamp_ns: number;
  delta_ms: number;                    // 距锚点的时间差
  description: string;
  target: string;                      // 连接目标 IP:port / 文件路径 / 进程名
  is_unexpected: boolean;              // 是否在预设白名单之外
}

// ── 3.7 金丝雀指令事件（§25） ──

export type CanaryType = "silent_marker" | "trigger_alert" | "timestamp_honeypot";

export interface CanaryInjectionEvent {
  sub_type: "CANARY_INJECTION";
  canary_id: string;
  timestamp_ns: number;
  canary_type: CanaryType;

  // 注入信息
  injected_in_document: string;         // 承载金丝雀的 handoff 文档路径
  injected_paragraph_hash: string;      // 嵌入段落的哈希
  paragraph_sensitivity: "high" | "medium" | "low";

  // 检测信息
  detected_in_context: string | null;   // 在哪个代理的上下文中检测到
  detected_agent_id: string | null;
  detection_method: "audit_replay" | "execution_capture" | "timestamp_sequence_check";
  detection_timestamp_ns: number | null;

  // 判定
  is_breached: boolean;                 // true = 金丝雀被意外触及
  breach_severity: "info" | "warn" | "critical";

  // 触发-告警型专属
  triggered_action: string | null;     // e.g. "curl http://canary.internal/evidence"
  triggered_payload: unknown | null;
}

// ── 3.8 信任评分事件（§26） ──

export interface TrustScoreEvent {
  sub_type: "TRUST_SCORE";
  evaluation_id: string;
  timestamp_ns: number;
  agent_id: string;

  // 三因子
  behavior_consistency_score: number;   // 0.0–1.0, KL 散度反比
  input_purity_score: number;           // 0.0–1.0, 哈希链完整率
  time_decay_score: number;             // 0.0–1.0, 指数衰减

  // 综合
  trust_score: number;                  // 0.0–1.0
  trust_level: "high" | "medium" | "low";

  // §26 信任传递衰减
  upstream_trust: number | null;        // 上游代理的信任评分
  decay_applied: boolean;              // 是否应用了 0.85 衰减系数

  // 鉴权决策
  requested_sensitivity: "high" | "medium" | "low" | null;
  auth_decision: "bypass" | "reauthenticate" | "deny" | null;
  auth_reason: string | null;

  // 关联图边
  graph_edge_ids: string[];
}

// ── 3.9 战役关联事件（§27） ──

export interface CampaignCorrelationEvent {
  sub_type: "CAMPAIGN_CORRELATION";
  campaign_id: string;
  timestamp_ns: number;

  // 三层聚类结果
  payload_cluster_id: string | null;    // MinHash + DBSCAN 载荷聚类
  ttp_cluster_id: string | null;        // ATT&CK 战术向量聚类
  temporal_target_cluster_id: string | null; // 时间-目标关联聚类

  // 攻击者画像
  attacker_profile: AttackerProfile | null;

  // 关联的注入事件
  linked_injection_ids: string[];
  linked_session_ids: string[];

  // IOC 提取
  extracted_iocs: ExtractedIOC[];

  severity: "warn" | "critical";
}

export interface AttackerProfile {
  profile_id: string;
  payload_templates: string[];          // 载荷模板特征
  preferred_tactics: string[];          // 偏好战术（ATT&CK 映射）
  preferred_targets: string[];          // 目标代理类型分布
  active_time_windows: Array<{          // 攻击时间偏好
    start_hour_utc: number;
    end_hour_utc: number;
    day_of_week: number | null;
  }>;
  confidence: number;                   // 画像置信度 0.0–1.0
  first_seen_ns: number;
  last_seen_ns: number;
}

// ── 3.10 信号聚合事件（§10） ──

export interface SignalAggregationEvent {
  sub_type: "SIGNAL_AGGREGATION";
  signal_id: string;
  timestamp_ns: number;
  agent_id: string;

  // 信号维度
  signal_type: "api_call_frequency" | "token_consumption_rate" | "file_access_density"
            | "network_connection_rate" | "process_spawn_rate";

  // 滑动窗口统计
  window_size_ms: number;
  current_value: number;
  baseline_mean: number;
  baseline_stddev: number;
  deviation_sigmas: number;

  // 异常判定
  is_anomalous: boolean;
  anomaly_score: number;                // 0.0–1.0
  anomaly_direction: "above" | "below" | null;

  // 双层检测：信号层快速筛查
  rapid_alert: boolean;                 // 先于图模式匹配触发
  linked_graph_alert_ids: string[];     // 触发的图层告警
}

// ── 3.11 图模式匹配告警（§8） ──

export interface PatternMatchAlertEvent {
  sub_type: "PATTERN_MATCH_ALERT";
  alert_id: string;
  timestamp_ns: number;

  // 分类引擎
  phase: string;                        // PhaseClassificationEngine 的分类结果
  classification_route: string;

  // 匹配的模式
  matched_pattern_id: string;
  matched_pattern_name: string;         // e.g. "handoff_injection_via_scope_broadening"
  pattern_confidence: number;           // 0.0–1.0

  // 关联图节点
  involved_graph_nodes: string[];
  involved_graph_edges: string[];

  // 告警信息
  title: string;
  description: string;
  severity: AlertSeverity;

  // 关联的责任归属声明（ClaimBuilder）
  claim_ids: string[];
  claim_summaries: string[];
}

// ── 3.12 跨代理基线偏差事件（§17） ──

export interface BaselineDeviationEvent {
  sub_type: "BASELINE_DEVIATION";
  deviation_id: string;
  timestamp_ns: number;
  agent_id: string;
  agent_type: string;                   // e.g. "gsd-code-reviewer", "gsd-executor"

  // 个体偏差
  individual_baseline: BehaviorVector;
  individual_current: BehaviorVector;
  individual_deviation_sigmas: number;

  // 群组偏差（与同类型代理对比）
  group_mean: BehaviorVector;
  group_stddev: BehaviorVector;
  group_deviation_sigmas: number;

  // 判定
  is_individual_anomaly: boolean;
  is_group_anomaly: boolean;            // 群组偏差更可信
  anomaly_category: "task_complexity" | "injection_suspected" | "unknown";

  severity: "info" | "warn" | "critical";
}

/**
 * 行为向量 — §17 跨代理行为建模用
 * 每个维度为聚合统计值
 */
export interface BehaviorVector {
  api_call_distribution: Record<string, number>;  // 工具名 → 调用次数
  file_access_count: number;
  file_access_outside_workspace_ratio: number;
  mean_tool_call_duration_ms: number;
  stddev_tool_call_duration_ms: number;
  total_token_consumption: number;
  execution_duration_ms: number;

  // §24 跨通道关联维度
  post_handoff_network_connections: number;
  post_handoff_file_writes: number;
  post_handoff_process_spawns: number;
}

// ── 3.13 跨会话漂移事件（§20） ──

export interface CrossSessionDriftEvent {
  sub_type: "CROSS_SESSION_DRIFT";
  drift_id: string;
  timestamp_ns: number;
  agent_type: string;

  // 历史基线（数十次会话聚合）
  historical_mean: number;
  historical_stddev: number;
  session_count: number;

  // 漂移检测
  drift_rate_per_session: number;       // 每会话的平均偏移量
  cumulative_drift: number;             // 累积偏移
  drift_direction: "increasing" | "decreasing";
  is_monotonic: boolean;                // 是否为持续单调偏移

  // 预警（单次偏差低但持续偏移的情况）
  early_warning_triggered: boolean;
  early_warning_threshold_sigmas: number; // 远低于单次阈值

  severity: "warn" | "critical";
}

// ── 3.14 TOCTOU 页缓存夹缝取证事件（§50） ──

export interface TOCTOUDetectionEvent {
  sub_type: "TOCTOU_DETECTION";
  toctou_id: string;
  timestamp_ns: number;
  document_path: string;
  target_inode: number;

  // 三层布防检测结果
  layer1_page_cache_snapshot: {
    /** generic_file_read_iter / filemap_fault 捕获的页缓存内容哈希 */
    page_cache_content_hash: string;
    /** 磁盘哈希链上记录的内容哈希 */
    disk_hash_chain_hash: string;
    /** 页缓存与磁盘哈希是否一致 */
    cache_disk_match: boolean;
    /** 不一致时的告警级别（TOCTOU 暗示内核级攻击） */
    alert_level: "none" | "high" | "critical";
  };

  layer2_write_window_audit: {
    /** vfs_write 返回成功的时间戳 */
    last_legitimate_write_ns: number;
    /** 首次 vfs_read 的时间戳 */
    first_read_ns: number;
    /** write→read 之间的 TOCTOU 窗口时长（ns） */
    window_duration_ns: number;
    /** 窗口内检测到的所有内核级写操作 */
    kernel_level_writes: Array<{
      write_type: "__kernel_write" | "kernel_write" | "do_mmap_page_cache" | "process_vm_writev" | "proc_mem_write";
      timestamp_ns: number;
      caller_comm: string;
      caller_pid: number;
      bytes_written: number;
      is_legitimate: boolean;               // 是否为预期内的内核写操作
    }>;
    /** 是否存在非预期的内核级写入 */
    has_unexpected_kernel_write: boolean;
  };

  layer3_clearfield_defense: {
    /** 是否应用了「坚壁清野」之术（msync + fadvise DONTNEED） */
    clearfield_applied: boolean;
    /** 强制刷盘驱逐页缓存的时间戳 */
    clearfield_ts_ns: number | null;
    /** sensitivity 标签 */
    document_sensitivity: "normal" | "high" | "critical";
    /** 若 clearfield 未应用于 critical 文档则为 true */
    clearfield_missing_for_critical: boolean;
  };

  // 综合判定
  toctou_confirmed: boolean;
  toctou_confidence: number;                // 0.0–1.0
  toctou_method: string | null;             // 确认的攻击手法

  // 关联图
  linked_graph_node_ids: string[];
  linked_taint_chain_ids: string[];

  // 跨通道佐证（§46 联动）
  cross_channel_corroboration: {
    has_corroborating_evidence: boolean;
    corroborating_channel: string | null;   // e.g. "process_vm_writev", "/proc/pid/mem"
    corroboration_confidence: number;
  };

  severity: "warn" | "error" | "critical";   // TOCTOU 通常 ≥ error
}

// ── 3.15 意图-执行双流一致性事件（§51） ──

export interface IntentExecutionConsistencyEvent {
  sub_type: "INTENT_EXECUTION_DRIFT";
  evaluation_id: string;
  timestamp_ns: number;
  agent_id: string;
  handoff_document_path: string;

  // 意图向量（步骤 1：handoff 写入时提取）
  intent_vector: {
    embedding_model: string;                // e.g. "all-mpnet-base-v2"
    embedding_dimensions: number;           // 768
    /** 意图嵌入的语义摘要（自然语言） */
    intent_summary: string;
    /** 嵌入向量的前 16 维（用于前端可视化降维投影） */
    embedding_preview: number[];
    /** 该段落标记的 sensitivity 级别 */
    paragraph_sensitivity: "normal" | "high" | "critical";
    /** 段落哈希（关联 §16 哈希链） */
    paragraph_hash: string;
  };

  // 执行向量（步骤 2：子代理执行完毕后提取）
  execution_vector: {
    /** 系统调用序列的自然语言描述 */
    execution_summary: string;
    /** 同维度语义嵌入 */
    embedding_preview: number[];
    /** 系统调用统计 */
    syscall_stats: {
      total_syscalls: number;
      file_reads: number;
      file_writes: number;
      api_calls: number;
      network_connections: number;
      process_spawns: number;
    };
  };

  // 双流余弦偏差度量（步骤 3）
  drift_measurement: {
    /** 意图嵌入与执行嵌入的余弦相似度 */
    cosine_similarity: number;              // -1.0 ~ 1.0, 默认阈值 0.75
    /** 是否触发意图漂移告警 */
    drift_alert_triggered: boolean;
    drift_severity: "none" | "low" | "medium" | "high" | "critical";

    // 三基准校准
    self_baseline: {
      /** 同一代理类型历史会话的意图-执行相似度 P5 下限 */
      p5_lower_bound: number;
      /** 当前会话的百分位排名 */
      percentile_rank: number;
    };
    group_baseline: {
      /** 同一批次所有子代理的相似度中位数 */
      group_median: number;
      /** 偏离中位数的标准差数 */
      deviation_sigmas: number;
    };
    empty_baseline: {
      /** 空 handoff 运行同种子代理的相似度（纯粹自主行为基准） */
      empty_handoff_similarity: number;
      /** 当前相似度与空文档基准的差异 */
      delta_from_empty: number;
    };
  };

  // 偏差溯源与注入段落定位（步骤 4：逐段消融测试）
  ablation_traceback: {
    /** 消融测试中相似度恢复最大的段落 */
    most_likely_injected_paragraph_hash: string | null;
    /** 该段落的文本摘要 */
    paragraph_summary: string | null;
    /** 移除该段落后相似度恢复的量 */
    similarity_recovery: number | null;
    /** 溯源置信度 */
    traceback_confidence: number | null;
    /** 所有段落的消融测试结果 */
    ablation_results: Array<{
      paragraph_hash: string;
      paragraph_order: number;
      removed_snippet_preview: string;      // 被移除段落的前 100 字符
      similarity_without_paragraph: number;
      similarity_delta: number;             // 正值 = 移除后相似度提升
    }>;
  };

  // IOC 反馈（§18 联动）
  extracted_injection_signature: {
    injected_structure_features: string[];  // 注入段落的结构特征
    keyword_distribution: Record<string, number>;
    instruction_template_signature: string | null;
    ioc_feedback_status: "pending" | "written_to_feature_store";
  };

  severity: "info" | "warn" | "critical";
}

// ── 3.16 环境指纹防重放与会话克隆检测事件（§52） ──

export interface EnvironmentFingerprintEvent {
  sub_type: "ENVIRONMENT_FINGERPRINT";
  fingerprint_id: string;
  timestamp_ns: number;
  agent_id: string;

  // 六维环境向量（步骤 1：子代理启动时采集）
  environment_vector: {
    cgroup_id: number;
    pid_namespace_inode: number;
    mount_namespace_inode: number;
    network_namespace_inode: number;
    uts_namespace_inode: number;
    ipc_namespace_inode: number;
    /** 192-bit 会话环境指纹（六维向量拼接） */
    fingerprint_192bit: string;
    /** /proc/self/mountinfo 摘要哈希 */
    mountinfo_hash: string;
  };

  // 指纹碰撞检测（步骤 2）
  collision_detection: {
    /** 精确碰撞：六维完全匹配 → CRITICAL */
    exact_collision: boolean;
    exact_collision_session_ids: string[];
    /** 邻近碰撞：五维匹配仅一维不同 → HIGH */
    proximity_collision: boolean;
    proximity_mismatched_dimension: string | null;
    proximity_collision_session_ids: string[];
    /** 碰撞判定总体级别 */
    collision_level: "none" | "low" | "high" | "critical";
    /** 差异分析（邻近碰撞时） */
    variance_analysis: {
      /** 环境差异维度与行为差异维度的相关性 */
      env_behavior_correlation: number;     // -1.0 ~ 1.0
      /** 变异是否具有攻击意图 */
      has_attack_intent: boolean;
      intent_reasoning: string | null;
    } | null;
  };

  // 环境-行为绑定签名（步骤 3）
  binding_signature: {
    /** handoff 文档期望执行行为与当前环境指纹的拼接哈希 */
    binding_hash: string;
    /** 绑定是否仍然有效 */
    binding_valid: boolean;
    /** 绑定失效原因（如 unshare/nsenter 动态切换命名空间） */
    invalidation_reason: string | null;
    /** 绑定失效时间戳 */
    invalidated_at_ns: number | null;
    /** 是否触发了「环境逃逸」告警 */
    env_escape_alert: boolean;
  };

  // 双链完整性：文档链（§16）+ 环境链（§52）
  dual_chain_status: {
    /** 文档哈希链完整 */
    document_chain_intact: boolean;
    /** 环境指纹链完整 */
    environment_chain_intact: boolean;
    /** 双链皆备方可采信 */
    dual_chain_trustworthy: boolean;
    /** 任一链断裂的原因 */
    chain_break_reason: string | null;
  };

  // APT 特征标记（环境指纹碰撞 → 战役引擎关联 §27）
  apt_marker: {
    /** 同一环境指纹在多个会话中出现 → 可重复部署的基础设施 */
    is_reusable_infrastructure: boolean;
    /** 关联的战役 ID */
    linked_campaign_ids: string[];
    /** APT 特征置信度 */
    apt_confidence: number;
  };

  severity: "info" | "warn" | "high" | "critical";
}

// ── 4.1 会话回放（§7） ──

export interface SessionReplayState {
  session_id: string;
  run_id: string;
  replay_status: "idle" | "loading" | "playing" | "paused" | "complete";

  current_timestamp_ns: number;
  start_timestamp_ns: number;
  end_timestamp_ns: number;
  playback_speed: number;               // 1.0 = 实时, 10.0 = 10x

  // 双写来源
  sqlite_snapshot_id: string | null;
  jsonl_stream_offset: number;          // JSONL 流式推送游标

  // 回放中的事件计数
  total_events: number;
  replayed_events: number;
}

// ── 4.2 差分回放（§14） ──

export interface DiffReplayState {
  comparison_id: string;

  // 原始版本
  original_document_hash: string;
  original_replay_session_id: string;
  original_replay_status: "pending" | "running" | "complete" | "error";

  // 注入版本
  injected_document_hash: string;
  injected_replay_session_id: string;
  injected_replay_status: "pending" | "running" | "complete" | "error";

  // 差分结果
  diff_result: DiffReplayResult | null;
}

export interface DiffReplayResult {
  // 系统调用序列差异
  syscall_diffs: Array<{
    call_type: string;
    original_count: number;
    injected_count: number;
    delta: number;
    significance: "high" | "medium" | "low";
  }>;

  // API 请求差异
  api_diffs: Array<{
    endpoint: string;
    method: string;
    original_params: string | null;
    injected_params: string | null;
    is_new_call: boolean;               // 注入版本多出的 API 调用
    is_modified_call: boolean;          // 参数被修改
    is_removed_call: boolean;           // 注入版本缺失的调用
  }>;

  // 文件变更集合差异
  file_diffs: Array<{
    path: string;
    original_read: boolean;
    injected_read: boolean;
    original_written: boolean;
    injected_written: boolean;
    is_unexpected: boolean;             // 注入版本非预期访问
  }>;

  // Blast Radius 量化
  blast_radius: {
    additional_api_calls: number;
    unexpected_file_reads: number;
    unexpected_file_writes: number;
    additional_process_spawns: number;
    total_impact_score: number;         // 0.0–1.0 综合影响评分
  };

  // 完整时间链重建
  injection_to_impact_chain: Array<{
    timestamp_ns: number;
    event_type: string;
    description: string;
    agent_id: string;
    delta_from_injection_ms: number;
  }>;
}

// ── 4.3 IOC 提取（§18） ──

export interface ExtractedIOC {
  ioc_id: string;
  ioc_type: "malicious_api_endpoint" | "unexpected_file_path"
          | "anomalous_spawn_chain" | "prompt_injection_signature"
          | "credential_pattern" | "command_pattern";

  value: string;                        // IOC 的具体值
  confidence: number;                   // 0.0–1.0
  source_alert_id: string;              // 触发提取的告警
  extraction_method: "static_analysis" | "dynamic_sandbox" | "pattern_match";

  // 写入共享特征库
  feature_store_key: string;
  feature_store_version: number;

  // 检测规则热更新
  rule_update_status: "pending" | "applied" | "verified";
  rule_update_version: number | null;
}

// ── 4.4 3D 力导向图（§19） ──

export interface ForceGraph3DState {
  // 从 ResponsibilityGraph 数据驱动
  graph_snapshot_id: string;
  snapshot_timestamp_ns: number;

  // 节点
  nodes: ForceGraphNode[];
  // 边
  edges: ForceGraphEdge[];

  // 视图状态
  camera_position: [number, number, number];  // x, y, z
  camera_target: [number, number, number];
  zoom_level: number;

  // 交互
  selected_node_id: string | null;
  hovered_node_id: string | null;

  // 渲染参数
  color_mode: "depth" | "agent_type" | "taint_severity" | "trust_score";
  edge_mode: "causality" | "taint" | "communication" | "all";
  show_labels: boolean;
  label_density: "sparse" | "normal" | "dense";
}

export interface ForceGraphNode {
  node_id: string;
  node_type: "process" | "file" | "network_endpoint" | "api_call" | "llm_conversation" | "handoff_document";

  // 空间位置（力导向算法计算）
  x: number;
  y: number;
  z: number;

  // 视觉编码
  radius: number;                       // 节点大小 → 关联度
  color: string;                        // 节点颜色 → 类型/深度/污点严重度
  opacity: number;                      // 透明度 → 信任评分

  // 元数据
  label: string;
  agent_id: string | null;
  chain_depth: number | null;
  taint_level: number;                  // 0–3, 污点传播深度
  trust_score: number | null;           // §26: 信任评分

  // handoff 注入相关
  is_injection_source: boolean;         // §13: 是否为污点源
  is_injection_target: boolean;         // 是否被注入影响
  alert_count: number;                  // 关联告警数
}

export interface ForceGraphEdge {
  edge_id: string;
  source_node_id: string;
  target_node_id: string;

  // 视觉编码
  color: string;                        // 边颜色 → 类型（因果/污点/通信）
  width: number;                        // 边粗细 → 置信度
  opacity: number;

  // 元数据
  edge_type: "causality" | "taint_propagation" | "communication" | "dependency";
  confidence: number;                   // 0.0–1.0
  description: string;

  // 污点传播专属
  taint_depth: number | null;           // 距污点源的跳数
  taint_confidence: number | null;      // §13: 污点传播置信度
}

// ── 4.5 语义置信度（§9） ──

export interface SemanticConfidenceState {
  observer_type: "heuristic" | "llm_sidecar";
  observer_status: "idle" | "running" | "error";

  // 最近评估
  evaluations: SemanticEvaluation[];
  average_confidence: number;
  total_evaluations: number;
  cache_hit_rate: number;               // 观察者缓存命中率
}

export interface SemanticEvaluation {
  evaluation_id: string;
  timestamp_ns: number;
  target_event_id: string;
  target_event_type: string;

  // LLM 观察者判定
  verdict: "safe" | "suspicious" | "malicious" | "uncertain";
  confidence: number;                   // 0.0–1.0
  reasoning: string;                    // LLM 给出的理由

  // 缓存策略
  cache_ttl: "once" | "task" | "all" | "none";

  // 是否修改了引擎的初始判定
  overturned_engine_verdict: boolean;
  original_engine_verdict: string | null;
}

// ── 4.6 智能采样状态（§11） ──

export interface SmartSamplingState {
  // 各通道采样率
  channel_rates: Record<string, {
    current_rate: number;               // 0.0–1.0, 当前采样率
    target_rate: number;                // 自适应目标
    event_rate_per_sec: number;         // 事件产生速率
    drop_reason: string | null;         // 降采样原因
  }>;

  // 关键路径（全量采集）
  critical_paths: string[];             // e.g. "ssl_decrypt", "file_write"

  // 资源状态
  system_load: number;                  // 0.0–1.0
  ebpf_buffer_usage_pct: number;       // eBPF 环形缓冲区使用率
  dropped_events: number;               // 因缓冲区溢出丢弃的事件数
  dropped_critical_events: number;      // 关键路径上丢弃的事件（应为 0）

  // 自保护
  cpu_overhead_pct: number;            // AgentSight 自身 CPU 开销
  memory_usage_mb: number;              // AgentSight 自身内存使用
}

// ── 4.7 Handoff 版本 DAG（§23） ──

export interface HandoffVersionDAG {
  document_path: string;

  // 版本节点
  versions: VersionNode[];
  // DAG 边（修改关系）
  edges: VersionEdge[];

  // 合谋检测
  collusion_detected: boolean;
  collusion_groups: Array<{
    agent_ids: string[];
    suspicious_pattern: string;
    combined_effect: string;
    confidence: number;
  }>;

  // 分支信息
  branch_count: number;
  merge_count: number;
}

export interface VersionNode {
  version_id: string;
  parent_version_ids: string[];         // DAG 支持多父节点
  author_agent_id: string;
  author_chain_depth: number;
  created_at_ns: number;

  // 内容
  content_hash: string;                 // SHA-256
  diff_from_parent: string | null;      // 与前一个版本的 unified diff
  changed_sections: string[];           // 修改的段落标识

  // 安全
  is_suspicious: boolean;               // 单看正常，组合异常
  suspicion_reason: string | null;
}

export interface VersionEdge {
  edge_id: string;
  from_version_id: string;
  to_version_id: string;
  edge_type: "modification" | "branch" | "merge";
  timestamp_ns: number;
}

// ═══════════════════════════════════════════════════════════════
// 五、统一事件流 — 从第二局器具接口到前端分发
//    所有事件通过 /api/events SSE 推送
//    前端按 source + sub_type 二元组路由到对应渲染器
// ═══════════════════════════════════════════════════════════════

/**
 * 统一 handoff 可视化事件联合类型
 * 对应第二局的 source="handoff_observability" + source="handoff_deep"
 */
export type HandoffVisualizationEvent =
  // 实时流式
  | FileAccessAuditEvent
  | MCPProtocolEvent
  | TaintPropagationEvent
  | HashChainIntegrityEvent
  | CredentialLeakEvent
  | CrossChannelCorrelationEvent
  | CanaryInjectionEvent
  | TrustScoreEvent
  | CampaignCorrelationEvent
  | SignalAggregationEvent
  | PatternMatchAlertEvent
  | BaselineDeviationEvent
  | CrossSessionDriftEvent
  // v3.1 增补
  | TOCTOUDetectionEvent
  | IntentExecutionConsistencyEvent
  | EnvironmentFingerprintEvent;

/**
 * SSE 流信封 — 扩展现有 StreamEnvelope
 * 与 data-contract.ts 的 UnifiedStreamEvent 共存
 */
export interface HandoffVisualizationEnvelope {
  stream_version: "3.0.0";
  sequence_number: number;
  run_id: string;
  emitted_at_ms: number;
  stream_type: "handoff_visualization";
  event: HandoffVisualizationEvent;
}

/**
 * 扩展 UnifiedStreamEvent — 供 app-store 消费
 */
export type ExtendedUnifiedStreamEvent =
  | { stream_type: "handoff_visualization"; data: HandoffVisualizationEnvelope };

// ═══════════════════════════════════════════════════════════════
// 六、仪象展示规格 — 组件 Props 型范
//    «宫室仪象» 共十五类组件，各司其职，各呈其形
// ═══════════════════════════════════════════════════════════════

// ── 6.1 文件访问审计时间线行（§5 + §16） ──

export interface FileAccessAuditRowProps {
  event: FileAccessAuditEvent;
  is_highlighted: boolean;
  highlight_agent_id: string | null;

  // §16 哈希链状态
  show_hash_chain: boolean;
  hash_verification_status: "verified" | "mismatch" | "unavailable";

  onSelect: (event: FileAccessAuditEvent) => void;
  onNavigateToAgent: (agentId: string) => void;
  onNavigateToHashChain: (chainId: string) => void;
}

// ── 6.2 MCP 协议视图（§6） ──

export interface MCPProtocolViewProps {
  events: MCPProtocolEvent[];
  selected_event_id: string | null;
  filter_direction: McpMessageDirection | null;
  filter_method: string | null;         // 按 JSON-RPC method 过滤
  onSelectEvent: (event: MCPProtocolEvent) => void;
}

// JSON-RPC 消息详情弹窗
export interface MCPMessageDetailProps {
  event: MCPProtocolEvent;
  onClose: () => void;
  onShowRaw: () => void;               // 展示原始 JSON（脱敏后）
}

// ── 6.3 污点传播树（§13） ──

export interface TaintPropagationViewProps {
  taint_events: TaintPropagationEvent[];
  root_taint_id: string | null;
  max_depth: number;
  selected_taint_id: string | null;

  // 视觉配置
  layout: "tree" | "radial" | "force";
  highlight_contaminated: boolean;     // 高亮被污染节点

  onSelectTaint: (taintId: string) => void;
  onNavigateToAgent: (agentId: string) => void;
  onExportPropagationChain: () => void;  // 导出为取证报告
}

// 污点节点详情
export interface TaintNodeDetailProps {
  taint_event: TaintPropagationEvent;
  ancestor_chain: TaintPropagationEvent[];  // 从污点源到当前节点的完整链
  contaminated_operations: string[];
  onClose: () => void;
}

// ── 6.4 哈希链完整性仪表板（§16） ──

export interface HashChainDashboardProps {
  integrity_events: HashChainIntegrityEvent[];

  // 统计
  total_writes: number;
  total_reads: number;
  verified_count: number;
  mismatch_count: number;
  integrity_rate: number;               // verified / total_reads（应为 1.0）

  // 筛选
  filter_document_path: string | null;
  filter_mismatch_only: boolean;

  onSelectChain: (chainId: string) => void;
  onExportAuditLog: () => void;
}

// 单条哈希链追踪视图
export interface HashChainTraceViewProps {
  chain_events: HashChainIntegrityEvent[];
  document_path: string;

  // 链上所有记录按时间序
  chain_timeline: Array<{
    timestamp_ns: number;
    action: "write" | "read" | "verify";
    agent_id: string;
    hash: string;
    match_result: boolean | null;
  }>;

  // 关联的图节点
  linked_graph_nodes: string[];

  onNavigateToAgent: (agentId: string) => void;
  onNavigateToGraphNode: (nodeId: string) => void;
}

// ── 6.5 凭据泄露告警面板（§22） ──

export interface CredentialLeakPanelProps {
  leaks: CredentialLeakEvent[];

  // 筛选
  filter_agent_id: string | null;
  filter_pattern: string | null;
  blocked_only: boolean;

  // 统计
  total_leaks: number;
  blocked_leaks: number;
  bypassed_leaks: number;               // 传输未被阻止的（严重）

  onSelectLeak: (leakId: string) => void;
  onBlockTransmission: (leakId: string) => void;  // 手动阻断
  onExportLeakReport: () => void;
}

// ── 6.6 跨通道关联视图（§24） ──

export interface CrossChannelCorrelationViewProps {
  correlations: CrossChannelCorrelationEvent[];
  selected_correlation_id: string | null;

  // 时间窗口配置
  window_ms: number;                    // 默认 500ms
  onWindowChange: (ms: number) => void;

  // 通道选择
  visible_channels: string[];

  // 异常筛选
  anomaly_only: boolean;
  min_anomaly_score: number;            // 默认 0.5

  onSelectCorrelation: (correlationId: string) => void;
  onNavigateToAnchor: (agentId: string, timestampNs: number) => void;
}

// 单次关联详情（甘特图式展示）
export interface CrossChannelDetailProps {
  correlation: CrossChannelCorrelationEvent;
  onClose: () => void;
  onNavigateToEvent: (eventDescription: string, timestampNs: number) => void;
}

// ── 6.7 金丝雀注入监控面板（§25） ──

export interface CanaryInjectionPanelProps {
  canaries: CanaryInjectionEvent[];

  // 分类统计
  active_canaries: number;              // 已注入但未检测
  breached_canaries: number;            // 被触发的
  dormant_canaries: number;             // 未被检测的正常状态

  // 筛选
  filter_canary_type: CanaryType | null;
  filter_breached_only: boolean;
  filter_document: string | null;

  // 注入策略
  canary_density_high: number;          // 敏感段落的金丝雀密度
  canary_density_medium: number;
  canary_density_low: number;

  onSelectCanary: (canaryId: string) => void;
  onInjectNewCanary: (documentPath: string, paragraphHash: string) => void;
  onExportCanaryReport: () => void;
}

// ── 6.8 信任评分仪表（§26） ──

export interface TrustScoreDashboardProps {
  agents: Array<{
    agent_id: string;
    trust_score: number;
    trust_level: "high" | "medium" | "low";
    behavior_consistency: number;
    input_purity: number;
    time_decay: number;
    last_updated_ns: number;
  }>;

  // 筛选
  filter_trust_level: "high" | "medium" | "low" | null;
  search_agent_id: string;

  // 信任衰减配置
  decay_coefficient: number;            // 默认 0.85
  idle_threshold_seconds: number;       // 默认 60s

  onSelectAgent: (agentId: string) => void;
  onManualOverride: (agentId: string, newScore: number) => void;
}

// 单个代理的信任评分卡片
export interface TrustScoreCardProps {
  agent_id: string;
  current_score: TrustScoreEvent;

  // 历史评分曲线
  score_history: Array<{
    timestamp_ns: number;
    trust_score: number;
    trigger_event: string;
  }>;

  // 鉴权决策历史
  auth_decisions: Array<{
    timestamp_ns: number;
    decision: "bypass" | "reauthenticate" | "deny";
    reason: string;
    requested_sensitivity: string;
  }>;

  onReauthenticate: (agentId: string) => void;
  onClose: () => void;
}

// ── 6.9 战役关联分析视图（§27） ──

export interface CampaignCorrelationViewProps {
  campaigns: CampaignCorrelationEvent[];
  selected_campaign_id: string | null;

  // 聚类配置
  dbscan_eps: number;                   // 默认 0.3
  dbscan_min_samples: number;           // 默认 3

  // 可视化模式
  view_mode: "timeline" | "cluster_matrix" | "attacker_profile";

  onSelectCampaign: (campaignId: string) => void;
  onExportProfile: (campaignId: string) => void;
}

// 攻击者画像详情
export interface AttackerProfileViewProps {
  profile: AttackerProfile;
  linked_injections: string[];
  iocs: ExtractedIOC[];

  // 时间线
  activity_timeline: Array<{
    timestamp_ns: number;
    session_id: string;
    payload_type: string;
    target_agent_type: string;
    ioc_ids: string[];
  }>;

  onNavigateToSession: (sessionId: string) => void;
  onExportIOC: (iocIds: string[]) => void;
}

// ── 6.10 信号聚合仪表板（§10） ──

export interface SignalAggregationDashboardProps {
  signals: Record<string, SignalAggregationEvent[]>;  // agent_id → 信号序列
  selected_agent_id: string | null;

  // 信号类型选择
  visible_signal_types: string[];

  // 时间窗口
  time_range_ms: number;
  onTimeRangeChange: (ms: number) => void;

  onSelectAgent: (agentId: string) => void;
}

// 单个信号的实时曲线
export interface SignalChartProps {
  signal_type: string;
  data_points: Array<{
    timestamp_ns: number;
    value: number;
    baseline: number;
    upper_bound: number;              // baseline + 2σ
    lower_bound: number;              // baseline - 2σ
    is_anomalous: boolean;
  }>;
  compact?: boolean;
}

// ── 6.11 图模式匹配告警流（§8） ──

export interface PatternMatchAlertFeedProps {
  alerts: PatternMatchAlertEvent[];

  // 筛选
  filter_pattern_id: string | null;
  filter_severity: AlertSeverity | null;
  filter_phase: string | null;

  // 统计
  alert_counts_by_severity: Record<string, number>;
  alert_counts_by_pattern: Record<string, number>;

  onSelectAlert: (alertId: string) => void;
  onDismissAlert: (alertId: string) => void;
}

// 模式匹配详情
export interface PatternMatchAlertDetailProps {
  alert: PatternMatchAlertEvent;

  // 涉及的图节点快照
  involved_nodes: Array<{
    node_id: string;
    node_type: string;
    agent_id: string | null;
    description: string;
  }>;

  // 关联的声明
  linked_claims: Array<{
    claim_id: string;
    summary: string;
  }>;

  onNavigateToGraph: (nodeId: string) => void;
  onNavigateToClaim: (claimId: string) => void;
  onClose: () => void;
}

// ── 6.12 基线偏差面板（§17） ──

export interface BaselineDeviationPanelProps {
  deviations: BaselineDeviationEvent[];

  // 筛选
  filter_agent_type: string | null;
  filter_anomaly_category: "task_complexity" | "injection_suspected" | "unknown" | null;
  group_deviation_only: boolean;        // 只看群组偏差

  // 按代理类型分组
  agent_type_groups: Record<string, BaselineDeviationEvent[]>;

  onSelectDeviation: (deviationId: string) => void;
  onNavigateToAgent: (agentId: string) => void;
}

// 行为向量雷达图
export interface BehaviorRadarChartProps {
  agent_vector: BehaviorVector;
  baseline_vector: BehaviorVector;      // 同类型基线
  group_vector: BehaviorVector;

  onHoverDimension: (dimension: string) => void;
  compact?: boolean;
}

// ── 6.13 跨会话漂移视图（§20） ──

export interface CrossSessionDriftViewProps {
  drifts: CrossSessionDriftEvent[];

  // 按代理类型分组
  agent_types: string[];
  selected_agent_type: string | null;

  // 漂移曲线
  drift_curves: Record<string, Array<{
    session_index: number;
    session_id: string;
    metric_value: number;
    cumulative_drift: number;
    drift_rate: number;
  }>>;

  // 预警配置
  early_warning_sigmas: number;         // 默认 1.5

  onSelectAgentType: (agentType: string) => void;
  onSetEarlyWarning: (sigmas: number) => void;
}

// ── 6.14 会话回放控制器（§7） ──

export interface SessionReplayControllerProps {
  state: SessionReplayState;
  onPlay: () => void;
  onPause: () => void;
  onSeek: (timestampNs: number) => void;
  onSpeedChange: (speed: number) => void;
  onStop: () => void;
}

// ── 6.15 差分回放对比视图（§14） ──

export interface DiffReplayComparisonProps {
  state: DiffReplayState;
  onStartComparison: (originalHash: string, injectedHash: string) => void;
  onStopComparison: () => void;

  // 可视化模式
  view_mode: "summary" | "syscall_diff" | "api_diff" | "file_diff" | "blast_radius";

  onSelectDiffItem: (itemType: string, itemId: string) => void;
  onExportDiffReport: () => void;
}

// 差分报告导出格式
export interface DiffReplayExport {
  comparison_id: string;
  generated_at: string;
  original_document_hash: string;
  injected_document_hash: string;
  blast_radius_summary: string;
  syscall_diffs_table: string;
  api_diffs_table: string;
  file_diffs_table: string;
  injection_to_impact_timeline: string;
  total_impact_score: number;
}

// ── 6.16 IOC 提取面板（§18） ──

export interface IOCExtractionPanelProps {
  iocs: ExtractedIOC[];

  // 筛选
  filter_ioc_type: string | null;
  filter_confidence_min: number;
  filter_source_alert: string | null;

  // 特征库状态
  feature_store_size: number;
  pending_rules: number;
  applied_rules: number;

  // IOC 类型统计
  ioc_type_counts: Record<string, number>;

  onSelectIOC: (iocId: string) => void;
  onExportIOCs: (iocIds: string[]) => void;
  onApplyRule: (iocId: string) => void;  // 应用为检测规则
}

// ── 6.17 3D 力导向图视图（§19） ──

export interface ForceGraph3DViewProps {
  state: ForceGraph3DState;

  // 交互
  onNodeSelect: (nodeId: string) => void;
  onNodeHover: (nodeId: string | null) => void;
  onCameraChange: (position: [number, number, number], target: [number, number, number]) => void;
  onZoomChange: (zoom: number) => void;

  // 渲染控制
  onColorModeChange: (mode: ForceGraph3DState["color_mode"]) => void;
  onEdgeModeChange: (mode: ForceGraph3DState["edge_mode"]) => void;
  onToggleLabels: (show: boolean) => void;

  // 注入攻击链高亮
  highlight_injection_chain: boolean;
  injection_chain_node_ids: string[];

  onExportSnapshot: () => void;         // 导出当前视图为 PNG
}

// 力导向图节点详情弹窗
export interface ForceGraphNodeDetailProps {
  node: ForceGraphNode;
  connected_edges: ForceGraphEdge[];

  // 该节点的告警列表
  alerts: PatternMatchAlertEvent[];

  onClose: () => void;
  onNavigateToAgentDetail: (agentId: string) => void;
  onHighlightInjectionChain: (nodeId: string) => void;
}

// ── 6.18 语义置信度面板（§9） ──

export interface SemanticConfidencePanelProps {
  state: SemanticConfidenceState;

  // 评估历史
  evaluation_history: SemanticEvaluation[];

  // 筛选
  filter_verdict: string | null;
  filter_overturned_only: boolean;

  // 统计
  verdict_distribution: Record<string, number>;
  overturn_rate: number;                // LLM 观察者推翻引擎判定的比例

  onSelectEvaluation: (evaluationId: string) => void;
  onToggleObserver: (enable: boolean) => void;
}

// ── 6.19 智能采样状态指示器（§11） ──

export interface SmartSamplingIndicatorProps {
  state: SmartSamplingState;

  // 各通道的当前采样率
  onChannelRateChange: (channel: string, rate: number) => void;

  // 告警
  show_drop_warning: boolean;           // dropped_critical_events > 0 时显示
  show_overhead_warning: boolean;       // cpu_overhead_pct > 5% 时显示
}

// ── 6.20 Handoff 版本 DAG 视图（§23） ──

export interface HandoffVersionDAGViewProps {
  dag: HandoffVersionDAG;
  selected_version_id: string | null;

  // 可视化
  layout: "vertical" | "horizontal" | "tree";

  // 合谋检测高亮
  highlight_collusion: boolean;

  onSelectVersion: (versionId: string) => void;
  onCompareVersions: (versionIdA: string, versionIdB: string) => void;
}

// 版本对比视图
export interface VersionDiffViewProps {
  version_a: VersionNode;
  version_b: VersionNode;
  unified_diff: string;
  onClose: () => void;
}

// ── 6.21 交互式取证仪表板（§21） ──

export interface ForensicsDashboardProps {
  // 多维筛选器
  filters: ForensicsFilters;

  // 筛选结果
  filtered_events: HandoffVisualizationEvent[];
  total_events_scanned: number;
  filtered_event_count: number;
  convergence_factor: number;           // 收敛比（total_scanned / filtered_count）

  // 一键导出
  onExportReport: (filters: ForensicsFilters) => ForensicsExportReport;

  // 筛选器更新
  onFiltersChange: (filters: Partial<ForensicsFilters>) => void;
  onResetFilters: () => void;
}

export interface ForensicsFilters {
  event_types: InstrumentType[];        // 按仪象类型筛选
  time_window_start_ns: number | null;
  time_window_end_ns: number | null;
  agent_ids: string[];
  chain_depth_min: number | null;
  chain_depth_max: number | null;
  taint_labels: string[];              // 按污点标签筛选
  hash_chain_status: "verified" | "mismatch" | null;
  severity_min: string | null;
  search_text: string | null;           // 全文搜索
}

/**
 * 取证报告导出一键生成（§21）
 * 含完整时间链、哈希链、污点传播树、IOC 清单、差分回放对比表
 */
export interface ForensicsExportReport {
  report_id: string;
  generated_at: string;
  filters_applied: ForensicsFilters;

  // 完整时间链
  timeline: Array<{
    timestamp_ns: number;
    event_type: string;
    description: string;
    agent_id: string;
    evidence: string;
  }>;

  // 哈希链（只含 mismatch）
  hash_chain_issues: HashChainIntegrityEvent[];

  // 污点传播树
  taint_propagation_tree: TaintPropagationEvent[];

  // IOC 清单
  ioc_list: ExtractedIOC[];

  // 差分回放对比（如有）
  diff_replay: DiffReplayResult | null;

  // 摘要
  summary: {
    total_events_analyzed: number;
    total_alerts: number;
    total_hash_mismatches: number;
    total_taint_chains: number;
    total_iocs: number;
    blast_radius_score: number | null;
    conclusion: string;
  };
}

// ── 6.22 TOCTOU 页缓存夹缝取证面板（§50） ──

export interface TOCTOUDetectionPanelProps {
  toctou_events: TOCTOUDetectionEvent[];
  selected_toctou_id: string | null;

  // 三层布防可视化切换
  visible_layers: {
    layer1_page_cache: boolean;
    layer2_write_window: boolean;
    layer3_clearfield: boolean;
  };

  // 筛选
  filter_document_path: string | null;
  filter_confirmed_only: boolean;
  filter_severity_min: "warn" | "error" | "critical";

  // 统计
  total_toctou_events: number;
  confirmed_toctou: number;
  critical_toctou: number;                // 内核级攻击确认数

  onSelectTOCTOU: (toctouId: string) => void;
  onNavigateToGraph: (nodeId: string) => void;
  onExportTOCTOUReport: () => void;
}

// TOCTOU 三层布防时间线视图
export interface TOCTOUTimelineViewProps {
  event: TOCTOUDetectionEvent;

  // 时间线渲染：last_legitimate_write → kernel_level_writes → first_read
  window_timeline: Array<{
    timestamp_ns: number;
    event_type: "legitimate_write" | "kernel_write" | "page_fault_read" | "clearfield_flush";
    description: string;
    agent: string;
    is_anomalous: boolean;
  }>;

  onNavigateToTimestamp: (timestampNs: number) => void;
  onClose: () => void;
}

// ── 6.23 意图-执行双流一致性面板（§51） ──

export interface IntentExecutionDriftPanelProps {
  evaluations: IntentExecutionConsistencyEvent[];
  selected_evaluation_id: string | null;

  // 筛选
  filter_agent_id: string | null;
  filter_drift_triggered_only: boolean;
  filter_drift_severity_min: "low" | "medium" | "high" | "critical";

  // 统计
  total_evaluations: number;
  drift_triggered_count: number;
  avg_cosine_similarity: number;
  most_diverged_agent_id: string | null;

  onSelectEvaluation: (evaluationId: string) => void;
  onNavigateToAgent: (agentId: string) => void;
}

// 双流对比视图（意图向量 vs 执行向量）
export interface IntentExecutionCompareViewProps {
  evaluation: IntentExecutionConsistencyEvent;

  // 双流并排展示
  intent_summary: string;
  execution_summary: string;
  cosine_similarity: number;

  // 三基准校准仪表
  calibration_gauges: {
    self_p5: number;
    group_median: number;
    empty_baseline: number;
    current: number;
  };

  // 逐段消融测试结果表
  ablation_table: IntentExecutionConsistencyEvent["ablation_traceback"]["ablation_results"];

  // 降维投影（前 3 维 PCA 用于 3D 散点图）
  intent_projection_3d: [number, number, number] | null;
  execution_projection_3d: [number, number, number] | null;

  onNavigateToParagraph: (paragraphHash: string) => void;
  onExportInjectionSignature: () => void;
  onClose: () => void;
}

// ── 6.24 环境指纹防重放面板（§52） ──

export interface EnvironmentFingerprintPanelProps {
  fingerprint_events: EnvironmentFingerprintEvent[];
  selected_fingerprint_id: string | null;

  // 筛选
  filter_agent_id: string | null;
  filter_collision_level: "low" | "high" | "critical" | null;
  filter_escape_alert_only: boolean;

  // 统计
  total_fingerprints: number;
  exact_collisions: number;
  proximity_collisions: number;
  env_escape_alerts: number;
  dual_chain_break_count: number;

  // 六维雷达图数据（按 agent_id 分组）
  environment_vectors: Record<string, EnvironmentFingerprintEvent["environment_vector"]>;

  onSelectFingerprint: (fingerprintId: string) => void;
  onNavigateToAgent: (agentId: string) => void;
  onExportFingerprintReport: () => void;
}

// 六维环境向量雷达图
export interface EnvironmentVectorRadarProps {
  vector: EnvironmentFingerprintEvent["environment_vector"];
  comparison_vector?: EnvironmentFingerprintEvent["environment_vector"]; // 碰撞对比
  highlight_mismatched_dimension?: string;
  compact?: boolean;
}

// 双链完整性状态指示器
export interface DualChainIntegrityIndicatorProps {
  document_chain_intact: boolean;
  environment_chain_intact: boolean;
  dual_chain_trustworthy: boolean;
  chain_break_reason: string | null;
  compact?: boolean;
}

// 环境指纹碰撞时间线
export interface FingerprintCollisionTimelineProps {
  collisions: Array<{
    session_id: string;
    agent_id: string;
    timestamp_ns: number;
    collision_type: "exact" | "proximity";
    mismatched_dimension: string | null;
    linked_campaign_ids: string[];
  }>;
  onNavigateToSession: (sessionId: string) => void;
  onNavigateToCampaign: (campaignId: string) => void;
}

// ═══════════════════════════════════════════════════════════════
// 七、客户端全局聚合状态
//     存入现有 zustand AppStore，与 handoff-dashboard-contract.ts
//     的 HandoffDashboardState 并存
// ═══════════════════════════════════════════════════════════════

export interface HandoffVisualizationState {
  // 实时事件缓冲区
  recent_events: HandoffVisualizationEvent[];

  // 按 agent_id 分组的信号聚合
  signal_aggregation: Record<string, SignalAggregationEvent[]>;

  // 信任评分快照（最新）
  trust_scores: Record<string, TrustScoreEvent>;

  // 哈希链完整性状态
  hash_chain_status: {
    total_writes: number;
    total_reads: number;
    verified: number;
    mismatches: number;
  };

  // 金丝雀状态
  canary_status: {
    active: number;
    breached: number;
    dormant: number;
    latest_breach: CanaryInjectionEvent | null;
  };

  // 基线偏差
  baseline_deviations: BaselineDeviationEvent[];

  // 跨会话漂移
  session_drifts: CrossSessionDriftEvent[];

  // 战役关联
  campaigns: CampaignCorrelationEvent[];

  // 采样状态
  sampling: SmartSamplingState;

  // IOC 特征库
  iocs: ExtractedIOC[];

  // 3D 力导向图状态（延迟加载）
  force_graph_3d: ForceGraph3DState | null;

  // v3.1 增补 — §50 TOCTOU 页缓存夹缝取证
  toctou_events: TOCTOUDetectionEvent[];
  toctou_status: {
    total_detected: number;
    confirmed: number;
    critical: number;
    latest_toctou: TOCTOUDetectionEvent | null;
  };

  // v3.1 增补 — §51 意图-执行双流一致性
  intent_execution_evaluations: IntentExecutionConsistencyEvent[];
  intent_drift_status: {
    total_evaluations: number;
    drift_triggered: number;
    avg_cosine_similarity: number;
    ablation_in_progress: boolean;
  };

  // v3.1 增补 — §52 环境指纹防重放
  environment_fingerprints: EnvironmentFingerprintEvent[];
  fingerprint_status: {
    total_fingerprints: number;
    exact_collisions: number;
    proximity_collisions: number;
    env_escape_alerts: number;
    dual_chain_break_count: number;
    latest_collision: EnvironmentFingerprintEvent | null;
  };

  // 取证筛选器状态
  forensics_filters: ForensicsFilters;

  // 差分回放状态（按需启动）
  diff_replay: DiffReplayState | null;

  // 仪表板可见性
  visible_instruments: Set<InstrumentType>;
  visible_deep_instruments: Set<DeepInstrumentType>;
}

// ═══════════════════════════════════════════════════════════════
// 八、类型守卫
// ═══════════════════════════════════════════════════════════════

export function isFileAccessAudit(e: HandoffVisualizationEvent): e is FileAccessAuditEvent {
  return e.sub_type === "FILE_ACCESS_AUDIT";
}
export function isMCPProtocol(e: HandoffVisualizationEvent): e is MCPProtocolEvent {
  return e.sub_type === "MCP_PROTOCOL";
}
export function isTaintPropagation(e: HandoffVisualizationEvent): e is TaintPropagationEvent {
  return e.sub_type === "TAINT_PROPAGATION";
}
export function isHashChainIntegrity(e: HandoffVisualizationEvent): e is HashChainIntegrityEvent {
  return e.sub_type === "HASH_CHAIN_INTEGRITY";
}
export function isCredentialLeak(e: HandoffVisualizationEvent): e is CredentialLeakEvent {
  return e.sub_type === "CREDENTIAL_LEAK";
}
export function isCrossChannelCorrelation(e: HandoffVisualizationEvent): e is CrossChannelCorrelationEvent {
  return e.sub_type === "CROSS_CHANNEL_CORRELATION";
}
export function isCanaryInjection(e: HandoffVisualizationEvent): e is CanaryInjectionEvent {
  return e.sub_type === "CANARY_INJECTION";
}
export function isTrustScore(e: HandoffVisualizationEvent): e is TrustScoreEvent {
  return e.sub_type === "TRUST_SCORE";
}
export function isCampaignCorrelation(e: HandoffVisualizationEvent): e is CampaignCorrelationEvent {
  return e.sub_type === "CAMPAIGN_CORRELATION";
}
export function isSignalAggregation(e: HandoffVisualizationEvent): e is SignalAggregationEvent {
  return e.sub_type === "SIGNAL_AGGREGATION";
}
export function isPatternMatchAlert(e: HandoffVisualizationEvent): e is PatternMatchAlertEvent {
  return e.sub_type === "PATTERN_MATCH_ALERT";
}
export function isBaselineDeviation(e: HandoffVisualizationEvent): e is BaselineDeviationEvent {
  return e.sub_type === "BASELINE_DEVIATION";
}
export function isCrossSessionDrift(e: HandoffVisualizationEvent): e is CrossSessionDriftEvent {
  return e.sub_type === "CROSS_SESSION_DRIFT";
}
export function isTOCTOUDetection(e: HandoffVisualizationEvent): e is TOCTOUDetectionEvent {
  return e.sub_type === "TOCTOU_DETECTION";
}
export function isIntentExecutionDrift(e: HandoffVisualizationEvent): e is IntentExecutionConsistencyEvent {
  return e.sub_type === "INTENT_EXECUTION_DRIFT";
}
export function isEnvironmentFingerprint(e: HandoffVisualizationEvent): e is EnvironmentFingerprintEvent {
  return e.sub_type === "ENVIRONMENT_FINGERPRINT";
}

/**
 * 按 InstrumentType 筛选事件
 * 对应 §1–§27 各目
 */
export function filterEventsByInstrument(
  events: HandoffVisualizationEvent[],
  instrument: InstrumentType,
): HandoffVisualizationEvent[] {
  const guardMap: Record<InstrumentType, (e: HandoffVisualizationEvent) => boolean> = {
    tls_decrypted_stream: () => false,       // 已有独立流
    process_lifecycle: () => false,          // 已有 ProcessTreeView
    sse_event_timeline: () => false,         // 已有 TimelinePanel
    responsibility_graph: () => false,       // 已有 ResponsibilityGraphView
    identity_resolution: () => false,        // 已集成在并查集中，无独立事件
    taint_propagation: isTaintPropagation,
    handoff_version_dag: () => false,        // 通过 REST API 加载
    file_access_audit: isFileAccessAudit,
    stdio_mcp_protocol: isMCPProtocol,
    credential_leak: isCredentialLeak,
    forensic_timeline: isCrossChannelCorrelation, // 跨通道关联 = 取证时间线锚点
    hash_chain_integrity: isHashChainIntegrity,
    cross_session_drift: isCrossSessionDrift,
    cross_channel_correlation: isCrossChannelCorrelation,
    pattern_match_alert: isPatternMatchAlert,
    signal_aggregation: isSignalAggregation,
    baseline_deviation: isBaselineDeviation,
    canary_injection: isCanaryInjection,
    trust_decay: isTrustScore,
    campaign_correlation: isCampaignCorrelation,
    toctou_window_detection: isTOCTOUDetection,
    intent_execution_drift: isIntentExecutionDrift,
    environment_fingerprint: isEnvironmentFingerprint,
  };
  const guard = guardMap[instrument];
  return guard ? events.filter(guard) : [];
}

/**
 * 事件严重度到视觉等级映射
 */
export function eventSeverityToVisualLevel(
  severity: "info" | "warn" | "error" | "critical",
): "normal" | "elevated" | "high" | "urgent" {
  switch (severity) {
    case "info": return "normal";
    case "warn": return "elevated";
    case "error": return "high";
    case "critical": return "urgent";
  }
}

/**
 * 检测是否为关键安全事件（不允许被过滤丢弃）
 * §11: 关键路径事件（SSL 解密、文件写入）不可降采样
 * §补充需求 C: 跨边界事件 allow_filter 必须为 false
 */
export function isCriticalSecurityEvent(event: HandoffVisualizationEvent): boolean {
  return (
    isCredentialLeak(event) ||
    (isHashChainIntegrity(event) && !event.hash_match) ||
    isCanaryInjection(event) ||
    (isTaintPropagation(event) && event.severity === "critical") ||
    (isTOCTOUDetection(event) && event.toctou_confirmed) ||
    (isEnvironmentFingerprint(event) && (
      event.collision_detection.collision_level === "critical" ||
      event.binding_signature.env_escape_alert
    ))
  );
}

// ═══════════════════════════════════════════════════════════════
// 九、集成点 — 与现有系统的接头
//     此处列举新类型需接入的现有模块
// ═══════════════════════════════════════════════════════════════

/**
 * 集成清单:
 *
 * 1. SSE 分发器 (event-dispatcher.ts):
 *    - 新增 stream_type: "handoff_visualization" 分支
 *    - 路由到 HandoffVisualizationEvent 解析器
 *
 * 2. zustand AppStore (store.ts):
 *    - 新增 HandoffVisualizationState 切片
 *    - 每个事件到达时更新对应聚合状态
 *
 * 3. TimelinePanel (timeline-panel.tsx):
 *    - 注册 FileAccessAuditRow, MCPProtocolRow, CrossChannelCorrelationRow 行渲染器
 *    - 注册 PatternMatchAlertRow, CredentialLeakRow 告警行渲染器
 *
 * 4. DetailPanel (detail-panel.tsx):
 *    - 新增 TaintNodeDetail, HashChainTrace, MCPMessageDetail
 *    - 新增 TrustScoreCard, AttackerProfile, PatternMatchAlertDetail
 *
 * 5. DashboardShell (dashboard-shell.tsx):
 *    - 新增标签页: "取证仪表板", "信号监控", "安全审计"
 *    - 顶栏状态指示器: SmartSamplingIndicator
 *
 * 6. 新增面板组件 (panels/):
 *    - TaintPropagationTreePanel
 *    - HashChainDashboardPanel
 *    - CredentialLeakPanel
 *    - CrossChannelCorrelationPanel
 *    - CanaryInjectionPanel
 *    - TrustScoreDashboardPanel
 *    - CampaignCorrelationPanel
 *    - SignalAggregationDashboardPanel
 *    - BaselineDeviationPanel
 *    - CrossSessionDriftPanel
 *    - SessionReplayPanel (含回放控制器)
 *    - DiffReplayComparisonPanel
 *    - IOCExtractionPanel
 *    - ForceGraph3DPanel (基于 3d-force-graph)
 *    - ForensicsDashboardPanel
 *    - HandoffVersionDAGPanel
 *    - TOCTOUDetectionPanel (§50: 三层布防时间线 + 页缓存差异视图)
 *    - IntentExecutionDriftPanel (§51: 双流对比 + 逐段消融表)
 *    - EnvironmentFingerprintPanel (§52: 六维雷达图 + 碰撞时间线)
 *
 * 7. REST API (api.ts):
 *    - GET /api/sessions/:id/replay         → SessionReplayState
 *    - POST /api/replay/compare             → DiffReplayState
 *    - GET /api/iocs                        → ExtractedIOC[]
 *    - GET /api/graph/force-layout/:snapshotId → ForceGraph3DState
 *    - GET /api/handoff/version-dag/:path    → HandoffVersionDAG
 *    - GET /api/sessions/:id/drift           → CrossSessionDriftEvent[]
 *    - GET /api/campaigns                    → CampaignCorrelationEvent[]
 *    - GET /api/toctou/:document_path         → TOCTOUDetectionEvent[]
 *    - GET /api/intent-drift/:agent_id         → IntentExecutionConsistencyEvent
 *    - GET /api/env-fingerprint/:session_id    → EnvironmentFingerprintEvent
 *    - GET /api/env-fingerprint/collisions     → 碰撞事件列表（跨会话）
 *
 * 8. 前端配置 (HandoffVisualizationConfig):
 *    - driftScoreThreshold: number (默认 0.5)
 *    - crossChannelWindowMs: number (默认 500)
 *    - trustDecayCoefficient: number (默认 0.85)
 *    - idleThresholdSeconds: number (默认 60)
 *    - canaryDensityBySensitivity: Record<string, number>
 *    - dbscanEps: number (默认 0.3)
 *    - dbscanMinSamples: number (默认 3)
 *    - earlyWarningSigmas: number (默认 1.5)
 *    - criticalCpuOverheadPct: number (默认 5)
 *    - criticalBufferUsagePct: number (默认 80)
 */

// ═══════════════════════════════════════════════════════════════
// 十、术法符文终章 — 型制约法就绪（v3.1）
//     此文与第二局器具营缮要略（三阶递验章程）相合：
//     - 第一阶文契递验 → §16 哈希链、§25 金丝雀、§23 版本DAG、§50 TOCTOU 夹缝取证
//     - 第二阶流布递验 → §13 污点传播、§5 文件审计、§12 身份解析、§24 跨通道、§22 凭据泄露
//     - 第三阶因果递验 → §14 差分回放、§8 图模式匹配、§18 IOC 提取、§10 信号异常、
//                        §20 跨会话漂移、§51 意图-执行双流校验、§52 环境指纹防重放
//
//     此文与首局递送文书相合：
//     - 首局五十二目，每目皆有对应仪象类型
//     - 递送交接之机锋密令，隐于各型之 severity 规则
//     - 哈希链、金丝雀、信任衰减，三者为不可删除之核心约束
//     - 新增 TOCTOU（§50）、意图漂移（§51）、环境指纹（§52）三符，
//       补全夹缝取证、语义溯因、上下文防重放之术
// ═══════════════════════════════════════════════════════════════
