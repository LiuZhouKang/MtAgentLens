import type {
  Action,
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
  primarySignal: string | null; // provisional.primary_signal
  confidence: number | null; // provisional.confidence
  status: 'provisional' | 'authoritative'; // 是否出现在 agent_identities
  roots: ProcessTreeNode[]; // 该 agent 的进程树
  orphanPids: number[]; // identity_map 有但 process_events 无记录的 pid
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
      const parentKey =
        ev.parent_pid != null ? processKey(ev.parent_pid, ev.parent_start_time_ns ?? 0) : null;
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

export function buildAgentFamilyTree(
  subagentBoundaries: SubagentBoundary[],
): Map<string, AgentFamilyNode> {
  const tree = new Map<string, AgentFamilyNode>();
  for (const b of subagentBoundaries) {
    const node = tree.get(b.parent_agent_id) ?? { agentId: b.parent_agent_id, children: [] };
    node.children.push({ childAgentId: b.child_agent_id, reason: b.reason, childPid: b.child_pid });
    tree.set(b.parent_agent_id, node);
    // 无子节点的子 agent 也登记为空节点（渲染时可展开显示「无委派」）
    if (!tree.has(b.child_agent_id))
      tree.set(b.child_agent_id, { agentId: b.child_agent_id, children: [] });
  }
  return tree;
}

// ═══ 进程时间瀑布（M2 顶级化）——按真实时间顺序构建进程生命周期，供深度调查 ═══

export interface TimelineProcess {
  pid: number;
  start_time_ns: number;
  /** 事件时间戳（更接近真实生命周期起点） */
  ts_ns: number;
  comm: string;
  parentPid: number | null;
  /** 相对起点毫秒（瀑布 X 轴） */
  startMs: number;
  /** 生命周期终点（下一个同层事件或树尾），瀑布条宽 */
  endMs: number;
  depth: number;
  agentId: string | null;
  children: TimelineProcess[];
}

/** 该 agent 的时间瀑布：进程按真实时间顺序 + 层级，root 为无父进程的起点。 */
export interface AgentTimeline {
  agentId: string;
  /** 相对起点毫秒（瀑布 X 轴总宽） */
  totalMs: number;
  /** 起点真实时间（毫秒，格式化用） */
  startWallMs: number;
  roots: TimelineProcess[];
}

/**
 * 把 agent 的进程事件构建为时间瀑布树。
 * 排序：按事件时间升序（真实发生顺序）；父子关系由 parent_pid 建立；
 * 生命周期 endMs = 子树内最大 ts（近似，无退出事件时用当前点）。
 * 纯函数：只依赖输入，无副作用。
 */
export function buildAgentProcessTimeline(input: {
  processEvents: ProcessEvent[];
  identityProcessMap: IdentityProcessMapEntry[];
  agentId: string;
}): AgentTimeline | null {
  const { processEvents, identityProcessMap, agentId } = input;
  // 该 agent 归属的 pid 集（identity_map）
  const ownedPids = new Set(
    identityProcessMap.filter((m) => m.agent_id === agentId).map((m) => m.pid),
  );
  // 该 agent 的进程事件（含 JOIN 标注为自身的，或 pid 在 ownedPids）
  const events = processEvents.filter(
    (e) => e.agent_id === agentId || ownedPids.has(e.pid),
  );
  if (events.length === 0) return null;

  const byKey = new Map<string, ProcessEvent>();
  for (const e of events) byKey.set(processKey(e.pid, e.start_time_ns), e);

  const startNs = Math.min(...events.map((e) => e.timestamp_ns));
  const totalMs = Math.max(1, Math.max(...events.map((e) => e.timestamp_ns)) - startNs) / 1_000_000;

  const toNode = (key: string): TimelineProcess | null => {
    const e = byKey.get(key);
    if (!e) return null;
    const children: TimelineProcess[] = [];
    for (const c of events) {
      if (
        c.parent_pid === e.pid &&
        (c.parent_start_time_ns ?? 0) === e.start_time_ns &&
        byKey.has(processKey(c.pid, c.start_time_ns))
      ) {
        const child = toNode(processKey(c.pid, c.start_time_ns));
        if (child) children.push(child);
      }
    }
    children.sort((a, b) => a.ts_ns - b.ts_ns || a.pid - b.pid);
    // 生命周期终点：子树内最大 ts（自身或后代）
    let endNs = e.timestamp_ns;
    const maxDesc = (n: TimelineProcess) => {
      endNs = Math.max(endNs, n.ts_ns);
      n.children.forEach(maxDesc);
    };
    children.forEach(maxDesc);
    return {
      pid: e.pid,
      start_time_ns: e.start_time_ns,
      ts_ns: e.timestamp_ns,
      comm: e.comm,
      parentPid: e.parent_pid,
      startMs: (e.timestamp_ns - startNs) / 1_000_000,
      endMs: Math.max((endNs - startNs) / 1_000_000, 0.5),
      depth: 0,
      agentId: e.agent_id,
      children,
    };
  };

  const roots: TimelineProcess[] = [];
  // 根：无父事件或父不在本 agent 事件集
  for (const e of events) {
    const key = processKey(e.pid, e.start_time_ns);
    const hasParentHere =
      e.parent_pid != null && byKey.has(processKey(e.parent_pid, e.parent_start_time_ns ?? 0));
    if (!hasParentHere) {
      const node = toNode(key);
      if (node) roots.push(node);
    }
  }
  roots.sort((a, b) => a.ts_ns - b.ts_ns || a.pid - b.pid);
  if (roots.length === 0) return null;

  // 计算 depth（树状缩进）
  const assignDepth = (node: TimelineProcess, d: number) => {
    node.depth = d;
    node.children.forEach((c) => assignDepth(c, d + 1));
  };
  roots.forEach((r) => assignDepth(r, 0));

  return {
    agentId,
    totalMs,
    startWallMs: Math.floor(startNs / 1_000_000),
    roots,
  };
}

// ═══ 进程-动作关联（M2 顶级化）——把进程事件与具体行为（动作/文件）打通 ═══

export interface ProcessAction {
  actionId: string;
  actionType: string; // FileRead / FileWrite / ProcessExec ...
  /** 文件路径或命令（normalized_args 提取），无则为 null */
  target: string | null;
  signalClass: string;
}

/** 从 normalized_args 提取首个 Path 或 command（derive-monitoring 同模式）。 */
function extractActionTarget(args: unknown[]): string | null {
  for (const arg of args ?? []) {
    if (arg && typeof arg === 'object') {
      const obj = arg as Record<string, unknown>;
      if (typeof obj.Path === 'string' && obj.Path) return obj.Path;
      if (typeof obj.command === 'string' && obj.command) return obj.command;
      if (typeof obj.path === 'string' && obj.path) return obj.path;
    }
  }
  return null;
}

/**
 * 按 pid 聚合动作（进程 → 具体行为）。
 * 输入 actions（db 提取），输出 pid → ProcessAction[]（按时间升序）。
 * 纯函数：只依赖输入，无副作用。
 */
export function buildProcessActions(actions: Action[]): Map<number, ProcessAction[]> {
  const map = new Map<number, ProcessAction[]>();
  for (const a of actions) {
    const pid = a.process_id?.pid;
    if (pid == null) continue;
    const list = map.get(pid) ?? [];
    list.push({
      actionId: a.action_id,
      actionType: a.action_type,
      target: extractActionTarget(a.normalized_args ?? []),
      signalClass: a.signal_class,
    });
    map.set(pid, list);
  }
  // 每 pid 内按时间升序
  for (const list of map.values()) {
    list.sort((x, y) => x.actionId.localeCompare(y.actionId));
  }
  return map;
}
