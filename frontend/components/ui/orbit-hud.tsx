'use client';

import { useEffect, useState } from 'react';
import { useAppStore } from '@/lib/store';

const hudItems = [
  { key: 'agent_count', label: 'Agents', value: (s: any) => s.graphMetrics?.agent_count },
  { key: 'alert_count', label: 'Alerts', value: (s: any) => s.alerts.length || null },
  { key: 'avg_confidence', label: 'Confidence', value: (s: any) => s.graphMetrics?.avg_confidence?.toFixed(2) },
  { key: 'taint_chains', label: 'Taint Chains', value: (s: any) => s.graphMetrics?.taint_chains },
  { key: 'delegation_depth', label: 'Delegation', value: (s: any) => s.graphMetrics?.delegation_depth },
];

export default function OrbitHUD() {
  const store = useAppStore();
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setMounted(true);
  }, []);

  const hasData = store.graphMetrics != null || store.alerts.length > 0;
  if (!mounted || !hasData) return null;

  const phaseLabel = store.appState === 'live'
    ? 'Phase 1 · Provisional'
    : store.appState === 'replay'
    ? 'Phase 2 · Authoritative'
    : null;

  return (
    <div
      style={{ position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', pointerEvents: 'none' }}
      aria-hidden="true"
    >
      <div style={{ position: 'relative', width: 'min(72vw, 780px)', height: 'min(72vw, 780px)', maxWidth: 780, maxHeight: 780 }}>
        {phaseLabel && (
          <div style={{
            position: 'absolute',
            left: '50%',
            top: '7%',
            transform: 'translateX(-50%)',
            display: 'flex',
            alignItems: 'center',
            gap: 8,
            padding: '8px 12px',
            borderRadius: 999,
            border: '1px solid rgba(108, 194, 255, 0.16)',
            background: 'rgba(4, 10, 22, 0.42)',
            backdropFilter: 'blur(18px)',
          }}>
            <span style={{
              width: 8, height: 8, borderRadius: '50%',
              background: store.appState === 'live' ? '#60a5fa' : '#6366f1',
              animation: store.appState === 'live' ? 'pulse 2s ease-in-out infinite' : 'none',
            }} />
            <span style={{ fontSize: 10, fontWeight: 600, color: 'rgba(255,255,255,0.62)', letterSpacing: '0.14em', textTransform: 'uppercase' }}>
              {phaseLabel}
            </span>
          </div>
        )}

        <div
          style={{
            position: 'absolute',
            inset: '17%',
            borderRadius: '50%',
            border: '1px solid rgba(108, 194, 255, 0.08)',
            boxShadow: '0 0 0 26px rgba(108, 194, 255, 0.015), 0 0 120px rgba(108, 194, 255, 0.04)',
          }}
        />
        <div
          style={{
            position: 'absolute',
            inset: '28%',
            borderRadius: '50%',
            border: '1px dashed rgba(129, 140, 248, 0.1)',
          }}
        />

        {hudItems.map((item, i) => {
          const angle = (i / hudItems.length) * Math.PI * 2 - Math.PI / 2;
          const cx = 50 + 40 * Math.cos(angle);
          const cy = 50 + 40 * Math.sin(angle);
          const raw = item.value(store);
          const displayValue = raw != null ? String(raw) : null;
          if (displayValue == null) return null;

          return (
            <div
              key={item.key}
              style={{
                position: 'absolute',
                display: 'flex',
                flexDirection: 'column',
                alignItems: 'center',
                justifyContent: 'center',
                minWidth: 98,
                minHeight: 72,
                left: `${cx}%`,
                top: `${cy}%`,
                transform: 'translate(-50%, -50%)',
                padding: '10px 12px',
                borderRadius: 18,
                border: '1px solid rgba(108, 184, 255, 0.12)',
                background: 'rgba(5, 11, 23, 0.34)',
                boxShadow: '0 12px 28px rgba(0, 0, 0, 0.16)',
                backdropFilter: 'blur(16px)',
              }}
            >
              <span style={{ fontSize: 22, fontWeight: 600, color: 'rgba(255,255,255,0.92)', lineHeight: 1 }}>
                {displayValue}
              </span>
              <span style={{ marginTop: 6, fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.12em', color: 'rgba(255,255,255,0.45)' }}>
                {item.label}
              </span>
            </div>
          );
        })}
      </div>

      <style>{`
        @keyframes pulse {
          0%, 100% { opacity: 1; }
          50% { opacity: 0.4; }
        }
      `}</style>
    </div>
  );
}
