'use client';

import type { ReactNode } from 'react';
import type {
  PerspectiveKey,
  MonitoringState,
  SnapshotMetricFrame,
} from '@/lib/monitoring/monitoring-types';
import { TrendChart } from '@/components/ui/linear/trend-chart';
import VirtualList from '@/components/ui/virtual-list';
import { StatNumber } from '@/components/motion-primitives/stat-number';
import {
  actionTypeLabel,
  boundaryLabel,
  reasonLabel,
  signalClassLabel,
  toolLabel,
} from '@/lib/monitoring/perspective-labels';

// ── 共享小件 ──

/** 统计格：label + 数字（StatNumber → 整数走 DigitReel 翻牌，字符串/小数静态） */
function StatCell({ label, value }: { label: string; value: number | string }) {
  return (
    <div className="ln-panel ln-detail-block" style={{ padding: 12 }}>
      <div className="ln-metric__label">{label}</div>
      <div style={{ fontSize: 24, fontWeight: 700, fontVariantNumeric: 'tabular-nums', marginTop: 4 }}>
        <StatNumber value={value} />
      </div>
    </div>
  );
}

function ChartCard({
  eyebrow,
  title,
  children,
}: {
  eyebrow: string;
  title: string;
  children: ReactNode;
}) {
  return (
    <div
      className="ln-panel ln-detail-block"
      style={{ padding: 14, display: 'flex', flexDirection: 'column', gap: 10 }}
    >
      <span className="ln-eyebrow">{eyebrow}</span>
      <h4 style={{ margin: 0, fontSize: 13, fontWeight: 600 }}>{title}</h4>
      {children}
    </div>
  );
}

function DistributionRows({
  record,
  formatter,
  labelMap,
}: {
  record: Record<string, number>;
  /** 保留格式化（如路径缩写）；不传则原样显示 */
  formatter?: (k: string) => string;
  /** 专业中文标签映射：raw → { label, desc? }；行内显示 label，title 显示 desc */
  labelMap?: (raw: string) => { label: string; desc?: string };
}) {
  const entries = Object.entries(record)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 10);
  const max = Math.max(1, ...entries.map(([, n]) => n));
  if (entries.length === 0) return <div className="ln-metric__label">无数据</div>;
  return (
    <ul
      style={{
        listStyle: 'none',
        margin: 0,
        padding: 0,
        display: 'flex',
        flexDirection: 'column',
        gap: 4,
      }}
    >
      {entries.map(([k, n]) => {
        const mapped = labelMap ? labelMap(k) : null;
        const label = mapped?.label ?? (formatter ? formatter(k) : k);
        const desc = mapped?.desc ?? k;
        return (
        <li
          key={k}
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 10,
            fontSize: 12,
            padding: '3px 6px',
            borderRadius: 6,
            transition: 'background 140ms ease',
          }}
          onMouseEnter={(e) => {
            e.currentTarget.style.background = 'var(--ln-surface-2)';
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.background = 'transparent';
          }}
        >
          <span
            style={{
              width: 110,
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
              color: 'var(--ln-text-secondary)',
            }}
            title={desc}
          >
            {label}
          </span>
          <span
            style={{ flex: 1, height: 6, borderRadius: 999, background: 'var(--ln-surface-3)' }}
          >
            <span
              style={{
                display: 'block',
                height: '100%',
                width: `${Math.round((n / max) * 100)}%`,
                borderRadius: 999,
                background:
                  'linear-gradient(90deg, var(--ln-accent), rgba(94, 106, 210, 0.35))',
              }}
            />
          </span>
          <span
            className="ln-metric__label"
            style={{ fontVariantNumeric: 'tabular-nums', minWidth: 28, textAlign: 'right' }}
          >
            {n}
          </span>
        </li>
        );
      })}
    </ul>
  );
}

function frameTrend(frames: SnapshotMetricFrame[]) {
  return frames.map((f, i) => ({
    name: String(i + 1),
    node: f.nodeCount,
    edge: f.edgeCount,
    agent: f.agentCount,
  }));
}

// ── 六个视图 ──

function TopologyDriftView({ monitoring }: { monitoring: MonitoringState }) {
  const s = monitoring.topologyDrift;
  if (!s) return <EmptyView label="拓扑漂移" />;
  const sampled =
    s.frames.length > 50
      ? s.frames.filter((_, i) => i % Math.ceil(s.frames.length / 50) === 0)
      : s.frames;
  return (
    <div style={{ display: 'grid', gap: 10 }}>
      <ChartCard eyebrow="Frames" title="节点/边/Agent 随快照帧演进">
        <TrendChart
          type="area"
          data={frameTrend(sampled)}
          xKey="name"
          seriesKey="node"
          height={180}
        />
      </ChartCard>
      {s.delta ? (
        <div style={{ display: 'flex', gap: 8 }}>
          {(
            [
              ['Δ节点', s.delta.nodes],
              ['Δ边', s.delta.edges],
              ['ΔAgent', s.delta.agents],
            ] as const
          ).map(([label, v]) => (
            <span key={label} className="ln-badge" data-tone={v !== 0 ? 'warning' : 'muted'}>
              {label} {v >= 0 ? '+' : ''}
              {v}
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
  );
}

function TopologyHealthView({
  monitoring,
  onFocusAgent,
}: {
  monitoring: MonitoringState;
  onFocusAgent: (id: string) => void;
}) {
  const s = monitoring.topologyHealth;
  if (!s) return <EmptyView label="拓扑健康" />;
  const sev = s.alertSeverity;
  const sevData = [
    { name: '严重', value: sev.critical },
    { name: '关注', value: sev.warning },
    { name: '信息', value: sev.info },
  ];
  return (
    <div style={{ display: 'grid', gap: 10 }}>
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))',
          gap: 8,
        }}
      >
        {[
          ['Agent', s.latest?.agentCount ?? '—'],
          ['跨 Agent 边', s.latest?.crossAgentEdges ?? '—'],
          ['污染链', s.latest?.taintChains ?? '—'],
          ['平均置信', s.latest ? s.latest.avgConfidence.toFixed(2) : '—'],
        ].map(([label, value]) => (
          <StatCell key={label as string} label={label as string} value={value as number | string} />
        ))}
      </div>
      <ChartCard eyebrow="Alerts" title="告警严重度">
        <TrendChart type="bar" data={sevData} xKey="name" seriesKey="value" height={120} />
      </ChartCard>
      <ChartCard eyebrow="Pollution" title="上下文污染来源（点击聚焦 Agent）">
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
          {Object.entries(s.pollutionBySource).map(([agentId, count]) => (
            <button
              key={agentId}
              type="button"
              onClick={() => onFocusAgent(agentId)}
              className="ln-btn"
              title="聚焦该 agent 的告警"
            >
              {agentId.slice(0, 8)}… × {count}
            </button>
          ))}
          {s.pollutionCount === 0 ? <span className="ln-metric__label">无污染事件</span> : null}
        </div>
      </ChartCard>
    </div>
  );
}

function IntentMismatchView({ monitoring }: { monitoring: MonitoringState }) {
  const s = monitoring.intentMismatch;
  if (!s) return <EmptyView label="意图-动作失配" />;
  const matchData = [
    { name: '强证据', value: s.evidenceMatch.strong },
    { name: '弱证据 L5', value: s.evidenceMatch.weak },
  ];
  return (
    <div style={{ display: 'grid', gap: 10 }}>
      <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap' }}>
        <StatCell label="失配诊断" value={s.diagnosticsCount} />
        <StatCell label="失配密度" value={s.mismatchDensity?.toFixed(1) ?? '—'} />
        <StatCell
          label="弱证据率"
          value={
            s.evidenceMatch.total > 0
              ? `${Math.round((s.evidenceMatch.weak / s.evidenceMatch.total) * 100)}%`
              : '—'
          }
        />
      </div>
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))',
          gap: 10,
        }}
      >
        <ChartCard eyebrow="Reason" title="诊断原因分布">
          <DistributionRows record={s.byReason} labelMap={reasonLabel} />
        </ChartCard>
        <ChartCard eyebrow="Evidence" title="证据匹配强度">
          <TrendChart type="bar" data={matchData} xKey="name" seriesKey="value" height={140} />
        </ChartCard>
      </div>
    </div>
  );
}

function PolicyComplianceView({ monitoring }: { monitoring: MonitoringState }) {
  const s = monitoring.policyCompliance;
  if (!s) return <EmptyView label="策略合规" />;
  return (
    <div style={{ display: 'grid', gap: 10 }}>
      <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap' }}>
        <StatCell
          label="违规率"
          value={s.violationRatio != null ? `${(s.violationRatio * 100).toFixed(1)}%` : '—'}
        />
        <StatCell label="违规 / 评估" value={`${s.violations} / ${s.evaluationsTotal}`} />
      </div>
      <ChartCard eyebrow="Boundary" title="边界分布">
        <DistributionRows record={s.byBoundary} labelMap={boundaryLabel} />
      </ChartCard>
    </div>
  );
}

function ToolFootprintView({ monitoring }: { monitoring: MonitoringState }) {
  const s = monitoring.toolFootprint;
  if (!s) return <EmptyView label="工具足迹" />;
  return (
    <div
      style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))',
        gap: 10,
      }}
    >
      <ChartCard eyebrow="Tools" title="工具调用分布">
        <DistributionRows record={s.byTool} labelMap={toolLabel} />
      </ChartCard>
      <ChartCard eyebrow="Actions" title="动作类型分布">
        <DistributionRows record={s.byActionType} labelMap={actionTypeLabel} />
      </ChartCard>
      <ChartCard eyebrow="Signals" title="信号类">
        <DistributionRows record={s.bySignalClass} labelMap={signalClassLabel} />
      </ChartCard>
      <ChartCard eyebrow="Files" title="Top 文件路径">
        <DistributionRows
          record={Object.fromEntries(s.topFiles.map((f) => [f.path, f.count]))}
          formatter={(p) => p.replace(/^.*\/([^/]+)$/, '…/$1')}
        />
      </ChartCard>
    </div>
  );
}

/** 纳秒时间戳 → 本地 HH:MM（会话趋势 X 轴标签）。无效/越界回退序号。 */
function nsToClock(ns: number, fallback: string): string {
  if (!Number.isFinite(ns) || ns <= 0) return fallback;
  const d = new Date(Math.floor(ns / 1_000_000));
  if (Number.isNaN(d.getTime())) return fallback;
  return d.toLocaleTimeString('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  });
}

function NetworkSessionsView({ monitoring }: { monitoring: MonitoringState }) {
  const s = monitoring.networkSessions;
  if (!s) return <EmptyView label="网络会话" />;
  // 真实时间桶：X 轴用桶起始时间的 HH:MM（t 为纳秒桶起点），tooltip 也能看
  const trend = s.trend.map((b, i) => ({
    name: nsToClock(b.t, String(i + 1)),
    value: b.count,
  }));
  const range =
    s.trend.length >= 2
      ? `${nsToClock(s.trend[0]!.t, '—')} – ${nsToClock(s.trend[s.trend.length - 1]!.t, '—')}`
      : null;
  return (
    <div style={{ display: 'grid', gap: 10 }}>
      <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap' }}>
        <StatCell label="会话总数" value={s.total} />
        <StatCell label="进程数" value={Object.keys(s.byPid).length} />
      </div>
      <ChartCard eyebrow="Trend" title={`会话趋势${range ? ` · ${range}` : ''}`}>
        <TrendChart type="area" data={trend} xKey="name" seriesKey="value" height={160} />
      </ChartCard>
      <ChartCard eyebrow="Pids" title="按进程分布">
        <DistributionRows record={s.byPid} />
      </ChartCard>
    </div>
  );
}

function EmptyView({ label }: { label: string }) {
  return (
    <div className="ln-metric__label" style={{ padding: 16 }}>
      {label}：无此表（旧版数据）
    </div>
  );
}

// ── 统一出口 ──

export function renderPerspectiveView(
  key: PerspectiveKey,
  monitoring: MonitoringState,
  onFocusAgent: (agentId: string) => void,
): ReactNode {
  switch (key) {
    case 'topologyDrift':
      return <TopologyDriftView monitoring={monitoring} />;
    case 'topologyHealth':
      return <TopologyHealthView monitoring={monitoring} onFocusAgent={onFocusAgent} />;
    case 'intentMismatch':
      return <IntentMismatchView monitoring={monitoring} />;
    case 'policyCompliance':
      return <PolicyComplianceView monitoring={monitoring} />;
    case 'toolFootprint':
      return <ToolFootprintView monitoring={monitoring} />;
    case 'networkSessions':
      return <NetworkSessionsView monitoring={monitoring} />;
  }
}
