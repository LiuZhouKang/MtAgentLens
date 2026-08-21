'use client';

import { DigitReel } from '@/components/motion-primitives/digit-reel';
import type {
  PerspectiveKey,
  MonitoringState,
  PerspectiveHealth,
} from '@/lib/monitoring/monitoring-types';
import { derivePerspectiveMetric } from '@/lib/monitoring/monitoring-metrics';
import { PERSPECTIVE_META } from './perspective-config';
import { Sparkline } from './sparkline';

/** 状态强调色 —— 统一走全站 ln token（danger/warning/success/muted），不硬编码。 */
export function statusColor(h: PerspectiveHealth): string {
  if (!h.available) return 'var(--ln-text-muted)';
  return h.status === 'critical'
    ? 'var(--ln-danger)'
    : h.status === 'warning'
      ? 'var(--ln-warning)'
      : 'var(--ln-success)';
}

/** 状态词（与 derive-monitoring 的词表一致） */
export function statusLabel(h: PerspectiveHealth): string {
  if (!h.available) return '无此表';
  return h.status === 'critical' ? '严重' : h.status === 'warning' ? '关注' : '正常';
}

type Props = {
  perspectiveKey: PerspectiveKey;
  health: PerspectiveHealth;
  monitoring: MonitoringState;
  onOpen: () => void;
};

/**
 * 监控卡 —— 与页面 KPI 卡（.ln-metric）完全同构：同一 chrome、同一 mono 数字、
 * 同一 delta 语言。无装饰堆砌：状态只用一个色点 + 状态色 delta，
 * 数字用真实派生值，趋势用真实 sparkline。hover 轻抬由页面 .ln-metric GSAP lift 统一。
 */
export function MonitoringCard({ perspectiveKey, health, monitoring, onOpen }: Props) {
  const metric = derivePerspectiveMetric(perspectiveKey, monitoring);
  const accent = statusColor(health);
  const trendData =
    metric.trend
      ?.map((t) => ({
        name: t.name as string | number,
        value: Number(t[metric.trendKey ?? 'value'] ?? 0),
      }))
      .filter((t) => Number.isFinite(t.value)) ?? [];
  const label = PERSPECTIVE_META[perspectiveKey].label;

  return (
    <button
      type="button"
      onClick={onOpen}
      aria-label={`${label}：${statusLabel(health)}${metric.value != null ? `，${metric.value}${metric.unit ?? ''}` : ''}`}
      className="ln-panel ln-metric ln-monitor-card"
      data-status={health.available ? health.status : 'muted'}
    >
      {/* 头部：视角名 + 状态点/词 */}
      <span className="ln-monitor-card__head">
        <span className="ln-monitor-card__name">{label}</span>
        <span className="ln-monitor-card__status">
          <i aria-hidden className="ln-monitor-card__dot" style={{ background: accent }} />
          {statusLabel(health)}
        </span>
      </span>

      {/* 主数字 —— 顶级翻牌机计数器（DigitReel 位滚动；SSR/reduced-motion 直接终值） */}
      <span className="ln-monitor-card__metric">
        {typeof metric.value === 'number' ? (
          <DigitReel value={metric.value} className="ln-monitor-card__value" />
        ) : (
          <strong className="ln-monitor-card__value">{metric.value ?? '—'}</strong>
        )}
        {metric.unit ? <span className="ln-monitor-card__unit">{metric.unit}</span> : null}
      </span>

      {/* 真实迷你趋势 —— 交互式无轴 sparkline（GSAP 绘制 + hover 圆点跟随；有 ≥2 点才画） */}
      {trendData.length >= 2 ? (
        <span className="ln-monitor-card__trend">
          <Sparkline points={trendData} color={accent} />
        </span>
      ) : null}

      {/* 信号行 —— 状态色 delta（同 .ln-metric__delta 语言） */}
      {health.signals.length > 0 ? (
        <span className="ln-monitor-card__signals">{health.signals[0]}</span>
      ) : null}
    </button>
  );
}
