// ═══════════════════════════════════════════════════════════════
// 第四局 — 宫室仪象 · 三阶递验型制约法
// 阅  首局递送文书 full_handoff.md（计五十二目 + 三目补遗）
// 及  第二局营缮要略（三阶递验章程 · 器具接口与流转程式）
//
// 拟定宫室仪象所呈之型制：
//   一阶验其形（文契不破）—— 不动根本印
//   二阶验其传（流布不泄）—— 水月镜花印
//   三阶验其果（因果不断）—— 天网恢恢印
//
// 三阶相承，缺一不可。前阶不通，后阶不行。
//
// 与第二局之器相合：
//   sha256sum / diff -u / grep -n          → 一阶文契仪象
//   sqlite3 / jq / cargo test --lib *      → 二阶流布仪象
//   mtagentlens replay / diff / 浏览器DOM    → 三阶因果仪象
//
// 术法符文之形（TypeScript 类型契约）
// 对齐现有模式: lib/data-contract.ts · lib/types.ts
//               lib/handoff-visualization-contract.ts
//               lib/analyzer-dashboard-contract.ts
// ═══════════════════════════════════════════════════════════════

import type { AnomalySeverity } from "./data-contract";
import type { AlertSeverity, MatchLevel } from "./types";
import type { InstrumentType, DeepInstrumentType } from "./handoff-visualization-contract";

// ═══════════════════════════════════════════════════════════════
// 一、递验三阶 — 阶名、符名、门禁约束
// ═══════════════════════════════════════════════════════════════

/** 递验三阶鉴别符 */
export type VerificationTier =
  | "tier_1_document"    // 第一阶 · 文契递验 · 不动根本印
  | "tier_2_propagation" // 第二阶 · 流布递验 · 水月镜花印
  | "tier_3_causal";     // 第三阶 · 因果递验 · 天网恢恢印

/** 阶名与符名映射 */
export const TIER_SEAL_NAMES: Record<VerificationTier, { name: string; seal: string }> = {
  tier_1_document:    { name: "文契递验", seal: "不动根本印" },
  tier_2_propagation: { name: "流布递验", seal: "水月镜花印" },
  tier_3_causal:      { name: "因果递验", seal: "天网恢恢印" },
};

/** 递验阶状态 */
export type TierStatus =
  | "pending"       // 尚未开始
  | "in_progress"   // 递验执行中
  | "passed"        // 全部断言通过
  | "partial_fail"  // 部分断言失败（可继续但需标记）
  | "blocked"       // 前阶未通过，本阶被阻断
  | "failed";       // 递验失败，需回归修复

/** 阶间门禁约束：前阶不通，后阶不行 */
export interface TierGateState {
  tier: VerificationTier;
  status: TierStatus;
  /** 本阶断言总数 */
  total_assertions: number;
  /** 已通过断言数 */
  passed_assertions: number;
  /** 失败断言数 */
  failed_assertions: number;
  /** 阻断后续阶的失败断言（若 status === "failed" 则非空） */
  blocking_failures: string[];
  /** 阶开始时间 (epoch ms) */
  started_at_ms: number | null;
  /** 阶完成时间 (epoch ms) */
  completed_at_ms: number | null;
  /** 前阶状态（用于门禁判定） */
  prerequisite_tier: VerificationTier | null;
  /** 前阶是否已通过（门禁开关） */
  prerequisite_satisfied: boolean;
}

/** 三阶贯通状态 */
export interface VerificationProgress {
  tiers: Record<VerificationTier, TierGateState>;
  /** 当前活跃阶 */
  active_tier: VerificationTier | null;
  /** 整体递验是否完成（三阶全部 PASSED） */
  all_tiers_passed: boolean;
  /** 跨阶一致性约束是否全部满足 */
  cross_tier_consistency_passed: boolean;
  /** 自进化闭环上次迭代时间 */
  last_immunity_loop_ms: number | null;
}

// ═══════════════════════════════════════════════════════════════
// 二、递验断言矩阵 — 每阶验符核心类型
// ═══════════════════════════════════════════════════════════════

/** 断言验证方式分类（与第二局器具对应） */
export type AssertionMethod =
  | "sha256_compare"          // sha256sum 比对
  | "diff_output"             // diff -u 输出比对
  | "grep_count"              // grep -c 计数
  | "sql_query"               // SQLite 查询
  | "jq_filter"               // jq JSONL 过滤
  | "python_assert"           // Python 脚本断言
  | "mtagentlens_replay_diff"  // mtagentlens diff 命令
  | "mtagentlens_cli"          // mtagentlens replay/record 命令
  | "browser_dom"             // 浏览器 DOM 断言
  | "browser_sse"             // 浏览器 SSE 事件流验证
  | "zustand_devtools"        // Zustand DevTools store 验证
  | "cargo_test";             // cargo test --lib 单元测试

/** 断言结果 */
export type AssertionVerdict =
  | "PASS"
  | "FAIL"
  | "NA"        // 不适用（如数据不足）
  | "INFO"      // 信息性（无循环事件等场景的信息输出）
  | "SKIP";     // 已跳过（前阶阻断）

/** 单条递验断言 */
export interface VerificationAssertion {
  /** 断言编号，如 "1.3.1", "2.3.5", "3.3.12" */
  assertion_id: string;
  /** 所属递验阶 */
  tier: VerificationTier;
  /** 断言描述 */
  description: string;
  /** 验证方式 */
  method: AssertionMethod;
  /** 通过阈值（人类可读） */
  threshold: string;
  /** 判定结果 */
  verdict: AssertionVerdict;
  /** 实际输出（截断至 500 字符） */
  actual_output: string;
  /** 执行时间 (ms) */
  execution_ms: number;
  /** 失败时指向的破执（失败处置）记录 */
  failure_remediation_ref: string | null;
  /** 断言对应的第二局步法引用（如 "步 1.2.2", "步 3.2.7.5"） */
  step_ref: string;
  /** 关联的首局术法目次（如 "§38", "§47"） */
  clause_refs: string[];
  /** 关联的仪器类型（前端渲染跳转） */
  instrument_type: InstrumentType | DeepInstrumentType | null;
  /** 详细输出（完整 diff / SQL 结果集 / DOM snapshot），按需加载 */
  detail_payload: string | null;
}

/** 阶断言矩阵（按 tier 分组） */
export interface TierAssertionMatrix {
  tier: VerificationTier;
  assertions: VerificationAssertion[];
  /** 判定汇总 */
  summary: {
    total: number;
    passed: number;
    failed: number;
    na: number;
    info: number;
    skipped: number;
    pass_rate: number; // 0.0–1.0，NA 不计入分母
  };
  /** 失败断言列表（按严重度排序） */
  failures: VerificationAssertion[];
  /** 阻断后续阶的致命失败 */
  blocking_failures: VerificationAssertion[];
}

// ═══════════════════════════════════════════════════════════════
// 三、破执（失败处置）— 自动修复动作与指南
// ═══════════════════════════════════════════════════════════════

/** 失败模式（与三阶递验章程 §1.4/§2.4/§3.4 对齐） */
export type FailureMode =
  // 第一阶破执
  | "hash_mismatch"
  | "canary_triggered"
  | "version_dag_collusion"
  | "timestamp_honeypot_broken"
  | "seda_cross_pid_collision"
  | "seda_toctou_masking"
  | "seda_false_suppression"
  // 第二阶破执
  | "propagation_chain_gap"
  | "cross_boundary_unmarked"
  | "filter_dropped_cross_boundary"
  | "identity_union_failure"
  | "credential_redaction_incomplete"
  | "tool_call_stuck_pending"
  | "tool_definition_parse_failure"
  | "cross_agent_tool_chain_broken"
  | "unauthorized_tool_false_positive"
  // 第三阶破执
  | "diff_replay_no_difference"
  | "causal_edge_chain_broken"
  | "alert_not_triggered"
  | "ioc_extraction_incomplete"
  | "signal_anomaly_lagging"
  | "frontend_panel_render_error"
  | "ald_false_negative"
  | "ald_false_positive"
  | "ald_cooldown_severity_block"
  | "ald_tool_extraction_failure";

/** 破执记录：失败模式 → 根因 → 修复动作 */
export interface FailureRemediation {
  /** 失败模式鉴别符 */
  mode: FailureMode;
  /** 所属递验阶 */
  tier: VerificationTier;
  /** 人类可读名称 */
  label: string;
  /** 根因分析 */
  root_cause: string;
  /** 修复动作（人类可读步骤） */
  remediation_steps: string[];
  /** 关联的第二局步法引用 */
  step_refs: string[];
  /** 是否需要人工介入（true = 自动修复不可行） */
  requires_human_intervention: boolean;
  /** 是否已在当前会话尝试修复 */
  fix_attempted: boolean;
  /** 修复结果（若已尝试） */
  fix_result: "fixed" | "partial" | "failed" | null;
}

/** 失败模式注册表 */
export const FAILURE_REMEDIATION_CATALOG: Record<FailureMode, Omit<FailureRemediation, "fix_attempted" | "fix_result">> = {
  // 第一阶破执
  hash_mismatch: {
    mode: "hash_mismatch",
    tier: "tier_1_document",
    label: "哈希不匹配",
    root_cause: "文件在递送中被篡改",
    remediation_steps: ["回滚至上一版本", "阻断当前子代理执行", "生成 hash_mismatch 告警"],
    step_refs: ["步 1.2.2"],
    requires_human_intervention: true,
  },
  canary_triggered: {
    mode: "canary_triggered",
    tier: "tier_1_document",
    label: "金丝雀被触发",
    root_cause: "子代理盲读了全文并执行了不可见指令",
    remediation_steps: ["标记该子代理为 compromised", "阻断其输出", "IOC 提取后纳入特征库"],
    step_refs: ["步 1.2.3"],
    requires_human_intervention: false,
  },
  version_dag_collusion: {
    mode: "version_dag_collusion",
    tier: "tier_1_document",
    label: "版本 DAG 检测到合谋边",
    root_cause: "多个子代理协同修改形成恶意组合指令",
    remediation_steps: ["隔离涉及的子代理", "DAG 拓扑分析识别协同模式", "生成 collusion_injection 告警"],
    step_refs: ["步 1.2.4"],
    requires_human_intervention: true,
  },
  timestamp_honeypot_broken: {
    mode: "timestamp_honeypot_broken",
    tier: "tier_1_document",
    label: "时间戳蜜罐断裂",
    root_cause: "文档被剪切重组",
    remediation_steps: ["定位断裂点前后的代理操作日志", "还原篡改时间窗"],
    step_refs: ["步 1.2.3"],
    requires_human_intervention: true,
  },
  seda_cross_pid_collision: {
    mode: "seda_cross_pid_collision",
    tier: "tier_1_document",
    label: "SEDA 跨 PID 指纹碰撞",
    root_cause: "去重指纹哈希冲突（极低概率）或 PID 字段未正确提取",
    remediation_steps: ["检查 EventFingerprint::pid 提取逻辑", "启用 content-hash 模式做二次校验"],
    step_refs: ["步 1.2.5.2"],
    requires_human_intervention: false,
  },
  seda_toctou_masking: {
    mode: "seda_toctou_masking",
    tier: "tier_1_document",
    label: "SEDA 掩盖 TOCTOU 注入",
    root_cause: "time_quantum 设置过大（≥ TOCTOU 窗口）",
    remediation_steps: ["减小 time_quantum 至 100ms", "对 handoff 文件路径启用逐事件去重白名单绕过"],
    step_refs: ["步 1.2.5.3"],
    requires_human_intervention: false,
  },
  seda_false_suppression: {
    mode: "seda_false_suppression",
    tier: "tier_1_document",
    label: "SEDA 误杀安全告警",
    root_cause: "fingerprint 设计中 payload_length 代理内容哈希导致误合并",
    remediation_steps: ["启用 content_hash_mode = true", "对关键事件源（ssl, file_write）启用完整内容哈希"],
    step_refs: ["步 1.2.6"],
    requires_human_intervention: false,
  },
  // 第二阶破执
  propagation_chain_gap: {
    mode: "propagation_chain_gap",
    tier: "tier_2_propagation",
    label: "传播链缺跳",
    root_cause: "eBPF 事件丢失（缓冲区溢出、采样率不足）",
    remediation_steps: ["提高关键路径缓冲区大小", "检查 resource_monitor 日志"],
    step_refs: ["步 2.2.2"],
    requires_human_intervention: true,
  },
  cross_boundary_unmarked: {
    mode: "cross_boundary_unmarked",
    tier: "tier_2_propagation",
    label: "跨边界事件未标记",
    root_cause: "workspace_boundary 逻辑未正确识别工作区边界",
    remediation_steps: ["检查 IdentityResolver 工作区划分配置"],
    step_refs: ["步 2.2.3"],
    requires_human_intervention: true,
  },
  filter_dropped_cross_boundary: {
    mode: "filter_dropped_cross_boundary",
    tier: "tier_2_propagation",
    label: "过滤层丢弃跨边界事件",
    root_cause: "过滤规则未将 allow_filter=false 事件设为最高优先级",
    remediation_steps: ["修复事件过滤层优先级排序", "运行 FilterIntegrityState 回归测试"],
    step_refs: ["步 2.2.3"],
    requires_human_intervention: false,
  },
  identity_union_failure: {
    mode: "identity_union_failure",
    tier: "tier_2_propagation",
    label: "并查集无法缝合会话",
    root_cause: "tls_session 或 root_pid 信号丢失",
    remediation_steps: ["检查 eBPF uprobe 挂载状态", "确认 SSL 探针覆盖目标二进制"],
    step_refs: ["步 2.2.4"],
    requires_human_intervention: true,
  },
  credential_redaction_incomplete: {
    mode: "credential_redaction_incomplete",
    tier: "tier_2_propagation",
    label: "凭据脱敏不完整",
    root_cause: "正则模式未覆盖该凭据格式",
    remediation_steps: ["添加凭据格式到模式库", "更新 CredentialFinding.pattern_summary"],
    step_refs: ["步 2.2.6"],
    requires_human_intervention: false,
  },
  tool_call_stuck_pending: {
    mode: "tool_call_stuck_pending",
    tier: "tier_2_propagation",
    label: "tool_call 状态卡在 Pending",
    root_cause: "tool_result 未匹配到对应 tool_use_id（HTTP 响应截断或 SSE 流不完整）",
    remediation_steps: ["检查 HTTPParser 响应体完整性", "增加 ToolExecutionTracker timeout_ms 日志"],
    step_refs: ["步 2.2.7.2"],
    requires_human_intervention: false,
  },
  tool_definition_parse_failure: {
    mode: "tool_definition_parse_failure",
    tier: "tier_2_propagation",
    label: "tool_definition 解析失败",
    root_cause: "API 格式变更（Anthropic/OpenAI 新版本 tools[] schema 变化）",
    remediation_steps: ["更新 ToolCallExtractor 的 Anthropic/OpenAI 解析分支", "增加未知格式的 passthrough"],
    step_refs: ["步 2.2.7.1"],
    requires_human_intervention: false,
  },
  cross_agent_tool_chain_broken: {
    mode: "cross_agent_tool_chain_broken",
    tier: "tier_2_propagation",
    label: "跨 Agent 工具调用链断裂",
    root_cause: "eBPF 事件时间戳对齐偏差超过 ±1s 关联窗口",
    remediation_steps: ["放宽 JOIN 窗口至 ±5s", "使用 conversation_id 替代时间戳关联"],
    step_refs: ["步 2.2.7.3"],
    requires_human_intervention: false,
  },
  unauthorized_tool_false_positive: {
    mode: "unauthorized_tool_false_positive",
    tier: "tier_2_propagation",
    label: "未授权工具调用检测误报",
    root_cause: "tools[] 定义中工具名与实际调用工具名的字符串匹配不一致",
    remediation_steps: ["规范化工具名称比较：统一小写 + 移除命名空间前缀"],
    step_refs: ["步 2.2.8.2"],
    requires_human_intervention: false,
  },
  // 第三阶破执
  diff_replay_no_difference: {
    mode: "diff_replay_no_difference",
    tier: "tier_3_causal",
    label: "差分回放无差异",
    root_cause: "注入版回放未触发恶意指令（沙箱隔离不足或指令未被解析执行）",
    remediation_steps: ["检查沙箱环境配置", "确认回放执行器正确解析 handoff 文档中的所有指令"],
    step_refs: ["步 3.2.1"],
    requires_human_intervention: true,
  },
  causal_edge_chain_broken: {
    mode: "causal_edge_chain_broken",
    tier: "tier_3_causal",
    label: "因果边链断裂",
    root_cause: "eBPF 事件丢失或 ResponsibilityGraph 构建时的竞态条件",
    remediation_steps: ["检查事件时间戳对齐", "增加图构建的重试/延迟合并逻辑"],
    step_refs: ["步 3.2.2"],
    requires_human_intervention: true,
  },
  alert_not_triggered: {
    mode: "alert_not_triggered",
    tier: "tier_3_causal",
    label: "告警未触发",
    root_cause: "图模式匹配规则未覆盖当前注入模式",
    remediation_steps: ["提取当前注入模式为新的 GraphPattern", "注册至 PhaseClassificationEngine"],
    step_refs: ["步 3.2.3"],
    requires_human_intervention: false,
  },
  ioc_extraction_incomplete: {
    mode: "ioc_extraction_incomplete",
    tier: "tier_3_causal",
    label: "IOC 提取不完整",
    root_cause: "载荷分析管道未识别注入内容结构",
    remediation_steps: ["手动标注注入载荷", "扩展 IOC 提取的静态分析规则"],
    step_refs: ["步 3.2.4"],
    requires_human_intervention: true,
  },
  signal_anomaly_lagging: {
    mode: "signal_anomaly_lagging",
    tier: "tier_3_causal",
    label: "信号异常滞后于告警",
    root_cause: "SignalGraph 窗口参数过大，信号平滑过度",
    remediation_steps: ["减小滑动窗口大小", "降低信号异常触发延迟"],
    step_refs: ["步 3.2.5"],
    requires_human_intervention: false,
  },
  frontend_panel_render_error: {
    mode: "frontend_panel_render_error",
    tier: "tier_3_causal",
    label: "前端面板渲染异常",
    root_cause: "SSE 事件路由错误或 store action 未正确实现",
    remediation_steps: ["检查 routeHandoffSSEEvent() switch-case 分支", "确认 Zustand slice 已注册"],
    step_refs: ["步 3.2.8"],
    requires_human_intervention: false,
  },
  ald_false_negative: {
    mode: "ald_false_negative",
    tier: "tier_3_causal",
    label: "ALD 漏报注入诱导循环",
    root_cause: "循环重复次数未达 exact_repeat_threshold 或循环被 ignored_tools 排除",
    remediation_steps: ["降低阈值至 3", "检查 ignored_tools 列表是否误排除了注入诱发的工具"],
    step_refs: ["步 3.2.7.1"],
    requires_human_intervention: false,
  },
  ald_false_positive: {
    mode: "ald_false_positive",
    tier: "tier_3_causal",
    label: "ALD 误报正常 handoff 链",
    root_cause: "正常多 Agent 协作模式被误判为 PingPong",
    remediation_steps: ["增加 ping_pong_min_alternations 至 5", "检查是否为正常的多读者模式"],
    step_refs: ["步 3.2.7.5"],
    requires_human_intervention: false,
  },
  ald_cooldown_severity_block: {
    mode: "ald_cooldown_severity_block",
    tier: "tier_3_causal",
    label: "ALD 冷却期内漏报升级告警",
    root_cause: "冷却期锁定了 severity 升级路径",
    remediation_steps: ["冷却期内在窗口计数更新后检查是否需要 emit severity 升级事件"],
    step_refs: ["步 3.2.7.6"],
    requires_human_intervention: false,
  },
  ald_tool_extraction_failure: {
    mode: "ald_tool_extraction_failure",
    tier: "tier_3_causal",
    label: "ALD 工具调用提取失败",
    root_cause: "新型 Agent 框架的工具调用格式未被 ToolCallExtractor 覆盖",
    remediation_steps: ["为新的 API 格式添加 Extractor 实现", "未识别格式的事件透传不影响管道"],
    step_refs: ["步 3.2.7.1"],
    requires_human_intervention: false,
  },
};

// ═══════════════════════════════════════════════════════════════
// 四、跨阶一致性约束 — 三阶贯通验证
// ═══════════════════════════════════════════════════════════════

/** 跨阶一致性约束条目 */
export interface CrossTierConsistencyConstraint {
  /** 约束标识 */
  constraint_id: string;
  /** 约束描述 */
  description: string;
  /** 涉及的递验阶 */
  involved_tiers: VerificationTier[];
  /** 验证方式 */
  verification_method: string;
  /** 判定结果 */
  verdict: AssertionVerdict;
  /** 实际输出 */
  actual_output: string;
  /** 关联的断言编号列表 */
  linked_assertion_ids: string[];
  /** 失败时的破执引用 */
  failure_remediation_ref: string | null;
}

/** 跨阶一致性约束注册表（与三阶递验章程 §跨阶一致性约束 对齐） */
export const CROSS_TIER_CONSISTENCY_CONSTRAINTS: Omit<CrossTierConsistencyConstraint, "verdict" | "actual_output">[] = [
  {
    constraint_id: "CROSS-001",
    description: "Finding ID 一致性：cred-001-agent-b-security-review 在所有三层中出现且字段一致",
    involved_tiers: ["tier_1_document", "tier_2_propagation", "tier_3_causal"],
    verification_method: "跨阶 grep finding_id 比对",
    linked_assertion_ids: ["1.3.1", "2.3.2", "3.3.3"],
    failure_remediation_ref: null,
  },
  {
    constraint_id: "CROSS-002",
    description: "哈希链一致性：第一阶确定的 BASE_HASH 与第二、三阶传播链中引用的 content_hash 一致",
    involved_tiers: ["tier_1_document", "tier_2_propagation", "tier_3_causal"],
    verification_method: "跨阶 SHA-256 比对",
    linked_assertion_ids: ["1.3.1", "2.3.1", "3.3.2"],
    failure_remediation_ref: null,
  },
  {
    constraint_id: "CROSS-003",
    description: "时间戳单调性：hop.access_timestamp_ns 严格递增，propagation_hops.hop_order 与时间戳排序一致",
    involved_tiers: ["tier_2_propagation", "tier_3_causal"],
    verification_method: "时间戳序列单调性校验",
    linked_assertion_ids: ["2.3.2", "3.3.2"],
    failure_remediation_ref: null,
  },
  {
    constraint_id: "CROSS-004",
    description: "补充需求 §C 反过滤约束：后端 allow_filter=false 设置与前端 FilterIntegrityState 显示一致，无丢事件",
    involved_tiers: ["tier_2_propagation", "tier_3_causal"],
    verification_method: "前后端 FilterIntegrityState 比对",
    linked_assertion_ids: ["2.3.5", "3.3.9"],
    failure_remediation_ref: null,
  },
  {
    constraint_id: "CROSS-005",
    description: "SEDA 去重—安全事件守恒：启用 SEDA 后告警数 = 禁用 SEDA 后告警数（suppressed_by_dedup = 0）",
    involved_tiers: ["tier_1_document", "tier_2_propagation", "tier_3_causal"],
    verification_method: "mtagentlens diff --baseline-session replay-dedup-off --injected-session replay-dedup-on",
    linked_assertion_ids: ["1.3.11", "3.3.3"],
    failure_remediation_ref: "seda_false_suppression",
  },
  {
    constraint_id: "CROSS-006",
    description: "ToolExecutionTracker—ResponsibilityGraph 对齐：tool_call.call_id 在 responsibility_edges 中存在对应因果边节点",
    involved_tiers: ["tier_2_propagation", "tier_3_causal"],
    verification_method: "SQL JOIN tool_call_records ↔ responsibility_edges",
    linked_assertion_ids: ["2.3.13", "3.3.2"],
    failure_remediation_ref: "cross_agent_tool_chain_broken",
  },
  {
    constraint_id: "CROSS-007",
    description: "AgentLoopDetector—时序一致性：agent_loop.first_occurrence_ns > 最早 handoff file_read 时间戳，无时间倒错",
    involved_tiers: ["tier_3_causal"],
    verification_method: "Python 时间戳比对",
    linked_assertion_ids: ["3.3.11", "3.3.16"],
    failure_remediation_ref: null,
  },
  {
    constraint_id: "CROSS-008",
    description: "三层 analyzer 消费-生产契约无冲突：各 analyzer 的 consumes/produces 在 PipelineOrchestrator 注册阶段通过 StageContract 验证",
    involved_tiers: ["tier_1_document", "tier_2_propagation", "tier_3_causal"],
    verification_method: "cargo test --lib pipeline_stage_contract",
    linked_assertion_ids: ["1.3.8", "2.3.11", "3.3.14"],
    failure_remediation_ref: null,
  },
];

// ═══════════════════════════════════════════════════════════════
// 五、自进化免疫闭环 — 闭环节点与数据流
// ═══════════════════════════════════════════════════════════════

/** 闭环节点类型 */
export type ImmunityLoopNode =
  | "ioc_extraction"         // IOC 提取节点
  | "shared_feature_library"  // 共享特征库节点
  | "campaign_correlation"    // 战役聚合节点
  | "attacker_profiling"      // 攻击者画像节点
  | "detection_rule_update"   // 检测规则热更新节点
  | "seda_whitelist_update"   // SEDA 白名单更新节点
  | "tool_latency_baseline"   // 工具延迟基线更新节点
  | "loop_signature_library"; // ALD 循环签名库更新节点

/** 闭环节点状态 */
export interface ImmunityLoopNodeState {
  node: ImmunityLoopNode;
  label: string;
  /** 最近一次执行时间 */
  last_execution_ms: number | null;
  /** 产出物描述 */
  artifact_description: string;
  /** 产出物版本/哈希 */
  artifact_version: string;
  /** 数据流入（上游节点） */
  inputs: ImmunityLoopNode[];
  /** 数据流出（下游节点） */
  outputs: ImmunityLoopNode[];
  /** 节点是否活跃 */
  active: boolean;
  /** 最近一次迭代中是否有新数据 */
  has_new_data: boolean;
}

/** 自进化免疫闭环状态 */
export interface ImmunityLoopState {
  nodes: Record<ImmunityLoopNode, ImmunityLoopNodeState>;
  /** 闭环迭代计数 */
  iteration_count: number;
  /** 最近一次闭环迭代时间 */
  last_iteration_ms: number | null;
  /** 闭环总耗时 (ms) */
  last_cycle_duration_ms: number | null;
  /** 最近一次迭代中更新的特征数 */
  last_features_updated: number;
  /** 历史迭代记录（最近 10 次） */
  recent_iterations: ImmunityLoopIterationRecord[];
}

/** 单次闭环迭代记录 */
export interface ImmunityLoopIterationRecord {
  iteration: number;
  timestamp_ms: number;
  /** 触发源（IOC 提取 / 战役聚合 / 手动触发） */
  trigger_source: "ioc" | "campaign" | "manual" | "scheduled";
  /** 检测到的注入会话数 */
  injection_sessions_count: number;
  /** 提取的 IOC 数 */
  iocs_extracted: number;
  /** 更新的检测规则数 */
  rules_updated: number;
  /** 闭环周期 (ms) */
  duration_ms: number;
}

// ═══════════════════════════════════════════════════════════════
// 六、法器状态 — 第二局所用器具的运行时状态
// ═══════════════════════════════════════════════════════════════

/** 法器类型（与第二局法器总表对齐） */
export type InstrumentTool =
  | "sha256sum"
  | "diff"
  | "grep"
  | "cargo_test_lib_persistence"
  | "cargo_test_lib_taint"
  | "cargo_test_lib_identity"
  | "cargo_test_lib_classification"
  | "cargo_test_lib_agent_loop"
  | "cargo_test_lib_event_dedup"
  | "cargo_test_lib_tool_execution"
  | "mtagentlens_record"
  | "mtagentlens_trace"
  | "mtagentlens_replay"
  | "mtagentlens_diff"
  | "sqlite3"
  | "jq"
  | "chrome_devtools"
  | "zustand_devtools";

/** 法器运行时状态 */
export interface InstrumentToolStatus {
  tool: InstrumentTool;
  label: string;
  /** 本次递验中是否已就绪 */
  available: boolean;
  /** 本次递验中的调用次数 */
  invocation_count: number;
  /** 最近一次执行结果 */
  last_result: "success" | "failure" | "pending";
  /** 最近一次执行输出摘要（截断至 200 字符） */
  last_output_summary: string | null;
  /** 执行总耗时 (ms) */
  total_execution_ms: number;
}

/** 法器就绪检查：递验开始前验证所有必要器具可用 */
export interface ToolReadinessCheck {
  all_ready: boolean;
  tools: InstrumentToolStatus[];
  /** 不可用的法器列表（阻断递验） */
  unavailable: InstrumentTool[];
}

// ═══════════════════════════════════════════════════════════════
// 七、递验实时流事件 — 与 SSE 对接
// ═══════════════════════════════════════════════════════════════

/** 递验流事件类型鉴别符 */
export type VerificationEventType =
  | "TIER_GATE_CHANGE"           // 阶门禁状态变更
  | "ASSERTION_EVALUATED"         // 单条断言完成评估
  | "ASSERTION_MATRIX_COMPLETE"   // 阶断言矩阵全部完成
  | "FAILURE_REMEDIATION_STARTED" // 开始执行破执修复
  | "FAILURE_REMEDIATION_COMPLETE" // 破执修复完成
  | "CROSS_TIER_CHECK_STARTED"    // 跨阶一致性校验开始
  | "CROSS_TIER_CHECK_RESULT"     // 单条跨阶约束完成校验
  | "IMMUNITY_LOOP_ITERATION"     // 自进化闭环完成一次迭代
  | "TOOL_EXECUTION_RESULT"       // 单次器具调用结果
  | "VERIFICATION_COMPLETE";      // 全部递验完成

/** 递验流事件统一信封 */
export interface VerificationStreamEvent {
  event_type: VerificationEventType;
  timestamp_ms: number;
  session_id: string;
  payload: unknown; // 按 event_type 细化
}

/** 阶门禁变更事件 */
export interface TierGateChangeEvent extends VerificationStreamEvent {
  event_type: "TIER_GATE_CHANGE";
  payload: {
    tier: VerificationTier;
    previous_status: TierStatus;
    new_status: TierStatus;
    gate_unblocked: boolean; // true = 下一阶的门禁被打开
  };
}

/** 断言评估完成事件 */
export interface AssertionEvaluatedEvent extends VerificationStreamEvent {
  event_type: "ASSERTION_EVALUATED";
  payload: {
    assertion: VerificationAssertion;
    /** 若 FAIL，关联的破执记录 */
    failure_remediation: FailureRemediation | null;
  };
}

/** 阶矩阵完成事件 */
export interface AssertionMatrixCompleteEvent extends VerificationStreamEvent {
  event_type: "ASSERTION_MATRIX_COMPLETE";
  payload: {
    tier: VerificationTier;
    matrix: TierAssertionMatrix;
    /** 是否可进入下一阶 */
    can_proceed: boolean;
  };
}

/** 破执执行结果事件 */
export interface FailureRemediationCompleteEvent extends VerificationStreamEvent {
  event_type: "FAILURE_REMEDIATION_COMPLETE";
  payload: {
    failure_mode: FailureMode;
    result: "fixed" | "partial" | "failed";
    /** 修复后重新评估的断言数 */
    re_evaluated_count: number;
    /** 修复后新通过的断言数 */
    newly_passed_count: number;
  };
}

/** 跨阶约束校验结果 */
export interface CrossTierCheckResultEvent extends VerificationStreamEvent {
  event_type: "CROSS_TIER_CHECK_RESULT";
  payload: {
    constraint: CrossTierConsistencyConstraint;
  };
}

/** 免疫闭环迭代事件 */
export interface ImmunityLoopIterationEvent extends VerificationStreamEvent {
  event_type: "IMMUNITY_LOOP_ITERATION";
  payload: {
    iteration: ImmunityLoopIterationRecord;
    /** 受影响的检测规则列表 */
    affected_rules: string[];
    /** 新增的 IOC 签名 */
    new_ioc_signatures: string[];
  };
}

/** 器具调用结果事件 */
export interface ToolExecutionResultEvent extends VerificationStreamEvent {
  event_type: "TOOL_EXECUTION_RESULT";
  payload: {
    tool: InstrumentTool;
    command: string;          // 实际执行的命令（截断至 500 字符）
    exit_code: number | null;
    stdout_snippet: string;   // 标准输出摘要
    stderr_snippet: string;   // 标准错误摘要
    duration_ms: number;
    success: boolean;
  };
}

/** 递验完成事件 */
export interface VerificationCompleteEvent extends VerificationStreamEvent {
  event_type: "VERIFICATION_COMPLETE";
  payload: {
    progress: VerificationProgress;
    overall_pass: boolean;
    total_duration_ms: number;
    /** 输出产物路径 */
    artifact_paths: string[];
  };
}

/** 递验流事件联合类型 */
export type VerificationEvent =
  | TierGateChangeEvent
  | AssertionEvaluatedEvent
  | AssertionMatrixCompleteEvent
  | FailureRemediationCompleteEvent
  | CrossTierCheckResultEvent
  | ImmunityLoopIterationEvent
  | ToolExecutionResultEvent
  | VerificationCompleteEvent;

// ═══════════════════════════════════════════════════════════════
// 八、宫室仪象组件 — 型制 Props 与状态
// ═══════════════════════════════════════════════════════════════

// ── 8.1 三阶递验总览 — VerificationShell ──

/** 总览视图模式 */
export type VerificationViewMode =
  | "overview"            // 三阶递验总览（默认）
  | "tier_detail"         // 单阶详细矩阵
  | "assertion_detail"    // 单条断言详情 + 破执指南
  | "cross_tier"          // 跨阶一致性校验
  | "immunity_loop"       // 自进化免疫闭环
  | "tool_instruments"    // 法器状态面板
  | "export_report";      // 递验报告导出

/** VerificationShell Props */
export interface VerificationShellProps {
  state: VerificationDashboardState;
  callbacks: VerificationShellCallbacks;
}

export interface VerificationShellCallbacks {
  onStartTier: (tier: VerificationTier) => void;
  onRetryTier: (tier: VerificationTier) => void;
  onViewAssertionDetail: (assertionId: string) => void;
  onExecuteFailureRemediation: (mode: FailureMode) => void;
  onViewCrossTierConstraint: (constraintId: string) => void;
  onExportReport: () => void;
  onChangeViewMode: (mode: VerificationViewMode) => void;
  onTriggerImmunityLoop: () => void;
}

// ── 8.2 递验进度条 — VerificationProgressBar ──

export interface VerificationProgressBarProps {
  progress: VerificationProgress;
  onSelectTier: (tier: VerificationTier) => void;
}

// ── 8.3 单阶断言矩阵 — TierAssertionMatrixPanel ──

export interface TierAssertionMatrixPanelProps {
  tier: VerificationTier;
  matrix: TierAssertionMatrix;
  /** 过滤条件 */
  filter: {
    verdict: AssertionVerdict | "ALL";
    method: AssertionMethod | "ALL";
    search: string; // 断言描述搜索
  };
  onFilterChange: (filter: Partial<TierAssertionMatrixPanelProps["filter"]>) => void;
  onSelectAssertion: (assertionId: string) => void;
  onRetryFailed: () => void;
}

/** 断言矩阵视觉规格 */
export const ASSERTION_VERDICT_STYLE: Record<AssertionVerdict, {
  bg: string;
  text: string;
  border: string;
  icon: string;
}> = {
  PASS:  { bg: "bg-emerald-900/20",  text: "text-emerald-400",  border: "border-emerald-700",  icon: "✓" },
  FAIL:  { bg: "bg-red-900/20",     text: "text-red-400",      border: "border-red-700",     icon: "✗" },
  NA:    { bg: "bg-slate-800/30",   text: "text-slate-500",    border: "border-slate-700",   icon: "−" },
  INFO:  { bg: "bg-blue-900/20",    text: "text-blue-400",     border: "border-blue-700",    icon: "ℹ" },
  SKIP:  { bg: "bg-amber-900/10",   text: "text-amber-600",    border: "border-amber-800",   icon: "⏭" },
};

// ── 8.4 断言详情面板 — AssertionDetailPanel ──

export interface AssertionDetailPanelProps {
  assertion: VerificationAssertion;
  /** 关联的破执记录（若 FAIL） */
  remediation: FailureRemediation | null;
  /** 器具执行日志 */
  tool_logs: ToolExecutionResultEvent[];
  onExecuteRemediation: (mode: FailureMode) => void;
  onNavigateToInstrument: (type: InstrumentType | DeepInstrumentType) => void;
  onClose: () => void;
}

// ── 8.5 跨阶一致性面板 — CrossTierConsistencyPanel ──

export interface CrossTierConsistencyPanelProps {
  constraints: CrossTierConsistencyConstraint[];
  progress: VerificationProgress;
  onSelectConstraint: (constraintId: string) => void;
}

// ── 8.6 自进化免疫闭环面板 — ImmunityLoopPanel ──

export interface ImmunityLoopPanelProps {
  state: ImmunityLoopState;
  onTriggerIteration: () => void;
  onSelectNode: (node: ImmunityLoopNode) => void;
  onViewIterationDetail: (iteration: number) => void;
}

/** 闭环节点视觉规格 */
export const IMMUNITY_LOOP_NODE_STYLE: Record<ImmunityLoopNode, {
  label: string;
  color: string;
  shape: "circle" | "diamond" | "hexagon" | "rectangle";
  description: string;
}> = {
  ioc_extraction: {
    label: "IOC 提取",
    color: "#f59e0b",  // amber
    shape: "diamond",
    description: "从注入告警中自动提取 IOC，写入共享特征库",
  },
  shared_feature_library: {
    label: "共享特征库",
    color: "#3b82f6",  // blue
    shape: "hexagon",
    description: "集中存储 IOC、循环签名、工具延迟基线，供检测规则热加载",
  },
  campaign_correlation: {
    label: "战役聚合",
    color: "#8b5cf6",  // violet
    shape: "diamond",
    description: "跨会话三层聚类（载荷/TTP/时空），关联注入战役",
  },
  attacker_profiling: {
    label: "攻击者画像",
    color: "#ef4444",  // red
    shape: "rectangle",
    description: "聚合载荷模板库、偏好战术、攻击时间偏好",
  },
  detection_rule_update: {
    label: "检测规则更新",
    color: "#10b981",  // emerald
    shape: "circle",
    description: "前置检测规则热更新（金丝雀、跨通道、群组偏差）",
  },
  seda_whitelist_update: {
    label: "SEDA 白名单更新",
    color: "#06b6d4",  // cyan
    shape: "rectangle",
    description: "handoff 文件路径白名单热更新，确保安全事件 100% 到达下游",
  },
  tool_latency_baseline: {
    label: "工具延迟基线",
    color: "#f97316",  // orange
    shape: "rectangle",
    description: "各 Agent 类型工具延迟 p50/p95/p99 基线周期性更新",
  },
  loop_signature_library: {
    label: "循环签名库",
    color: "#ec4899",  // pink
    shape: "diamond",
    description: "注入诱发的循环模式签名写入，优先匹配已知注入循环",
  },
};

// ── 8.7 法器状态面板 — InstrumentToolsPanel ──

export interface InstrumentToolsPanelProps {
  tools: InstrumentToolStatus[];
  readiness: ToolReadinessCheck;
  executionLog: ToolExecutionResultEvent[];
  onRunTool: (tool: InstrumentTool) => void;
}

// ── 8.8 递验报告导出 — VerificationExportPanel ──

export interface VerificationExportPanelProps {
  progress: VerificationProgress;
  matrices: Record<VerificationTier, TierAssertionMatrix>;
  crossTierConstraints: CrossTierConsistencyConstraint[];
  failures: FailureRemediation[];
  immunityLoop: ImmunityLoopState;
  sessionId: string;
  onExportJSON: () => void;
  onExportMarkdown: () => void;
  onExportPDF: () => void;
}

// ── 8.9 阶门禁指示器 — TierGateIndicator ──

export interface TierGateIndicatorProps {
  gate: TierGateState;
  isActive: boolean;
  onActivate: () => void;
}

/** 阶状态视觉规格 */
export const TIER_STATUS_STYLE: Record<TierStatus, {
  ring: string;
  dot: string;
  label: string;
  pulse: boolean;
}> = {
  pending:       { ring: "ring-slate-700",   dot: "bg-slate-600",   label: "待递验", pulse: false },
  in_progress:   { ring: "ring-blue-500",    dot: "bg-blue-400",    label: "递验中", pulse: true  },
  passed:        { ring: "ring-emerald-500", dot: "bg-emerald-400", label: "已通过", pulse: false },
  partial_fail:  { ring: "ring-amber-500",   dot: "bg-amber-400",   label: "部分失败", pulse: false },
  blocked:       { ring: "ring-slate-500",   dot: "bg-slate-400",   label: "被阻断", pulse: false },
  failed:        { ring: "ring-red-500",     dot: "bg-red-400",     label: "递验失败", pulse: false },
};

// ── 8.10 递验器物时间线 — VerificationArtifactTimeline ──

/** 递验器物：递验过程中产生的中间产物 */
export interface VerificationArtifact {
  artifact_id: string;
  tier: VerificationTier;
  step_ref: string;
  type: "hash_chain_entry" | "canary_scan_result" | "version_dag_dot" |
        "sql_query_result" | "jq_filter_output" | "python_assert_output" |
        "replay_log" | "diff_report" | "dom_snapshot" | "sse_event_log";
  label: string;
  timestamp_ms: number;
  file_path: string | null; // 若持久化至文件
  content_preview: string;  // 前 300 字符预览
  /** 是否为失败断言的关键证据 */
  is_evidence_for_failure: boolean;
  /** 关联的断言 ID */
  linked_assertion_id: string | null;
}

export interface VerificationArtifactTimelineProps {
  artifacts: VerificationArtifact[];
  selectedArtifactId: string | null;
  onSelectArtifact: (artifactId: string) => void;
  filter: {
    tier: VerificationTier | "ALL";
    type: VerificationArtifact["type"] | "ALL";
    evidenceOnly: boolean;
  };
  onFilterChange: (filter: Partial<VerificationArtifactTimelineProps["filter"]>) => void;
}

// ═══════════════════════════════════════════════════════════════
// 九、递验仪表板全局状态 — Zustand Slice
// ═══════════════════════════════════════════════════════════════

/** 递验仪表板全局状态（可合并至 zustand AppStore） */
export interface VerificationDashboardState {
  /** 三阶递验进度 */
  progress: VerificationProgress;

  /** 各阶断言矩阵（按需加载） */
  matrices: Partial<Record<VerificationTier, TierAssertionMatrix>>;

  /** 跨阶一致性约束 */
  crossTierConstraints: CrossTierConsistencyConstraint[];

  /** 失败模式与破执记录 */
  failures: FailureRemediation[];

  /** 自进化免疫闭环 */
  immunityLoop: ImmunityLoopState;

  /** 法器就绪状态 */
  toolReadiness: ToolReadinessCheck;

  /** 法器执行日志（最近 50 条） */
  toolExecutionLog: ToolExecutionResultEvent[];

  /** 递验器物时间线 */
  artifacts: VerificationArtifact[];

  /** 递验实时事件流连接状态 */
  sse_connected: boolean;

  /** 递验会话 ID */
  session_id: string;

  /** 递验开始时间 */
  started_at_ms: number | null;

  /** 递验完成时间 */
  completed_at_ms: number | null;

  // ── UI 状态 ──

  /** 当前视图模式 */
  view_mode: VerificationViewMode;

  /** 选中的递验阶 */
  selected_tier: VerificationTier | null;

  /** 选中的断言 ID */
  selected_assertion_id: string | null;

  /** 选中的跨阶约束 ID */
  selected_constraint_id: string | null;

  /** 选中的免疫闭环节点 */
  selected_immunity_node: ImmunityLoopNode | null;

  /** 选中的递验器物 ID */
  selected_artifact_id: string | null;
}

/** 创建默认递验仪表板状态 */
export function createDefaultVerificationDashboardState(sessionId: string): VerificationDashboardState {
  const defaultTierGate = (tier: VerificationTier, prerequisite: VerificationTier | null): TierGateState => ({
    tier,
    status: "pending",
    total_assertions: 0,
    passed_assertions: 0,
    failed_assertions: 0,
    blocking_failures: [],
    started_at_ms: null,
    completed_at_ms: null,
    prerequisite_tier: prerequisite,
    prerequisite_satisfied: prerequisite === null,
  });

  return {
    progress: {
      tiers: {
        tier_1_document:    defaultTierGate("tier_1_document", null),
        tier_2_propagation: defaultTierGate("tier_2_propagation", "tier_1_document"),
        tier_3_causal:      defaultTierGate("tier_3_causal", "tier_2_propagation"),
      },
      active_tier: null,
      all_tiers_passed: false,
      cross_tier_consistency_passed: false,
      last_immunity_loop_ms: null,
    },
    matrices: {},
    crossTierConstraints: [],
    failures: [],
    immunityLoop: {
      nodes: {} as Record<ImmunityLoopNode, ImmunityLoopNodeState>,
      iteration_count: 0,
      last_iteration_ms: null,
      last_cycle_duration_ms: null,
      last_features_updated: 0,
      recent_iterations: [],
    },
    toolReadiness: {
      all_ready: false,
      tools: [],
      unavailable: [],
    },
    toolExecutionLog: [],
    artifacts: [],
    sse_connected: false,
    session_id: sessionId,
    started_at_ms: null,
    completed_at_ms: null,
    view_mode: "overview",
    selected_tier: null,
    selected_assertion_id: null,
    selected_constraint_id: null,
    selected_immunity_node: null,
    selected_artifact_id: null,
  };
}

// ═══════════════════════════════════════════════════════════════
// 十、递验配置 — 前端可调参数
// ═══════════════════════════════════════════════════════════════

/** 递验仪表板配置（与第二局器具参数对应） */
export interface VerificationDashboardConfig {
  // ── 第一阶参数 ──
  /** 哈希链日志路径 */
  hashChainLogPath: string;
  /** 金丝雀检测正则模式 */
  canaryPatterns: string[];
  /** 时间戳蜜罐预期序列 */
  honeypotTimestampSequence: number[];
  /** SEDA time_quantum (ms) */
  sedaTimeQuantumMs: number;
  /** SEDA fingerprint_cache_size capacity */
  sedaCacheCapacity: number;
  /** 是否启用 content_hash_mode */
  sedaContentHashMode: boolean;

  // ── 第二阶参数 ──
  /** 跨通道关联时间窗口 (ms) */
  crossChannelWindowMs: number;
  /** 工具调用超时 (ms) */
  toolCallTimeoutMs: number;
  /** 跨 Agent 工具链关联 JOIN 窗口 (ns) */
  crossAgentToolJoinWindowNs: number;

  // ── 第三阶参数 ──
  /** 差分回放沙箱路径 */
  sandboxPath: string;
  /** 信号异常 z_score 阈值 */
  signalAnomalyZScoreThreshold: number;
  /** ALD 精确重复阈值 */
  aldExactRepeatThreshold: number;
  /** ALD 冷却期 (ms) */
  aldCooldownMs: number;
  /** ALD 乒乓最少交替次数 */
  aldPingPongMinAlternations: number;
  /** 意图漂移余弦相似度阈值 */
  intentDriftCosineThreshold: number;

  // ── 自进化闭环参数 ──
  /** DBSCAN eps */
  dbscanEps: number;
  /** DBSCAN min_samples */
  dbscanMinSamples: number;
  /** 信任衰减系数 */
  trustDecayCoefficient: number;
  /** 代空闲阈值 (s) */
  idleThresholdSeconds: number;
}

/** 默认递验配置 */
export const DEFAULT_VERIFICATION_CONFIG: VerificationDashboardConfig = {
  hashChainLogPath: "hash_chain.log",
  canaryPatterns: [
    "canary://internal/evidence",
    "curl http://canary.internal/",
  ],
  honeypotTimestampSequence: [0x3A1B, 0x7C4D, 0xBE5E],
  sedaTimeQuantumMs: 500,
  sedaCacheCapacity: 10_000,
  sedaContentHashMode: false, // 默认关闭，仅关键路径启用

  crossChannelWindowMs: 500,
  toolCallTimeoutMs: 120_000,
  crossAgentToolJoinWindowNs: 1_000_000_000, // ±1s

  sandboxPath: "/tmp/mtagentlens_sandbox",
  signalAnomalyZScoreThreshold: 3.0,
  aldExactRepeatThreshold: 5,
  aldCooldownMs: 60_000,
  aldPingPongMinAlternations: 3,
  intentDriftCosineThreshold: 0.75,

  dbscanEps: 0.3,
  dbscanMinSamples: 3,
  trustDecayCoefficient: 0.85,
  idleThresholdSeconds: 60,
};

// ═══════════════════════════════════════════════════════════════
// 十一、Type Guards 与工具函数
// ═══════════════════════════════════════════════════════════════

/** 判断递验阶是否已被门禁阻断 */
export function isTierBlocked(tier: VerificationTier, progress: VerificationProgress): boolean {
  const gate = progress.tiers[tier];
  return gate.status === "blocked" || (!gate.prerequisite_satisfied && gate.prerequisite_tier !== null);
}

/** 获取下一待递验阶 */
export function getNextPendingTier(progress: VerificationProgress): VerificationTier | null {
  const order: VerificationTier[] = ["tier_1_document", "tier_2_propagation", "tier_3_causal"];
  for (const tier of order) {
    const gate = progress.tiers[tier];
    if (gate.status === "pending" && gate.prerequisite_satisfied) {
      return tier;
    }
  }
  return null;
}

/** 判断三阶是否全部通过 */
export function allTiersPassed(progress: VerificationProgress): boolean {
  return Object.values(progress.tiers).every(g => g.status === "passed");
}

/** 计算递验整体通过率 */
export function computeOverallPassRate(matrices: Partial<Record<VerificationTier, TierAssertionMatrix>>): number {
  let totalAssertions = 0;
  let passedAssertions = 0;
  for (const matrix of Object.values(matrices)) {
    if (matrix) {
      totalAssertions += matrix.summary.total;
      passedAssertions += matrix.summary.passed;
    }
  }
  return totalAssertions > 0 ? passedAssertions / totalAssertions : 0;
}

/** 按严重度排序失败断言 */
export function sortFailuresBySeverity(
  failures: VerificationAssertion[],
  matrices: Partial<Record<VerificationTier, TierAssertionMatrix>>
): VerificationAssertion[] {
  // 先按阶排序（阶 1 > 阶 2 > 阶 3），同阶内按 assertion_id 排序
  const tierOrder: Record<VerificationTier, number> = {
    tier_1_document: 0,
    tier_2_propagation: 1,
    tier_3_causal: 2,
  };
  return [...failures].sort((a, b) => {
    const tierDiff = tierOrder[a.tier] - tierOrder[b.tier];
    if (tierDiff !== 0) return tierDiff;
    // 阻断性失败优先
    const aBlocking = matrices[a.tier]?.blocking_failures.some(f => f.assertion_id === a.assertion_id) ?? false;
    const bBlocking = matrices[b.tier]?.blocking_failures.some(f => f.assertion_id === b.assertion_id) ?? false;
    if (aBlocking && !bBlocking) return -1;
    if (!aBlocking && bBlocking) return 1;
    return a.assertion_id.localeCompare(b.assertion_id);
  });
}

/** 根据失败断言查找破执记录 */
export function findRemediationForAssertion(
  assertion: VerificationAssertion,
  failures: FailureRemediation[]
): FailureRemediation | null {
  if (!assertion.failure_remediation_ref) return null;
  return failures.find(f => f.mode === assertion.failure_remediation_ref) ?? null;
}

/** 判断递验流事件类型（Type Guard 工厂） */
export function isVerificationEventType<T extends VerificationEvent>(
  event: VerificationStreamEvent,
  type: VerificationEventType
): event is T {
  return event.event_type === type;
}

/** 递验流事件分发器：按 event_type 路由到对应 handler */
export function dispatchVerificationEvent(
  event: VerificationStreamEvent,
  handlers: Partial<Record<VerificationEventType, (e: VerificationStreamEvent) => void>>
): void {
  const handler = handlers[event.event_type];
  if (handler) {
    handler(event);
  }
}

/** 从递验状态生成递验报告摘要（纯文本） */
export function generateVerificationSummary(
  progress: VerificationProgress,
  matrices: Partial<Record<VerificationTier, TierAssertionMatrix>>
): string {
  const lines: string[] = [];
  lines.push("═══════════════════════════════════════");
  lines.push("  三阶递验报告摘要");
  lines.push("═══════════════════════════════════════");
  lines.push(`会话 ID: ${progress.active_tier ?? "N/A"}`);
  lines.push(`整体通过: ${progress.all_tiers_passed ? "✓ 是" : "✗ 否"}`);
  lines.push(`跨阶一致性: ${progress.cross_tier_consistency_passed ? "✓ 通过" : "✗ 未通过"}`);
  lines.push("");

  const tierOrder: VerificationTier[] = ["tier_1_document", "tier_2_propagation", "tier_3_causal"];
  for (const tier of tierOrder) {
    const gate = progress.tiers[tier];
    const { name, seal } = TIER_SEAL_NAMES[tier];
    const statusLabel = TIER_STATUS_STYLE[gate.status].label;
    lines.push(`── ${name} · ${seal} ──`);
    lines.push(`  状态: ${statusLabel}`);
    lines.push(`  断言: ${gate.passed_assertions}/${gate.total_assertions} 通过`);
    if (gate.failed_assertions > 0) {
      lines.push(`  失败: ${gate.failed_assertions} 条`);
    }
    const matrix = matrices[tier];
    if (matrix) {
      lines.push(`  通过率: ${(matrix.summary.pass_rate * 100).toFixed(1)}%`);
    }
    lines.push("");
  }

  lines.push("═══════════════════════════════════════");
  return lines.join("\n");
}

// ═══════════════════════════════════════════════════════════════
// 十二、集成点 — 与现有系统的接头
// ═══════════════════════════════════════════════════════════════

/**
 * 集成清单:
 *
 * 1. SSE 分发器 (event-dispatcher.ts):
 *    - 新增 stream_type: "handoff_verification" 分支
 *    - 路由到 VerificationEvent 解析器
 *    - 调用 dispatchVerificationEvent() 按 event_type 分发
 *
 * 2. zustand AppStore (store.ts):
 *    - 新增 VerificationDashboardState 切片
 *    - Actions:
 *      · startTier(tier)           — 开始递验某阶
 *      · completeTier(tier, matrix) — 阶递验完成
 *      · updateAssertion(assertion) — 单条断言更新
 *      · addFailureRemediation(failure) — 添加破执记录
 *      · updateImmunityLoop(node, state) — 更新闭环节点
 *      · setViewMode(mode)          — 切换视图
 *      · selectAssertion(id)        — 选中断言
 *      · selectConstraint(id)       — 选中跨阶约束
 *      · selectImmunityNode(node)   — 选中闭环节点
 *      · addArtifact(artifact)      — 添加递验器物
 *      · addToolLog(event)          — 添加器具日志
 *
 * 3. VerificationShell (verification-shell.tsx):
 *    - 顶部：三阶递验进度条 (VerificationProgressBar)
 *    - 左侧：阶选择器 (TierGateIndicator ×3) + 视图模式切换
 *    - 中央：按 view_mode 渲染对应面板
 *      · overview       → 三阶概览卡片 + 关键断言摘要 + 破执统计
 *      · tier_detail    → TierAssertionMatrixPanel
 *      · assertion_detail → AssertionDetailPanel
 *      · cross_tier     → CrossTierConsistencyPanel
 *      · immunity_loop  → ImmunityLoopPanel
 *      · tool_instruments → InstrumentToolsPanel
 *      · export_report  → VerificationExportPanel
 *    - 右侧：递验器物时间线 (VerificationArtifactTimeline)
 *    - 底部：实时器具执行日志流
 *
 * 4. REST API (api.ts):
 *    - POST /api/verification/start          → 开始递验，返回 session_id
 *    - GET  /api/verification/:id/progress   → VerificationProgress
 *    - GET  /api/verification/:id/matrix/:tier → TierAssertionMatrix
 *    - GET  /api/verification/:id/assertion/:assertionId → VerificationAssertion (含 detail_payload)
 *    - POST /api/verification/:id/remediate  → 执行破执修复
 *    - GET  /api/verification/:id/cross-tier  → CrossTierConsistencyConstraint[]
 *    - GET  /api/verification/:id/immunity    → ImmunityLoopState
 *    - GET  /api/verification/:id/artifacts   → VerificationArtifact[]
 *    - GET  /api/verification/:id/export      → 导出递验报告 (JSON/Markdown)
 *    - GET  /api/verification/:id/tools        → ToolReadinessCheck
 *    - POST /api/verification/:id/tools/:tool/run → 运行指定法器
 *
 * 5. 与现有 handoff-visualization 的联动:
 *    - 断言 detail_panel 中的 instrument_type 字段 → 点击跳转至对应的 HandoffVisualization 面板
 *    - 递验中检测到的 IOC → 自动推送至 IOCExtractionPanel
 *    - 递验中的 ALD 循环事件 → 联动 AgentLoopDashboard
 *    - 递验中的工具调用异常 → 联动 ToolExecutionDashboard
 *    - 递验中的 SEDA 去重诊断 → 联动 DedupDiagnosticsPanel
 *
 * 6. 递验配置持久化:
 *    - localStorage key: "mtagentlens_verification_config"
 *    - 启动时加载配置覆盖 DEFAULT_VERIFICATION_CONFIG
 *    - 配置变更后自动持久化
 */

// ═══════════════════════════════════════════════════════════════
// 十三、术法符文终章 — 型制约法就绪
//     此文与第二局器具营缮要略（三阶递验章程）完全相合：
//     - 第一阶文契递验 → 断言 1.3.1–1.3.11 + 破执 7 种模式 + 法器 sha256sum/diff/grep/cargo test
//     - 第二阶流布递验 → 断言 2.3.1–2.3.16 + 破执 9 种模式 + 法器 sqlite3/jq/mtagentlens record
//     - 第三阶因果递验 → 断言 3.3.1–3.3.17 + 破执 10 种模式 + 法器 mtagentlens replay/diff/chrome/zustand
//
//     此文与首局递送文书相合：
//     - 首局五十二目，对应 handoff-visualization-contract 仪象类型
//     - 递验仪表板为元层视图，聚合仪象类型的递验结果
//     - 三阶门禁约束确保递验不脱序，前阶不通后阶不行
//     - 自进化闭环将递验结果反哺检测规则，形成逐次递进的免疫体系
//
//     宫室既成，仪象具备。递验之法，昭然若揭。
// ═══════════════════════════════════════════════════════════════
