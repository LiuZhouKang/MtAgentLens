'use client';

import { useAppStore } from '@/lib/store';

/**
 * 「实时运行」徽章——appState==='live' 时显示，否则渲染空。
 * 接入 Overview/Investigations/Traces 页，标明当前视图正实时服务后端。
 */
export function LiveRunningBadge() {
  const appState = useAppStore((s) => s.appState);
  if (appState !== 'live') return null;
  return (
    <span
      data-testid="live-running-badge"
      title="正在实时采集与分析"
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 5,
        fontSize: 10,
        fontWeight: 600,
        letterSpacing: '0.05em',
        textTransform: 'uppercase',
        color: '#4ade80',
        border: '1px solid rgba(74,222,128,0.35)',
        borderRadius: 999,
        padding: '2px 9px',
        background: 'rgba(74,222,128,0.08)',
        whiteSpace: 'nowrap',
      }}
    >
      <span
        aria-hidden
        style={{
          width: 6,
          height: 6,
          borderRadius: '50%',
          background: '#4ade80',
          boxShadow: '0 0 6px rgba(74,222,128,0.8)',
        }}
      />
      实时运行
    </span>
  );
}
