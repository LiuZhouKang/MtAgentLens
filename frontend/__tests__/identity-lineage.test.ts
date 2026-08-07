import { describe, expect, it } from 'vitest';
import { buildAgentFamilyTree, buildAgentProcessTrees } from '@/lib/identity-lineage';
import type { AgentIdentity, IdentityProcessMapEntry, ProcessEvent, ProvisionalIdentity, SubagentBoundary } from '@/lib/types';

const provisional: ProvisionalIdentity[] = [
  { agent_id: 'agent-a', process_ids: [{ pid: 42, start_time_ns: 1000 }, { pid: 102, start_time_ns: 1000 }, { pid: 103, start_time_ns: 1000 }], primary_signal: 'TlsSessionNew', confidence: 0.92, run_id: 'run-1' },
  { agent_id: 'agent-b', process_ids: [{ pid: 43, start_time_ns: 1000 }, { pid: 202, start_time_ns: 1000 }, { pid: 203, start_time_ns: 1000 }], primary_signal: 'ProcessEvent', confidence: 0.85, run_id: 'run-1' },
  { agent_id: 'agent-c', process_ids: [{ pid: 400, start_time_ns: 5000 }], primary_signal: 'TlsSessionNew', confidence: 0.6, run_id: 'run-1' },
];
const authoritative: AgentIdentity[] = [
  { agent_id: 'agent-a', process_ids: [{ pid: 42, start_time_ns: 1000 }, { pid: 102, start_time_ns: 1000 }, { pid: 103, start_time_ns: 1000 }], message_ids: ['msg-1'], tls_sessions: ['s-1'], confidence: 'Authoritative', provisional_refs: ['agent-a'], run_id: 'run-1' },
];
const map: IdentityProcessMapEntry[] = [
  { agent_id: 'agent-a', pid: 42, start_time_ns: 1000 },
  { agent_id: 'agent-a', pid: 102, start_time_ns: 1000 },
  { agent_id: 'agent-a', pid: 103, start_time_ns: 1000 },
  { agent_id: 'agent-a', pid: 99, start_time_ns: 900 }, // orphanPid：无 process_events 记录
  { agent_id: 'agent-b', pid: 43, start_time_ns: 1000 },
  { agent_id: 'agent-b', pid: 202, start_time_ns: 1000 },
  { agent_id: 'agent-b', pid: 203, start_time_ns: 1000 },
];
const events: ProcessEvent[] = [
  { process_event_id: 1, pid: 42, start_time_ns: 1000, parent_pid: null, parent_start_time_ns: null, comm: 'claude', timestamp_ns: 1000, agent_id: 'agent-a' },
  { process_event_id: 2, pid: 102, start_time_ns: 1000, parent_pid: 42, parent_start_time_ns: 1000, comm: 'bash', timestamp_ns: 1001, agent_id: 'agent-a' },
  { process_event_id: 3, pid: 103, start_time_ns: 1000, parent_pid: 102, parent_start_time_ns: 1000, comm: 'sh', timestamp_ns: 1002, agent_id: 'agent-a' },
  { process_event_id: 4, pid: 43, start_time_ns: 1000, parent_pid: null, parent_start_time_ns: null, comm: 'claude', timestamp_ns: 1000, agent_id: 'agent-b' },
  { process_event_id: 5, pid: 202, start_time_ns: 1000, parent_pid: 43, parent_start_time_ns: 1000, comm: 'node', timestamp_ns: 2000, agent_id: 'agent-b' },
  { process_event_id: 6, pid: 203, start_time_ns: 1000, parent_pid: 202, parent_start_time_ns: 1000, comm: 'script', timestamp_ns: 2001, agent_id: 'agent-b' },
  { process_event_id: 7, pid: 300, start_time_ns: 4000, parent_pid: null, parent_start_time_ns: null, comm: 'orphan-tool', timestamp_ns: 4000, agent_id: null },
];

describe('buildAgentProcessTrees', () => {
  it('按 agent 组进程树（父子嵌套 + 跨 agent 父挂根）+ status/primarySignal/orphanPids', () => {
    const trees = buildAgentProcessTrees({ provisionalIdentities: provisional, agentIdentities: authoritative, identityProcessMap: map, processEvents: events });

    // agent-a：升级（provisional ∩ authoritative）→ status authoritative
    const a = trees.get('agent-a')!;
    expect(a.status).toBe('authoritative');
    expect(a.primarySignal).toBe('TlsSessionNew');
    expect(a.confidence).toBe(0.92);
    expect(a.orphanPids).toEqual([99]);
    // 树根 = pid 42（claude），子 = 102（bash），孙 = 103（sh）
    expect(a.roots).toHaveLength(1);
    expect(a.roots[0]).toMatchObject({ pid: 42, comm: 'claude' });
    expect(a.roots[0].children).toHaveLength(1);
    expect(a.roots[0].children[0]).toMatchObject({ pid: 102, comm: 'bash' });
    expect(a.roots[0].children[0].children[0].pid).toBe(103);

    // agent-c：仅 provisional → status provisional，无进程事件记录 → roots 空 + orphanPids 空
    const c = trees.get('agent-c')!;
    expect(c.status).toBe('provisional');
    expect(c.roots).toEqual([]);
    expect(c.orphanPids).toEqual([]);

    // agent-b：status provisional（不在 authoritative）
    expect(trees.get('agent-b')!.status).toBe('provisional');
    expect(trees.get('agent-b')!.roots[0].pid).toBe(43);
  });

  it('跨 agent 父进程挂根：父不在该 agent 的 identity_map 时仍组树（父子关系来自 process_events）', () => {
    // 构造：agent-z 的 pid 900，父 901（agent-z 无父记录但 process_events 有父子）
    const trees = buildAgentProcessTrees({
      provisionalIdentities: [{ agent_id: 'agent-z', process_ids: [{ pid: 900, start_time_ns: 1 }], primary_signal: 'ProcessEvent', confidence: 0.5, run_id: 'run-1' }],
      agentIdentities: [],
      identityProcessMap: [{ agent_id: 'agent-z', pid: 900, start_time_ns: 1 }],
      processEvents: [
        { process_event_id: 1, pid: 900, start_time_ns: 1, parent_pid: null, parent_start_time_ns: null, comm: 'child', timestamp_ns: 1, agent_id: 'agent-z' },
      ],
    });
    expect(trees.get('agent-z')!.roots).toHaveLength(1);
    expect(trees.get('agent-z')!.roots[0].pid).toBe(900);
  });

  it('空数据 → 空 Map', () => {
    const trees = buildAgentProcessTrees({ provisionalIdentities: [], agentIdentities: [], identityProcessMap: [], processEvents: [] });
    expect(trees.size).toBe(0);
  });
});

describe('buildAgentFamilyTree', () => {
  it('由 subagent_boundaries 组 parent→child 委派树', () => {
    const boundaries: SubagentBoundary[] = [
      { boundary_id: 'bnd-1', parent_agent_id: 'agent-a', child_agent_id: 'agent-b', parent_pid: 42, parent_start_time_ns: 1000, child_pid: 43, child_start_time_ns: 1000, reason: 'delegation', run_id: 'run-1' },
      { boundary_id: 'bnd-2', parent_agent_id: 'agent-b', child_agent_id: 'agent-c', parent_pid: 43, parent_start_time_ns: 1000, child_pid: 400, child_start_time_ns: 5000, reason: 'subagent spawn', run_id: 'run-1' },
    ];
    const tree = buildAgentFamilyTree(boundaries);
    expect(tree.get('agent-a')!.children).toEqual([{ childAgentId: 'agent-b', reason: 'delegation', childPid: 43 }]);
    expect(tree.get('agent-b')!.children).toEqual([{ childAgentId: 'agent-c', reason: 'subagent spawn', childPid: 400 }]);
  });

  it('空边界 → 空 Map', () => {
    expect(buildAgentFamilyTree([]).size).toBe(0);
  });
});
