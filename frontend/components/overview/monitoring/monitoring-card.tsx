'use client';

import type { ComponentType } from 'react';
import {
  Crosshair,
  GitBranch,
  HeartPulse,
  Network,
  ShieldCheck,
  Wrench,
} from 'lucide-react';
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

/** 状态强调色（卡片顶部色条）：critical 红 / warning 黄 / normal 绿 / 缺表灰。 */
export function statusColor(h: PerspectiveHealth): string {
  if (!h.available) return '#5c5c5f';
  return h.status === 'critical' ? '#f87171' : h.status === 'warning' ? '#fbbf24' : '#4ade80';
}

/** 每视角专属图标——顶级监控卡的可识别性核心。 */
const PERSPECTIVE_ICON: Record<PerspectiveKey, ComponentType<{ size?: number; strokeWidth?: number }>> = {
  topologyDrift: GitBranch,
  topologyHealth: HeartPulse,
  intentMismatch: Crosshair,
  policyCompliance: ShieldCheck,
  toolFootprint: Wrench,
  networkSessions: Network,
};

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
  const accent = statusColor(health);
  const Icon = PERSPECTIVE_ICON[perspectiveKey];
  const score = health.score;

  return (
    <button
      type="button"
      onClick={onOpen}
      aria-label={`${meta.label}：${health.available ? health.status : '无此表'}${score != null ? `，${score} 分` : ''}`}
      className="ln-panel"
      style={{
        position: 'relative',
        textAlign: 'left',
        display: 'flex',
        flexDirection: 'column',
        gap: 8,
        padding: '14px 14px 12px',
        cursor: 'pointer',
        width: '100%',
        overflow: 'hidden',
        // 顶级悬停动效：轻抬 + 边框提亮 + 状态色光晕（纯 CSS，无布局抖动）
        transition: 'transform 160ms ease, border-color 160ms ease, box-shadow 160ms ease',
      }}
      onMouseEnter={(e) => {
        e.currentTarget.style.transform = 'translateY(-2px)';
        e.currentTarget.style.borderColor = `${accent}55`;
        e.currentTarget.style.boxShadow = `0 4px 18px rgba(0,0,0,0.35), 0 0 0 1px ${accent}22`;
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.transform = '';
        e.currentTarget.style.borderColor = '';
        e.currentTarget.style.boxShadow = '';
      }}
    >
      {/* 状态色强调条 */}
      <span
        aria-hidden
        style={{
          position: 'absolute',
          top: 0,
          left: 0,
          right: 0,
          height: 2,
          background: accent,
          opacity: health.available ? 0.85 : 0.3,
        }}
      />

      {/* 图标 + 标签 + 状态徽章 */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <span
          aria-hidden
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            width: 24,
            height: 24,
            borderRadius: 7,
            background: `${accent}18`,
            color: accent,
            flexShrink: 0,
          }}
        >
          <Icon size={14} strokeWidth={2} />
        </span>
        <span style={{ fontWeight: 600, fontSize: 13, flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {meta.label}
        </span>
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

      {/* 评分 + gauge */}
      {score != null ? (
        <>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 6 }}>
            <strong
              style={{
                fontSize: 24,
                fontWeight: 700,
                fontVariantNumeric: 'tabular-nums',
                color: 'var(--ln-text)',
                letterSpacing: '-0.02em',
              }}
            >
              {score}
            </strong>
            <span className="ln-metric__label">分</span>
          </div>
          <div
            aria-hidden
            style={{
              height: 3,
              borderRadius: 999,
              background: 'var(--ln-surface-3)',
              overflow: 'hidden',
            }}
          >
            <div
              style={{
                height: '100%',
                width: `${Math.min(100, score)}%`,
                background: accent,
                borderRadius: 999,
                transition: 'width 600ms cubic-bezier(0.22, 1, 0.36, 1)',
              }}
            />
          </div>
        </>
      ) : (
        <div style={{ fontSize: 24, fontWeight: 700, color: 'var(--ln-text-muted)' }}>—</div>
      )}

      {trend && trend.length > 1 ? (
        <TrendChart type="area" data={trend} xKey="name" seriesKey="value" height={56} />
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
