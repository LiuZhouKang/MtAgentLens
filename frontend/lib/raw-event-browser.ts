import type {
  Action,
  ContextPollutionEvent,
  IdentityProcessMapEntry,
  Intent,
  ProcessEvent,
  TlsSession,
} from '@/lib/types';

/** 原始事件类型：进程事件 / TLS 会话 / LLM 意图 / 落地动作 / 上下文污染。
 *  进程与 TLS 来自 eBPF 层；意图/动作为语义层；污染为责任分析原生事件
 * （context_pollution_events 表，带 detected_at_ns 时间戳与 source_agent_id）。 */
export type RawEventKind = 'process' | 'tls' | 'intent' | 'action' | 'pollution';

/** 归一化后的原始事件行（所有 detail 在提取时截断，避免超长 args 撑爆 UI）。 */
export interface RawEventRow {
  id: string;
  kind: RawEventKind;
  timestampNs: number;
  title: string;
  detail: string;
  agentId: string | null;
  /** 搜索缓存：title + detail + agentId + 截断的原始载荷（小写），免每次过滤重复序列化。 */
  searchText: string;
  /** 原始对象引用（展开行展示完整 args，不受 DETAIL_MAX 截断）。 */
  raw: unknown;
}

export interface RawEventFilters {
  kinds: RawEventKind[];
  agentId: string | null;
  text: string;
  sinceNs: number | null;
}

export const DETAIL_MAX = 80;

/** 超长详情截断：超过 DETAIL_MAX 后截断并追加省略号。 */
export function truncateDetail(s: string): string {
  return s.length > DETAIL_MAX ? `${s.slice(0, DETAIL_MAX)}…` : s;
}

/** 任意 args（tool_args / normalized_args）序列化为单行文本并截断。 */
export function argsToDetail(args: unknown[]): string {
  return truncateDetail(JSON.stringify(args));
}

export interface RawEventInput {
  processEvents: ProcessEvent[];
  tlsSessions: TlsSession[];
  intents: Intent[];
  actions: Action[];
  pollutionEvents: ContextPollutionEvent[];
  /** pid + start_time_ns → agent_id 映射：intents/actions 无 agent 列，靠它解析真实归属。 */
  identityProcessMap: IdentityProcessMapEntry[];
}

/**
 * pid + process_start_time_ns → agent_id（identity_process_map 真实 JOIN）。
 * 无映射时返回 null（诚实标注，不臆测归属）。O(1) 预建 Map。
 */
export function buildAgentIndex(
  identityProcessMap: IdentityProcessMapEntry[],
): Map<string, string> {
  const index = new Map<string, string>();
  for (const entry of identityProcessMap) {
    index.set(`${entry.pid}:${entry.start_time_ns}`, entry.agent_id);
  }
  return index;
}

function resolveAgentId(
  agentIndex: Map<string, string>,
  pid: number,
  processStartTimeNs: number,
): string | null {
  return agentIndex.get(`${pid}:${processStartTimeNs}`) ?? null;
}

function toSearchText(
  title: string,
  detail: string,
  agentId: string | null,
  raw: unknown,
): string {
  let payload = '';
  try {
    payload = JSON.stringify(raw);
  } catch {
    payload = '';
  }
  // 载荷只取前 400 字符进搜索索引——完整内容仍在 raw 中可展开查看。
  return `${title} ${detail} ${agentId ?? ''} ${payload.slice(0, 400)}`.toLowerCase();
}

/**
 * 六类原始数据归一化为统一行结构，按 timestampNs 升序排列。
 * 同刻事件保持 kind 插入序（process → tls → intent → action → pollution），依赖稳定排序。
 * intents/actions 通过 identity_process_map 解析 agent 归属（真实 JOIN，不臆测）。
 */
export function buildRawEventRows(input: RawEventInput): RawEventRow[] {
  const { processEvents, tlsSessions, intents, actions, pollutionEvents, identityProcessMap } = input;
  const agentIndex = buildAgentIndex(identityProcessMap);
  const rows: RawEventRow[] = [
    ...processEvents.map((pe) => {
      const detail =
        pe.parent_pid != null ? `pid ${pe.pid} ← parent ${pe.parent_pid}` : `pid ${pe.pid}`;
      return {
        id: `process:${pe.pid}:${pe.start_time_ns}`,
        kind: 'process' as const,
        timestampNs: pe.timestamp_ns,
        title: pe.comm,
        detail,
        agentId: pe.agent_id,
        searchText: toSearchText(pe.comm, detail, pe.agent_id, pe),
        raw: pe,
      };
    }),
    ...tlsSessions.map((s) => {
      const detail = `pid ${s.pid}`;
      return {
        id: `tls:${s.tls_session_id}`,
        kind: 'tls' as const,
        timestampNs: s.timestamp_ns,
        title: s.tls_session_id,
        detail,
        agentId: s.agent_id,
        searchText: toSearchText(s.tls_session_id, detail, s.agent_id, s),
        raw: s,
      };
    }),
    ...intents.map((intent) => {
      const agentId = resolveAgentId(agentIndex, intent.process_id.pid, intent.process_id.start_time_ns);
      const detail = argsToDetail(intent.tool_args);
      return {
        id: `intent:${intent.intent_id}`,
        kind: 'intent' as const,
        timestampNs: intent.timestamp_ns,
        title: intent.tool_name,
        detail,
        agentId,
        searchText: toSearchText(intent.tool_name, detail, agentId, intent),
        raw: intent,
      };
    }),
    ...actions.map((a) => {
      const agentId = resolveAgentId(agentIndex, a.process_id.pid, a.process_id.start_time_ns);
      const detail = argsToDetail(a.normalized_args);
      return {
        id: `action:${a.action_id}`,
        kind: 'action' as const,
        timestampNs: a.timestamp_ns,
        title: a.action_type,
        detail,
        agentId,
        searchText: toSearchText(a.action_type, detail, agentId, a),
        raw: a,
      };
    }),
    ...pollutionEvents.map((p) => {
      const detail =
        p.propagation_path.length > 0
          ? `沿 ${p.propagation_path.slice(0, 3).join(' → ')}${p.propagation_path.length > 3 ? ` +${p.propagation_path.length - 3}` : ''} 传播`
          : '单一污染源';
      return {
        id: `pollution:${p.pollution_id}`,
        kind: 'pollution' as const,
        timestampNs: p.detected_at_ns,
        title: p.contaminated_artifact,
        detail,
        agentId: p.source_agent_id,
        searchText: toSearchText(p.contaminated_artifact, detail, p.source_agent_id, p),
        raw: p,
      };
    }),
  ];
  return rows.sort((a, b) => a.timestampNs - b.timestampNs);
}

/** 文本命中域：搜索缓存（title + detail + agentId + 截断载荷）。 */
function matchesText(row: RawEventRow, text: string): boolean {
  if (!text) return true;
  return row.searchText.includes(text.toLowerCase());
}

/** 多维过滤：kind ∈ kinds（空 = 全部）、agentId 精确匹配（null = 全部）、文本子串、时间下界。 */
export function filterRawEventRows(rows: RawEventRow[], filters: RawEventFilters): RawEventRow[] {
  const { kinds, agentId, text, sinceNs } = filters;
  return rows.filter((row) => {
    if (kinds.length > 0 && !kinds.includes(row.kind)) return false;
    if (agentId != null && row.agentId !== agentId) return false;
    if (!matchesText(row, text)) return false;
    if (sinceNs != null && row.timestampNs < sinceNs) return false;
    return true;
  });
}

/** 时间档位选项（window = 距 maxTs 的回看窗口；null = 全部）。 */
export interface TimeRangeOption {
  label: string;
  window: number | null;
}

const TIME_UNITS: { n: number; label: string }[] = [
  { n: 10, label: '最近 10 秒' },
  { n: 30, label: '最近 30 秒' },
  { n: 60, label: '最近 1 分钟' },
  { n: 120, label: '最近 2 分钟' },
  { n: 300, label: '最近 5 分钟' },
  { n: 900, label: '最近 15 分钟' },
  { n: 1800, label: '最近 30 分钟' },
  { n: 3600, label: '最近 1 小时' },
  { n: 7200, label: '最近 2 小时' },
  { n: 21600, label: '最近 6 小时' },
  { n: 86400, label: '最近 1 天' },
];

/**
 * 自适应时间档位：按数据实际跨度生成「必然改变结果」的最近 X 档位。
 * 固定「最近 1 小时」对仅 2.5 分钟的数据等于全选 → 用户点选无变化（报告缺陷）。
 * 取能真正切中数据的档位（window < span），最多 4 档；跨度过短则用比例档位。
 */
export function buildTimeRanges(minTs: number, maxTs: number): TimeRangeOption[] {
  if (maxTs <= minTs) return [{ label: '全部时间', window: null }];
  const span = maxTs - minTs;
  const cutting = TIME_UNITS.filter((u) => u.n * 1_000_000_000 < span).slice(-4);
  if (cutting.length === 0) {
    // 跨度 < 10 秒：比例档位仍能切分
    return [
      { label: '全部时间', window: null },
      { label: '最近 1/2', window: span / 2 },
      { label: '最近 1/4', window: span / 4 },
    ];
  }
  return [
    { label: '全部时间', window: null },
    ...cutting.map((u) => ({ label: u.label, window: u.n * 1_000_000_000 })),
  ];
}

/** 按 kind 统计数量（缺失 kind 计 0）。 */
export function summarizeRawEvents(rows: RawEventRow[]): Record<RawEventKind, number> {
  const summary: Record<RawEventKind, number> = {
    process: 0,
    tls: 0,
    intent: 0,
    action: 0,
    pollution: 0,
  };
  for (const row of rows) summary[row.kind] += 1;
  return summary;
}

const KIND_ORDER: RawEventKind[] = ['process', 'tls', 'intent', 'action', 'pollution'];

/** 指定 agent 实际拥有的 kind 集合（级联收窄；agentId=null 视为不限 agent）。 */
export function availableKindsForAgent(
  rows: RawEventRow[],
  agentId: string | null,
): RawEventKind[] {
  const present = new Set<RawEventKind>();
  for (const row of rows) {
    if (agentId != null && row.agentId !== agentId) continue;
    present.add(row.kind);
  }
  return KIND_ORDER.filter((k) => present.has(k));
}
