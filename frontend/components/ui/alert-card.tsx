'use client';

import type { ResponsibilityAlert } from '@/lib/types';
import EngineSection from './engine-section';
import ObserverVerdicts from './observer-verdicts';
import BehaviorTimeline from './behavior-timeline';
import { useState, useRef } from 'react';
import { ChevronRight, AlertTriangle, Info } from 'lucide-react';
import { getSeverityPalette } from '@/lib/severity-palette';

const decisionLabel: Record<string, string> = {
  Safe: 'Safe',
  Unsafe: 'Unsafe',
  Uncertain: 'Uncertain',
};
const authLabel: Record<string, string> = {
  Authoritative: 'Authoritative',
  Supporting: 'Supporting',
  Unavailable: 'Unavailable',
};

export default function AlertCard({ alert }: { alert: ResponsibilityAlert }) {
  const [expanded, setExpanded] = useState(false);
  const cardRef = useRef<HTMLDivElement>(null);
  const s = getSeverityPalette(alert.severity);
  const Icon = alert.severity === 'Critical' || alert.severity === 'Warning' ? AlertTriangle : Info;
  const showObserver = alert.display_profile === 'ObserverJudged';

  // SpotlightCard pattern: CSS custom properties for radial gradient hover
  const handleMouseMove = (e: React.MouseEvent) => {
    const card = cardRef.current;
    if (!card) return;
    const rect = card.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const y = e.clientY - rect.top;
    card.style.setProperty('--mx', `${x}px`);
    card.style.setProperty('--my', `${y}px`);
  };

  const cardStyle: React.CSSProperties = {
    position: 'relative',
    borderRadius: 10,
    padding: '14px 16px',
    cursor: 'pointer',
    background: 'rgba(12, 14, 28, 0.85)',
    border: `1px solid ${s.border}`,
    borderTop: `2px solid ${s.color}`,
    overflow: 'hidden',
    transition: 'border-color 0.2s, background 0.2s',
  };

  return (
    <article
      ref={cardRef}
      style={cardStyle}
      onClick={() => setExpanded(!expanded)}
      onMouseMove={handleMouseMove}
      onMouseLeave={() => {
        cardRef.current?.style.setProperty('--mx', '-100px');
      }}
      aria-label={`Alert: ${alert.title}`}
    >
      {/* Spotlight hover glow — CSS custom-property driven */}
      <div
        style={{
          position: 'absolute',
          inset: 0,
          background:
            'radial-gradient(circle 180px at var(--mx, -100px) var(--my, -100px), rgba(99,102,241,0.06), transparent)',
          pointerEvents: 'none',
          transition: 'opacity 0.3s',
        }}
      />

      {/* Header row */}
      <div
        style={{
          display: 'flex',
          alignItems: 'flex-start',
          justifyContent: 'space-between',
          gap: 8,
          position: 'relative',
          zIndex: 1,
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, minWidth: 0 }}>
          <Icon size={15} color={s.color} style={{ flexShrink: 0 }} />
          <span
            style={{
              fontSize: 13,
              fontWeight: 600,
              color: 'rgba(255,255,255,0.92)',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
            }}
          >
            {alert.title}
          </span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexShrink: 0 }}>
          <span
            style={{
              fontSize: 10,
              fontWeight: 600,
              padding: '2px 6px',
              borderRadius: 4,
              background: s.bg,
              color: s.color,
              textTransform: 'uppercase',
              letterSpacing: '0.05em',
            }}
          >
            {alert.severity}
          </span>
          <span
            style={{
              fontSize: 10,
              padding: '2px 6px',
              borderRadius: 4,
              background: 'rgba(255,255,255,0.04)',
              color: 'rgba(255,255,255,0.4)',
            }}
          >
            {alert.display_profile === 'ObserverJudged' ? 'LLM' : 'Engine'}
          </span>
          <ChevronRight
            size={14}
            color="rgba(255,255,255,0.25)"
            style={{ transition: 'transform 0.2s', transform: expanded ? 'rotate(90deg)' : 'none' }}
          />
        </div>
      </div>

      {/* Summary */}
      <p
        style={{
          marginTop: 8,
          fontSize: 12,
          color: 'rgba(255,255,255,0.5)',
          lineHeight: 1.6,
          position: 'relative',
          zIndex: 1,
        }}
      >
        {alert.summary}
      </p>

      {/* Meta footer */}
      <div
        style={{
          marginTop: 8,
          display: 'flex',
          alignItems: 'center',
          gap: 12,
          position: 'relative',
          zIndex: 1,
        }}
      >
        <span style={{ fontSize: 11, color: 'rgba(255,255,255,0.35)' }}>
          {decisionLabel[alert.decision]}
        </span>
        <span style={{ fontSize: 11, color: 'rgba(255,255,255,0.35)' }}>
          {alert.confidence.toFixed(2)}
        </span>
        <span style={{ fontSize: 11, color: 'rgba(255,255,255,0.35)', marginLeft: 'auto' }}>
          {authLabel[alert.authority]}
        </span>
      </div>

      {/* Expandable detail */}
      {expanded && (
        <div
          style={{
            marginTop: 12,
            paddingTop: 12,
            borderTop: '1px solid rgba(255,255,255,0.04)',
            display: 'flex',
            flexDirection: 'column',
            gap: 12,
            position: 'relative',
            zIndex: 1,
          }}
        >
          <EngineSection engine={alert.engine} />
          {showObserver && <ObserverVerdicts alert={alert} />}
          {alert.timeline.steps.length > 0 && <BehaviorTimeline timeline={alert.timeline} />}
        </div>
      )}
    </article>
  );
}
