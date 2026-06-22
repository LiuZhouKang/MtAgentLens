// Handoff Demo Data — mirrors collect/src/responsibility/server/handoff_store.rs::seed_demo_data()
// Provides realistic delegation tree data for development without a running backend.

import type {
  DelegationNode,
  HandoffObsMetrics,
} from './handoff-api';

const now = Date.now();

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
  spawned_at_ms: now - 60000,
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
      spawned_at_ms: now - 45000,
      completed_at_ms: now - 12000,
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
          spawned_at_ms: now - 35000,
          completed_at_ms: now - 5000,
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
          spawned_at_ms: now - 30000,
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
      spawned_at_ms: now - 40000,
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
  orphaned_artifacts: 0,
  workspace_tag_survival_rate: 1.0,
};

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
    built_at_ms: now,
    metrics: demoMetrics,
  };
}
