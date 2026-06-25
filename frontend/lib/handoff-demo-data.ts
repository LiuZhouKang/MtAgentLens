// Handoff Demo Data — mirrors collect/src/responsibility/server/handoff_store.rs::seed_demo_data()
// Provides realistic delegation tree data for development without a running backend.

import type {
  DelegationNode,
  HandoffObsMetrics,
} from './handoff-api';
import type { HandoffObservabilityEvent } from './handoff-observability-contract';

export const HANDOFF_DEMO_REFERENCE_TIME_MS = Date.parse('2026-06-23T16:00:00.000+08:00');

export const emptyMetrics: HandoffObsMetrics = {
  total_spawned: 0,
  total_completed: 0,
  active_count: 0,
  max_chain_depth: 0,
  fan_out_ratio: 0,
  fan_out_critical: false,
  avg_context_overhead: 0,
  orphaned_artifacts: 0,
  workspace_tag_survival_rate: 1.0,
};

// Demo tree matches the Rust backend's seed_demo_data() exactly
const demoRootAgent: DelegationNode = {
  node_id: 'agent-root',
  agent_id: 'root-agent',
  parent_node_id: null,
  chain_depth: 0,
  spawned_at_ms: HANDOFF_DEMO_REFERENCE_TIME_MS - 60000,
  completed_at_ms: null,
  bytes_in: 15240,
  bytes_out: 89320,
  token_count: 2341,
  context_overhead_ratio: 0.35,
  idle_breakdown: {
    queued_awaiting_dispatch_ms: 0,
    context_injection_io_ms: 0,
    model_inference_queueing_ms: 0,
    tool_call_round_trip_ms: 0,
  },
  model_assigned: 'claude-sonnet-4-6',
  model_optimal: 'claude-sonnet-4-6',
  child_ids: ['agent-planner', 'agent-auditor'],
  children: [
    {
      node_id: 'agent-planner',
      agent_id: 'planner-agent',
      parent_node_id: 'agent-root',
      chain_depth: 1,
      spawned_at_ms: HANDOFF_DEMO_REFERENCE_TIME_MS - 45000,
      completed_at_ms: HANDOFF_DEMO_REFERENCE_TIME_MS - 12000,
      bytes_in: 32400,
      bytes_out: 45600,
      token_count: 1230,
      context_overhead_ratio: 0.58,
      idle_breakdown: {
        queued_awaiting_dispatch_ms: 320,
        context_injection_io_ms: 1450,
        model_inference_queueing_ms: 8200,
        tool_call_round_trip_ms: 3400,
      },
      model_assigned: 'claude-sonnet-4-6',
      model_optimal: 'claude-sonnet-4-6',
      child_ids: ['agent-writer', 'agent-tester'],
      children: [
        {
          node_id: 'agent-writer',
          agent_id: 'writer-agent',
          parent_node_id: 'agent-planner',
          chain_depth: 2,
          spawned_at_ms: HANDOFF_DEMO_REFERENCE_TIME_MS - 35000,
          completed_at_ms: HANDOFF_DEMO_REFERENCE_TIME_MS - 5000,
          bytes_in: 28000,
          bytes_out: 62000,
          token_count: 1890,
          context_overhead_ratio: 0.72,
          idle_breakdown: {
            queued_awaiting_dispatch_ms: 500,
            context_injection_io_ms: 2100,
            model_inference_queueing_ms: 12400,
            tool_call_round_trip_ms: 5600,
          },
          model_assigned: 'claude-sonnet-4-6',
          model_optimal: 'claude-sonnet-4-6',
          child_ids: [],
        },
        {
          node_id: 'agent-tester',
          agent_id: 'tester-agent',
          parent_node_id: 'agent-planner',
          chain_depth: 2,
          spawned_at_ms: HANDOFF_DEMO_REFERENCE_TIME_MS - 30000,
          completed_at_ms: null,
          bytes_in: 15000,
          bytes_out: 8900,
          token_count: 430,
          context_overhead_ratio: 0.45,
          idle_breakdown: {
            queued_awaiting_dispatch_ms: 0,
            context_injection_io_ms: 0,
            model_inference_queueing_ms: 0,
            tool_call_round_trip_ms: 0,
          },
          model_assigned: 'claude-haiku-4-5',
          model_optimal: 'claude-haiku-4-5',
          child_ids: [],
          children: [],
        },
      ],
    },
    {
      node_id: 'agent-auditor',
      agent_id: 'security-auditor',
      parent_node_id: 'agent-root',
      chain_depth: 1,
      spawned_at_ms: HANDOFF_DEMO_REFERENCE_TIME_MS - 40000,
      completed_at_ms: null,
      bytes_in: 12800,
      bytes_out: 2400,
      token_count: 87,
      context_overhead_ratio: 0.12,
      idle_breakdown: {
        queued_awaiting_dispatch_ms: 0,
        context_injection_io_ms: 0,
        model_inference_queueing_ms: 0,
        tool_call_round_trip_ms: 0,
      },
      model_assigned: 'claude-haiku-4-5',
      model_optimal: 'claude-sonnet-4-6',
      child_ids: [],
      children: [],
    },
  ],
};

export const demoMetrics: HandoffObsMetrics = {
  total_spawned: 5,
  total_completed: 2,
  active_count: 3,
  max_chain_depth: 2,
  fan_out_ratio: 2.5,
  fan_out_critical: false,
  avg_context_overhead: 0.444,
  orphaned_artifacts: 1,
  workspace_tag_survival_rate: 0.92,
};

export const demoEvents: HandoffObservabilityEvent[] = [
  {
    sub_type: 'DATA_LEAK',
    alert_id: 'handoff-alert-leak-1',
    timestamp_ms: HANDOFF_DEMO_REFERENCE_TIME_MS - 8000,
    leaking_agent_id: 'planner-agent',
    accessing_agent_id: 'security-auditor',
    exposed_path: '/tmp/security-audit-notes.md',
    content_hash: 'hash-demo-1',
  },
  {
    sub_type: 'CONTEXT_SATURATION',
    timestamp_ms: HANDOFF_DEMO_REFERENCE_TIME_MS - 9000,
    spawn_id: 'agent-writer',
    agent_id: 'writer-agent',
    chain_depth: 2,
    injected_bytes: 74200,
    budget_bytes: 102400,
    overhead_ratio: 0.72,
  },
  {
    sub_type: 'ORPHANED_WORK',
    alert_id: 'handoff-alert-orphan-1',
    timestamp_ms: HANDOFF_DEMO_REFERENCE_TIME_MS - 14000,
    artifact_id: 'artifact-test-plan',
    producer_agent_id: 'planner-agent',
    artifact_path: '/tmp/test-plan.md',
    idle_since_ms: 25000,
  },
  {
    sub_type: 'PIPELINE_INTEGRITY',
    alert_id: 'handoff-alert-pipeline-1',
    timestamp_ms: HANDOFF_DEMO_REFERENCE_TIME_MS - 6000,
    tag_survival_rate: 0.92,
    failed_analyzer_stage: 'workspace_boundary_tagging',
  },
  {
    sub_type: 'BOUNDARY_DRIFT',
    alert_id: 'handoff-alert-boundary-1',
    timestamp_ms: HANDOFF_DEMO_REFERENCE_TIME_MS - 5000,
    agent_id: 'security-auditor',
    chain_depth: 1,
    cross_boundary_ratio: 1.84,
    sibling_median: 0.63,
    deviation_sigmas: 1.9,
  },
  {
    sub_type: 'FANOUT_STORM',
    alert_id: 'handoff-alert-fanout-1',
    timestamp_ms: HANDOFF_DEMO_REFERENCE_TIME_MS - 11000,
    spawn_rate: 5,
    completion_rate: 2,
    ratio: 2.5,
    window_seconds: 30,
  },
];

export const demoDependencyEdges = [
  {
    edge_id: 'de-planner-writer',
    from_spawn_id: 'agent-planner',
    to_spawn_id: 'agent-writer',
    artifact_path: '/tmp/design-doc.md',
    is_orphaned: false,
    idle_ms: 1200,
    producer_agent_id: 'planner-agent',
    consumer_agent_id: 'writer-agent',
  },
  {
    edge_id: 'de-planner-tester',
    from_spawn_id: 'agent-planner',
    to_spawn_id: 'agent-tester',
    artifact_path: '/tmp/test-plan.md',
    is_orphaned: true,
    idle_ms: 25000,
    producer_agent_id: 'planner-agent',
    consumer_agent_id: null,
  },
];

export function getDemoDelegationTreeResponse() {
  return {
    tree: demoRootAgent,
    tree_id: 'tree-demo',
    built_at_ms: HANDOFF_DEMO_REFERENCE_TIME_MS,
    metrics: demoMetrics,
  };
}
