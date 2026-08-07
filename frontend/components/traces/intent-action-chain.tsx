'use client';

import { useMemo } from 'react';
import type { Action, CorrelationEvidence, Intent } from '@/lib/types';

const T = { xs: 9.5, sm: 10.5, md: 12, lg: 14, xl: 16 };
const S = { xs: 2, sm: 4, md: 8, lg: 12, xl: 16 };

const matchColors: Record<string, string> = {
  L1Exact: '#34d399', L2Normalized: '#60a5fa', L3Containment: '#f59e0b',
  L4Basename: '#f59e0b', L5Substring: '#f43f5e',
};
const signalColors: Record<string, string> = { HighSignal: '#f87171', LowSignal: '#60a5fa' };

interface IntentGroup {
  intent: Intent;
  actions: Action[];            // 该 intent 直接触发的 actions（经 evidence 桥接）
  evidence: CorrelationEvidence[];
  orphan: boolean;              // 无任何 evidence 关联 → 灰显
}

function shortArgs(args: unknown[]): string {
  const items = (args ?? [])
    .map((a) => (a && typeof a === 'object' ? Object.values(a as object)[0] : a))
    .filter((v) => typeof v === 'string' || typeof v === 'number');
  return items.join(' ').slice(0, 120);
}

export default function IntentActionChain({
  intents, actions, evidence,
}: { intents: Intent[]; actions: Action[]; evidence: CorrelationEvidence[] }) {
  const groups = useMemo<IntentGroup[]>(() => {
    const actionById = new Map(actions.map((a) => [a.action_id, a]));
    const evidenceByIntent = new Map<string, CorrelationEvidence[]>();
    const evidenceByAction = new Map<string, CorrelationEvidence[]>();
    for (const ev of evidence) {
      if (!evidenceByIntent.has(ev.intent_id)) evidenceByIntent.set(ev.intent_id, []);
      evidenceByIntent.get(ev.intent_id)!.push(ev);
      if (!evidenceByAction.has(ev.action_id)) evidenceByAction.set(ev.action_id, []);
      evidenceByAction.get(ev.action_id)!.push(ev);
    }
    return intents.map((intent) => {
      const intentEvidence = evidenceByIntent.get(intent.intent_id) ?? [];
      const actionIds = new Set(intentEvidence.map((ev) => ev.action_id));
      const linkedActions = Array.from(actionIds)
        .map((id) => actionById.get(id))
        .filter((a): a is Action => a != null)
        .sort((a, b) => a.timestamp_ns - b.timestamp_ns);
      return {
        intent,
        actions: linkedActions,
        evidence: intentEvidence.sort((a, b) => a.timestamp_ns - b.timestamp_ns),
        orphan: intentEvidence.length === 0,
      };
    });
  }, [intents, actions, evidence]);

  const orphanActions = useMemo(() => {
    const linkedActionIds = new Set(groups.flatMap((g) => g.actions.map((a) => a.action_id)));
    return actions.filter((a) => !linkedActionIds.has(a.action_id));
  }, [groups, actions]);

  if (groups.length === 0 && orphanActions.length === 0) {
    return (
      <div style={{ textAlign: 'center', padding: '20px 0', color: 'var(--ln-text-muted)', fontSize: T.sm }}>
        此数据源未提取到意图/动作关联
      </div>
    );
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 0 }}>
      {groups.map(({ intent, actions: intentActions, evidence: intentEvidence, orphan }) => (
        <div key={intent.intent_id} style={{ opacity: orphan ? 0.55 : 1, borderBottom: '1px solid var(--ln-border)' }}>
          {/* intent group header — static row, always visible */}
          <div
            style={{ display: 'flex', alignItems: 'center', gap: S.sm, width: '100%', padding: `${S.sm}px ${S.sm}px`, textAlign: 'left' }}
          >
            <span style={{ fontSize: T.sm, fontWeight: 600, color: orphan ? 'var(--ln-text-muted)' : 'var(--ln-accent)', flexShrink: 0 }}>
              {intent.tool_name}
            </span>
            <span style={{ fontSize: 9, padding: '1px 5px', borderRadius: 3, background: 'var(--ln-surface-2)', color: 'var(--ln-text-muted)', fontFamily: 'var(--ln-mono)' }}>
              {intent.intent_class}
            </span>
            <span className="ln-panel__meta" style={{ fontFamily: 'var(--ln-mono)' }}>
              {intent.authority_source}
            </span>
            <span className="ln-panel__meta" style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {shortArgs(intent.tool_args)}
            </span>
            <span className="ln-panel__meta">{intentEvidence.length} 证据</span>
            {orphan && <span style={{ fontSize: 9, color: 'var(--ln-text-muted)' }}>无证据关联</span>}
          </div>

          {/* action cards + evidence — always rendered */}
          <div style={{ padding: `${S.xs}px 0 ${S.md}px ${S.md}px`, display: 'flex', flexDirection: 'column', gap: S.sm }}>
            {intentActions.length === 0 ? (
              <span className="ln-panel__meta">该意图未产生系统效应</span>
            ) : (
              intentActions.map((action) => {
                const actionEvidence = intentEvidence.filter((ev) => ev.action_id === action.action_id);
                return (
                  <div key={action.action_id} style={{ padding: `${S.xs + 2}px ${S.sm}px`, borderRadius: 6, background: 'var(--ln-surface-0)', border: '1px solid var(--ln-border)' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                      <span style={{ fontSize: T.sm, fontWeight: 600, color: 'var(--ln-text)' }}>{action.action_type}</span>
                      <span style={{ fontSize: 9, padding: '1px 5px', borderRadius: 3, background: `${signalColors[action.signal_class] ?? '#68687a'}18`, color: signalColors[action.signal_class] ?? '#68687a', fontFamily: 'var(--ln-mono)' }}>
                        {action.signal_class}
                      </span>
                      <span className="ln-panel__meta">{action.derivation_mode}</span>
                    </div>
                    {shortArgs(action.normalized_args) && (
                      <div style={{ fontSize: 10, color: 'var(--ln-text-muted)', fontFamily: 'var(--ln-mono)', marginTop: 2, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {shortArgs(action.normalized_args)}
                      </div>
                    )}
                    {actionEvidence.map((ev) => (
                      <div key={ev.evidence_id} style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 4, fontSize: 10 }}>
                        <span style={{ color: matchColors[ev.match_level] ?? '#68687a', fontWeight: 600, fontFamily: 'var(--ln-mono)' }}>
                          {ev.match_level}
                        </span>
                        <div style={{ width: 40, height: 3, borderRadius: 999, background: 'var(--ln-surface-2)', overflow: 'hidden' }}>
                          <div style={{ height: '100%', width: `${Math.round(ev.confidence * 100)}%`, background: matchColors[ev.match_level] ?? '#68687a' }} />
                        </div>
                        <span className="ln-panel__meta" style={{ fontFamily: 'var(--ln-mono)' }}>
                          {ev.evidence_id.slice(0, 12)}
                        </span>
                      </div>
                    ))}
                  </div>
                );
              })
            )}
          </div>
        </div>
      ))}

      {/* orphan actions list — always rendered when present */}
      {orphanActions.length > 0 && (
        <div style={{ marginTop: S.sm, opacity: 0.55 }}>
          <span className="ln-panel__meta">孤立动作（{orphanActions.length}）</span>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, marginTop: 4 }}>
            {orphanActions.map((a) => (
              <span key={a.action_id} style={{ fontSize: 9, padding: '2px 6px', borderRadius: 4, background: 'var(--ln-surface-2)', color: 'var(--ln-text-muted)', fontFamily: 'var(--ln-mono)' }}>
                {a.action_type}
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
