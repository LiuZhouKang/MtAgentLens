'use client';

import type {
  PerspectiveKey,
  MonitoringState,
  PerspectiveHealth,
} from '@/lib/monitoring/monitoring-types';
import { StatusBadge, type StatusTone } from '@/components/ui/linear/status-badge';
import { TrendChart } from '@/components/ui/linear/trend-chart';
import { PERSPECTIVE_META } from './perspective-config';

export function statusToTone(h: PerspectiveHealth): StatusTone {
  if (!h.available) return 'muted';
  return h.status === 'critical' ? 'danger' : h.status === 'warning' ? 'warning' : 'success';
}

/** 只有存在时间序列的视角提供迷你趋势；其余为 null（诚实，不造曲线）。 */
export function cardTrend(
  key: PerspectiveKey,
  monitoring: MonitoringState,
): Array<Record<string, number | string>> | null {
  if (key === 'topologyDrift') {
    const frames = monitoring.topologyDrift?.frames ?? [];
    return frames.map((f, i) => ({ name: String(i + 1), value: f.nodeCount }));
  }
  if (key === 'networkSessions') {
    const trend = monitoring.networkSessions?.trend ?? [];
    return trend.map((b, i) => ({ name: String(i + 1), value: b.count }));
  }
  return null;
}

type Props = {
  perspectiveKey: PerspectiveKey;
  health: PerspectiveHealth;
  monitoring: MonitoringState;
  onOpen: () => void;
};

export function MonitoringCard({ perspectiveKey, health, monitoring, onOpen }: Props) {
  const meta = PERSPECTIVE_META[perspectiveKey];
  const trend = cardTrend(perspectiveKey, monitoring);
  return (
    <button
      type="button"
      onClick={onOpen}
      aria-label={`${meta.label}：${health.available ? health.status : '无此表'}${health.score != null ? `，${health.score} 分` : ''}`}
      className="ln-panel"
      style={{
        textAlign: 'left',
        display: 'flex',
        flexDirection: 'column',
        gap: 10,
        padding: 14,
        cursor: 'pointer',
        width: '100%',
      }}
    >
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <span style={{ fontWeight: 600, fontSize: 13 }}>{meta.label}</span>
        <StatusBadge tone={statusToTone(health)}>
          {health.available
            ? health.status === 'critical'
              ? '严重'
              : health.status === 'warning'
                ? '关注'
                : '正常'
            : '无此表'}
        </StatusBadge>
      </div>
      {health.score != null ? (
        <div style={{ fontSize: 24, fontWeight: 700, fontVariantNumeric: 'tabular-nums' }}>
          {health.score}
        </div>
      ) : (
        <div style={{ fontSize: 24, fontWeight: 700, color: 'var(--ln-text-muted)' }}>—</div>
      )}
      {trend && trend.length > 1 ? (
        <TrendChart type="area" data={trend} xKey="name" seriesKey="value" height={64} />
      ) : null}
      <ul
        style={{
          display: 'flex',
          flexDirection: 'column',
          gap: 2,
          margin: 0,
          padding: 0,
          listStyle: 'none',
        }}
      >
        {health.signals.map((s, i) => (
          <li key={i} className="ln-metric__label" style={{ fontSize: 11 }}>
            {s}
          </li>
        ))}
      </ul>
    </button>
  );
}
