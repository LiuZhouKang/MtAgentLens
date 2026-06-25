import type {
  GraphData,
  GraphMetrics,
  NormalizedResource,
  PhaseIndicator,
  ResponsibilityEdge,
  ResponsibilityEdgeType,
  ResponsibilityNode,
} from './types';

interface LargeAgentGraphOptions {
  agentSatelliteCount?: number;
  resourceSatelliteCount?: number;
}

const DEFAULT_AGENT_SATELLITES = 360;
const DEFAULT_RESOURCE_SATELLITES = 84;

function phase(timestamp_ns: number): PhaseIndicator {
  return { Authoritative: { timestamp_ns, overrides: null } };
}

function makeResource(id: string, resource: NormalizedResource, timestamp_ns: number, evidence_refs: string[] = []): ResponsibilityNode {
  return {
    node_id: id,
    node_type: 'Resource',
    agent_id: null,
    resource,
    evidence_refs,
    timestamp_ns,
    phase: phase(timestamp_ns),
  };
}

function edge(
  edge_id: string,
  edge_type: ResponsibilityEdgeType,
  source: string,
  target: string,
  timestamp_ns: number,
  confidence: number,
  evidence_refs: string[] = [],
): ResponsibilityEdge {
  return {
    edge_id,
    edge_type,
    source,
    target,
    evidence_refs,
    confidence,
    phase: phase(timestamp_ns),
  };
}

function resourceName(node: ResponsibilityNode): string {
  if (!node.resource) return node.node_id;
  if ('File' in node.resource) return node.resource.File.canonical_path.split('/').filter(Boolean).pop() ?? 'file';
  if ('Network' in node.resource) return node.resource.Network.host;
  if ('LlmConversation' in node.resource) return node.resource.LlmConversation.model;
  if ('ToolCall' in node.resource) return node.resource.ToolCall.tool_name;
  if ('Process' in node.resource) return `pid-${node.resource.Process.process_id.pid}`;
  if ('Credential' in node.resource) return node.resource.Credential.credential_id;
  return node.resource.Context.context_id;
}

function addUniqueNode(nodes: ResponsibilityNode[], seen: Set<string>, node: ResponsibilityNode) {
  if (seen.has(node.node_id)) return;
  seen.add(node.node_id);
  nodes.push(node);
}

function addUniqueEdge(edges: ResponsibilityEdge[], seen: Set<string>, next: ResponsibilityEdge) {
  if (seen.has(next.edge_id)) return;
  seen.add(next.edge_id);
  edges.push(next);
}

export function deriveGraphMetrics(graph: GraphData): GraphMetrics {
  const node_counts = graph.nodes.reduce<Record<string, number>>((acc, node) => {
    acc[node.node_type] = (acc[node.node_type] ?? 0) + 1;
    return acc;
  }, {});

  const edge_counts = graph.edges.reduce<Record<string, number>>((acc, item) => {
    acc[item.edge_type] = (acc[item.edge_type] ?? 0) + 1;
    return acc;
  }, {});

  const nodesById = new Map(graph.nodes.map((node) => [node.node_id, node]));
  const cross_agent_edges = graph.edges.filter((item) => {
    const source = nodesById.get(item.source);
    const target = nodesById.get(item.target);
    return source?.agent_id && target?.agent_id && source.agent_id !== target.agent_id;
  }).length;

  const avg_confidence = graph.edges.length
    ? graph.edges.reduce((sum, item) => sum + item.confidence, 0) / graph.edges.length
    : 0;

  return {
    node_counts,
    edge_counts,
    agent_count: node_counts.Agent ?? 0,
    cross_agent_edges,
    delegation_depth: edge_counts.AgentDelegatesTo ?? 0,
    taint_chains: edge_counts.TaintPropagatesTo ?? 0,
    avg_confidence,
  };
}

export function buildLargeAgentGraph(baseGraph: GraphData, options: LargeAgentGraphOptions = {}): GraphData {
  const agentSatelliteCount = options.agentSatelliteCount ?? DEFAULT_AGENT_SATELLITES;
  const resourceSatelliteCount = options.resourceSatelliteCount ?? DEFAULT_RESOURCE_SATELLITES;
  const nodes = [...baseGraph.nodes];
  const edges = [...baseGraph.edges];
  const seenNodeIds = new Set(nodes.map((node) => node.node_id));
  const seenEdgeIds = new Set(edges.map((item) => item.edge_id));
  const agents = baseGraph.nodes.filter((node) => node.node_type === 'Agent');
  const resources = baseGraph.nodes.filter((node) => node.node_type === 'Resource');
  const boundaries = baseGraph.nodes.filter((node) => node.node_type === 'PolicyBoundary');
  const systemBoundary = boundaries.find((node) => node.node_id.includes('system')) ?? boundaries[0] ?? null;

  agents.forEach((agent, agentIndex) => {
    const agentId = agent.agent_id ?? agent.node_id;
    const timestampBase = agent.timestamp_ns || 1;
    let previousContextId: string | null = null;

    for (let index = 0; index < agentSatelliteCount; index += 1) {
      const timestamp_ns = timestampBase + (index + 1) * 3_000_000;
      const kind = index % 6;
      const nodeId = `large:${agent.node_id}:${kind}:${index}`;
      const evidenceId = `ev:${agentId}:large:${index}`;
      const resource: NormalizedResource =
        kind === 0
          ? { LlmConversation: { provider: index % 2 === 0 ? 'openai' : 'anthropic', model: index % 3 === 0 ? 'gpt-4.1' : 'claude-sonnet', message_id: `msg-${agentIndex}-${index}` } }
          : kind === 1
            ? { ToolCall: { tool_name: ['shell', 'browser', 'filesystem', 'git'][index % 4], call_id: `tool-${agentIndex}-${index}` } }
            : kind === 2
              ? { Process: { process_id: { pid: 3200 + agentIndex * 100 + index, start_time_ns: timestamp_ns - 250_000 } } }
              : kind === 3
                ? { Context: { context_id: `ctx-${agentIndex}-${Math.floor(index / 3)}` } }
                : kind === 4
                  ? { Network: { host: ['api.openai.com', 'collector.local', 'metadata.google.internal', 'pypi.org'][index % 4], port: index % 4 === 2 ? 80 : 443, scheme: index % 4 === 2 ? 'http' : 'https' } }
                  : { File: { canonical_path: `/workspace/.mtagentlens/${agentId}/artifact-${index}.json` } };

      addUniqueNode(nodes, seenNodeIds, makeResource(nodeId, resource, timestamp_ns, [evidenceId]));
      addUniqueEdge(
        edges,
        seenEdgeIds,
        edge(
          `large-edge:${agent.node_id}:intent:${index}`,
          kind === 3 ? 'ContextFlowsFrom' : 'IntentCausesAction',
          agent.node_id,
          nodeId,
          timestamp_ns,
          0.62 + (index % 7) * 0.045,
          [evidenceId],
        ),
      );

      if (previousContextId) {
        addUniqueEdge(
          edges,
          seenEdgeIds,
          edge(
            `large-edge:${agent.node_id}:context:${index}`,
            index % 5 === 0 ? 'TaintPropagatesTo' : 'ContextFlowsFrom',
            previousContextId,
            nodeId,
            timestamp_ns,
            0.58 + (index % 5) * 0.055,
            [evidenceId],
          ),
        );
      }

      if (kind === 3 || kind === 0) {
        previousContextId = nodeId;
      }

      if (systemBoundary && index % 17 === 10) {
        addUniqueEdge(
          edges,
          seenEdgeIds,
          edge(
            `large-edge:${agent.node_id}:boundary:${index}`,
            'PolicyBoundaryCrossing',
            nodeId,
            systemBoundary.node_id,
            timestamp_ns,
            0.74,
            [evidenceId],
          ),
        );
      }
    }
  });

  resources.forEach((resource, resourceIndex) => {
    const timestampBase = resource.timestamp_ns || 1;
    const anchorName = resourceName(resource).replace(/[^a-zA-Z0-9._-]/g, '-').slice(0, 32) || `resource-${resourceIndex}`;

    for (let index = 0; index < resourceSatelliteCount; index += 1) {
      const timestamp_ns = timestampBase + (index + 1) * 2_000_000;
      const nodeId = `large:${resource.node_id}:evidence:${index}`;
      const evidenceId = `ev:${anchorName}:sample:${index}`;
      const satellite = index % 4 === 0
        ? { Credential: { credential_id: `${anchorName}:credential:${index}` } }
        : index % 4 === 1
          ? { Context: { context_id: `${anchorName}:context:${index}` } }
          : index % 4 === 2
            ? { File: { canonical_path: `/var/log/mtagentlens/${anchorName}/event-${index}.jsonl` } }
            : { ToolCall: { tool_name: 'evidence-normalizer', call_id: `${anchorName}:${index}` } };

      addUniqueNode(nodes, seenNodeIds, makeResource(nodeId, satellite, timestamp_ns, [evidenceId]));
      addUniqueEdge(
        edges,
        seenEdgeIds,
        edge(
          `large-edge:${resource.node_id}:evidence:${index}`,
          index % 5 === 0 ? 'TaintPropagatesTo' : 'ContextFlowsFrom',
          resource.node_id,
          nodeId,
          timestamp_ns,
          0.52 + (index % 8) * 0.04,
          [evidenceId],
        ),
      );

      if (systemBoundary && index % 13 === 6) {
        addUniqueEdge(
          edges,
          seenEdgeIds,
          edge(
            `large-edge:${resource.node_id}:boundary:${index}`,
            'PolicyBoundaryCrossing',
            nodeId,
            systemBoundary.node_id,
            timestamp_ns,
            0.7,
            [evidenceId],
          ),
        );
      }
    }
  });

  agents.forEach((agent, index) => {
    const target = agents[(index + 1) % agents.length];
    if (!target || target.node_id === agent.node_id) return;

    addUniqueEdge(
      edges,
      seenEdgeIds,
      edge(
        `large-edge:${agent.node_id}:delegates:${target.node_id}`,
        'AgentDelegatesTo',
        agent.node_id,
        target.node_id,
        (agent.timestamp_ns || 1) + 1_500_000,
        0.76,
        [`ev:${agent.node_id}:delegates:${target.node_id}`],
      ),
    );
  });

  return { nodes, edges };
}
