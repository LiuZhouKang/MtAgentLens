import type { Action, Intent, ResponsibilityAlert } from '../types'
import type {
  MonitoringState,
  MonitoringStatus,
  PerspectiveHealth,
  SnapshotMetricsExtract,
  MismatchExtract,
  PollutionExtract,
  PolicyExtract,
  TlsExtract,
  TopologyDriftState,
  TopologyHealthState,
  IntentMismatchState,
  PolicyComplianceState,
  ToolFootprintState,
  NetworkSessionsState,
} from './monitoring-types'

export interface MonitoringDeriveInput {
  sourceLabel: string
  derivedAtMs: number
  snapshots: SnapshotMetricsExtract
  alerts: ResponsibilityAlert[]
  pollution: PollutionExtract
  mismatch: MismatchExtract
  policy: PolicyExtract
  tls: TlsExtract
  intents: Intent[]
  actions: Action[]
}

const clampScore = (n: number): number => Math.max(0, Math.min(100, Math.round(n)))

/** 状态派生唯一入口：每个视角的状态/分数/信号由同一纯函数产出。 */
export function deriveMonitoringState(input: MonitoringDeriveInput): MonitoringState {
  const { sourceLabel, derivedAtMs, snapshots, alerts, pollution, mismatch, policy, tls, intents, actions } = input
  const frames = snapshots.frames

  // ── 拓扑漂移 ──
  const driftFirst = frames.find((f) => f.agentCount + f.nodeCount > 0) ?? null
  const driftLatest = frames.length > 0 ? frames[frames.length - 1] : null
  const driftDelta =
    driftFirst && driftLatest && driftFirst.snapshot_id !== driftLatest.snapshot_id
      ? {
          nodes: driftLatest.nodeCount - driftFirst.nodeCount,
          edges: driftLatest.edgeCount - driftFirst.edgeCount,
          agents: driftLatest.agentCount - driftFirst.agentCount,
        }
      : null
  const driftRatio =
    driftFirst && driftLatest
      ? Math.abs(driftLatest.nodeCount - driftFirst.nodeCount) / Math.max(1, driftFirst.nodeCount)
      : null
  const driftStatus: MonitoringStatus =
    driftRatio == null ? 'normal' : driftRatio >= 2 ? 'critical' : driftRatio >= 0.5 ? 'warning' : 'normal'
  const driftHealth: PerspectiveHealth = {
    available: snapshots.available,
    status: driftStatus,
    score: snapshots.available && driftRatio != null ? clampScore(driftRatio * 100) : null,
    signals: snapshots.available
      ? [
          `${frames.length} 帧`,
          driftLatest ? `${driftLatest.nodeCount} 节点` : '无帧',
          driftDelta ? `Δ${driftDelta.nodes >= 0 ? '+' : ''}${driftDelta.nodes} 节点` : '单帧无差分',
        ]
      : ['无快照表'],
  }
  const topologyDrift: TopologyDriftState = { frames, latest: driftLatest, delta: driftDelta }

  // ── 拓扑健康 ──
  const alertSeverity = { critical: 0, warning: 0, info: 0 }
  for (const a of alerts) {
    if (a.severity === 'Critical') alertSeverity.critical++
    else if (a.severity === 'Warning') alertSeverity.warning++
    else alertSeverity.info++
  }
  const healthStatus: MonitoringStatus =
    alertSeverity.critical > 0 ? 'critical' : pollution.count > 0 || alertSeverity.warning > 0 ? 'warning' : 'normal'
  const healthAvailable = snapshots.available || pollution.available
  const topologyHealth: TopologyHealthState = {
    latest: driftLatest,
    alertSeverity,
    pollutionCount: pollution.count,
    pollutionBySource: pollution.bySource,
  }
  const healthSignals = healthAvailable
    ? [
        `告警 ${alerts.length}（严重 ${alertSeverity.critical}）`,
        `污染 ${pollution.count}`,
        driftLatest ? `链 ${driftLatest.taintChains}` : '无最新帧',
      ]
    : ['无快照/污染表']

  // ── 意图-动作失配 ──
  const mismatchDensity = mismatch.available ? mismatch.count / Math.max(1, intents.length) : null
  const mismatchStatus: MonitoringStatus =
    !mismatch.available || intents.length === 0 || mismatchDensity == null
      ? 'normal'
      : mismatchDensity > 10
        ? 'warning'
        : 'normal'
  const intentMismatch: IntentMismatchState = {
    diagnosticsCount: mismatch.count,
    byReason: mismatch.byReason,
    intentsCount: intents.length,
    mismatchDensity,
    evidenceMatch: { weak: mismatch.weak, strong: mismatch.strong, total: mismatch.weak + mismatch.strong },
  }

  // ── 策略合规 ──
  const violationRatio = policy.available && policy.total > 0 ? policy.violations / policy.total : null
  const complianceStatus: MonitoringStatus =
    violationRatio == null ? 'normal' : violationRatio > 0.5 ? 'critical' : violationRatio > 0.05 ? 'warning' : 'normal'
  const policyCompliance: PolicyComplianceState = {
    evaluationsTotal: policy.total,
    violations: policy.violations,
    violationRatio,
    byBoundary: policy.byBoundary,
  }

  // ── 工具足迹（复用既有 intents/actions，available 恒 true）──
  const byTool: Record<string, number> = {}
  for (const i of intents) {
    const t = i.tool_name || '未知'
    byTool[t] = (byTool[t] ?? 0) + 1
  }
  const byActionType: Record<string, number> = {}
  const bySignalClass: Record<string, number> = {}
  const fileCounts = new Map<string, number>()
  for (const a of actions) {
    byActionType[a.action_type] = (byActionType[a.action_type] ?? 0) + 1
    bySignalClass[a.signal_class] = (bySignalClass[a.signal_class] ?? 0) + 1
    for (const arg of a.normalized_args ?? []) {
      if (arg && typeof arg === 'object' && 'Path' in arg) {
        const p = String((arg as { Path?: unknown }).Path ?? '')
        if (p) fileCounts.set(p, (fileCounts.get(p) ?? 0) + 1)
      }
    }
  }
  const topFiles = [...fileCounts.entries()]
    .map(([path, count]) => ({ path, count }))
    .sort((a, b) => b.count - a.count)
    .slice(0, 5)
  const toolFootprint: ToolFootprintState = {
    intentsCount: intents.length,
    actionsCount: actions.length,
    byTool,
    byActionType,
    bySignalClass,
    topFiles,
  }
  const footprintHasSignal = intents.length > 0 || actions.length > 0

  // ── 网络会话 ──
  const networkSessions: NetworkSessionsState = { total: tls.total, byPid: tls.byPid, trend: tls.trend }

  return {
    derivedAtMs,
    sourceLabel,
    health: {
      topologyDrift: driftHealth,
      topologyHealth: {
        available: healthAvailable,
        status: healthStatus,
        score: null, // 无自然比率（存在性视角）——诚实留空，靠 signals 展示原始计数
        signals: healthSignals,
      },
      intentMismatch: {
        available: mismatch.available,
        status: mismatchStatus,
        score: mismatchDensity != null ? clampScore(mismatchDensity * 10) : null,
        signals: mismatch.available
          ? [
              `失配 ${mismatch.count}`,
              mismatchDensity != null ? `密度 ${mismatchDensity.toFixed(1)}` : '无意图',
              `弱证据 ${mismatch.weak}/${mismatch.weak + mismatch.strong}`,
            ]
          : ['无失配表'],
      },
      policyCompliance: {
        available: policy.available,
        status: complianceStatus,
        score: violationRatio != null ? clampScore(violationRatio * 100) : null,
        signals: policy.available
          ? [
              `违规 ${policy.violations}/${policy.total}`,
              violationRatio != null ? `率 ${(violationRatio * 100).toFixed(1)}%` : '无评估',
            ]
          : ['无策略评估表'],
      },
      toolFootprint: {
        available: true,
        status: footprintHasSignal ? 'normal' : 'normal',
        score: null,
        signals: footprintHasSignal
          ? [`工具 ${intents.length}`, `动作 ${actions.length}`, `文件 ${topFiles.length}`]
          : ['无意图/动作'],
      },
      networkSessions: {
        available: tls.available,
        status: tls.total > 0 ? 'normal' : 'normal',
        score: null,
        signals: tls.available ? [`会话 ${tls.total}`, `进程 ${Object.keys(tls.byPid).length}`] : ['无 TLS 会话表'],
      },
    },
    // 详情 state 与 available 一致：缺表视角 = null（类型契约 `| null`，测试断言）
    topologyDrift: snapshots.available ? topologyDrift : null,
    topologyHealth: healthAvailable ? topologyHealth : null,
    intentMismatch: mismatch.available ? intentMismatch : null,
    policyCompliance: policy.available ? policyCompliance : null,
    toolFootprint,
    networkSessions: tls.available ? networkSessions : null,
  }
}
