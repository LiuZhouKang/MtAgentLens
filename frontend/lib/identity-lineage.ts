import type {
  AgentIdentity,
  IdentityProcessMapEntry,
  ProcessEvent,
  ProvisionalIdentity,
  SubagentBoundary,
} from './types';

// ═══ 身份血缘（M2）——纯函数派生进程谱系树与委派家族树，无造数 ═══

export interface ProcessTreeNode {
  pid: number;
  start_time_ns: number;
  comm: string;
  agentId: string | null;
  children: ProcessTreeNode[];
}

export interface AgentProcessTree {
  agentId: string;
  primarySignal: string | null;    // provisional.primary_signal
  confidence: number | null;       // provisional.confidence
  status: 'provisional' | 'authoritative'; // 是否出现在 agent_identities
  roots: ProcessTreeNode[];        // 该 agent 的进程树
  orphanPids: number[];            // identity_map 有但 process_events 无记录的 pid
}

export interface AgentFamilyNode {
  agentId: string;
  children: Array<{ childAgentId: string; reason: string; childPid: number }>;
}

/** pid + start_time_ns 组合键。 */
function processKey(pid: number, start_time_ns: number): string {
  return `${pid}:${start_time_ns}`;
}

export function buildAgentProcessTrees(input: {
  provisionalIdentities: ProvisionalIdentity[];
  agentIdentities: AgentIdentity[];
  identityProcessMap: IdentityProcessMapEntry[];
  processEvents: ProcessEvent[];
}): Map<string, AgentProcessTree> {
  const { provisionalIdentities, agentIdentities, identityProcessMap, processEvents } = input;
  const authoritativeSet = new Set(agentIdentities.map((a) => a.agent_id));

  // process_events 索引：进程键 → 事件（父子关系唯一来源）
  const eventByKey = new Map<string, ProcessEvent>();
  for (const e of processEvents) eventByKey.set(processKey(e.pid, e.start_time_ns), e);

  // 每个 agent 的 identity_map pid 集合
  const mapByAgent = new Map<string, { pid: number; start_time_ns: number }[]>();
  for (const m of identityProcessMap) {
    const list = mapByAgent.get(m.agent_id) ?? [];
    list.push({ pid: m.pid, start_time_ns: m.start_time_ns });
    mapByAgent.set(m.agent_id, list);
  }

  const trees = new Map<string, AgentProcessTree>();
  // 遍历 provisional（含 agent_identities 的升级交集通过 status 体现；authoritative-only 也纳入）
  const allAgents = new Set<string>([
    ...provisionalIdentities.map((p) => p.agent_id),
    ...agentIdentities.map((a) => a.agent_id),
  ]);

  for (const agentId of allAgents) {
    const prov = provisionalIdentities.find((p) => p.agent_id === agentId);
    const mapPids = mapByAgent.get(agentId) ?? [];

    // 该 agent 有 process_events 记录的 pid（保留 identity_map 的归属优先）
    const ownedKeys = new Set(mapPids.map((m) => processKey(m.pid, m.start_time_ns)));
    const orphanPids: number[] = [];
    const rootKeys = new Set<string>();

    for (const m of mapPids) {
      const key = processKey(m.pid, m.start_time_ns);
      const ev = eventByKey.get(key);
      if (!ev) {
        orphanPids.push(m.pid); // identity_map 有、process_events 无 → orphan
        continue;
      }
      const parentKey = ev.parent_pid != null ? processKey(ev.parent_pid, ev.parent_start_time_ns ?? 0) : null;
      // 父进程不在该 agent 的 identity_map（跨 agent / 无归属）→ 本进程作根
      if (parentKey == null || !ownedKeys.has(parentKey)) rootKeys.add(key);
    }

    const buildNode = (key: string): ProcessTreeNode | null => {
      const ev = eventByKey.get(key);
      if (!ev) return null;
      const children: ProcessTreeNode[] = [];
      // 子进程 = 以本进程为父、且属于该 agent 的 identity_map pid
      for (const m of mapPids) {
        const ckey = processKey(m.pid, m.start_time_ns);
        const cev = eventByKey.get(ckey);
        if (cev && cev.parent_pid === ev.pid && cev.parent_start_time_ns === ev.start_time_ns) {
          const child = buildNode(ckey);
          if (child) children.push(child);
        }
      }
      children.sort((a, b) => a.start_time_ns - b.start_time_ns || a.pid - b.pid);
      return {
        pid: ev.pid,
        start_time_ns: ev.start_time_ns,
        comm: ev.comm,
        agentId: ev.agent_id,
        children,
      };
    };

    const roots: ProcessTreeNode[] = [];
    for (const key of rootKeys) {
      const node = buildNode(key);
      if (node) roots.push(node);
    }
    roots.sort((a, b) => a.start_time_ns - b.start_time_ns || a.pid - b.pid);

    trees.set(agentId, {
      agentId,
      primarySignal: prov?.primary_signal ?? null,
      confidence: prov?.confidence ?? null,
      status: authoritativeSet.has(agentId) ? 'authoritative' : 'provisional',
      roots,
      orphanPids,
    });
  }

  return trees;
}

export function buildAgentFamilyTree(subagentBoundaries: SubagentBoundary[]): Map<string, AgentFamilyNode> {
  const tree = new Map<string, AgentFamilyNode>();
  for (const b of subagentBoundaries) {
    const node = tree.get(b.parent_agent_id) ?? { agentId: b.parent_agent_id, children: [] };
    node.children.push({ childAgentId: b.child_agent_id, reason: b.reason, childPid: b.child_pid });
    tree.set(b.parent_agent_id, node);
    // 无子节点的子 agent 也登记为空节点（渲染时可展开显示「无委派」）
    if (!tree.has(b.child_agent_id)) tree.set(b.child_agent_id, { agentId: b.child_agent_id, children: [] });
  }
  return tree;
}
