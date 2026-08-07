'use client';

import { useMemo } from 'react';
import { SectionPanel } from '@/components/ui/linear/section-panel';
import { collectAlertEvidenceRefs } from '@/lib/investigation';
import { buildForensicChain } from '@/lib/forensic-chain';
import type {
  ContextPollutionEvent,
  CorrelationEvidence,
  GraphData,
  NonLinkDiagnostic,
  PolicyEvaluation,
  ResponsibilityAlert,
  ResponsibilityClaim,
} from '@/lib/types';

const T = { xs: 9.5, sm: 10.5, md: 12 };

function resultTone(result: string | null): { color: string; label: string } {
  if (result === 'Violation') return { color: '#f87171', label: '违规' };
  if (result === 'Allow') return { color: '#4ade80', label: '放行' };
  if (result === 'Compliant') return { color: '#fbbf24', label: '合规' };
  return { color: '#64748b', label: result ?? '未知' };
}

export type ForensicChainProps = {
  alert: ResponsibilityAlert;
  pollutionEvents: ContextPollutionEvent[];
  policyEvaluations: PolicyEvaluation[];
  nonLinkDiagnostics: NonLinkDiagnostic[];
  claims: ResponsibilityClaim[];
  evidence: CorrelationEvidence[];
  fallbackGraph: GraphData;
};

/**
 * 告警取证链面板——四区组装（引擎判定 / 污染传播链 / 策略矩阵 / 失配诊断）。
 * 全部数据经 buildForensicChain 纯函数派生，无造数；某源为空 → 该区显示「无」。
 */
export default function ForensicChain({
  alert,
  pollutionEvents,
  policyEvaluations,
  nonLinkDiagnostics,
  claims,
  evidence,
  fallbackGraph,
}: ForensicChainProps) {
  const chain = useMemo(() => {
    const alertEvidenceRefs = collectAlertEvidenceRefs(alert, fallbackGraph);
    const evidenceRefSet = new Set(alertEvidenceRefs);
    // alert 证据 → 关联 intent_id（evidence 桥接），供诊断区按 intentIds 过滤
    const intentIds = evidence
      .filter((e) => evidenceRefSet.has(e.evidence_id))
      .map((e) => e.intent_id);
    return buildForensicChain({
      alert,
      pollutionEvents,
      policyEvaluations,
      nonLinkDiagnostics,
      claims,
      intentIds,
      alertEvidenceRefs,
    });
  }, [alert, pollutionEvents, policyEvaluations, nonLinkDiagnostics, claims, evidence, fallbackGraph]);

  const dbEmpty = pollutionEvents.length === 0 && policyEvaluations.length === 0 && nonLinkDiagnostics.length === 0;
  const title = dbEmpty ? '待 db 数据' : `${chain.verdicts.length} 判定 / ${chain.policyRows.length} 策略`;

  return (
    <SectionPanel eyebrow="取证链" title={title} bodyClassName="ln-panel-body">
      <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
        {/* 1. 引擎判定 */}
        <div>
          <div style={{ fontSize: T.xs, fontWeight: 600, color: 'var(--ln-text-muted)', marginBottom: 6, letterSpacing: '0.03em', textTransform: 'uppercase' }}>引擎判定</div>
          {chain.verdicts.length === 0 ? (
            <div style={{ fontSize: T.sm, color: 'var(--ln-text-muted)' }}>无引擎判定</div>
          ) : (
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(150px, 1fr))', gap: 8 }}>
              {chain.verdicts.map((v) => (
                <div key={`${v.kind}-${v.id}`} style={{ padding: '10px 12px', borderRadius: 8, background: 'var(--ln-surface-0)', border: '1px solid var(--ln-border)' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 6 }}>
                    <span style={{
                      fontSize: 9, padding: '2px 5px', borderRadius: 4, fontWeight: 600, fontFamily: 'var(--ln-mono)',
                      background: v.kind === 'rule' ? 'rgba(94,106,210,0.14)' : 'rgba(167,139,250,0.14)',
                      color: v.kind === 'rule' ? 'var(--ln-accent)' : '#a78bfa',
                    }}>{v.kind === 'rule' ? 'RULE' : 'PATTERN'}</span>
                    <span style={{ fontSize: T.xs, fontFamily: 'var(--ln-mono)', color: 'var(--ln-text)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', flex: 1 }}>{v.id}</span>
                  </div>
                  {/* score bar */}
                  <div style={{ height: 3, borderRadius: 999, background: 'var(--ln-surface-2)', marginBottom: 6, overflow: 'hidden' }}>
                    <div style={{ height: '100%', width: `${Math.round(v.score * 100)}%`, borderRadius: 999, background: 'linear-gradient(90deg, rgba(94,106,210,0.5), var(--ln-accent))' }} />
                  </div>
                  {v.reason && <div style={{ fontSize: T.xs, color: 'var(--ln-text-secondary)', lineHeight: 1.5 }}>{v.reason}</div>}
                  {v.targets.length > 0 && (
                    <div style={{ fontSize: T.xs, color: 'var(--ln-text-muted)', fontFamily: 'var(--ln-mono)', marginTop: 4, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{v.targets.join(', ')}</div>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>

        {/* 2. 污染传播链 */}
        <div>
          <div style={{ fontSize: T.xs, fontWeight: 600, color: 'var(--ln-text-muted)', marginBottom: 6, letterSpacing: '0.03em', textTransform: 'uppercase' }}>污染传播链</div>
          {chain.pollutionChains.length === 0 ? (
            <div style={{ fontSize: T.sm, color: 'var(--ln-text-muted)' }}>无污染传播</div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {chain.pollutionChains.map((p) => {
                const hops = p.hops.length > 0 ? p.hops : [p.source_agent_id, '…', p.contaminated_artifact];
                return (
                  // pollution_id 唯一（同 agent 可多次污染同 artifact，source+artifact 会 key 碰撞）
                  <div key={p.pollution_id} style={{ padding: '10px 12px', borderRadius: 8, background: 'rgba(248,113,113,0.04)', border: '1px solid rgba(248,113,113,0.16)' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: T.xs, marginBottom: 8 }}>
                      <span style={{ fontFamily: 'var(--ln-mono)', fontWeight: 600, color: '#f87171' }}>{p.source_agent_id}</span>
                      <span style={{ color: 'var(--ln-text-muted)' }}>污染</span>
                      <span style={{ fontFamily: 'var(--ln-mono)', color: 'var(--ln-text-secondary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{p.contaminated_artifact}</span>
                    </div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
                      {hops.map((hop, i) => (
                        <span key={i} style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                          <span style={{ fontSize: T.xs, padding: '3px 8px', borderRadius: 999, background: 'var(--ln-surface-0)', border: '1px solid var(--ln-border)', color: i === 0 ? '#f87171' : 'var(--ln-text-secondary)', fontFamily: 'var(--ln-mono)' }}>{hop}</span>
                          {i < hops.length - 1 && <span style={{ fontSize: T.xs, color: 'var(--ln-text-muted)' }}>→</span>}
                        </span>
                      ))}
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>

        {/* 3. 策略判定 */}
        <div>
          <div style={{ fontSize: T.xs, fontWeight: 600, color: 'var(--ln-text-muted)', marginBottom: 6, letterSpacing: '0.03em', textTransform: 'uppercase' }}>策略判定</div>
          {chain.policyRows.length === 0 ? (
            <div style={{ fontSize: T.sm, color: 'var(--ln-text-muted)' }}>无策略判定</div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              {chain.policyRows.map((row) => {
                const tone = resultTone(row.result);
                return (
                  // policy_id 唯一（boundary 可重复：同边界多条判定会 key 碰撞）
                  <div key={row.policy_id} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '8px 10px', borderRadius: 8, background: 'var(--ln-surface-0)', border: '1px solid var(--ln-border)' }}>
                    <span style={{ fontSize: T.xs, fontFamily: 'var(--ln-mono)', color: 'var(--ln-text)', minWidth: 90 }}>{row.boundary}</span>
                    <span style={{ fontSize: T.xs, padding: '2px 8px', borderRadius: 999, fontWeight: 600, background: `${tone.color}16`, color: tone.color }}>{tone.label}</span>
                    <span style={{ fontSize: T.xs, fontFamily: 'var(--ln-mono)', color: 'var(--ln-text-muted)', marginLeft: 'auto' }}>{Math.round(row.confidence * 100)}%</span>
                    {row.reason && <span style={{ fontSize: T.xs, color: 'var(--ln-text-muted)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{row.reason}</span>}
                  </div>
                );
              })}
            </div>
          )}
        </div>

        {/* 4. 失配诊断 */}
        <div>
          <div style={{ fontSize: T.xs, fontWeight: 600, color: 'var(--ln-text-muted)', marginBottom: 6, letterSpacing: '0.03em', textTransform: 'uppercase' }}>失配诊断</div>
          {chain.diagnostics.length === 0 ? (
            <div style={{ fontSize: T.sm, color: 'var(--ln-text-muted)' }}>无失配诊断</div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              {chain.diagnostics.map((d) => (
                <div key={d.diagnostic_id} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '8px 10px', borderRadius: 8, background: 'var(--ln-surface-0)', border: '1px solid var(--ln-border)' }}>
                  <span style={{ fontSize: T.xs, fontFamily: 'var(--ln-mono)', color: '#fbbf24', fontWeight: 600 }}>{d.reason}</span>
                  <span style={{ fontSize: T.xs, color: 'var(--ln-text-muted)' }}>intent</span>
                  <span style={{ fontSize: T.xs, fontFamily: 'var(--ln-mono)', color: 'var(--ln-text-secondary)' }}>{d.intent_id ?? '—'}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </SectionPanel>
  );
}
