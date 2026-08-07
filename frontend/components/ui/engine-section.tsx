'use client';

import type { EngineSection as EngineSectionType } from '@/lib/types';

export default function EngineSection({ engine }: { engine: EngineSectionType }) {
  return (
    <div>
      <h4
        style={{
          fontSize: 11,
          fontWeight: 500,
          color: '#6366f1',
          marginBottom: 8,
          textTransform: 'uppercase',
          letterSpacing: '0.05em',
        }}
      >
        Engine Analysis &middot; Score {engine.classification_score.toFixed(2)}
      </h4>

      {engine.rule_verdicts.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 4, marginBottom: 8 }}>
          {engine.rule_verdicts.map((rv) => (
            <div
              key={rv.rule_id}
              style={{ display: 'flex', alignItems: 'baseline', gap: 6, fontSize: 12 }}
            >
              <code
                style={{
                  fontSize: 11,
                  color: '#a5b4fc',
                  background: 'rgba(165,180,252,0.08)',
                  padding: '1px 4px',
                  borderRadius: 3,
                }}
              >
                {rv.rule_id}
              </code>
              <span style={{ color: 'rgba(255,255,255,0.6)', flex: 1 }}>{rv.description}</span>
              <span
                style={{ color: 'rgba(255,255,255,0.3)', fontSize: 11, fontFamily: 'monospace' }}
              >
                {rv.matched_resource}
              </span>
            </div>
          ))}
        </div>
      )}

      {engine.pattern_verdicts.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
          {engine.pattern_verdicts.map((pv) => (
            <div
              key={pv.template_id}
              style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12 }}
            >
              <code
                style={{
                  fontSize: 11,
                  color: '#67e8f9',
                  background: 'rgba(103,232,249,0.08)',
                  padding: '1px 4px',
                  borderRadius: 3,
                }}
              >
                {pv.template_id}
              </code>
              <span style={{ color: 'rgba(255,255,255,0.4)', fontSize: 11 }}>
                confidence {pv.match_confidence.toFixed(2)}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
