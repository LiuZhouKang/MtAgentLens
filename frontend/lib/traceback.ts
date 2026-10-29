import type { ResponsibilityAlert, ResponsibilityEdge } from './types';

function collectEvidenceRefs(alert: ResponsibilityAlert): Set<string> {
  const refs = new Set<string>();

  for (const step of alert.timeline?.steps ?? []) {
    if (step.evidence_ref) refs.add(step.evidence_ref);
  }

  return refs;
}

export function deriveTracebackEdgeIds(
  alert: ResponsibilityAlert,
  fallbackEdges: ResponsibilityEdge[] = [],
): string[] {
  const evidenceRefs = collectEvidenceRefs(alert);
  const graphEdges = alert.graph_snapshot?.edges?.length
    ? alert.graph_snapshot.edges
    : fallbackEdges;

  const candidateEdges = graphEdges.filter((edge) =>
    (edge.evidence_refs ?? []).some((ref) => evidenceRefs.has(ref)),
  );

  return Array.from(new Set(candidateEdges.map((edge) => edge.edge_id)));
}
