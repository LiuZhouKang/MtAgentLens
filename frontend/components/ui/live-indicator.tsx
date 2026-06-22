'use client';

import { useAppStore } from '@/lib/store';

export default function LiveIndicator() {
  const appState = useAppStore((s) => s.appState);

  if (appState !== 'live' && appState !== 'replay') return null;

  const isLive = appState === 'live';
  return (
    <div className="flex items-center gap-2">
      <span
        className={`w-2 h-2 rounded-full ${
          isLive ? 'bg-green-500 animate-pulse' : 'bg-amber-500'
        }`}
      />
      <span className="text-xs font-medium tracking-wider text-white/70">
        {isLive ? 'LIVE' : 'REPLAY'}
      </span>
    </div>
  );
}
