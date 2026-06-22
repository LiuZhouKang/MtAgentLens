'use client';

import LiveIndicator from './live-indicator';
import RunSelector from './run-selector';

export default function Topbar() {
  return (
    <header className="absolute top-0 left-0 right-0 z-30 flex items-center justify-between px-6 py-3 pointer-events-none">
      <div className="pointer-events-auto flex items-center gap-4">
        <span style={{ fontSize: 14, fontWeight: 600, letterSpacing: '0.2em', color: 'rgba(255,255,255,0.85)' }}>
          AGENTSIGHT
        </span>
        <RunSelector />
      </div>
      <div className="pointer-events-auto">
        <LiveIndicator />
      </div>
    </header>
  );
}
