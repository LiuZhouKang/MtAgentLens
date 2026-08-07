'use client'

import type { ReactNode } from 'react'
import type { PerspectiveKey, MonitoringState, SnapshotMetricFrame } from '@/lib/monitoring/monitoring-types'
import { TrendChart } from '@/components/ui/linear/trend-chart'
import VirtualList from '@/components/ui/virtual-list'

// ── 共享小件 ──

function ChartCard({ eyebrow, title, children }: { eyebrow: string; title: string; children: ReactNode }) {
  return (
    <div className="ln-panel" style={{ padding: 12, display: 'flex', flexDirection: 'column', gap: 8 }}>
      <span className="ln-eyebrow">{eyebrow}</span>
      <h4 style={{ margin: 0, fontSize: 13 }}>{title}</h4>
      {children}
    </div>
  )
}

function DistributionRows({ record, formatter = (k) => k }: { record: Record<string, number>; formatter?: (k: string) => string }) {
  const entries = Object.entries(record).sort((a, b) => b[1] - a[1]).slice(0, 10)
  const max = Math.max(1, ...entries.map(([, n]) => n))
  if (entries.length === 0) return <div className="ln-metric__label">无数据</div>
  return (
    <ul style={{ listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: 6 }}>
      {entries.map(([k, n]) => (
        <li key={k} style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12 }}>
          <span style={{ width: 120, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={k}>
            {formatter(k)}
          </span>
          <span style={{ flex: 1, height: 6, borderRadius: 999, background: 'var(--ln-surface-3)' }}>
            <span style={{ display: 'block', height: '100%', width: `${Math.round((n / max) * 100)}%`, borderRadius: 999, background: 'var(--ln-accent)' }} />
          </span>
          <span className="ln-metric__label" style={{ fontVariantNumeric: 'tabular-nums' }}>{n}</span>
        </li>
      ))}
    </ul>
  )
}

function frameTrend(frames: SnapshotMetricFrame[]) {
  return frames.map((f, i) => ({ name: String(i + 1), node: f.nodeCount, edge: f.edgeCount, agent: f.agentCount }))
}

// ── 六个视图 ──

function TopologyDriftView({ monitoring }: { monitoring: MonitoringState }) {
  const s = monitoring.topologyDrift
  if (!s) return <EmptyView label="拓扑漂移" />
  const sampled = s.frames.length > 50 ? s.frames.filter((_, i) => i % Math.ceil(s.frames.length / 50) === 0) : s.frames
  return (
    <div style={{ display: 'grid', gap: 10 }}>
      <ChartCard eyebrow="Frames" title="节点/边/Agent 随快照帧演进">
        <TrendChart type="area" data={frameTrend(sampled)} xKey="name" seriesKey="node" height={180} />
      </ChartCard>
      {s.delta ? (
        <div style={{ display: 'flex', gap: 8 }}>
          {([
            ['Δ节点', s.delta.nodes],
            ['Δ边', s.delta.edges],
            ['ΔAgent', s.delta.agents],
          ] as const).map(([label, v]) => (
            <span key={label} className="ln-badge" data-tone={v !== 0 ? 'warning' : 'muted'}>
              {label} {v >= 0 ? '+' : ''}{v}
            </span>
          ))}
        </div>
      ) : null}
      {/* VirtualList 根容器是 height:100%，必须包在定高容器内否则塌缩为 0 */}
      <div style={{ height: 240 }} className="ln-panel" aria-label="快照帧明细">
        <VirtualList
          items={s.frames}
          estimateSize={30}
          className="scrollbar-none"
          renderRow={(f) => (
            <div style={{ display: 'flex', gap: 12, fontSize: 12, padding: '4px 8px' }}>
              <span className="ln-metric__label">{f.snapshot_id}</span>
              <span>节点 {f.nodeCount}</span>
              <span>边 {f.edgeCount}</span>
              <span>链 {f.taintChains}</span>
            </div>
          )}
        />
      </div>
    </div>
  )
}

function TopologyHealthView({ monitoring, onFocusAgent }: { monitoring: MonitoringState; onFocusAgent: (id: string) => void }) {
  const s = monitoring.topologyHealth
  if (!s) return <EmptyView label="拓扑健康" />
  const sev = s.alertSeverity
  const sevData = [
    { name: '严重', value: sev.critical },
    { name: '关注', value: sev.warning },
    { name: '信息', value: sev.info },
  ]
  return (
    <div style={{ display: 'grid', gap: 10 }}>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))', gap: 8 }}>
        {[
          ['Agent', s.latest?.agentCount ?? '—'],
          ['跨 Agent 边', s.latest?.crossAgentEdges ?? '—'],
          ['污染链', s.latest?.taintChains ?? '—'],
          ['平均置信', s.latest ? s.latest.avgConfidence.toFixed(2) : '—'],
        ].map(([label, value]) => (
          <div key={label as string} className="ln-panel" style={{ padding: 10 }}>
            <div className="ln-metric__label">{label}</div>
            <div style={{ fontSize: 18, fontWeight: 700, fontVariantNumeric: 'tabular-nums' }}>{value}</div>
          </div>
        ))}
      </div>
      <ChartCard eyebrow="Alerts" title="告警严重度">
        <TrendChart type="bar" data={sevData} xKey="name" seriesKey="value" height={120} />
      </ChartCard>
      <ChartCard eyebrow="Pollution" title="上下文污染来源（点击聚焦 Agent）">
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
          {Object.entries(s.pollutionBySource).map(([agentId, count]) => (
            <button key={agentId} type="button" onClick={() => onFocusAgent(agentId)} className="ln-btn" title="聚焦该 agent 的告警">
              {agentId.slice(0, 8)}… × {count}
            </button>
          ))}
          {s.pollutionCount === 0 ? <span className="ln-metric__label">无污染事件</span> : null}
        </div>
      </ChartCard>
    </div>
  )
}

function IntentMismatchView({ monitoring }: { monitoring: MonitoringState }) {
  const s = monitoring.intentMismatch
  if (!s) return <EmptyView label="意图-动作失配" />
  const matchData = [
    { name: '强证据', value: s.evidenceMatch.strong },
    { name: '弱证据 L5', value: s.evidenceMatch.weak },
  ]
  return (
    <div style={{ display: 'grid', gap: 10 }}>
      <div style={{ display: 'flex', gap: 16 }}>
        <div className="ln-panel" style={{ padding: 10 }}>
          <div className="ln-metric__label">失配诊断</div>
          <div style={{ fontSize: 22, fontWeight: 700, fontVariantNumeric: 'tabular-nums' }}>{s.diagnosticsCount}</div>
        </div>
        <div className="ln-panel" style={{ padding: 10 }}>
          <div className="ln-metric__label">失配密度</div>
          <div style={{ fontSize: 22, fontWeight: 700, fontVariantNumeric: 'tabular-nums' }}>{s.mismatchDensity?.toFixed(1) ?? '—'}</div>
        </div>
        <div className="ln-panel" style={{ padding: 10 }}>
          <div className="ln-metric__label">弱证据率</div>
          <div style={{ fontSize: 22, fontWeight: 700, fontVariantNumeric: 'tabular-nums' }}>
            {s.evidenceMatch.total > 0 ? `${Math.round((s.evidenceMatch.weak / s.evidenceMatch.total) * 100)}%` : '—'}
          </div>
        </div>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 10 }}>
        <ChartCard eyebrow="Reason" title="诊断原因分布">
          <DistributionRows record={s.byReason} />
        </ChartCard>
        <ChartCard eyebrow="Evidence" title="证据匹配强度">
          <TrendChart type="bar" data={matchData} xKey="name" seriesKey="value" height={140} />
        </ChartCard>
      </div>
    </div>
  )
}

function PolicyComplianceView({ monitoring }: { monitoring: MonitoringState }) {
  const s = monitoring.policyCompliance
  if (!s) return <EmptyView label="策略合规" />
  return (
    <div style={{ display: 'grid', gap: 10 }}>
      <div style={{ display: 'flex', gap: 16 }}>
        <div className="ln-panel" style={{ padding: 10 }}>
          <div className="ln-metric__label">违规率</div>
          <div style={{ fontSize: 22, fontWeight: 700, fontVariantNumeric: 'tabular-nums' }}>
            {s.violationRatio != null ? `${(s.violationRatio * 100).toFixed(1)}%` : '—'}
          </div>
        </div>
        <div className="ln-panel" style={{ padding: 10 }}>
          <div className="ln-metric__label">违规 / 评估</div>
          <div style={{ fontSize: 22, fontWeight: 700, fontVariantNumeric: 'tabular-nums' }}>{s.violations} / {s.evaluationsTotal}</div>
        </div>
      </div>
      <ChartCard eyebrow="Boundary" title="边界分布">
        <DistributionRows record={s.byBoundary} />
      </ChartCard>
    </div>
  )
}

function ToolFootprintView({ monitoring }: { monitoring: MonitoringState }) {
  const s = monitoring.toolFootprint
  if (!s) return <EmptyView label="工具足迹" />
  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 10 }}>
      <ChartCard eyebrow="Tools" title="工具调用分布">
        <DistributionRows record={s.byTool} />
      </ChartCard>
      <ChartCard eyebrow="Actions" title="动作类型分布">
        <DistributionRows record={s.byActionType} />
      </ChartCard>
      <ChartCard eyebrow="Signals" title="信号类">
        <DistributionRows record={s.bySignalClass} />
      </ChartCard>
      <ChartCard eyebrow="Files" title="Top 文件路径">
        <DistributionRows
          record={Object.fromEntries(s.topFiles.map((f) => [f.path, f.count]))}
          formatter={(p) => p.replace(/^.*\/([^/]+)$/, '…/$1')}
        />
      </ChartCard>
    </div>
  )
}

function NetworkSessionsView({ monitoring }: { monitoring: MonitoringState }) {
  const s = monitoring.networkSessions
  if (!s) return <EmptyView label="网络会话" />
  const trend = s.trend.map((b, i) => ({ name: String(i + 1), value: b.count }))
  return (
    <div style={{ display: 'grid', gap: 10 }}>
      <div style={{ display: 'flex', gap: 16 }}>
        <div className="ln-panel" style={{ padding: 10 }}>
          <div className="ln-metric__label">会话总数</div>
          <div style={{ fontSize: 22, fontWeight: 700, fontVariantNumeric: 'tabular-nums' }}>{s.total}</div>
        </div>
        <div className="ln-panel" style={{ padding: 10 }}>
          <div className="ln-metric__label">进程数</div>
          <div style={{ fontSize: 22, fontWeight: 700, fontVariantNumeric: 'tabular-nums' }}>{Object.keys(s.byPid).length}</div>
        </div>
      </div>
      <ChartCard eyebrow="Trend" title="60s 时间分桶">
        <TrendChart type="area" data={trend} xKey="name" seriesKey="value" height={160} />
      </ChartCard>
      <ChartCard eyebrow="Pids" title="按 PID 分布">
        <DistributionRows record={s.byPid} />
      </ChartCard>
    </div>
  )
}

function EmptyView({ label }: { label: string }) {
  return <div className="ln-metric__label" style={{ padding: 16 }}>{label}：无此表（旧版数据）</div>
}

// ── 统一出口 ──

export function renderPerspectiveView(
  key: PerspectiveKey,
  monitoring: MonitoringState,
  onFocusAgent: (agentId: string) => void,
): ReactNode {
  switch (key) {
    case 'topologyDrift':
      return <TopologyDriftView monitoring={monitoring} />
    case 'topologyHealth':
      return <TopologyHealthView monitoring={monitoring} onFocusAgent={onFocusAgent} />
    case 'intentMismatch':
      return <IntentMismatchView monitoring={monitoring} />
    case 'policyCompliance':
      return <PolicyComplianceView monitoring={monitoring} />
    case 'toolFootprint':
      return <ToolFootprintView monitoring={monitoring} />
    case 'networkSessions':
      return <NetworkSessionsView monitoring={monitoring} />
  }
}
