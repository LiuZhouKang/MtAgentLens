'use client';

import type { DatasetOverview } from '@/lib/types';
import { deriveDatasetSummary, type FrictionSignal } from '@/lib/dataset-summary';
import { SectionPanel } from '@/components/ui/linear/section-panel';
import { StatusBadge, type StatusTone } from '@/components/ui/linear/status-badge';

const T = { xs: 9.5, sm: 10.5, md: 12, lg: 14, xl: 16 };
const S = { xs: 2, sm: 4, md: 8, lg: 12, xl: 16 };

const sevColor: Record<string, string> = {
  Critical: '#f87171',
  Warning: '#fbbf24',
  Info: '#60a5fa',
};

function fmtDuration(ms: number | null): string {
  if (ms == null) return '—';
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`;
  return `${Math.round(ms / 60_000)}m`;
}

/** Formats ns as HH:MM:SS assuming epoch-based nanoseconds. The collector's
 *  event timestamps are boot-relative (bpf_ktime_get_ns); the real db's
 *  runs.started_at_ns / ended_at_ns are currently NULL so this is latent. */
function fmtClock(ns: number | null): string {
  if (ns == null) return '—';
  return new Date(ns / 1_000_000).toISOString().slice(11, 19);
}

function signalTone(s: FrictionSignal['severity']): StatusTone {
  if (s === 'Critical') return 'danger';
  if (s === 'Warning') return 'warning';
  return 'info';
}

export default function OverviewDatasetPanel({ overview }: { overview: DatasetOverview | null }) {
  if (!overview) return null; // 非 db 数据源（demo/sample/API fallback）无概览，诚实不渲染

  const summary = deriveDatasetSummary(overview);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: S.lg }}>
      {/* ── SummaryBand：健康徽章 + 身份行 + 元信息 ── */}
      <SectionPanel
        eyebrow="数据源详情"
        title={overview.run?.run_id ?? '数据库导入'}
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

        {/* 6× KPI tile */}
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fit, minmax(130px, 1fr))',
            gap: S.sm,
            marginTop: S.lg,
          }}
        >
          {summary.kpi.map((k) => (
            <div
              key={k.key}
              className="ln-panel ln-metric"
              style={{ padding: `${S.sm}px ${S.md}px` }}
            >
              <span className="ln-metric__label">{k.label}</span>
              <strong
                className="ln-metric__value"
                style={{
                  fontSize: 20,
                  color: k.tone === 'danger' ? 'var(--ln-danger, #f87171)' : 'var(--ln-text)',
                }}
              >
                {k.value}
              </strong>
              <span className="ln-metric__delta" data-tone={k.tone}>
                {k.hint || '—'}
              </span>
            </div>
          ))}
        </div>
      </SectionPanel>

      {/* ── 信号面板：规则聚合的 FrictionSignals ── */}
      {summary.signals.length > 0 && (
        <SectionPanel
          eyebrow="Friction Signals"
          title="风险信号"
          meta={`${summary.signals.length} 类`}
          bodyClassName="ln-panel-body"
        >
          <div style={{ display: 'flex', flexDirection: 'column', gap: 0 }}>
            {summary.signals.map((sig) => {
              const c = sevColor[sig.severity] ?? '#60a5fa';
              return (
                <div
                  key={sig.category}
                  style={{
                    display: 'flex',
                    gap: S.sm,
                    alignItems: 'flex-start',
                    padding: `${S.sm}px 0`,
                    borderBottom: '1px solid var(--ln-border)',
                  }}
                >
                  <span
                    style={{
                      width: 8,
                      height: 8,
                      borderRadius: 999,
                      background: c,
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
              );
            })}
          </div>
        </SectionPanel>
      )}

      {/* ── 统计卡组：各表 count ── */}
      <SectionPanel
        eyebrow="Table Coverage"
        title="全表概要"
        meta={`${Object.values(overview).length} 类`}
        bodyClassName="ln-panel-body"
      >
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))',
            gap: S.md,
          }}
        >
          {summary.statCards.map((group) => (
            <div key={group.category}>
              <div
                style={{
                  fontSize: T.xs,
                  fontWeight: 600,
                  color: 'var(--ln-accent)',
                  letterSpacing: '0.04em',
                  textTransform: 'uppercase',
                  marginBottom: S.sm,
                }}
              >
                {group.category}
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
                {group.items.map((item) => (
                  <div
                    key={item.label}
                    style={{ display: 'flex', justifyContent: 'space-between', fontSize: T.sm }}
                  >
                    <span className="ln-panel__meta">{item.label}</span>
                    <strong className="ln-mono" style={{ color: 'var(--ln-text)' }}>
                      {item.value}
                    </strong>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      </SectionPanel>
    </div>
  );
}
