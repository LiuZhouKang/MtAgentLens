'use client';

import { useAppStore } from '@/lib/store';

export default function LiveIndicator() {
  const appState = useAppStore((s) => s.appState);

  if (appState !== 'live' && appState !== 'replay') return null;

  const isLive = appState === 'live';
  return (
    <div className="prism-chip" aria-live="polite">
      <span
        aria-hidden="true"
        style={{
          width: 8,
          height: 8,
          borderRadius: '50%',
          background: isLive ? 'var(--normal)' : 'var(--warning)',
          boxShadow: isLive
            ? '0 0 14px rgba(52, 211, 153, 0.5)'
            : '0 0 14px rgba(251, 191, 36, 0.4)',
          animation: isLive ? 'prismLivePulse 1.6s ease-in-out infinite' : undefined,
        }}
      />
      <span
        style={{
          fontSize: 11,
          fontWeight: 600,
          letterSpacing: '0.14em',
          color: 'rgba(255,255,255,0.7)',
        }}
      >
        {isLive ? 'LIVE' : 'REPLAY'}
      </span>
    </div>
  );
}
