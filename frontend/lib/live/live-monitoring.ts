// 直播运行监控派生——从 API 增量数据（snapshots/alerts/evidence）构造
// `MonitoringDeriveInput` 并复用纯函数 `deriveMonitoringState` 产出 6 视角。
//
// 与 db 导入版共享同一派生入口；API 拿不到的视角（pollution/policy/tls）
// 诚实标 unavailable（缺表视角 UI 空态兜底），不造数。

import { deriveMonitoringState } from '../monitoring/derive-monitoring';
import type {
  MismatchExtract,
  SnapshotMetricsExtract,
} from '../monitoring/monitoring-types';
import type {
  Action,
  ApiNonLinkDiagnostic,
  GraphSnapshot,
  Intent,
  ResponsibilityAlert,
} from '../types';

const sumValues = (m: Record<string, number>): number =>
  Object.values(m).reduce((a, b) => a + (b ?? 0), 0);

/** API 快照数组 → SnapshotMetricsExtract（复用 db 版的 SnapshotMetricFrame 映射）。 */
export function apiSnapshotsToMetricExtract(snapshots: GraphSnapshot[]): SnapshotMetricsExtract {
  const frames = snapshots.map((s) => ({
    snapshot_id: s.snapshot_id,
    timestamp_ns: s.timestamp_ns,
    nodeCount: sumValues(s.metrics?.node_counts ?? {}),
    edgeCount: sumValues(s.metrics?.edge_counts ?? {}),
    agentCount: Number(s.metrics?.agent_count ?? 0),
    crossAgentEdges: Number(s.metrics?.cross_agent_edges ?? 0),
    delegationDepth: Number(s.metrics?.delegation_depth ?? 0),
    taintChains: Number(s.metrics?.taint_chains ?? 0),
    avgConfidence: Number(s.metrics?.avg_confidence ?? 0),
  }));
  return { available: snapshots.length > 0, frames };
}

export interface LiveMonitoringInput {
  snapshots: GraphSnapshot[];
  alerts: ResponsibilityAlert[];
  intents: Intent[];
  actions: Action[];
  nonLinks: ApiNonLinkDiagnostic[];
}

/** 直播运行监控派生入口（LiveSession 周期调用）。 */
export function deriveLiveMonitoring(input: LiveMonitoringInput) {
  const { snapshots, alerts, intents, actions, nonLinks } = input;

  // 意图-动作失配：non_link_diagnostics 经 /api/runs/{id}/evidence 暴露
  const byReason: Record<string, number> = {};
  let weak = 0;
  let strong = 0;
  for (const d of nonLinks) {
    byReason[d.reason] = (byReason[d.reason] ?? 0) + 1;
    if (d.match_level_considered === 'L5Substring') weak += 1;
    else strong += 1;
  }
  const mismatch: MismatchExtract = nonLinks.length
    ? { available: true, count: nonLinks.length, byReason, weak, strong }
    : { available: false, count: 0, byReason: {}, weak: 0, strong: 0 };

  return deriveMonitoringState({
    sourceLabel: '实时运行',
    derivedAtMs: Date.now(),
    snapshots: apiSnapshotsToMetricExtract(snapshots),
    alerts,
    // 以下视角无直播 API 增量来源 → 诚实 unavailable（缺表视角 UI 空态）
    pollution: { available: false, count: 0, bySource: {} },
    mismatch,
    policy: { available: false, total: 0, violations: 0, byBoundary: {} },
    tls: { available: false, total: 0, byPid: {}, trend: [] },
    intents,
    actions,
  });
}
