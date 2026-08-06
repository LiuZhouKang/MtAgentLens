import type { AlertSeverity, CorrelationEvidence, ResponsibilityAlert } from './types';

export type TraceEventKind = 'agent' | 'llm' | 'tool' | 'file' | 'network' | 'boundary' | 'verdict';

export interface TraceEventModel {
  id: string;
  offsetMs: number;
  durationMs: number;
  kind: TraceEventKind;
  title: string;
  actor: string;
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
  prompt: string | null;       // LLM prompt (system + user messages)
  response: string | null;     // LLM response (assistant + tool calls)
  toolCall: string | null;     // tool name + full arguments
  toolResult: string | null;   // tool execution result
  processTree: string | null;  // process chain (pid/ppid/comm)
  rawEvidence: Record<string, unknown> | null; // all matched_fields
  parentEventId: string | null;
  depth: number;
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
  evidenceCount: number;
  boundaryEvents: number;
  timeline: TraceEventModel[];
  verdict: {
    headline: string;
    body: string;
    evidence: string;
  };
  kindCounts: Record<TraceEventKind, number>;
}

function classifyEventKind(eventType: string): TraceEventKind {
  const normalized = eventType.toLowerCase();
  if (normalized.includes('boundary')) return 'boundary';
  if (normalized.includes('file')) return 'file';
  if (normalized.includes('network')) return 'network';
  if (normalized.includes('tool')) return 'tool';
  if (normalized.includes('llm')) return 'llm';
  if (
    normalized.includes('role') ||
    normalized.includes('agent') ||
    normalized.includes('delegation')
  )
    return 'agent';
  return 'verdict';
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

export function buildTraceRunModel(
  alert: ResponsibilityAlert,
  evidenceItems: CorrelationEvidence[],
): TraceRunModel {
  const evidenceById = new Map(evidenceItems.map((item) => [item.evidence_id, item]));
  const sortedSteps = [...alert.timeline.steps].sort(
    (left, right) => left.timestamp_ns - right.timestamp_ns,
  );
  const startNs = sortedSteps[0]?.timestamp_ns ?? alert.timestamp_ns;

  const timeline = sortedSteps.map((step, index): TraceEventModel => {
    const evidence = step.evidence_ref ? evidenceById.get(step.evidence_ref) : undefined;
    const kind = classifyEventKind(step.event_type);
    const isBoundary = kind === 'boundary';
    const hasRules = step.triggering_rule_ids.length > 0 || !!step.triggering_pattern_id;

    // derive parent/child for flame graph nesting
    const prevStep = index > 0 ? sortedSteps[index - 1] : null;
    const nextStep = index < sortedSteps.length - 1 ? sortedSteps[index + 1] : null;
    const stepDuration = nextStep
      ? Math.max(1, Math.round((nextStep.timestamp_ns - step.timestamp_ns) / 1_000_000))
      : Math.max(10, Math.round((alert.timeline.total_duration_ms || 1000) * 0.1));
    const parentId = prevStep
      ? (prevStep.event_type === 'agent_role_claim' || prevStep.event_type.includes('delegation')) ? prevStep.source_event_id ?? `${alert.alert_id}-step-${index - 1}` : null
      : null;

    // synthesize rich I/O from evidence + alert data — no more black box
    const evidenceMaterialStr = evidenceMaterial(evidence);
    const fullArgs = evidence?.matched_fields.raw_args?.join(' ') || null;
    const command = evidence?.matched_fields.command || null;
    const path = evidence?.matched_fields.canonical_path || null;
    const host = evidence?.matched_fields.host || null;
    const rawEv = evidence?.matched_fields ? { ...evidence.matched_fields } as Record<string, unknown> : null;

    // input: what went INTO this event
    const input = command || fullArgs || step.resource || null;
    // output: what came OUT
    const output = path || host || step.operation || null;

    // LLM-specific: prompt/response from reasoning chain + alert context
    const isLLM = kind === 'llm';
    const isTool = kind === 'tool';
    const prompt = isLLM ? (alert.reasoning || step.description || null) : null;
    const response = isLLM ? (alert.action_hint || step.resource || null) : null;
    const toolCall = isTool ? (step.resource || fullArgs || step.description || null) : null;
    const toolResult = isTool ? (path || host || step.operation || null) : null;
    const processTree = (step.operation && step.operation !== 'unknown') ? `${step.operation} · ${step.agent_id ?? alert.primary_agent.agent_id}${command ? ' · ' + command : ''}` : null;

    return {
      id: step.source_event_id ?? `${alert.alert_id}-step-${index}`,
      offsetMs: Math.max(0, Math.round((step.timestamp_ns - startNs) / 1_000_000)),
      durationMs: stepDuration,
      kind,
      title: step.description,
      actor: step.agent_id ?? alert.primary_agent.agent_id,
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
    };
  });

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
    evidenceCount: evidenceItems.length,
    boundaryEvents,
    timeline,
    verdict: {
      headline: `${alert.decision} / ${alert.authority}`,
      body: alert.action_hint || alert.summary || alert.reasoning,
      evidence: firstMaterial,
    },
    kindCounts,
  };
}
