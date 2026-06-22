'use client';

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

  const hasData = store.graphMetrics != null || store.alerts.length > 0;
  if (!hasData) return null;

  const phaseLabel = store.appState === 'live'
    ? 'Phase 1 · Provisional'
    : store.appState === 'replay'
    ? 'Phase 2 · Authoritative'
    : null;

  return (
    <div
      style={{ position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', pointerEvents: 'none' }}
      aria-live="polite"
    >
      <div style={{ position: 'relative', width: 600, height: 600 }}>
        {/* Phase indicator — center top */}
        {phaseLabel && (
          <div style={{
            position: 'absolute', left: '50%', top: '8%', transform: 'translateX(-50%)',
            display: 'flex', alignItems: 'center', gap: 6,
          }}>
            <span style={{
              width: 8, height: 8, borderRadius: '50%',
              background: store.appState === 'live' ? '#60a5fa' : '#6366f1',
              animation: store.appState === 'live' ? 'pulse 2s ease-in-out infinite' : 'none',
            }} />
            <span style={{ fontSize: 11, fontWeight: 500, color: 'rgba(255,255,255,0.6)', letterSpacing: '0.05em' }}>
              {phaseLabel}
            </span>
          </div>
        )}

        {/* Metric ring */}
        {hudItems.map((item, i) => {
          const angle = (i / hudItems.length) * Math.PI * 2 - Math.PI / 2;
          const cx = 50 + 42 * Math.cos(angle);
          const cy = 50 + 42 * Math.sin(angle);
          const raw = item.value(store);
          const displayValue = raw != null ? String(raw) : null;
          if (displayValue == null) return null;

          return (
            <div
              key={item.key}
              style={{
                position: 'absolute', display: 'flex', flexDirection: 'column', alignItems: 'center',
                left: `${cx}%`, top: `${cy}%`, transform: 'translate(-50%, -50%)',
              }}
            >
              <span style={{ fontSize: 20, fontWeight: 600, color: 'rgba(255,255,255,0.9)' }}>
                {displayValue}
              </span>
              <span style={{ fontSize: 12, textTransform: 'uppercase', letterSpacing: '0.05em', color: '#68687a' }}>
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
