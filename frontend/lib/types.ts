// Mirrors backend types from collector/src/accountability/types/
// Verified against actual Rust serde serialization format (2026-05-23)

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
  'L1Exact' | 'L2Normalized' | 'L3Containment' | 'L4Basename' | 'L5Substring';

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
  graph_snapshot: GraphSnapshot;
  phase: PhaseIndicator;
  provisional_ref: string | null;
}

export interface EngineSection {
  rule_verdicts: RuleVerdict[];
  pattern_verdicts: PatternVerdict[];
  classification_score: number;
  classification_route: string;
  score_breakdown: ScoreBreakdown;
}

export interface ScoreBreakdown {
  rule_score: number;
  pattern_score: number;
  boundary_score: number;
  evidence_score: number;
  role_score: number;
  repetition_score: number;
  total: number;
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
  | 'PolicyViolation' | 'AccountabilityAnomaly' | 'SuspiciousDelegation'
  | 'TaintedContextUse' | 'UnauthorizedCapabilityUse' | 'BlockedAttempt';

/** 实测 22 条 claim_state 分布：Inferred 18 / Confirmed 3 / Ambiguous 1。未知值由渲染侧灰显兜底。 */
export type ClaimState = 'Inferred' | 'Confirmed' | 'Ambiguous';

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
  chain_node_ids: string[];
  chain_edge_ids: string[];
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
  intents: { count: number; by_tool_name: Record<string, number>; by_authority_source: Record<string, number> };
  actions: { count: number; by_action_type: Record<string, number>; by_signal_class: Record<string, number> };
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
  refinements: { claim: number; classification: number; evidence: number; graph: number; identity: number };
  non_link_diagnostics: { count: number };
}

export interface NonLinkDiagnostic {
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
