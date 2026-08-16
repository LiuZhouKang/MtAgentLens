'use client';

import { useRef } from 'react';
import { useGSAP } from '@gsap/react';
import gsap from 'gsap';
import type { DatasetOverview } from '@/lib/types';
import { deriveDatasetSummary, type FrictionSignal } from '@/lib/dataset-summary';
import { SectionPanel } from '@/components/ui/linear/section-panel';
import { StatusBadge, type StatusTone } from '@/components/ui/linear/status-badge';
import { DigitReel } from '@/components/motion-primitives/digit-reel';

const T = { xs: 9.5, sm: 10.5, md: 12, lg: 14, xl: 16 };
const S = { xs: 2, sm: 4, md: 8, lg: 12, xl: 16 };

function fmtDuration(ms: number | null): string {
  if (ms == null) return '—';
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`;
  return `${Math.round(ms / 60_000)}m`;
}

/** Formats ns as HH:MM:SS assuming epoch-based nanoseconds. */
function fmtClock(ns: number | null): string {
  if (ns == null) return '—';
  return new Date(ns / 1_000_000).toISOString().slice(11, 19);
}

function signalTone(s: FrictionSignal['severity']): StatusTone {
  if (s === 'Critical') return 'danger';
  if (s === 'Warning') return 'warning';
  return 'info';
}

/** 状态色（ln token，克制）—— 严重/关注/正常 */
const SIGNAL_DOT: Record<string, string> = {
  Critical: 'var(--ln-danger)',
  Warning: 'var(--ln-warning)',
  Info: 'var(--ln-info)',
};

/**
 * 运行概览面板——一次采集 run 的完整画像（db 即 run，非"数据集"）。
 *
 * 语言与全站统一：KPI tile = MetricCard 同构（ln-panel ln-metric + DigitReel），
 * 无光晕 / 无彩色图标底 / 无发光点。动态克制：KPI 错峰 fade-up +
 * 覆盖比例条从 0 生长（纯 accent）。reduced-motion 全降级。
 * 数据全部纯函数派生，无造数。
 */
export default function OverviewDatasetPanel({ overview }: { overview: DatasetOverview | null }) {
  const rootRef = useRef<HTMLDivElement>(null);

  useGSAP(
    () => {
      const root = rootRef.current;
      if (!root) return;
      if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
      // KPI tile 错峰 fade-up（与页面 KPI 区一致）
      const tiles = root.querySelectorAll<HTMLElement>('.ln-run-kpi');
      if (tiles.length > 0) {
        gsap.from(tiles, {
          opacity: 0,
          y: 10,
          duration: 0.4,
          stagger: 0.04,
          ease: 'power2.out',
          delay: 0.05,
          clearProps: 'transform',
        });
      }
      // 覆盖比例条从 0 生长
      const fills = root.querySelectorAll<HTMLElement>('.ln-run-cover__fill');
      if (fills.length > 0) {
        gsap.from(fills, { width: 0, duration: 0.6, stagger: 0.03, ease: 'power2.out', delay: 0.2 });
      }
      // 风险信号行错峰左滑入（与侧边栏 .ln-rail-item 同语言）
      const signals = root.querySelectorAll<HTMLElement>('.ln-run-signal');
      if (signals.length > 0) {
        gsap.from(signals, {
          opacity: 0,
          x: -8,
          duration: 0.35,
          stagger: 0.05,
          ease: 'power2.out',
          delay: 0.1,
          clearProps: 'transform',
        });
      }
    },
    { scope: rootRef, dependencies: [overview] },
  );

  if (!overview) return null;

  const summary = deriveDatasetSummary(overview);

  return (
    <div ref={rootRef} style={{ display: 'flex', flexDirection: 'column', gap: S.lg }}>
      {/* ── SummaryBand：健康徽章 + 身份行 + 元信息 ── */}
      <SectionPanel
        eyebrow="运行详情"
        title={overview.run?.run_id ?? '本地导入'}
        bodyClassName="ln-panel-body"
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
          <StatusBadge
            tone={
              summary.health === 'critical'
                ? 'danger'
                : summary.health === 'warning'
                  ? 'warning'
                  : 'success'
            }
          >
            {summary.health === 'critical'
              ? '需关注'
              : summary.health === 'warning'
                ? '告警'
                : '正常'}
          </StatusBadge>
          <span className="ln-panel__meta">{summary.healthReason}</span>
          {summary.identity.scenarioId && (
            <span className="ln-panel__meta">{summary.identity.scenarioId}</span>
          )}
          {summary.identity.mode && <span className="ln-panel__meta">{summary.identity.mode}</span>}
          {summary.identity.collectorVersion && (
            <span className="ln-panel__meta" style={{ fontFamily: 'var(--ln-mono)' }}>
              v{summary.identity.collectorVersion}
            </span>
          )}
          <span
            className="ln-panel__meta"
            style={{ marginLeft: 'auto', fontFamily: 'var(--ln-mono)' }}
          >
            {fmtClock(summary.timeRange.startedAtNs)} → {fmtClock(summary.timeRange.endedAtNs)} ·{' '}
            {fmtDuration(summary.timeRange.durationMs)}
          </span>
        </div>

        {/* 6× KPI tile —— 与 MetricCard 完全同构（label + DigitReel + delta） */}
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))',
            gap: S.sm,
            marginTop: S.lg,
          }}
        >
          {summary.kpi.map((k) => (
            <div
              key={k.key}
              className="ln-panel ln-metric ln-run-kpi"
            >
              <span className="ln-metric__label">{k.label}</span>
              <DigitReel value={k.value} className="ln-metric__value" />
              <span className="ln-metric__delta" data-tone={k.tone}>
                {k.hint || '—'}
              </span>
            </div>
          ))}
        </div>
      </SectionPanel>

      {/* ── 风险信号面板：StatusBadge + 克制色点 ── */}
      {summary.signals.length > 0 && (
        <SectionPanel
          eyebrow="Friction Signals"
          title="风险信号"
          meta={`${summary.signals.length} 类`}
          bodyClassName="ln-panel-body"
        >
          <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
            {summary.signals.map((sig) => (
              <div
                key={sig.category}
                className="ln-run-signal"
                style={{ display: 'flex', gap: S.sm, alignItems: 'flex-start' }}
              >
                <span
                  aria-hidden
                  style={{
                    width: 6,
                    height: 6,
                    borderRadius: 999,
                    background: SIGNAL_DOT[sig.severity] ?? 'var(--ln-info)',
                    flexShrink: 0,
                    marginTop: 5,
                  }}
                />
                <StatusBadge tone={signalTone(sig.severity)}>{sig.severity}</StatusBadge>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                    <strong style={{ fontSize: T.sm, color: 'var(--ln-text)' }}>
                      {sig.title}
                    </strong>
                    <span className="ln-panel__meta" style={{ fontFamily: 'var(--ln-mono)' }}>
                      × {sig.count}
                    </span>
                  </div>
                  <div
                    style={{
                      fontSize: T.xs,
                      color: 'var(--ln-text-muted)',
                      marginTop: 2,
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                      whiteSpace: 'nowrap',
                    }}
                  >
                    {sig.detail}
                  </div>
                </div>
              </div>
            ))}
          </div>
        </SectionPanel>
      )}

      {/* ── 数据覆盖：label + 细比例条（纯 accent）+ mono 值 ── */}
      <SectionPanel
        eyebrow="Coverage"
        title="数据覆盖"
        meta={`${Object.values(overview).length} 类`}
        bodyClassName="ln-panel-body"
      >
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))',
            gap: S.md,
          }}
        >
          {summary.statCards.map((group) => {
            const max = Math.max(1, ...group.items.map((i) => i.value));
            return (
              <div key={group.category}>
                <div
                  style={{
                    fontSize: T.xs,
                    fontWeight: 600,
                    color: 'var(--ln-text-muted)',
                    letterSpacing: '0.04em',
                    textTransform: 'uppercase',
                    marginBottom: S.sm,
                  }}
                >
                  {group.category}
                </div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
                  {group.items.map((item) => (
                    <div key={item.label} className="ln-run-cover">
                      <span className="ln-panel__meta" style={{ width: 64, flexShrink: 0 }}>
                        {item.label}
                      </span>
                      <span className="ln-run-cover__track">
                        <span
                          className="ln-run-cover__fill"
                          style={{ width: `${Math.round((item.value / max) * 100)}%` }}
                        />
                      </span>
                      <strong className="ln-mono" style={{ color: 'var(--ln-text)' }}>
                        {item.value}
                      </strong>
                    </div>
                  ))}
                </div>
              </div>
            );
          })}
        </div>
      </SectionPanel>
    </div>
  );
}
