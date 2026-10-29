import type {
  Action,
  AlertSeverity,
  ContextPollutionEvent,
  CorrelationEvidence,
  Intent,
  PolicyEvaluation,
  ProcessEvent,
  ResponsibilityAlert,
} from './types';
import { actionTypeLabel, toolLabel } from './monitoring/perspective-labels';
import type { InvAction } from './investigation-timeline';

export type TraceEventKind = 'agent' | 'llm' | 'tool' | 'file' | 'network' | 'boundary' | 'verdict';

export interface TraceEventModel {
  id: string;
  offsetMs: number;
  durationMs: number;
  kind: TraceEventKind;
  title: string;
  actor: string;
  actorRole: string | null; // claimed_role of the acting agent, if known
  material: string;
  evidenceRef: string | null;
  evidenceLevel: string | null;
  operation: string | null;
  ruleIds: string[];
  patternId: string | null;
  confidence: number;
  severity: AlertSeverity | 'Normal';
  // rich span I/O — "no more black box"
  input: string | null;
  output: string | null;
  // full eBPF context
  prompt: string | null; // LLM prompt (system + user messages)
  response: string | null; // LLM response (assistant + tool calls)
  toolCall: string | null; // tool name + full arguments
  toolResult: string | null; // tool execution result
  processTree: string | null; // process chain (pid/ppid/comm)
  rawEvidence: Record<string, unknown> | null; // all matched_fields
  parentEventId: string | null;
  depth: number;
  // 真实进程关联（process_events / actions 表，pid 精确关联，不编造）
  pid: number | null;
  processComm: string | null;
  processAgent: string | null;
  /** 该进程的真实行为（文件/命令），时间升序 */
  actions: InvAction[];
}

export interface TraceRunModel {
  id: string;
  title: string;
  subtitle: string;
  severity: AlertSeverity;
  decision: string;
  durationMs: number;
  confidence: number;
  primaryAgent: string;
  actorRoles: Record<string, string>; // agent_id → claimed_role
  evidenceCount: number;
  boundaryEvents: number;
  timeline: TraceEventModel[];
  /** 时间线起点墙钟时间（毫秒），供 span 时钟 HH:MM:SS */
  startWallMs: number;
  verdict: {
    headline: string;
    body: string;
    evidence: string;
  };
  kindCounts: Record<TraceEventKind, number>;
}

/**
 * Canonical mapping for real backend event types (UPPER_SNAKE, emitted by
 * eBPF runners/analyzers) to responsibility-chain kinds. The older
 * substring fallback below only matched demo snake_case types (file_read,
 * boundary_cross), which silently collapsed almost every real event into
 * `verdict` — this table is the primary path.
 */
const REAL_EVENT_TYPE_KIND: Record<string, TraceEventKind> = {
  // agent / process lifecycle
  EXEC: 'agent',
  PROCESS_EXEC: 'agent',
  EXIT: 'agent',
  HANDOFF_SPAWN: 'agent',
  BASH_READLINE: 'agent',
  // resource access
  FILE_OPEN: 'file',
  FILE_WRITE: 'file',
  FILE_DELETE: 'file',
  FILE_RENAME: 'file',
  FILE_TRUNCATE: 'file',
  NETWORK_CONNECT: 'network',
  NETWORK_SEND: 'network',
  NETWORK_RECV: 'network',
  // SSL connection signals — network activity, not a verdict
  SIGNAL: 'network',
  // security / anomaly → policy boundary
  INJECTION_DETECTED: 'boundary',
  IDENTITY_CONFUSION: 'boundary',
  CONTEXT_POLLUTION_DETECTED: 'boundary',
  CREDENTIAL_LEAK_DETECTED: 'boundary',
  ANOMALY: 'boundary',
  ALERT: 'boundary',
  SUBPROCESS_ALERT: 'boundary',
  SIGNOFF_TRANSITION: 'boundary',
  // PascalCase contract types (data_contract.rs) — uppercased before lookup
  CREDENTIALACCESS: 'boundary',
  CONTEXTINJECTION: 'boundary',
  // system / control → verdict
  SUMMARY: 'verdict',
  CONTROL: 'verdict',
  CLOCK_SYNC: 'verdict',
};

export function classifyEventKind(eventType: string): TraceEventKind {
  const exact = REAL_EVENT_TYPE_KIND[eventType.toUpperCase()];
  if (exact) return exact;
  const normalized = eventType.toLowerCase();
  if (normalized.includes('boundary')) return 'boundary';
  if (normalized.includes('file')) return 'file';
  if (normalized.includes('network')) return 'network';
  if (normalized.includes('tool')) return 'tool';
  if (normalized.includes('llm')) return 'llm';
  if (
    normalized.includes('role') ||
    normalized.includes('agent') ||
    normalized.includes('delegation') ||
    normalized.includes('exec') ||
    normalized.includes('spawn')
  )
    return 'agent';
  return 'verdict';
}

interface CollapsibleStep {
  timestamp_ns: number;
  event_type: string;
  description: string;
}

/**
 * Collapse repetitive `signal` (SSL connection) timeline steps into a single
 * aggregate event. Real runs emit hundreds of identical "signal: ssl (pid=N)"
 * lines; surfacing every one floods the network rows with noise and drowns the
 * responsibility chain. The aggregate keeps the first signal's timestamp so it
 * stays in chronological position.
 */
function collapseSignalSteps<T extends CollapsibleStep>(steps: T[]): T[] {
  const signals = steps.filter((s) => s.event_type === 'signal');
  if (signals.length === 0) return steps;
  const kept = steps.filter((s) => s.event_type !== 'signal');
  const first = signals[0];
  const aggregate = {
    ...first,
    description: `SSL/TLS 连接 × ${signals.length}`,
  } as T;
  const insertAt = kept.findIndex((s) => s.timestamp_ns >= first.timestamp_ns);
  if (insertAt === -1) return [...kept, aggregate];
  kept.splice(insertAt, 0, aggregate);
  return kept;
}

function evidenceMaterial(evidence: CorrelationEvidence | undefined) {
  if (!evidence) return 'No linked material';
  if (evidence.matched_fields.canonical_path) return evidence.matched_fields.canonical_path;
  if (evidence.matched_fields.host) {
    return evidence.matched_fields.port
      ? `${evidence.matched_fields.host}:${evidence.matched_fields.port}`
      : evidence.matched_fields.host;
  }
  if (evidence.matched_fields.command) return evidence.matched_fields.command;
  return evidence.matched_fields.raw_args.join(' ') || evidence.evidence_id;
}

/**
 * Surface the LLM observer's judgments (engine.rule_verdicts/pattern_verdicts
 * where llm_agreed is set) as `llm`-kind timeline events. The observer is a
 * phase-2 semantic judge: it confirms or refutes each rule/pattern verdict, so
 * these rows sit at the end of the trace. Verdicts where the observer failed
 * (llm_agreed === null) carry no judgment and stay invisible.
 */
function buildObserverEvents(alert: ResponsibilityAlert): TraceEventModel[] {
  const events: TraceEventModel[] = [];
  const judge = alert.primary_agent.agent_id;
  const baseOffset = alert.timeline.total_duration_ms;

  const push = (
    id: string,
    label: string,
    description: string,
    material: string | null,
    comment: string | null,
    agreed: boolean,
    confidence: number,
  ) => {
    events.push({
      id: `observer-${id}`,
      offsetMs: baseOffset + events.length * 10,
      durationMs: 10,
      kind: 'llm',
      title: `LLM 观察者 · ${agreed ? '同意' : '驳回'} ${label}`,
      actor: judge,
      actorRole: null,
      material: material ?? description,
      evidenceRef: null,
      evidenceLevel: 'observer',
      operation: 'observer',
      ruleIds: [],
      patternId: null,
      confidence,
      severity: agreed ? 'Info' : 'Warning',
      input: description,
      output: comment ?? (agreed ? '同意引擎判定' : '不同意引擎判定'),
      prompt: `${label}：${description}`,
      response: comment,
      toolCall: null,
      toolResult: null,
      processTree: null,
      rawEvidence: null,
      parentEventId: null,
      depth: 0,
      pid: null,
      processComm: null,
      processAgent: null,
      actions: [],
    });
  };

  for (const rule of alert.engine.rule_verdicts) {
    if (rule.llm_agreed === null) continue;
    push(
      rule.rule_id,
      rule.rule_id,
      rule.description,
      rule.matched_resource,
      rule.llm_comment,
      rule.llm_agreed,
      alert.confidence,
    );
  }
  for (const pattern of alert.engine.pattern_verdicts) {
    if (pattern.llm_agreed === null) continue;
    push(
      pattern.template_id,
      pattern.template_id,
      `模式匹配 ${pattern.template_id}`,
      pattern.template_id,
      pattern.llm_comment,
      pattern.llm_agreed,
      pattern.match_confidence,
    );
  }
  return events;
}

/**
 * 融合全数据源事件（不编造，全部来自真实表）：
 *  - intents → tool（真实工具调用 + 参数）
 *  - actions → file / tool（真实文件读/写/覆写路径）
 *  - policy_evaluations → verdict（真实边界判定结果，按 boundary 聚合）
 *  - context_pollution_events → boundary（真实污染传播，按 source→artifact 聚合）
 * 这些事件与 timeline 进程事件按 offsetMs 合并，构成完整责任图谱。
 */
function buildEnrichedEvents(input: {
  actions?: Action[];
  intents?: Intent[];
  policyEvaluations?: PolicyEvaluation[];
  pollutionEvents?: ContextPollutionEvent[];
  startNs: number;
  /** 策略判定无时间戳，用告警形成时间作锚点 */
  policyAnchorNs: number;
  pidAgent: Map<number, string>;
  actorFallback: string;
}): TraceEventModel[] {
  const {
    actions,
    intents,
    policyEvaluations,
    pollutionEvents,
    startNs,
    policyAnchorNs,
    pidAgent,
    actorFallback,
  } = input;
  const T = 1_000_000;
  const events: TraceEventModel[] = [];
  const agentFor = (pid: number | null | undefined): string =>
    pid != null && pidAgent.has(pid) ? pidAgent.get(pid)! : actorFallback;
  const off = (ns: number): number => Math.max(0, Math.round((ns - startNs) / T));

  const mk = (partial: Partial<TraceEventModel>): TraceEventModel => ({
    id: '',
    offsetMs: 0,
    durationMs: 1,
    kind: 'file',
    title: '',
    actor: actorFallback,
    actorRole: null,
    material: '',
    evidenceRef: null,
    evidenceLevel: null,
    operation: null,
    ruleIds: [],
    patternId: null,
    confidence: 0,
    severity: 'Normal',
    input: null,
    output: null,
    prompt: null,
    response: null,
    toolCall: null,
    toolResult: null,
    processTree: null,
    rawEvidence: null,
    parentEventId: null,
    depth: 0,
    pid: null,
    processComm: null,
    processAgent: null,
    actions: [],
    ...partial,
  });

  // 意图 → 工具调用
  for (const intent of intents ?? []) {
    events.push(
      mk({
        id: `intent-${intent.intent_id}`,
        offsetMs: off(intent.timestamp_ns),
        kind: 'tool',
        title: `工具调用 · ${toolLabel(intent.tool_name).label}`,
        actor: agentFor(intent.process_id?.pid),
        operation: intent.tool_name,
        input: intent.tool_args?.length ? JSON.stringify(intent.tool_args).slice(0, 500) : null,
        output: intent.authority_source || null,
      }),
    );
  }

  // 行为 → 文件读/写/覆写（真实路径）
  for (const action of actions ?? []) {
    const target = extractActionTarget(action.normalized_args ?? []);
    const isFile = action.action_type.startsWith('File');
    events.push(
      mk({
        id: `action-${action.action_id}`,
        offsetMs: off(action.timestamp_ns),
        kind: isFile ? 'file' : 'tool',
        title: `${actionTypeLabel(action.action_type).label}${target ? ` ${target}` : ''}`,
        actor: agentFor(action.process_id?.pid),
        operation: action.action_type,
        input: target,
        output: action.signal_class === 'HighSignal' ? '高信号' : '低信号',
      }),
    );
  }

  // 策略判定 → 判定（按 boundary 聚合，避免 755 条同质事件；判定时间 = 告警形成锚点）
  // confidence 用该 boundary 的真实平均置信度（policy 表有 confidence 字段，不编造）
  const policyByBoundary = new Map<
    string,
    { boundary: string; count: number; agent: string; confSum: number }
  >();
  for (const p of policyEvaluations ?? []) {
    const key = p.boundary || '未知';
    const cur =
      policyByBoundary.get(key) ??
      { boundary: key, count: 0, agent: p.subject_agent_id, confSum: 0 };
    cur.count += 1;
    cur.confSum += p.confidence || 0;
    policyByBoundary.set(key, cur);
  }
  for (const row of policyByBoundary.values()) {
    events.push(
      mk({
        id: `policy-${row.boundary}`,
        offsetMs: off(policyAnchorNs),
        kind: 'verdict',
        title: `策略判定 · ${row.boundary} · 违规 ×${row.count}`,
        actor: row.agent || actorFallback,
        confidence: row.confSum / Math.max(1, row.count),
        operation: row.boundary,
        input: `违规评估 ${row.count} 次`,
        output: 'Violation',
      }),
    );
  }

  // 污染传播 → 边界（按 source→artifact 聚合）
  const pollutionKey = new Map<string, { source: string; artifact: string; count: number; tsNs: number }>();
  for (const p of pollutionEvents ?? []) {
    const key = `${p.source_agent_id}→${p.contaminated_artifact}`;
    const cur = pollutionKey.get(key) ?? { source: p.source_agent_id, artifact: p.contaminated_artifact, count: 0, tsNs: p.detected_at_ns };
    cur.count += 1;
    if (!cur.tsNs || p.detected_at_ns < cur.tsNs) cur.tsNs = p.detected_at_ns;
    pollutionKey.set(key, cur);
  }
  for (const row of pollutionKey.values()) {
    events.push(
      mk({
        id: `pollution-${row.source}-${row.artifact}`,
        offsetMs: off(row.tsNs),
        kind: 'boundary',
        title: `污染传播 · ${row.artifact.slice(0, 48)}`,
        actor: row.source || actorFallback,
        operation: 'Taint',
        input: `污染源 ${row.source}`,
        output: `受影响资源 ${row.artifact}`,
      }),
    );
  }

  return events;
}

const OBSERVER_ROUTE_LABEL: Record<string, string> = {
  observer_uncertain: '待定',
  observer_safe: '安全',
  observer_unsafe: '风险',
  observer_judged: '观察器判定',
};

/**
 * 基于 alert.engine 的 LLM observer 元数据创建 `llm` 事件（真实数据，不编造）：
 * classification_route / observer_trigger_reason / reasoning。
 * 无 observer 数据（route 非 observer 且无触发原因）→ null（诚实空态）。
 */
function buildLlmEvent(alert: ResponsibilityAlert): TraceEventModel | null {
  const engine = alert.engine;
  const route = engine?.classification_route ?? '';
  // 完整 observer 路由（observer_uncertain/safe/unsafe...）；demo 简化值 'observer' 不算
  const isObserver =
    (route.startsWith('observer_') && route.length > 'observer_'.length) ||
    engine?.observer_trigger_reason != null;
  if (!isObserver) return null;
  const reason = alert.reasoning ?? '';
  const trigger = engine?.observer_trigger_reason ?? null;
  const label = OBSERVER_ROUTE_LABEL[route] ?? route;
  const uncertain = route.includes('uncertain') || route.includes('unsafe');
  return {
    id: `llm-${alert.alert_id}`,
    offsetMs: alert.timeline.total_duration_ms,
    durationMs: 10,
    kind: 'llm',
    title: `LLM 观察者 · ${label}`,
    actor: alert.primary_agent.agent_id,
    actorRole: null,
    material: reason,
    evidenceRef: null,
    evidenceLevel: 'observer',
    operation: route,
    ruleIds: [],
    patternId: null,
    confidence: alert.confidence,
    severity: uncertain ? 'Warning' : 'Info',
    input: trigger ?? null,
    output: label,
    prompt: reason || null,
    response: trigger ?? null,
    toolCall: null,
    toolResult: null,
    processTree: null,
    rawEvidence: {
      classification_route: route,
      observer_trigger_reason: trigger,
      cusum_triggered: engine?.cusum_triggered ?? null,
      progressive_fused: engine?.progressive_fused ?? null,
    },
    parentEventId: null,
    depth: 0,
    pid: null,
    processComm: null,
    processAgent: null,
    actions: [],
  };
}

/** 从 actions.normalized_args 提取真实 Path / command（与 identity-lineage 同模式）。 */
function extractActionTarget(args: unknown[]): string | null {
  for (const arg of args ?? []) {
    if (arg && typeof arg === 'object') {
      const obj = arg as Record<string, unknown>;
      if (typeof obj.Path === 'string' && obj.Path) return obj.Path;
      if (typeof obj.command === 'string' && obj.command) return obj.command;
      if (typeof obj.path === 'string' && obj.path) return obj.path;
    }
  }
  return null;
}

export function buildTraceRunModel(
  alert: ResponsibilityAlert,
  evidenceItems: CorrelationEvidence[],
  processEvents?: ProcessEvent[],
  actions?: Action[],
  intents?: Intent[],
  policyEvaluations?: PolicyEvaluation[],
  pollutionEvents?: ContextPollutionEvent[],
): TraceRunModel {
  const evidenceById = new Map(evidenceItems.map((item) => [item.evidence_id, item]));
  const actorRoles: Record<string, string> = {};
  for (const entry of [alert.primary_agent, ...alert.involved_agents]) {
    if (entry.claimed_role) actorRoles[entry.agent_id] = entry.claimed_role;
  }
  // 真实进程索引（pid → comm / agent / 行为，来自 process_events / actions 表）
  const pidComm = new Map<number, string>();
  const pidAgent = new Map<number, string>();
  for (const pe of processEvents ?? []) {
    if (pe.pid == null) continue;
    if (pe.comm && !pidComm.has(pe.pid)) pidComm.set(pe.pid, pe.comm);
    if (pe.agent_id && !pidAgent.has(pe.pid)) pidAgent.set(pe.pid, pe.agent_id);
  }
  const pidActions = new Map<number, InvAction[]>();
  for (const a of actions ?? []) {
    const pid = a.process_id?.pid;
    if (pid == null || pid <= 0) continue;
    const list = pidActions.get(pid) ?? [];
    list.push({
      actionType: a.action_type,
      actionLabel: actionTypeLabel(a.action_type).label,
      target: extractActionTarget(a.normalized_args ?? []),
      signalClass: a.signal_class,
      tsNs: a.timestamp_ns,
    });
    pidActions.set(pid, list);
  }
  for (const list of pidActions.values()) list.sort((x, y) => x.tsNs - y.tsNs);

  const sortedSteps = [...alert.timeline.steps].sort(
    (left, right) => left.timestamp_ns - right.timestamp_ns,
  );
  // fold hundreds of identical SSL signals into one aggregate network event
  const collapsedSteps = collapseSignalSteps(sortedSteps);
  const startNs = collapsedSteps[0]?.timestamp_ns ?? alert.timestamp_ns;

  const stepEvents = collapsedSteps.map((step, index): TraceEventModel => {
    const evidence = step.evidence_ref ? evidenceById.get(step.evidence_ref) : undefined;
    const kind = classifyEventKind(step.event_type);
    const isBoundary = kind === 'boundary';
    const hasRules = step.triggering_rule_ids.length > 0 || !!step.triggering_pattern_id;

    // derive parent/child for flame graph nesting
    const prevStep = index > 0 ? collapsedSteps[index - 1] : null;
    const nextStep = index < collapsedSteps.length - 1 ? collapsedSteps[index + 1] : null;
    const stepDuration = nextStep
      ? Math.max(1, Math.round((nextStep.timestamp_ns - step.timestamp_ns) / 1_000_000))
      : Math.max(10, Math.round((alert.timeline.total_duration_ms || 1000) * 0.1));
    const parentId = prevStep
      ? prevStep.event_type === 'agent_role_claim' || prevStep.event_type.includes('delegation')
        ? (prevStep.source_event_id ?? `${alert.alert_id}-step-${index - 1}`)
        : null
      : null;

    // synthesize rich I/O from evidence + alert data — no more black box
    const evidenceMaterialStr = evidenceMaterial(evidence);
    const fullArgs = evidence?.matched_fields.raw_args?.join(' ') || null;
    const command = evidence?.matched_fields.command || null;
    const path = evidence?.matched_fields.canonical_path || null;
    const host = evidence?.matched_fields.host || null;
    const rawEv = evidence?.matched_fields
      ? ({ ...evidence.matched_fields } as Record<string, unknown>)
      : null;

    // input: what went INTO this event
    const input = command || fullArgs || step.resource || null;
    // output: what came OUT
    const output = path || host || step.operation || null;

    // LLM-specific: prompt/response from reasoning chain + alert context
    const isLLM = kind === 'llm';
    const isTool = kind === 'tool';
    const prompt = isLLM ? alert.reasoning || step.description || null : null;
    const response = isLLM ? alert.action_hint || step.resource || null : null;
    const toolCall = isTool ? step.resource || fullArgs || step.description || null : null;
    const toolResult = isTool ? path || host || step.operation || null : null;
    const processTree =
      step.operation && step.operation !== 'unknown'
        ? `${step.operation} · ${step.agent_id ?? alert.primary_agent.agent_id}${command ? ' · ' + command : ''}`
        : null;

    // 真实进程关联：从 description 解析 pid → process_events/actions 表（不编造）
    const pidMatch = (step.description ?? '').match(/pid=(\d+)/);
    const pid = pidMatch ? Number(pidMatch[1]) : null;
    const processComm = pid != null && pidComm.has(pid) ? pidComm.get(pid)! : null;
    const processAgent = pid != null && pidAgent.has(pid) ? pidAgent.get(pid)! : null;
    const realActions = pid != null ? (pidActions.get(pid) ?? []) : [];

    return {
      id: step.source_event_id ?? `${alert.alert_id}-step-${index}`,
      offsetMs: Math.max(0, Math.round((step.timestamp_ns - startNs) / 1_000_000)),
      durationMs: stepDuration,
      kind,
      title: step.description,
      actor: step.agent_id ?? alert.primary_agent.agent_id,
      actorRole: step.agent_id ? (actorRoles[step.agent_id] ?? null) : null,
      material: evidenceMaterialStr,
      evidenceRef: step.evidence_ref,
      evidenceLevel: step.evidence_level,
      operation: step.operation,
      ruleIds: step.triggering_rule_ids,
      patternId: step.triggering_pattern_id,
      confidence: evidence?.confidence ?? alert.confidence,
      severity: isBoundary ? alert.severity : hasRules ? 'Warning' : 'Normal',
      input,
      output,
      prompt,
      response,
      toolCall,
      toolResult,
      processTree,
      rawEvidence: rawEv,
      parentEventId: parentId,
      depth: parentId ? 1 : 0,
      pid,
      processComm,
      processAgent,
      actions: realActions,
    };
  });

  // 融合全数据源事件（文件/工具/判定/边界）+ LLM 观察者，构成完整责任图谱
  const enriched = buildEnrichedEvents({
    actions,
    intents,
    policyEvaluations,
    pollutionEvents,
    startNs,
    policyAnchorNs: alert.timestamp_ns,
    pidAgent,
    actorFallback: alert.primary_agent.agent_id,
  });
  const llmEvent = buildLlmEvent(alert);
  const timeline = [
    ...stepEvents,
    ...buildObserverEvents(alert),
    ...(llmEvent ? [llmEvent] : []),
    ...enriched,
  ].sort((a, b) => a.offsetMs - b.offsetMs || a.id.localeCompare(b.id));

  const kindCounts = timeline.reduce<Record<TraceEventKind, number>>(
    (counts, event) => {
      counts[event.kind] += 1;
      return counts;
    },
    {
      agent: 0,
      llm: 0,
      tool: 0,
      file: 0,
      network: 0,
      boundary: 0,
      verdict: 0,
    },
  );

  const priorityEvidenceRef =
    timeline.find((event) => event.kind === 'file' && event.evidenceRef)?.evidenceRef ??
    timeline.find((event) => event.kind === 'boundary' && event.evidenceRef)?.evidenceRef ??
    timeline.find((event) => event.evidenceRef)?.evidenceRef;
  const firstMaterial = evidenceMaterial(
    evidenceItems.find((item) => item.evidence_id === priorityEvidenceRef),
  );
  const boundaryEvents = timeline.filter(
    (event) => event.kind === 'boundary' || event.severity === 'Critical',
  ).length;

  return {
    id: alert.alert_id,
    title: alert.title,
    subtitle: alert.summary,
    severity: alert.severity,
    decision: alert.decision,
    durationMs: alert.timeline.total_duration_ms,
    confidence: alert.confidence,
    primaryAgent: alert.primary_agent.agent_id,
    actorRoles,
    evidenceCount: evidenceItems.length,
    boundaryEvents,
    timeline,
    startWallMs: Math.floor(startNs / 1_000_000),
    verdict: {
      headline: `${alert.decision} / ${alert.authority}`,
      body: alert.action_hint || alert.summary || alert.reasoning,
      evidence: firstMaterial,
    },
    kindCounts,
  };
}

export type ActorSummary = {
  spans: number;
  evidenceCount: number;
  highestSeverity: AlertSeverity | 'Normal';
};

const ACTOR_SEVERITY_WEIGHT: Record<AlertSeverity | 'Normal', number> = {
  Normal: 0,
  Info: 1,
  Warning: 2,
  Critical: 3,
};

/** Aggregate a list of events for one actor into a compact identity summary. */
export function summarizeActorEvents(events: TraceEventModel[]): ActorSummary {
  let evidenceCount = 0;
  let highestSeverity: AlertSeverity | 'Normal' = 'Normal';
  for (const event of events) {
    if (event.evidenceRef) evidenceCount += 1;
    if (ACTOR_SEVERITY_WEIGHT[event.severity] > ACTOR_SEVERITY_WEIGHT[highestSeverity]) {
      highestSeverity = event.severity;
    }
  }
  return { spans: events.length, evidenceCount, highestSeverity };
}
