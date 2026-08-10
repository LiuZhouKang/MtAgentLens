'use client';

import { useRouter } from 'next/navigation';
import { useAppStore } from '@/lib/store';
import type { LiveConnectionStatus } from '@/lib/live/live-types';

const STATUS_META: Record<
  LiveConnectionStatus,
  { label: string; color: string; pulse: boolean }
> = {
  // idle 文案对比度须 ≥4.5:1（#9ca3af on #010102 ≈ 7:1；原 #6b7280 仅 4.32:1，审查 M3）
  idle: { label: '实时未启用', color: '#9ca3af', pulse: false },
  connecting: { label: '连接中', color: '#fbbf24', pulse: true },
  connected: { label: '实时', color: '#4ade80', pulse: true },
  reconnecting: { label: '重连中', color: '#fbbf24', pulse: true },
  disconnected: { label: '实时断开', color: '#f87171', pulse: false },
};

/** 运行时长格式：`1m23s`；无起始时间返回空。 */
export function formatRuntime(startedAtNs: number | null): string {
  if (!startedAtNs) return '';
  const ms = Math.max(0, Date.now() - startedAtNs / 1e6);
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${s}s`;
  return `${Math.floor(s / 60)}m${s % 60}s`;
}

/**
 * 实时连接状态常驻栏（主布局顶栏）：
 * 状态点 + 状态文案 + 活跃 run + 事件速率 + lag 角标。点击跳转态势页。
 * `aria-live` 播报连接状态变化（a11y）。
 */
export function LiveStatusBar() {
  const router = useRouter();
  const live = useAppStore((s) => s.live);
  const meta = STATUS_META[live.status];
  const runLabel = live.run.runId ? live.run.runId.slice(0, 8) : null;
  const runtime = formatRuntime(live.run.startedAtNs);
  const hasLag = live.stats.droppedCount > 0;

  return (
    <button
      type="button"
      data-testid="live-status-bar"
      onClick={() => router.push('/console')}
      aria-label={`实时状态：${meta.label}${runLabel ? `，运行 ${runLabel}` : ''}${
        hasLag ? `，实时流已丢弃 ${live.stats.droppedCount} 条` : ''
      }`}
      title={hasLag ? `实时流丢弃 ${live.stats.droppedCount} 条（连接落后）` : '实时观测台'}
      // L2：hover 反馈（可点击性提示）+ L1 lag 角标警示色
      onMouseEnter={(e) => {
        e.currentTarget.style.borderColor = `${meta.color}55`;
        e.currentTarget.style.background = `${meta.color}14`;
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.borderColor = `${meta.color}33`;
        e.currentTarget.style.background = `${meta.color}0d`;
      }}
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 6,
        flexShrink: 0,
        padding: '2px 8px',
        borderRadius: 999,
        border: `1px solid ${meta.color}33`,
        background: `${meta.color}0d`,
        cursor: 'pointer',
        fontFamily: 'var(--ln-mono, monospace)',
        fontSize: 9,
        color: meta.color,
        letterSpacing: '0.02em',
      }}
    >
      <span
        aria-hidden
        style={{
          width: 6,
          height: 6,
          borderRadius: '50%',
          background: meta.color,
          boxShadow: meta.pulse ? `0 0 6px ${meta.color}` : 'none',
          flexShrink: 0,
        }}
      />
      <span aria-live="polite">{meta.label}</span>
      {live.status === 'connected' && runLabel && <span>{runLabel}</span>}
      {live.status === 'connected' && runtime && <span>{runtime}</span>}
      {live.status === 'connected' && live.stats.eventRate > 0 && (
        <span>{live.stats.eventRate}/s</span>
      )}
      {hasLag && (
        // L1：丢弃是问题态，用警示色而非成功绿
        <span title={`丢弃 ${live.stats.droppedCount}`} style={{ color: '#fbbf24' }}>
          ↯{live.stats.droppedCount}
        </span>
      )}
    </button>
  );
}
