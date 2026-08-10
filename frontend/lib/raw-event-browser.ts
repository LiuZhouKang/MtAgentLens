import type { Action, Intent, ProcessEvent, TlsSession } from '@/lib/types';

/** 原始事件四类：进程事件 / TLS 会话 / LLM 意图 / 落地动作。 */
export type RawEventKind = 'process' | 'tls' | 'intent' | 'action';

/** 归一化后的原始事件行（所有 detail 在提取时截断，避免超长 args 撑爆 UI）。 */
export interface RawEventRow {
  id: string;
  kind: RawEventKind;
  timestampNs: number;
  title: string;
  detail: string;
  agentId: string | null;
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
}

/**
 * 四类原始数据归一化为统一行结构，按 timestampNs 升序排列。
 * 同刻事件保持 kind 插入序（process → tls → intent → action），依赖稳定排序。
 * intents/actions 无 agent JOIN → agentId 恒 null（诚实标注，不臆测归属）。
 */
export function buildRawEventRows(input: RawEventInput): RawEventRow[] {
  const { processEvents, tlsSessions, intents, actions } = input;
  const rows: RawEventRow[] = [
    ...processEvents.map((pe) => ({
      id: `process:${pe.pid}:${pe.start_time_ns}`,
      kind: 'process' as const,
      timestampNs: pe.timestamp_ns,
      title: pe.comm,
      detail: pe.parent_pid != null ? `pid ${pe.pid} ← parent ${pe.parent_pid}` : `pid ${pe.pid}`,
      agentId: pe.agent_id,
      raw: pe,
    })),
    ...tlsSessions.map((s) => ({
      id: `tls:${s.tls_session_id}`,
      kind: 'tls' as const,
      timestampNs: s.timestamp_ns,
      title: s.tls_session_id,
      detail: `pid ${s.pid}`,
      agentId: s.agent_id,
      raw: s,
    })),
    ...intents.map((intent) => ({
      id: `intent:${intent.intent_id}`,
      kind: 'intent' as const,
      timestampNs: intent.timestamp_ns,
      title: intent.tool_name,
      detail: argsToDetail(intent.tool_args),
      agentId: null,
      raw: intent,
    })),
    ...actions.map((a) => ({
      id: `action:${a.action_id}`,
      kind: 'action' as const,
      timestampNs: a.timestamp_ns,
      title: a.action_type,
      detail: argsToDetail(a.normalized_args),
      agentId: null,
      raw: a,
    })),
  ];
  return rows.sort((a, b) => a.timestampNs - b.timestampNs);
}

/** 文本命中域：title + detail（comm 已并入 title、args 已并入 detail）。 */
function matchesText(row: RawEventRow, text: string): boolean {
  if (!text) return true;
  const q = text.toLowerCase();
  return `${row.title} ${row.detail}`.toLowerCase().includes(q);
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

/** 按 kind 统计数量（缺失 kind 计 0）。 */
export function summarizeRawEvents(rows: RawEventRow[]): Record<RawEventKind, number> {
  const summary: Record<RawEventKind, number> = { process: 0, tls: 0, intent: 0, action: 0 };
  for (const row of rows) summary[row.kind] += 1;
  return summary;
}

const KIND_ORDER: RawEventKind[] = ['process', 'tls', 'intent', 'action'];

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
