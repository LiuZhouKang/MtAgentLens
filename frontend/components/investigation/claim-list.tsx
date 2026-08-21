'use client';

import { useMemo, useState } from 'react';
import {
  Bot,
  Box,
  ChevronDown,
  ChevronRight,
  CircleAlert,
  FileSearch,
  GitBranch,
  Globe,
  HelpCircle,
  Info,
  KeyRound,
  ShieldAlert,
  Sparkles,
  TerminalSquare,
  Wrench,
} from 'lucide-react';
import type {
  ResponsibilityClaim,
  ResponsibilityTraceback,
} from '@/lib/types';
import { claimStateLabel, claimTypeLabel, severityLabel, TONE_COLOR } from '@/lib/investigation-labels';
import {
  parseTracebackSummary,
  TRACEBACK_KIND_LABEL,
  type TracebackNodeKind,
  type TracebackStep,
} from '@/lib/traceback-visual';
import AgentAvatar from '@/components/ui/agent-avatar';

const T = { xs: 9.5, sm: 10.5, md: 12, lg: 14, xl: 16 };
const S = { xs: 2, sm: 4, md: 8, lg: 12, xl: 16 };

const sevIcon: Record<string, typeof ShieldAlert> = {
  Critical: ShieldAlert,
  Warning: CircleAlert,
  Info,
};

const KIND_ICONS: Record<TracebackNodeKind, typeof FileSearch> = {
  agent: Bot,
  file: FileSearch,
  process: TerminalSquare,
  network: Globe,
  tool: Wrench,
  credential: KeyRound,
  context: Box,
  llm: Sparkles,
  unknown: HelpCircle,
};

/** 节点图标（Agent 用 AgentAvatar，其余用 kind 图标）。 */
function StepIcon({ step, size }: { step: TracebackStep; size: number }) {
  if (step.kind === 'agent') {
    return <AgentAvatar agentId={step.detail ?? step.label} size={size} />;
  }
  const Icon = KIND_ICONS[step.kind] ?? HelpCircle;
  return <Icon size={size} style={{ color: 'var(--ln-text-secondary)', flexShrink: 0 }} />;
}

/** 可视化因果链：Rust debug 文本 → AgentAvatar + 图标 + 可读标签 + 箭头流程。 */
function CausalChain({ tb }: { tb: ResponsibilityTraceback }) {
  const vis = parseTracebackSummary(tb.explanation_summary);
  if (!vis || vis.steps.length === 0) {
    return <span className="ln-panel__meta">此声明无回溯链</span>;
  }
  return (
    <div className="ln-tb">
      {vis.steps.map((step, i) => (
        <div key={i} className="ln-tb-step">
          <div className="ln-tb-step__body">
            <span className="ln-chip" data-tone="muted">
              {TRACEBACK_KIND_LABEL[step.kind]}
            </span>
            <StepIcon step={step} size={16} />
            <span className="ln-tb-step__label" title={step.detail ?? step.label}>
              {step.label}
            </span>
            {step.isBoundary && (
              <span className="ln-chip" data-tone="danger">
                边界穿越
              </span>
            )}
          </div>
          {i < vis.steps.length - 1 && <span className="ln-tb-arrow" aria-hidden />}
        </div>
      ))}
      {vis.rootCause && (
        <div className="ln-tb-root">
          <span className="ln-chip" data-tone="danger">
            根因
          </span>
          <span className="ln-chip" data-tone="muted">
            {TRACEBACK_KIND_LABEL[vis.rootCause.kind]}
          </span>
          <StepIcon step={vis.rootCause} size={16} />
          <span className="ln-tb-step__label" title={vis.rootCause.detail ?? vis.rootCause.label}>
            {vis.rootCause.label}
          </span>
        </div>
      )}
      {vis.evidenceCount != null && (
        <div className="ln-tb-ev">支持证据 {vis.evidenceCount} 条</div>
      )}
    </div>
  );
}

export interface ClaimListProps {
  claims: ResponsibilityClaim[];
  tracebacksByClaimId: Map<string, ResponsibilityTraceback>;
  /** 告警侧引用集合（weak link）——用于「与该告警共享 N 条证据」计数。 */
  alertEvidenceRefs: string[];
}

export default function ClaimList({
  claims,
  tracebacksByClaimId,
  alertEvidenceRefs,
}: ClaimListProps) {
  const [expandedId, setExpandedId] = useState<string | null>(null);

  // 排序：timestamp_ns asc + claim_id 次键（同时间戳稳定序）
  const sorted = useMemo(
    () =>
      [...claims].sort(
        (a, b) => a.timestamp_ns - b.timestamp_ns || a.claim_id.localeCompare(b.claim_id),
      ),
    [claims],
  );

  const alertRefSet = useMemo(() => new Set(alertEvidenceRefs), [alertEvidenceRefs]);

  if (sorted.length === 0) {
    return (
      <div
        style={{
          textAlign: 'center',
          padding: '20px 0',
          color: 'var(--ln-text-muted)',
          fontSize: T.sm,
        }}
      >
        暂无责任声明
      </div>
    );
  }

  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        gap: 0,
        maxHeight: 460,
        overflowY: 'auto',
      }}
    >
      {sorted.map((claim) => {
        const sev = severityLabel(claim.severity);
        const sevColor = TONE_COLOR[sev.tone ?? 'info'];
        const Icon = sevIcon[claim.severity] ?? Info;
        const type = claimTypeLabel(claim.claim_type);
        const state = claimStateLabel(claim.claim_state);
        const expanded = expandedId === claim.claim_id;
        const sharedCount = claim.evidence_refs.filter((ref) => alertRefSet.has(ref)).length;
        const tb = tracebacksByClaimId.get(claim.claim_id) ?? null;

        return (
          <div key={claim.claim_id} style={{ display: 'flex', flexDirection: 'column' }}>
            <button
              type="button"
              data-claim-row
              aria-expanded={expanded}
              onClick={() => setExpandedId(expanded ? null : claim.claim_id)}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: S.sm,
                padding: '9px 10px',
                textAlign: 'left',
                border: '1px solid var(--ln-border)',
                borderRadius: 8,
                background: 'var(--ln-surface-0)',
                cursor: 'pointer',
                transition: 'border-color 120ms ease',
              }}
            >
              <Icon size={14} style={{ color: sevColor, flexShrink: 0 }} />
              <span className="ln-chip" data-tone={type.tone ?? 'muted'}>
                {type.label}
              </span>
              {claim.subject_agent_id ? (
                <AgentAvatar agentId={claim.subject_agent_id} size={16} />
              ) : null}
              <span
                style={{
                  fontSize: T.sm,
                  fontWeight: 600,
                  color: 'var(--ln-text)',
                  fontFamily: 'var(--ln-mono)',
                  flexShrink: 0,
                }}
              >
                {claim.claim_id}
              </span>
              <span
                className="ln-panel__meta"
                style={{
                  flex: 1,
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                  whiteSpace: 'nowrap',
                }}
              >
                {claim.policy_id ?? '—'}
                {claim.boundary_id ? ` · ${claim.boundary_id}` : ''}
              </span>
              <span className="ln-chip" data-tone={state.tone ?? 'muted'}>
                {state.label}
              </span>
              <span style={{ width: 48, flexShrink: 0 }}>
                <div
                  style={{
                    height: 3,
                    borderRadius: 999,
                    background: 'var(--ln-surface-2)',
                    overflow: 'hidden',
                  }}
                >
                  <div
                    style={{
                      height: '100%',
                      width: `${Math.round(claim.confidence * 100)}%`,
                      background: sevColor,
                    }}
                  />
                </div>
              </span>
              {tb && <GitBranch size={12} style={{ color: 'var(--ln-info)', flexShrink: 0 }} />}
              {expanded ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
            </button>

            {/* 与告警共享证据数（具体证据清单见上方证据链，避免重复） */}
            {sharedCount > 0 && (
              <div
                style={{
                  padding: '2px 10px 8px',
                  display: 'flex',
                  alignItems: 'center',
                  gap: 6,
                }}
              >
                <span className="ln-chip" data-tone="success">
                  共享证据 ×{sharedCount}
                </span>
              </div>
            )}

            {expanded && (
              <div
                style={{
                  padding: S.sm,
                  marginLeft: S.md,
                  borderLeft: '1px solid var(--ln-border)',
                  display: 'flex',
                  flexDirection: 'column',
                  gap: S.sm,
                }}
              >
                {/* 回溯链 —— 可视化因果链（可读，非 Rust debug 文本） */}
                {tb ? (
                  <CausalChain tb={tb} />
                ) : (
                  <span className="ln-panel__meta">此声明无回溯链</span>
                )}

                {/* 竞争解释 */}
                {claim.competing_explanations.length > 0 && (
                  <div
                    style={{
                      padding: S.xs,
                      borderRadius: 6,
                      background: 'var(--ln-surface-2)',
                      fontSize: T.xs,
                      color: 'var(--ln-text-muted)',
                    }}
                  >
                    {claim.competing_explanations.map((alt) => (
                      <div key={alt.rule_id}>
                        <strong style={{ color: 'var(--ln-text-secondary)' }}>{alt.label}</strong>
                        <span> — {alt.rationale}</span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
