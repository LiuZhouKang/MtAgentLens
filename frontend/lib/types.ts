// Mirrors serialized backend types from collector/src/responsibility/types/.
// Selected enum and field contracts are checked by scripts/check_ts_contract.py.

// ═══ Phase Indicator (serde external tag) ═══

export type PhaseIndicator =
  | { Provisional: { timestamp_ns: number } }
  | { Authoritative: { timestamp_ns: number; overrides: PhaseIndicator | null } };

// ═══ NormalizedResource (serde external tag, 7 variants) ═══

export type NormalizedResource =
  | { File: { canonical_path: string } }
  | { Network: { host: string; port: number; scheme: string } }
  | { Process: { process_id: { pid: number; start_time_ns: number } } }
  | { LlmConversation: { provider: string; model: string; message_id: string } }
  | { ToolCall: { tool_name: string; call_id: string } }
  | { Credential: { credential_id: string } }
  | { Context: { context_id: string } };

// ═══ Graph Types ═══

export interface ResponsibilityNode {
  node_id: string;
  node_type:
    'Agent' | 'Resource' | 'PolicyBoundary' | 'Process' | 'ToolCall' | 'Credential' | 'Context';
  agent_id: string | null;
  resource: NormalizedResource | null;
  evidence_refs: string[];
  timestamp_ns: number;
  phase: PhaseIndicator;
}

export type ResponsibilityEdgeType =
  | 'IntentCausesAction'
  | 'AgentDelegatesTo'
  | 'ContextFlowsFrom'
  | 'TaintPropagatesTo'
  | 'PolicyBoundaryCrossing'
  | 'CompetingExplanation'
  | 'Delegation'
  | 'AuthorityTransfer'
  | 'CapabilityUse'
  | 'ContextInheritance'
  | 'TaintPropagation'
  | 'ViolationProvenance';

export interface ResponsibilityEdge {
  operation?: string | null;
  edge_id: string;
  edge_type: ResponsibilityEdgeType;
  source: string;
  target: string;
  evidence_refs: string[];
  confidence: number;
  phase: PhaseIndicator;
  signal?: unknown | null;
}

export type MatchLevel =
  'L1Exact' | 'L2Normalized' | 'L3Containment' | 'L4Basename' | 'L5Substring' | 'ProcessLineage';

export type DisplayProfile = 'SafeBaseline' | 'UnsafeByEngine' | 'ObserverJudged';
export type AlertSeverity = 'Info' | 'Warning' | 'Critical';
export type AlertDecision = 'Safe' | 'Unsafe' | 'Uncertain';
export type AlertAuthority = 'Authoritative' | 'Supporting' | 'Unavailable';

// ═══ Alert Types ═══

export interface ResponsibilityAlert {
  alert_id: string;
  timestamp_ns: number;
  display_profile: DisplayProfile;
  decision: AlertDecision;
  /**
   * Product contract: only `decision === 'Unsafe'` is actionable. Optional for
   * older imported rows; prefer this over re-deriving from `decision`.
   */
  actionable?: boolean;
  severity: AlertSeverity;
  confidence: number;
  authority: AlertAuthority;
  reasoning: string;
  title: string;
  summary: string;
  action_hint: string | null;
  primary_agent: { agent_id: string; claimed_role: string | null };
  involved_agents: { agent_id: string; claimed_role: string | null }[];
  engine: EngineSection;
  timeline: BehaviorTimeline;
  /** Present but empty under `/alerts?lean=1`; use the snapshot endpoint. */
  graph_snapshot: GraphSnapshot;
  phase: PhaseIndicator;
  provisional_ref: string | null;
}

export interface EngineSection {
  /** Exact claims used to compose this alert; absent in historical runs. */
  claim_ids?: string[];
  rule_verdicts: RuleVerdict[];
  pattern_verdicts: PatternVerdict[];
  classification_score: number;
  classification_route: string;
  score_breakdown: ScoreBreakdown;
  /** LLM 观察者元数据（observer 判定，真实 db 可能缺失 → 可选） */
  observer_trigger_reason?: string | null;
  cusum_triggered?: boolean | null;
  progressive_fused?: number | null;
  /** 六维 CUSUM 明细（后端 v14+，旧数据可能缺失）。 */
  cusum_dimensions?: unknown;
  /** Scoring formula version; offline analysis must filter on it. */
  score_model_version?: string;
}

export interface ScoreBreakdown {
  rule_score: number;
  pattern_score: number;
  boundary_score: number;
  evidence_score: number;
  role_score: number;
  repetition_score: number;
  /** Observer/行为层附加分（后端可能缺省）。 */
  behavioral_score?: number;
  total: number;
  /** Fused value before the fixed logistic squash (audit field). */
  raw_score?: number;
  /** Squashed value before the evidence gate. Not a fitted probability. */
  calibrated_score?: number;
  /** Evidence-gate factor applied after the squash. */
  gate_factor?: number;
  /** Which gate formula produced gate_factor (v1/v2/off). */
  gate_version?: string;
  platt_a?: number;
  platt_b?: number;
  observer_adjusted?: boolean;
}

export interface RuleVerdict {
  rule_id: string;
  description: string;
  matched_resource: string;
  llm_agreed: boolean | null;
  llm_comment: string | null;
}

export interface PatternVerdict {
  template_id: string;
  match_confidence: number;
  llm_agreed: boolean | null;
  llm_comment: string | null;
}

export interface BehaviorTimeline {
  steps: BehaviorTimelineEntry[];
  total_duration_ms: number;
}

export interface BehaviorTimelineEntry {
  timestamp_ns: number;
  event_type: string;
  description: string;
  resource: string | null;
  evidence_ref: string | null;
  agent_id: string | null;
  operation: string | null;
  evidence_level: string | null;
  triggering_rule_ids: string[];
  triggering_pattern_id: string | null;
  source_event_id: string | null;
}

export interface GraphSnapshot {
  snapshot_id: string;
  timestamp_ns: number;
  nodes: ResponsibilityNode[];
  edges: ResponsibilityEdge[];
  metrics: GraphMetrics;
}

export interface GraphMetrics {
  node_counts: Record<string, number>;
  edge_counts: Record<string, number>;
  agent_count: number;
  cross_agent_edges: number;
  delegation_depth: number;
  taint_chains: number;
  avg_confidence: number;
}

// ═══ Evidence Types ═══

export interface CorrelationEvidence {
  evidence_id: string;
  intent_id: string;
  action_id: string;
  match_level: MatchLevel;
  confidence: number;
  matched_fields: NormalizedArgs;
  derivation_mode: string;
  phase: PhaseIndicator;
  timestamp_ns: number;
}

export interface NormalizedArgs {
  canonical_path: string | null;
  host: string | null;
  port: number | null;
  command: string | null;
  raw_args: string[];
}

export interface Intent {
  intent_id: string;
  process_id: { pid: number; start_time_ns: number };
  message_id: string;
  tool_name: string;
  tool_args: unknown[];
  intent_class: string;
  parse_state: string;
  primary_matching_eligible: boolean;
  authority_source: string;
  timestamp_ns: number;
}

export interface Action {
  outcome?: 'unknown' | 'attempted' | 'pending' | 'succeeded' | 'failed';
  syscall_ret?: number | null;
  syscall_name?: string | null;
  action_id: string;
  process_id: { pid: number; start_time_ns: number };
  action_type: string;
  normalized_args: unknown[];
  derivation_mode: string;
  signal_class: string;
  timestamp_ns: number;
}

// ═══ Claim & Overview Types ═══

export type ClaimType =
  | 'PolicyViolation'
  | 'ResponsibilityAnomaly'
  | 'SuspiciousDelegation'
  | 'TaintedContextUse'
  | 'UnauthorizedCapabilityUse'
  | 'BlockedAttempt';

/** 后端 6 个 claim_state 变体；未知值由渲染侧灰显兜底。 */
export type ClaimState =
  'Observed' | 'Inferred' | 'Ambiguous' | 'Unproven' | 'Confirmed' | 'BlockedAttempt';

export interface ResponsibilityClaim {
  claim_id: string;
  claim_type: ClaimType;
  subject_agent_id: string | null;
  policy_id: string | null;
  boundary_id: string | null;
  claim_state: ClaimState;
  severity: AlertSeverity;
  confidence: number;
  evidence_refs: string[];
  competing_explanations: Array<{
    label: string;
    evidence_refs: string[];
    rule_id: string;
    rationale: string;
    aligned_pattern: string | null;
  }>;
  traceback_ref: string | null;
  phase: PhaseIndicator;
  /** 由 phase 提取（phaseTimestampNs），用于声明列表排序。 */
  timestamp_ns: number;
}

export interface ResponsibilityTraceback {
  traceback_id: string;
  claim_id: string;
  /** Legacy field name: evidence subgraph membership, not an ordered path. */
  chain_node_ids: string[];
  /** Resolve actual edge endpoints; never zip with chain_node_ids. */
  chain_edge_ids: string[];
  /** Inferred origin candidate, not independently verified ground truth. */
  root_cause_node_id: string;
  explanation_summary: string;
  confidence: number;
}

/** 各表概要——"Claude Code 全景"统计卡片数据。所有计数来自 COUNT(*)/GROUP BY，无造数。 */
export interface DatasetOverview {
  run: {
    run_id: string | null;
    started_at_ns: number | null;
    ended_at_ns: number | null;
    scenario_id: string | null;
    mode: string | null;
    collector_version: string | null;
  } | null;
  snapshots: { count: number };
  graph: { nodes: number; edges: number };
  alerts: { count: number };
  evidence: { count: number; by_match_level: Record<string, number> };
  claims: { count: number; by_type: Record<string, number>; by_severity: Record<string, number> };
  tracebacks: { count: number };
  intents: {
    count: number;
    by_tool_name: Record<string, number>;
    by_authority_source: Record<string, number>;
  };
  actions: {
    count: number;
    by_action_type: Record<string, number>;
    by_signal_class: Record<string, number>;
  };
  pollution: { count: number };
  /** count=评估总数，violations=judgment_json.result === 'Violation' 的真实违规数 */
  policy_evaluations: { count: number; violations: number };
  processes: { count: number };
  tls_sessions: { count: number };
  identity_process_map: { count: number };
  provisional_identities: { count: number };
  agent_identities: { count: number };
  message_bindings: { count: number };
  delegation_records: { count: number };
  subagent_boundaries: { count: number };
  refinements: {
    claim: number;
    classification: number;
    evidence: number;
    graph: number;
    identity: number;
  };
  non_link_diagnostics: { count: number };
}

/** API `/api/runs/:id/evidence` non_links 响应元素（causal db 形状，与 db 全行形状不同）。 */
export interface ApiNonLinkDiagnostic {
  schema_version: number;
  diagnostic_id: string;
  timestamp_ns: number;
  agent_id: string | null;
  intent_id: string | null;
  candidate_id: string | null;
  reason: string;
  stage: string;
  intent_class: string | null;
  parse_state: string | null;
  match_level_considered: string | null;
  confidence_observed: number | null;
  details: unknown | null;
}

// ═══ Diff & Data ═══

export interface GraphDiff {
  added_nodes: string[];
  removed_nodes: string[];
  added_edges: string[];
  removed_edges: string[];
  node_count_change: number;
  edge_count_change: number;
}

export interface GraphData {
  nodes: ResponsibilityNode[];
  edges: ResponsibilityEdge[];
}

export type AppState = 'init' | 'idle' | 'live' | 'replay' | 'error';
export type ZoomLevel = 0 | 1 | 2 | 3;

// ═══ DB 全行深链类型（responsibility.db 12 表全行，M0 提取器产物） ═══

/** context_pollution_events 全行 */
export interface ContextPollutionEvent {
  pollution_id: number;
  source_agent_id: string;
  contaminated_artifact: string;
  propagation_path: string[]; // propagation_path_json，agent 序列
  detected_at_ns: number;
  run_id: string | null;
}

/** policy_evaluations 全行 — judgment_json 解析为 { result, ... } */
export interface PolicyEvaluation {
  policy_id: string;
  boundary: string;
  subject_agent_id: string;
  judgment: { result?: string; [k: string]: unknown } | null;
  resource: NormalizedArgs | null; // resource_json；非 NormalizedArgs 形态时 null
  evidence_refs: string[];
  confidence: number;
  reason: string;
  run_id: string | null;
}

/** non_link_diagnostics 全行 */
export interface NonLinkDiagnostic {
  diagnostic_id: string;
  intent_id: string | null;
  action_id: string | null;
  reason: string;
  timestamp_ns: number;
  run_id: string | null;
}

/** agent_identities 全行 */
export interface AgentIdentity {
  agent_id: string;
  process_ids: Array<{ pid: number; start_time_ns: number }>;
  message_ids: string[];
  tls_sessions: string[];
  confidence: string; // 'Authoritative' 等
  provisional_refs: string[];
  run_id: string | null;
}

/** provisional_identities 全行 */
export interface ProvisionalIdentity {
  agent_id: string;
  process_ids: Array<{ pid: number; start_time_ns: number }>;
  primary_signal: string; // 'TlsSessionNew' 等
  confidence: number;
  run_id: string | null;
}

/** identity_process_map 全行 */
export interface IdentityProcessMapEntry {
  agent_id: string;
  pid: number;
  start_time_ns: number;
}

/** process_events 全行 + 身份标注（JOIN identity_process_map） */
export interface ProcessEvent {
  process_event_id: number;
  pid: number;
  start_time_ns: number;
  parent_pid: number | null;
  parent_start_time_ns: number | null;
  comm: string;
  timestamp_ns: number;
  agent_id: string | null; // JOIN 标注；无映射时 null
}

/** tls_sessions 全行 + 身份标注 */
export interface TlsSession {
  tls_session_id: string;
  pid: number;
  start_time_ns: number;
  timestamp_ns: number;
  agent_id: string | null; // JOIN 标注
}

/** subagent_boundaries 全行 */
export interface SubagentBoundary {
  boundary_id: string;
  parent_agent_id: string;
  child_agent_id: string;
  parent_pid: number;
  parent_start_time_ns: number;
  child_pid: number;
  child_start_time_ns: number;
  reason: string;
  run_id: string | null;
}

/** graph_snapshots 演化帧（kept 帧提取时即解析 nodes/edges） */
export interface GraphSnapshotFrame {
  snapshot_id: string;
  timestamp_ns: number;
  graph: GraphData; // 提取时即解析（loadDatabase 关闭 db 后无法再懒加载）
  metrics: GraphMetrics | null;
}
