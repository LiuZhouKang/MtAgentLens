'use client';

import { useState, type ReactNode } from 'react';
import { ChevronDown } from 'lucide-react';
import { SectionPanel } from '@/components/ui/linear/section-panel';
import { TONE_COLOR } from '@/lib/investigation-labels';
import type { FacetSelection, WhyFacets } from '@/lib/why-facets';

const FACET_LABELS: Record<FacetSelection, string> = {
  all: '全部',
  pollution: '污染',
  policy: '违规',
  diagnostic: '失配',
};
const FACET_ORDER: Array<Exclude<FacetSelection, 'all'>> = ['pollution', 'policy', 'diagnostic'];

const T = { xs: 9.5, sm: 10.5, md: 12 };

function Bar({ value, color, label }: { value: number; color: string; label: string }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
      <span style={{ fontSize: T.xs, color: 'var(--ln-text-muted)', minWidth: 64, flexShrink: 0 }}>
        {label}
      </span>
      <div
        style={{
          flex: 1,
          height: 4,
          borderRadius: 999,
          background: 'var(--ln-surface-2)',
          overflow: 'hidden',
        }}
      >
        <div
          style={{
            height: '100%',
            width: `${Math.min(100, value * 100)}%`,
            borderRadius: 999,
            background: color,
          }}
        />
      </div>
    </div>
  );
}

function Chip({
  label,
  count,
  active,
  disabled,
  onClick,
}: {
  label: string;
  count: number;
  active: boolean;
  disabled: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      aria-pressed={active}
      disabled={disabled}
      onClick={onClick}
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 6,
        padding: '5px 12px',
        borderRadius: 999,
        cursor: disabled ? 'not-allowed' : 'pointer',
        fontFamily: 'var(--ln-mono)',
        fontSize: T.xs,
        border: active ? '1px solid var(--ln-accent)' : '1px solid var(--ln-border)',
        background: active ? 'var(--ln-accent-soft)' : 'var(--ln-surface-0)',
        color: disabled ? 'var(--ln-text-muted)' : active ? 'var(--ln-accent)' : 'var(--ln-text)',
        opacity: disabled ? 0.5 : 1,
        transition: 'all 120ms ease',
      }}
    >
      {label}{' '}
      <span style={{ color: disabled ? 'var(--ln-text-muted)' : 'var(--ln-text-secondary)' }}>
        {count}
      </span>
    </button>
  );
}

const colWrap = { display: 'flex', flexDirection: 'column', gap: 6 } as const;
const colHead = {
  fontSize: T.xs,
  fontWeight: 600,
  color: 'var(--ln-text-muted)',
  textTransform: 'uppercase',
  letterSpacing: '0.03em',
} as const;
const mutedXs = { fontSize: T.xs, color: 'var(--ln-text-muted)' } as const;
const summarySm = { fontSize: T.sm, color: 'var(--ln-text)' } as const;
const rowBase = {
  display: 'flex',
  justifyContent: 'space-between',
  gap: 8,
  fontSize: T.xs,
} as const;
// minWidth: 0 让 flex 子项可收缩到内容以下，textOverflow: ellipsis 才真正生效（否则默认 min-width:auto 不截断）。
const monoLabel = {
  color: 'var(--ln-text-secondary)',
  overflow: 'hidden',
  textOverflow: 'ellipsis',
  whiteSpace: 'nowrap',
  minWidth: 0,
} as const;

/** 归因三列共用骨架：标题 + 摘要行；无数据 → 占位。 */
function FacetColumn({
  title,
  hasData,
  summary,
  children,
}: {
  title: string;
  hasData: boolean;
  summary: string;
  children: ReactNode;
}) {
  return (
    <div style={colWrap}>
      <div style={colHead}>{title}</div>
      {hasData ? (
        <>
          <div style={summarySm}>{summary}</div>
          {children}
        </>
      ) : (
        <div style={mutedXs}>无数据</div>
      )}
    </div>
  );
}

export default function WhyAttribution({
  why,
  facet,
  onFacetChange,
  alertCount,
  dbPresent,
}: {
  why: WhyFacets;
  facet: FacetSelection;
  onFacetChange: (f: FacetSelection) => void;
  alertCount: number;
  dbPresent: boolean;
}) {
  const [expanded, setExpanded] = useState(false);
  const { pollution, policy, diagnostic } = why;

  return (
    <SectionPanel
      eyebrow="全局归因"
      title={
        dbPresent
          ? `${alertCount} 条告警 · ${why.facetCounts.pollution} 污染 / ${why.facetCounts.policy} 违规 / ${why.facetCounts.diagnostic} 失配`
          : '待 db 数据'
      }
    >
      <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
        <div
          role="group"
          aria-label="告警分面"
          style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}
        >
          <Chip
            label="全部"
            count={alertCount}
            active={facet === 'all'}
            disabled={!dbPresent}
            onClick={() => onFacetChange('all')}
          />
          {FACET_ORDER.map((k) => (
            <Chip
              key={k}
              label={FACET_LABELS[k]}
              count={why.facetCounts[k]}
              active={facet === k}
              // 激活 chip 永不禁用：新导入 db 使该分面归 0 时 URL 残留 ?facet=，
              // active+disabled 矛盾态不可达；0 命中禁用仅作用于未激活 chip。
              disabled={!dbPresent || (facet !== k && why.facetCounts[k] === 0)}
              onClick={() => onFacetChange(k)}
            />
          ))}
          <span
            aria-live="polite"
            style={{ fontSize: T.xs, color: 'var(--ln-text-muted)', marginLeft: 'auto' }}
          >
            {facet === 'all'
              ? `${alertCount} 条告警`
              : `${why.facetCounts[facet]} 条告警 · ${FACET_LABELS[facet]}分面`}
          </span>
          <button
            type="button"
            aria-expanded={expanded}
            aria-controls="why-attribution-body"
            disabled={!dbPresent}
            onClick={() => setExpanded((v) => !v)}
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: 4,
              fontSize: T.xs,
              color: 'var(--ln-text-muted)',
              cursor: !dbPresent ? 'default' : 'pointer',
              opacity: !dbPresent ? 0.5 : 1,
              background: 'transparent',
              border: 'none',
              padding: '4px 6px',
            }}
          >
            归因
            <ChevronDown
              size={12}
              style={{
                transform: expanded ? 'rotate(180deg)' : 'none',
                transition: 'transform 150ms ease',
              }}
            />
          </button>
        </div>

        {facet !== 'all' && (
          <button
            type="button"
            aria-label="清除分面"
            onClick={() => onFacetChange('all')}
            style={{
              alignSelf: 'flex-start',
              display: 'inline-flex',
              alignItems: 'center',
              gap: 6,
              padding: '3px 10px',
              borderRadius: 999,
              fontSize: T.xs,
              cursor: 'pointer',
              border: '1px solid var(--ln-accent)',
              background: 'rgba(94,106,210,0.08)',
              color: 'var(--ln-accent)',
              fontFamily: 'var(--ln-mono)',
            }}
          >
            分面：{FACET_LABELS[facet]} ×
          </button>
        )}

        <div id="why-attribution-body" hidden={dbPresent && !expanded}>
          {!dbPresent ? (
            <div style={mutedXs}>导入 responsibility.db 后启用全局归因</div>
          ) : (
            <div
              style={{
                display: 'grid',
                gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))',
                gap: 14,
              }}
            >
              <FacetColumn
                title="污染传播"
                hasData={pollution.total > 0}
                summary={`${pollution.total} 链 · 多跳 ${pollution.multiHopCount} · 最长 ${pollution.maxHops} · 平均 ${pollution.avgHops}`}
              >
                {pollution.bySource.slice(0, 4).map((s) => (
                  <div key={s.agentId} style={rowBase}>
                    <span style={monoLabel}>{s.agentId}</span>
                    <span style={{ color: 'var(--ln-danger)' }}>×{s.count}</span>
                  </div>
                ))}
                {pollution.hubAgents.length > 0 && (
                  <div style={{ display: 'flex', alignItems: 'center', gap: 4, flexWrap: 'wrap' }}>
                    <span style={mutedXs}>枢纽</span>
                    {pollution.hubAgents.map((h) => (
                      <span key={h} className="ln-chip" data-tone="danger">
                        {h}
                      </span>
                    ))}
                  </div>
                )}
              </FacetColumn>

              <FacetColumn
                title="策略判定"
                hasData={policy.total > 0}
                summary={`${policy.total} 判定 · 违规 ${policy.violationCount}（${Math.round(policy.violationRate * 100)}%）`}
              >
                {policy.byJudgment.slice(0, 4).map((j) => (
                  <Bar
                    key={j.result}
                    label={j.result}
                    value={policy.total ? j.count / policy.total : 0}
                    color={
                      j.result === 'Violation'
                        ? TONE_COLOR.danger
                        : j.result === 'Allow'
                          ? TONE_COLOR.success
                          : 'var(--ln-accent)'
                    }
                  />
                ))}
                {policy.violationsByBoundary.slice(0, 3).map((b) => (
                  <div key={b.boundary} style={rowBase}>
                    <span style={monoLabel}>{b.boundary}</span>
                    <span
                      style={{ color: b.violationCount > 0 ? 'var(--ln-danger)' : 'var(--ln-text-muted)' }}
                    >
                      {b.violationCount}/{b.total}
                    </span>
                  </div>
                ))}
              </FacetColumn>

              <FacetColumn
                title="失配诊断"
                hasData={diagnostic.total > 0}
                summary={`${diagnostic.total} 诊断 · 涉及 ${diagnostic.intentsAffected} 意图`}
              >
                {diagnostic.byReason.slice(0, 3).map((r) => (
                  <Bar
                    key={r.reason}
                    label={r.reason}
                    value={diagnostic.total ? r.count / diagnostic.total : 0}
                    color={TONE_COLOR.warning}
                  />
                ))}
                {diagnostic.byIntentId.slice(0, 3).map((i) => (
                  <div key={i.intentId} style={rowBase}>
                    <span style={monoLabel}>{i.toolName ?? i.intentId}</span>
                    <span style={{ color: 'var(--ln-warning)' }}>×{i.count}</span>
                  </div>
                ))}
              </FacetColumn>
            </div>
          )}
        </div>
      </div>
    </SectionPanel>
  );
}

export function WhyFacetEmptyState({
  facet,
  filteredCount,
  onClear,
}: {
  facet: Exclude<FacetSelection, 'all'>;
  filteredCount: number;
  onClear: () => void;
}) {
  return (
    <div
      style={{
        textAlign: 'center',
        padding: '36px 0',
        display: 'flex',
        flexDirection: 'column',
        gap: 8,
      }}
    >
      <div style={{ fontSize: T.md, color: 'var(--ln-text)' }}>该分面无告警命中</div>
      <div style={{ fontSize: T.sm, color: 'var(--ln-text-muted)' }}>
        已过滤 {filteredCount} 条告警 · 分面：{FACET_LABELS[facet]}
      </div>
      <button
        type="button"
        onClick={onClear}
        style={{
          alignSelf: 'center',
          marginTop: 8,
          padding: '6px 14px',
          borderRadius: 999,
          fontSize: T.sm,
          cursor: 'pointer',
          border: '1px solid var(--ln-accent)',
          background: 'var(--ln-accent-soft)',
          color: 'var(--ln-accent)',
          fontFamily: 'var(--ln-mono)',
        }}
      >
        清除分面
      </button>
    </div>
  );
}
