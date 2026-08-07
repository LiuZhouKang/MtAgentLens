import type { DatasetOverview } from './types'

export interface KpiTile {
  key: string
  label: string
  value: number
  hint: string
  tone: 'danger' | 'warning' | 'info' | 'success'
}

export interface FrictionSignal {
  severity: 'Critical' | 'Warning' | 'Info'
  category: string
  title: string
  detail: string
  count: number
}

export interface DatasetSummary {
  health: 'critical' | 'warning' | 'normal'
  healthReason: string
  identity: { scenarioId: string | null; mode: string | null; collectorVersion: string | null }
  timeRange: { startedAtNs: number | null; endedAtNs: number | null; durationMs: number | null }
  kpi: KpiTile[]
  signals: FrictionSignal[]
  statCards: { category: string; items: { label: string; value: number }[] }[]
}

const sumValues = (record: Record<string, number>): number =>
  Object.values(record).reduce((sum, n) => sum + n, 0)

const formatBreakdown = (record: Record<string, number>): string =>
  Object.entries(record)
    .sort((a, b) => b[1] - a[1])
    .map(([k, n]) => `${k} ${n}`)
    .join(' · ')

/**
 * Derive the overview summary strictly from the dataset overview. Nothing is
 * invented: a panel that cannot be computed from the counts is left empty.
 */
export function deriveDatasetSummary(overview: DatasetOverview): DatasetSummary {
  const criticalClaims = overview.claims.by_severity.Critical ?? 0
  const criticalAlerts = overview.alerts.count // alerts 表无 severity 分布 → 保守按 count
  const health: DatasetSummary['health'] =
    criticalClaims > 0 ? 'critical' : criticalAlerts > 0 ? 'warning' : 'normal'
  const healthReason =
    criticalClaims > 0
      ? `${criticalClaims} 条 Critical 责任声明需关注`
      : criticalAlerts > 0
        ? `${criticalAlerts} 条告警需关注`
        : '无风险信号'

  const fileActivity = sumValues(
    Object.fromEntries(
      Object.entries(overview.actions.by_action_type).filter(([k]) => k.startsWith('File')),
    ),
  )

  const kpi: KpiTile[] = [
    {
      key: 'processes',
      label: '进程',
      value: overview.processes.count,
      hint: '',
      tone: 'info',
    },
    {
      key: 'fileActivity',
      label: '文件活动',
      value: fileActivity,
      hint: formatBreakdown(overview.actions.by_action_type),
      tone: 'info',
    },
    {
      key: 'tools',
      label: '工具调用',
      value: overview.intents.count,
      hint: formatBreakdown(overview.intents.by_tool_name),
      tone: 'info',
    },
    {
      key: 'network',
      label: '网络',
      value: overview.tls_sessions.count,
      hint: '',
      tone: 'info',
    },
    {
      key: 'claims',
      label: '责任声明',
      value: overview.claims.count,
      hint: formatBreakdown(overview.claims.by_severity),
      tone: criticalClaims > 0 ? 'danger' : 'info',
    },
    {
      key: 'alerts',
      label: '告警',
      value: overview.alerts.count,
      hint: `Warning ${overview.alerts.count}`,
      tone: criticalAlerts > 0 ? 'danger' : 'info',
    },
  ]

  const signals: FrictionSignal[] = []
  if (overview.pollution.count > 0) {
    signals.push({
      severity: 'Critical',
      category: 'context-pollution',
      title: '上下文污染事件',
      detail: '被污染的上下文进入后续请求上下文',
      count: overview.pollution.count,
    })
  }
  if (criticalClaims > 0) {
    signals.push({
      severity: 'Warning',
      category: 'critical-claims',
      title: 'Critical 责任声明',
      detail: formatBreakdown(overview.claims.by_type),
      count: criticalClaims,
    })
  }
  const weakCount = overview.evidence.by_match_level.L5Substring ?? 0
  if (weakCount > 0) {
    const pct = overview.evidence.count > 0 ? (weakCount / overview.evidence.count) * 100 : 0
    signals.push({
      severity: 'Info',
      category: 'weak-evidence',
      title: '弱匹配证据',
      detail: `L5 弱匹配 ${pct.toFixed(1)}%（${weakCount}/${overview.evidence.count}）`,
      count: weakCount,
    })
  }
  if (overview.policy_evaluations.count > 0) {
    signals.push({
      severity: 'Warning',
      category: 'policy-violations',
      title: '策略评估',
      detail: 'judgment_json.result = Violation',
      count: overview.policy_evaluations.count,
    })
  }

  const statCards: DatasetSummary['statCards'] = [
    {
      category: '图',
      items: [
        { label: '快照帧', value: overview.snapshots.count },
        { label: '节点', value: overview.graph.nodes },
        { label: '边', value: overview.graph.edges },
      ],
    },
    {
      category: '责任',
      items: [
        { label: '告警', value: overview.alerts.count },
        { label: '声明', value: overview.claims.count },
        { label: '回溯', value: overview.tracebacks.count },
      ],
    },
    {
      category: '意图动作',
      items: [
        { label: '意图', value: overview.intents.count },
        { label: '动作', value: overview.actions.count },
        { label: '证据', value: overview.evidence.count },
      ],
    },
    {
      category: '上下文',
      items: [
        { label: '污染', value: overview.pollution.count },
        { label: '策略评估', value: overview.policy_evaluations.count },
      ],
    },
    {
      category: '运行时',
      items: [
        { label: '进程', value: overview.processes.count },
        { label: 'TLS', value: overview.tls_sessions.count },
        { label: '身份映射', value: overview.identity_process_map.count },
        { label: '身份', value: overview.provisional_identities.count },
      ],
    },
  ]

  const run = overview.run
  const durationMs =
    run?.started_at_ns != null && run.ended_at_ns != null
      ? Math.max(0, (run.ended_at_ns - run.started_at_ns) / 1_000_000)
      : null

  return {
    health,
    healthReason,
    identity: {
      scenarioId: run?.scenario_id ?? null,
      mode: run?.mode ?? null,
      collectorVersion: run?.collector_version ?? null,
    },
    timeRange: {
      startedAtNs: run?.started_at_ns ?? null,
      endedAtNs: run?.ended_at_ns ?? null,
      durationMs,
    },
    kpi,
    signals,
    statCards,
  }
}
