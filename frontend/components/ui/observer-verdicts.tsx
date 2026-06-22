'use client';

import type { ResponsibilityAlert } from '@/lib/types';
import { Check, X, Eye } from 'lucide-react';

export default function ObserverVerdicts({ alert }: { alert: ResponsibilityAlert }) {
  return (
    <div>
      <h4 style={{ fontSize: 11, fontWeight: 500, color: '#a78bfa', marginBottom: 8, display: 'flex', alignItems: 'center', gap: 4, textTransform: 'uppercase', letterSpacing: '0.05em' }}>
        <Eye size={13} /> LLM Independent Judgment
      </h4>

      <p style={{ fontSize: 12, color: 'rgba(255,255,255,0.65)', marginBottom: 8, lineHeight: 1.6 }}>
        {alert.reasoning}
      </p>

      {alert.engine.rule_verdicts.filter(rv => rv.llm_agreed != null).length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
          {alert.engine.rule_verdicts.map((rv) => (
            <div key={rv.rule_id} style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12 }}>
              {rv.llm_agreed === true ? (
                <Check size={13} color="#4ade80" />
              ) : rv.llm_agreed === false ? (
                <X size={13} color="#f87171" />
              ) : null}
              <span style={{ color: 'rgba(255,255,255,0.4)', fontSize: 11 }}>{rv.rule_id}</span>
              {rv.llm_comment && (
                <span style={{ color: 'rgba(255,255,255,0.3)', fontSize: 11 }}>&mdash; {rv.llm_comment}</span>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
