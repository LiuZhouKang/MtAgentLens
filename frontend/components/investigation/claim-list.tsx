'use client';

import { useMemo, useState } from 'react';
import { ChevronDown, ChevronRight, ShieldAlert, CircleAlert, Info, FileSearch, GitBranch } from 'lucide-react';
import type { CorrelationEvidence, GraphData, ResponsibilityClaim, ResponsibilityTraceback } from '@/lib/types';
import { formatResponsibilityNodeLabel } from '@/lib/investigation';
import AgentAvatar from '@/components/ui/agent-avatar';

const T = { xs: 9.5, sm: 10.5, md: 12, lg: 14, xl: 16 };
const S = { xs: 2, sm: 4, md: 8, lg: 12, xl: 16 };

const sevColor: Record<string, string> = { Critical: '#f87171', Warning: '#fbbf24', Info: '#60a5fa' };
const sevIcon: Record<string, typeof ShieldAlert> = { Critical: ShieldAlert, Warning: CircleAlert, Info };

export interface ClaimListProps {
  claims: ResponsibilityClaim[];
  tracebacksByClaimId: Map<string, ResponsibilityTraceback>;
  graph: GraphData;
  evidence: CorrelationEvidence[];
  /** 告警侧引用集合（weak link）——用于「与该告警共享 N 条证据」计数。 */
  alertEvidenceRefs: string[];
}

export default function ClaimList({ claims, tracebacksByClaimId, graph, evidence, alertEvidenceRefs }: ClaimListProps) {
  const [expandedId, setExpandedId] = useState<string | null>(null);

  // 排序：timestamp_ns asc + claim_id 次键（同时间戳稳定序）
  const sorted = useMemo(
    () =>
      [...claims].sort(
        (a, b) => a.timestamp_ns - b.timestamp_ns || a.claim_id.localeCompare(b.claim_id),
      ),
    [claims],
  );

  const evidenceById = useMemo(() => new Map(evidence.map((e) => [e.evidence_id, e])), [evidence]);
  const nodeById = useMemo(() => new Map(graph.nodes.map((n) => [n.node_id, n])), [graph.nodes]);
  const alertRefSet = useMemo(() => new Set(alertEvidenceRefs), [alertEvidenceRefs]);

  if (sorted.length === 0) {
    return (
      <div style={{ textAlign: 'center', padding: '20px 0', color: 'var(--ln-text-muted)', fontSize: T.sm }}>
        暂无责任声明
      </div>
    );
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 0, maxHeight: 460, overflowY: 'auto' }}>
      {sorted.map((claim) => {
        const sc = sevColor[claim.severity] ?? '#60a5fa';
        const Icon = sevIcon[claim.severity] ?? Info;
        const expanded = expandedId === claim.claim_id;
        const sharedCount = claim.evidence_refs.filter((ref) => alertRefSet.has(ref)).length;
        const tb = tracebacksByClaimId.get(claim.claim_id) ?? null;
        // 链节点去重（实测 4/22 链含重复 UUID）
        const chainNodes = tb
          ? Array.from(new Set(tb.chain_node_ids))
              .map((id) => nodeById.get(id) ?? null)
              .filter((n): n is NonNullable<typeof n> => n != null)
          : [];

        return (
          <div key={claim.claim_id} style={{ display: 'flex', flexDirection: 'column' }}>
            <button
              type="button"
              data-claim-row
              aria-expanded={expanded}
              onClick={() => setExpandedId(expanded ? null : claim.claim_id)}
              style={{
                display: 'flex', alignItems: 'center', gap: S.sm, padding: '9px 10px', textAlign: 'left',
                border: '1px solid var(--ln-border)', borderRadius: 8, background: 'var(--ln-surface-0)',
                cursor: 'pointer', transition: 'border-color 120ms ease',
              }}
            >
              <Icon size={14} style={{ color: sc, flexShrink: 0 }} />
              <span style={{ padding: '1px 6px', borderRadius: 4, fontSize: 9, fontWeight: 600, background: `${sc}16`, color: sc, fontFamily: 'var(--ln-mono)', flexShrink: 0 }}>
                {claim.claim_type}
              </span>
              {claim.subject_agent_id ? (
                <AgentAvatar agentId={claim.subject_agent_id} size={16} />
              ) : null}
              <span style={{ fontSize: T.sm, fontWeight: 600, color: 'var(--ln-text)', fontFamily: 'var(--ln-mono)', flexShrink: 0 }}>
                {claim.claim_id}
              </span>
              <span className="ln-panel__meta" style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {claim.policy_id ?? '—'}{claim.boundary_id ? ` · ${claim.boundary_id}` : ''}
              </span>
              <span style={{ fontSize: 9, fontFamily: 'var(--ln-mono)', color: 'var(--ln-text-muted)', flexShrink: 0 }}>
                {claim.claim_state}
              </span>
              <span style={{ width: 48, flexShrink: 0 }}>
                <div style={{ height: 3, borderRadius: 999, background: 'var(--ln-surface-2)', overflow: 'hidden' }}>
                  <div style={{ height: '100%', width: `${Math.round(claim.confidence * 100)}%`, background: sc }} />
                </div>
              </span>
              {tb && <GitBranch size={12} style={{ color: '#60a5fa', flexShrink: 0 }} />}
              {expanded ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
            </button>

            {/* Always-visible sub-row: evidence chips + shared count */}
            <div style={{ padding: '2px 10px 8px', display: 'flex', flexWrap: 'wrap', gap: 4, alignItems: 'center' }}>
              {claim.evidence_refs.length > 0 && claim.evidence_refs.map((ref) => {
                const ev = evidenceById.get(ref);
                const label = ev?.matched_fields.canonical_path ?? ref;
                return (
                  <span key={ref} title={ref} style={{ display: 'inline-flex', alignItems: 'center', gap: 3, fontSize: 9, padding: '2px 6px', borderRadius: 4, background: 'rgba(74,222,128,0.10)', color: '#4ade80', fontFamily: 'var(--ln-mono)', maxWidth: 220, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    <FileSearch size={9} />{label}
                  </span>
                );
              })}
              {sharedCount > 0 && (
                <span className="ln-panel__meta">与该告警共享 {sharedCount} 条证据</span>
              )}
            </div>

            {expanded && (
              <div style={{ padding: S.sm, marginLeft: S.md, borderLeft: `2px solid ${sc}30`, display: 'flex', flexDirection: 'column', gap: S.sm }}>
                {/* 回溯链 */}
                {tb ? (
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                    {chainNodes.map((node, idx) => (
                      <div key={`${node.node_id}-${idx}`} style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: T.xs }}>
                        <span style={{ width: 18, height: 18, borderRadius: 5, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 9, fontFamily: 'var(--ln-mono)', background: `${sc}18`, border: `1px solid ${sc}40`, color: sc, flexShrink: 0 }}>
                          {idx + 1}
                        </span>
                        <span style={{ fontFamily: 'var(--ln-mono)', color: node.node_id === tb.root_cause_node_id ? '#f87171' : 'var(--ln-text-secondary)', fontWeight: node.node_id === tb.root_cause_node_id ? 700 : 400, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                          {formatResponsibilityNodeLabel(node)}
                        </span>
                        {node.node_id === tb.root_cause_node_id && (
                          <span style={{ fontSize: 9, padding: '1px 5px', borderRadius: 3, background: 'rgba(248,113,113,0.14)', color: '#f87171', flexShrink: 0 }}>根因</span>
                        )}
                      </div>
                    ))}
                    <div style={{ fontSize: T.xs, color: 'var(--ln-text-secondary)', lineHeight: 1.55, marginTop: 2 }}>
                      {tb.explanation_summary}
                    </div>
                  </div>
                ) : (
                  <span className="ln-panel__meta">此声明无回溯链</span>
                )}

                {/* 竞争解释 */}
                {claim.competing_explanations.length > 0 && (
                  <div style={{ padding: S.xs, borderRadius: 6, background: 'var(--ln-surface-2)', fontSize: T.xs, color: 'var(--ln-text-muted)' }}>
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
