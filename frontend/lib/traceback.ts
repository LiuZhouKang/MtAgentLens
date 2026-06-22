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

  let candidateEdges = graphEdges.filter((edge) =>
    (edge.evidence_refs ?? []).some((ref) => evidenceRefs.has(ref)),
  );

  if (!candidateEdges.length) {
    candidateEdges = graphEdges.filter((edge) => edge.edge_type === 'PolicyBoundaryCrossing');
  }

  if (!candidateEdges.length && alert.primary_agent?.agent_id) {
    candidateEdges = graphEdges.filter(
      (edge) =>
        edge.source === alert.primary_agent.agent_id ||
        edge.target === alert.primary_agent.agent_id,
    );
  }

  return Array.from(new Set(candidateEdges.map((edge) => edge.edge_id)));
}
