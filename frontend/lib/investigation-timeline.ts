// 告警时间线 —— 富化纯函数层（无 React）。
//
// 后端 responsibility_alerts.timeline_json 的 BehaviorTimelineEntry 字段极稀疏
// （实测 1558 步中仅 description 100% 填充，其余字段几乎全空），且 76% 是
// 重复的 `signal: ssl` 连接噪声——直接渲染必然「看不出东西」。本模块把原始
// 步骤富化为顶级瀑布可用的 InvestigationTimelineEvent：
//  1. 折叠数百条重复 SSL 信号为一条聚合事件（aggregated=N）
//  2. 从 description 解析 comm / pid / exitCode（"process exec: sudo (pid=588249)"）
//  3. 关联 correlation_evidence → matched 字段（command/path/host/rawArgs/confidence）
//  4. 推断 offsetMs / durationMs（真实时间差，供瀑布条定位）
//  5. classifyEventKind 分类 + severity 标注（boundary → 告警严重度）
//  6. 按 pid 精确关联真实数据（不编造）：
//     - process_events → 真实进程名 comm
//     - actions → 该进程的真实行为（文件路径 / 命令）
// 铁律：所有展示字段来自真实 db；推断字段（durationMs = 相邻事件时间差）诚实标注。

import type { Action, BehaviorTimelineEntry, CorrelationEvidence, ProcessEvent } from './types';
import { classifyEventKind, type TraceEventKind } from './trace-workbench';
import { actionTypeLabel } from './monitoring/perspective-labels';
import { eventTypeLabel } from './investigation-labels';

/** 进程的一条真实行为（来自 actions 表，按 pid 精确关联）。 */
export interface InvAction {
  actionType: string;
  /** 动作类型中文标签（FileRead → 文件读取） */
  actionLabel: string;
  /** 文件路径或命令（normalized_args 提取），无则为 null */
  target: string | null;
  signalClass: string;
  tsNs: number;
}

export interface InvestigationTimelineEvent {
  id: string;
  /** 相对时间线起点的偏移（毫秒，瀑布 X 轴定位） */
  offsetMs: number;
  /** 相邻事件时间差（毫秒，瀑布条宽）—— 真实时间差，非事件耗时 */
  durationMs: number;
  /** 事件绝对时间戳（ns，真实时钟 HH:MM:SS 来源） */
  tsNs: number;
  eventType: string;
  /** 中文事件标签（eventTypeLabel） */
  label: string;
  kind: TraceEventKind;
  description: string;
  /** description 解析出的进程名 / SSL 协议名 */
  comm: string | null;
  /** 真实进程名（process_events 表按 pid 精确关联，权威） */
  realComm: string | null;
  /** 该进程归属的 agent id（process_events 的 identity_map JOIN 标注） */
  agentId: string | null;
  pid: number | null;
  exitCode: number | null;
  resource: string | null;
  evidenceRef: string | null;
  evidenceLevel: string | null;
  /** 关联证据的置信度（0-1） */
  confidence: number | null;
  matchedPath: string | null;
  matchedCommand: string | null;
  matchedHost: string | null;
  rawArgs: string[];
  ruleIds: string[];
  patternId: string | null;
  isBoundary: boolean;
  /** boundary 继承告警严重度；有触发规则/L1 证据 → Warning；其余 Normal */
  severity: 'Normal' | 'Warning' | 'Critical';
  isCritical: boolean;
  hasRules: boolean;
  /** >1 表示聚合事件（重复 SSL 信号折叠） */
  aggregated: number;
  sourceEventId: string | null;
  /** 该进程的真实行为（actions 表按 pid 精确关联，时间升序） */
  actions: InvAction[];
}

export interface InvestigationTimeline {
  /** 折叠后的富化事件（瀑布渲染源） */
  events: InvestigationTimelineEvent[];
  /** 折叠前的原始步数 */
  rawCount: number;
  /** 折叠掉的重复信号数 */
  foldedSignals: number;
  /** 总耗时（毫秒，刻度尺右端） */
  totalMs: number;
  /** 起点真实时间（毫秒，格式化用） */
  startWallMs: number;
}

const T = 1_000_000; // ns → ms

/** 从 actions.normalized_args 提取真实 Path / command（与 identity-lineage 同模式）。 */
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

/** 解析 "process exec: sudo (pid=588249)" / "process exit: curl (pid=588149, code=0)" / "signal: ssl (pid=588149)"。 */
function parseDesc(desc: string): { comm: string | null; pid: number | null; code: number | null } {
  const pidMatch = desc.match(/pid=(\d+)/);
  const codeMatch = desc.match(/code=(-?\d+)/);
  const commMatch = desc.match(/process (?:exec|exit): (\S+)/) ?? desc.match(/signal: (\S+)/);
  return {
    comm: commMatch?.[1] ?? null,
    pid: pidMatch ? Number(pidMatch[1]) : null,
    code: codeMatch ? Number(codeMatch[1]) : null,
  };
}

export function buildInvestigationTimeline(input: {
  steps: BehaviorTimelineEntry[];
  evidence: CorrelationEvidence[];
  totalDurationMs: number;
  alertSeverity: string;
  /** process_events 表 → pid→真实进程名（精确关联，不编造） */
  processEvents?: ProcessEvent[];
  /** actions 表 → pid→真实行为（精确关联，不编造） */
  actions?: Action[];
}): InvestigationTimeline {
  const { steps, evidence, totalDurationMs, alertSeverity, processEvents, actions } = input;
  const evidenceById = new Map(evidence.map((e) => [e.evidence_id, e]));

  // 0. 真实数据索引（pid → comm / agentId / 行为，来自 process_events / actions 表）
  const pidComm = new Map<number, string>();
  const pidAgent = new Map<number, string>();
  for (const pe of processEvents ?? []) {
    if (pe.pid != null) {
      if (pe.comm && !pidComm.has(pe.pid)) pidComm.set(pe.pid, pe.comm);
      if (pe.agent_id && !pidAgent.has(pe.pid)) pidAgent.set(pe.pid, pe.agent_id);
    }
  }
  const pidActions = new Map<number, InvAction[]>();
  for (const a of actions ?? []) {
    const pid = a.process_id?.pid;
    if (pid == null || pid <= 0) continue;
    const list = pidActions.get(pid) ?? [];
    list.push({
      actionType: a.action_type,
      actionLabel: actionTypeLabel(a.action_type).label,
      target: extractActionTarget(a.normalized_args ?? []),
      signalClass: a.signal_class,
      tsNs: a.timestamp_ns,
    });
    pidActions.set(pid, list);
  }
  for (const list of pidActions.values()) list.sort((x, y) => x.tsNs - y.tsNs);

  // 1. 折叠重复 SSL signal → 一条聚合事件（保留信号集合的起止时间跨度）
  const signals = steps.filter((s) => s.event_type === 'signal' || s.event_type === 'SIGNAL');
  const kept = steps.filter((s) => s.event_type !== 'signal' && s.event_type !== 'SIGNAL');
  const foldedSignals = signals.length;
  const collapsed = foldedSignals > 0 ? [...kept, signals[0]] : kept;
  collapsed.sort((a, b) => a.timestamp_ns - b.timestamp_ns);
  // 聚合信号的时间跨度（瀑布条宽：SSL 连接贯穿的时间段）
  const signalStartNs = foldedSignals ? Math.min(...signals.map((s) => s.timestamp_ns)) : 0;
  const signalEndNs = foldedSignals ? Math.max(...signals.map((s) => s.timestamp_ns)) : 0;

  const rawCount = steps.length;
  if (collapsed.length === 0) {
    return { events: [], rawCount, foldedSignals, totalMs: 0, startWallMs: 0 };
  }

  const startNs = collapsed[0].timestamp_ns;
  const totalMs = Math.max(totalDurationMs || 0, 1);

  // 2. 富化每步
  const events: InvestigationTimelineEvent[] = collapsed.map((step, i) => {
    const next = collapsed[i + 1];
    const isSignal = step.event_type === 'signal' || step.event_type === 'SIGNAL';
    const offsetMs = Math.max(0, Math.round((step.timestamp_ns - startNs) / T));
    // 聚合信号：瀑布条 = 信号集合时间跨度（SSL 贯穿全程）；否则下一条间隔 / 末条余量
    const durationMs = isSignal
      ? Math.max(1, Math.round((signalEndNs - signalStartNs) / T))
      : next
        ? Math.max(1, Math.round((next.timestamp_ns - step.timestamp_ns) / T))
        : Math.max(1, totalDurationMs > 0 ? totalDurationMs - offsetMs : 1000);

    const { comm, pid, code } = parseDesc(step.description ?? '');
    // 真实进程名：process_events 表按 pid 精确关联；无则回落 description 解析
    const realComm = pid != null && pidComm.has(pid) ? pidComm.get(pid)! : comm;
    // 该进程归属的 agent（process_events 的 identity_map JOIN 标注）
    const agentId = pid != null && pidAgent.has(pid) ? pidAgent.get(pid)! : null;
    // 该进程的真实行为：actions 表按 pid 精确关联
    const realActions = pid != null ? (pidActions.get(pid) ?? []) : [];

    // 关联证据的富字段（signal 聚合只取第一条的引用；其余步骤按 evidence_ref 精确关联）
    const linked = evidenceById.get(step.evidence_ref ?? '');
    const kind = classifyEventKind(step.event_type ?? '');
    const isBoundary = kind === 'boundary';
    const isExact = step.evidence_level === 'L1Exact';
    const hasRules = (step.triggering_rule_ids?.length ?? 0) > 0 || !!step.triggering_pattern_id;
    // 严重度：boundary 继承告警严重度（Info → 提升 Warning），有规则/L1 → Warning
    const severity = (
      isBoundary
        ? alertSeverity === 'Critical' || alertSeverity === 'Warning'
          ? alertSeverity
          : 'Warning'
        : hasRules || isExact
          ? 'Warning'
          : 'Normal'
    ) as 'Normal' | 'Warning' | 'Critical';
    const isCritical = severity === 'Critical';

    return {
      id: step.source_event_id ?? `${startNs}-step-${i}`,
      offsetMs,
      durationMs,
      tsNs: step.timestamp_ns,
      eventType: step.event_type ?? '',
      label: isSignal ? 'SSL/TLS 连接' : eventTypeLabel(step.event_type ?? '').label,
      kind: isSignal ? ('network' as TraceEventKind) : kind,
      description: step.description ?? '',
      comm,
      realComm,
      agentId,
      pid,
      exitCode: code,
      resource: step.resource ?? null,
      evidenceRef: step.evidence_ref ?? null,
      evidenceLevel: step.evidence_level ?? null,
      confidence: linked?.confidence ?? null,
      matchedPath: linked?.matched_fields.canonical_path ?? null,
      matchedCommand: linked?.matched_fields.command ?? null,
      matchedHost:
        linked?.matched_fields.host != null
          ? `${linked.matched_fields.host}${linked.matched_fields.port != null ? `:${linked.matched_fields.port}` : ''}`
          : null,
      rawArgs: linked?.matched_fields.raw_args ?? [],
      ruleIds: step.triggering_rule_ids ?? [],
      patternId: step.triggering_pattern_id ?? null,
      isBoundary,
      severity,
      isCritical,
      hasRules,
      aggregated: isSignal ? foldedSignals : 0,
      sourceEventId: step.source_event_id ?? null,
      actions: realActions,
    };
  });

  return {
    events,
    rawCount,
    foldedSignals,
    totalMs,
    startWallMs: Math.floor(startNs / T),
  };
}
