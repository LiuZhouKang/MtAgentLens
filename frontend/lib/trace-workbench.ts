import type { AlertSeverity, CorrelationEvidence, ResponsibilityAlert } from './types';

export type TraceEventKind = 'agent' | 'llm' | 'tool' | 'file' | 'network' | 'boundary' | 'verdict';

export interface TraceEventModel {
  id: string;
  offsetMs: number;
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
  if (normalized.includes('role') || normalized.includes('agent') || normalized.includes('delegation')) return 'agent';
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
  const sortedSteps = [...alert.timeline.steps].sort((left, right) => left.timestamp_ns - right.timestamp_ns);
  const startNs = sortedSteps[0]?.timestamp_ns ?? alert.timestamp_ns;

  const timeline = sortedSteps.map((step, index): TraceEventModel => {
    const evidence = step.evidence_ref ? evidenceById.get(step.evidence_ref) : undefined;
    const kind = classifyEventKind(step.event_type);
    const isBoundary = kind === 'boundary';
    const hasRules = step.triggering_rule_ids.length > 0 || !!step.triggering_pattern_id;

    return {
      id: step.source_event_id ?? `${alert.alert_id}-step-${index}`,
      offsetMs: Math.max(0, Math.round((step.timestamp_ns - startNs) / 1_000_000)),
      kind,
      title: step.description,
      actor: step.agent_id ?? alert.primary_agent.agent_id,
      material: step.resource ?? evidenceMaterial(evidence),
      evidenceRef: step.evidence_ref,
      evidenceLevel: step.evidence_level,
      operation: step.operation,
      ruleIds: step.triggering_rule_ids,
      patternId: step.triggering_pattern_id,
      confidence: evidence?.confidence ?? alert.confidence,
      severity: isBoundary ? alert.severity : hasRules ? 'Warning' : 'Normal',
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
  const firstMaterial = evidenceMaterial(evidenceItems.find((item) => item.evidence_id === priorityEvidenceRef));
  const boundaryEvents = timeline.filter((event) => event.kind === 'boundary' || event.severity === 'Critical').length;

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
