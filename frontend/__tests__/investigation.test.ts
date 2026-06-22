import { describe, it, expect } from 'vitest';
import { demoAlerts, demoGraph } from '@/lib/demo-data';
import type { CorrelationEvidence } from '@/lib/types';
import {
  buildInvestigationModel,
  collectAlertEvidenceRefs,
  formatResponsibilityNodeLabel,
} from '@/lib/investigation';

describe('investigation helpers', () => {
  it('collects unique evidence refs from timeline and graph snapshot', () => {
    const refs = collectAlertEvidenceRefs(demoAlerts[0]);

    expect(refs).toEqual(['ev-3', 'ev-4']);
  });

  it('builds an investigation model with the main path and linked evidence', () => {
    const evidence: CorrelationEvidence[] = [
      {
        evidence_id: 'ev-3',
        intent_id: 'intent-1',
        action_id: 'action-1',
        match_level: 'L1Exact',
        confidence: 0.91,
        matched_fields: {
          canonical_path: '/etc/shadow',
          host: null,
          port: null,
          command: null,
          raw_args: [],
        },
        derivation_mode: 'demo',
        phase: { Authoritative: { timestamp_ns: demoAlerts[0].timestamp_ns, overrides: null } },
        timestamp_ns: demoAlerts[0].timestamp_ns,
      },
      {
        evidence_id: 'ev-4',
        intent_id: 'intent-2',
        action_id: 'action-2',
        match_level: 'L1Exact',
        confidence: 0.77,
        matched_fields: {
          canonical_path: null,
          host: null,
          port: null,
          command: 'delegate security-auditor',
          raw_args: ['security-auditor'],
        },
        derivation_mode: 'demo',
        phase: { Authoritative: { timestamp_ns: demoAlerts[0].timestamp_ns, overrides: null } },
        timestamp_ns: demoAlerts[0].timestamp_ns - 1,
      },
    ];

    const model = buildInvestigationModel(demoAlerts[0], demoGraph, evidence);

    expect(model.alertId).toBe('demo-alert-1');
    expect(model.boundaryCrossingCount).toBe(1);
    expect(model.pathEdges.map((edge) => edge.edge_id)).toEqual(
      expect.arrayContaining(['e-delegate', 'e-taint', 'e-boundary']),
    );
    expect(model.pathNodes.map((node) => node.label)).toEqual(
      expect.arrayContaining(['agent-security', '/etc/shadow', 'boundary-system']),
    );
    expect(model.linkedEvidence.map((item) => item.evidence_id)).toEqual(['ev-3', 'ev-4']);
  });

  it('formats responsibility node labels for different node types', () => {
    const graph = demoGraph;
    const agentNode = graph.nodes.find((node) => node.node_type === 'Agent');
    const fileNode = graph.nodes.find((node) => 'resource' in node && node.resource && 'File' in node.resource);
    const boundaryNode = graph.nodes.find((node) => node.node_type === 'PolicyBoundary');

    expect(agentNode && formatResponsibilityNodeLabel(agentNode)).toBe('agent-frontend');
    expect(fileNode && formatResponsibilityNodeLabel(fileNode)).toBe('/src/components/Button.tsx');
    expect(boundaryNode && formatResponsibilityNodeLabel(boundaryNode)).toBe('boundary-workspace');
  });

  it('formats responsibility labels for resource-backed node variants', () => {
    expect(
      formatResponsibilityNodeLabel({
        node_id: 'network-node',
        node_type: 'Resource',
        agent_id: null,
        resource: { Network: { host: 'api.example.com', port: 443, scheme: 'https' } },
        evidence_refs: [],
        timestamp_ns: 1,
        phase: { Provisional: { timestamp_ns: 1 } },
      }),
    ).toBe('api.example.com:443');

    expect(
      formatResponsibilityNodeLabel({
        node_id: 'process-node',
        node_type: 'Resource',
        agent_id: null,
        resource: { Process: { process_id: { pid: 4242, start_time_ns: 10 } } },
        evidence_refs: [],
        timestamp_ns: 1,
        phase: { Provisional: { timestamp_ns: 1 } },
      }),
    ).toBe('pid:4242');

    expect(
      formatResponsibilityNodeLabel({
        node_id: 'tool-node',
        node_type: 'Resource',
        agent_id: null,
        resource: { ToolCall: { tool_name: 'grep', call_id: 'call-1' } },
        evidence_refs: [],
        timestamp_ns: 1,
        phase: { Provisional: { timestamp_ns: 1 } },
      }),
    ).toBe('grep');

    expect(
      formatResponsibilityNodeLabel({
        node_id: 'credential-node',
        node_type: 'Resource',
        agent_id: null,
        resource: { Credential: { credential_id: 'cred-1' } },
        evidence_refs: [],
        timestamp_ns: 1,
        phase: { Provisional: { timestamp_ns: 1 } },
      }),
    ).toBe('cred-1');

    expect(
      formatResponsibilityNodeLabel({
        node_id: 'context-node',
        node_type: 'Resource',
        agent_id: null,
        resource: { Context: { context_id: 'ctx-1' } },
        evidence_refs: [],
        timestamp_ns: 1,
        phase: { Provisional: { timestamp_ns: 1 } },
      }),
    ).toBe('ctx-1');

    expect(
      formatResponsibilityNodeLabel({
        node_id: 'conversation-node',
        node_type: 'Resource',
        agent_id: null,
        resource: { LlmConversation: { provider: 'openai', model: 'gpt-4.1', message_id: 'msg-1' } },
        evidence_refs: [],
        timestamp_ns: 1,
        phase: { Provisional: { timestamp_ns: 1 } },
      }),
    ).toBe('openai/gpt-4.1');
  });

  it('falls back to the node id when no specialized label applies', () => {
    expect(
      formatResponsibilityNodeLabel({
        node_id: 'resource-fallback',
        node_type: 'Resource',
        agent_id: null,
        resource: null,
        evidence_refs: [],
        timestamp_ns: 1,
        phase: { Provisional: { timestamp_ns: 1 } },
      }),
    ).toBe('resource-fallback');
  });

  it('falls back to the provided graph and keeps the primary agent visible when traceback edges are missing', () => {
    const alertWithoutSnapshot = {
      ...demoAlerts[1],
      primary_agent: {
        agent_id: 'agent-frontend',
        claimed_role: 'frontend-expert',
      },
      graph_snapshot: {
        ...demoAlerts[1].graph_snapshot,
        nodes: [],
        edges: [],
      },
      timeline: {
        ...demoAlerts[1].timeline,
        steps: [],
      },
    };

    const fallbackGraph = {
      nodes: [
        {
          node_id: 'agent-frontend',
          node_type: 'Agent' as const,
          agent_id: 'agent-frontend',
          resource: null,
          evidence_refs: [],
          timestamp_ns: 1,
          phase: { Provisional: { timestamp_ns: 1 } },
        },
      ],
      edges: [],
    };

    const model = buildInvestigationModel(alertWithoutSnapshot, fallbackGraph, []);

    expect(model.primaryAgentId).toBe('agent-frontend');
    expect(model.pathEdges).toEqual([]);
    expect(model.pathNodes).toEqual([
      {
        nodeId: 'agent-frontend',
        nodeType: 'Agent',
        label: 'agent-frontend',
      },
    ]);
    expect(model.boundaryCrossingCount).toBe(0);
  });
});
