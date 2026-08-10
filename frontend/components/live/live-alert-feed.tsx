'use client';

import { useAppStore } from '@/lib/store';
import type { ResponsibilityAlert } from '@/lib/types';

const SEV_COLOR: Record<ResponsibilityAlert['severity'], string> = {
  Critical: '#f87171',
  Warning: '#fbbf24',
  Info: '#60a5fa',
};

/** 取最近 N 条告警（store.alerts 实时插入，最新在前）。 */
export function pickRecentAlerts(alerts: ResponsibilityAlert[], limit: number) {
  return alerts.slice(0, limit);
}

/**
 * 实时告警流面板——态势页的「实时」视觉证据：
 * 最新告警顶部插入、点击 focusAlert 高亮图路径；appState==='live' 时显示实时徽章。
 * `aria-live` 播报新告警（a11y）。
 */
export function LiveAlertFeed({ limit = 5 }: { limit?: number }) {
  const alerts = useAppStore((s) => s.alerts);
  const appState = useAppStore((s) => s.appState);
  const focusAlert = useAppStore((s) => s.focusAlert);
  const recent = pickRecentAlerts(alerts, limit);
  const isLive = appState === 'live';

  return (
    <div
      data-testid="live-alert-feed"
      role="region"
      aria-label="实时告警流"
      style={{
        display: 'flex',
        flexDirection: 'column',
        gap: 6,
        width: 260,
        maxHeight: 320,
        borderRadius: 12,
        border: '1px solid rgba(255,255,255,0.08)',
        background: 'rgba(6,6,10,0.82)',
        backdropFilter: 'blur(10px)',
        padding: '10px 10px 8px',
        overflow: 'hidden',
        boxShadow: '0 8px 30px rgba(0,0,0,0.4)',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, paddingBottom: 6 }}>
        <span
          aria-hidden
          style={{
            width: 6,
            height: 6,
            borderRadius: '50%',
            background: isLive ? '#4ade80' : '#6b7280',
            boxShadow: isLive ? '0 0 6px #4ade80' : 'none',
          }}
        />
        <span
          style={{
            fontSize: 10,
            fontWeight: 600,
            letterSpacing: '0.08em',
            color: isLive ? '#4ade80' : 'rgba(255,255,255,0.5)',
            textTransform: 'uppercase',
          }}
        >
          实时告警
        </span>
        {isLive && (
          <span
            data-testid="live-badge"
            style={{
              marginLeft: 'auto',
              fontSize: 9,
              fontWeight: 600,
              letterSpacing: '0.05em',
              color: '#4ade80',
              border: '1px solid rgba(74,222,128,0.35)',
              borderRadius: 999,
              padding: '1px 6px',
            }}
          >
            ● 实时
          </span>
        )}
      </div>

      {recent.length === 0 ? (
        <p style={{ fontSize: 10, color: 'rgba(255,255,255,0.55)', padding: '4px 0 6px' }}>
          暂无告警，实时监控运行中，新告警将在此出现
        </p>
      ) : (
        // role="log"：天然 aria-live=polite + aria-relevant=additions，语义即「日志流」，
        // 新告警增量播报而非首屏整段 + 避免面板级 aria-live 刷屏（审查 M1/M2）。
        // 注：axe 规定 role="log" 仅允许 generic 元素，故用 div 而非 ul。
        <div
          role="log"
          style={{
            margin: 0,
            padding: 0,
            display: 'flex',
            flexDirection: 'column',
            gap: 4,
            overflowY: 'auto',
          }}
        >
          {recent.map((a) => (
            <div key={a.alert_id}>
              <button
                type="button"
                onClick={() => focusAlert(a)}
                title={a.summary}
                // H1：严重度仅靠彩色圆点（aria-hidden）表达，读屏器丢失——并入可访问名
                aria-label={`${a.severity}：${a.title}，置信度 ${Math.round(a.confidence * 100)}%`}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 7,
                  width: '100%',
                  textAlign: 'left',
                  padding: '6px 8px',
                  borderRadius: 8,
                  border: '1px solid rgba(255,255,255,0.06)',
                  background: 'rgba(255,255,255,0.03)',
                  color: 'rgba(255,255,255,0.85)',
                  cursor: 'pointer',
                  fontSize: 11,
                  transition: 'background 120ms ease',
                }}
                onMouseEnter={(e) => (e.currentTarget.style.background = 'rgba(255,255,255,0.07)')}
                onMouseLeave={(e) => (e.currentTarget.style.background = 'rgba(255,255,255,0.03)')}
              >
                <span
                  aria-hidden
                  style={{
                    width: 5,
                    height: 5,
                    borderRadius: '50%',
                    background: SEV_COLOR[a.severity] ?? '#6b7280',
                    flexShrink: 0,
                  }}
                />
                <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {a.title}
                </span>
                <span
                  style={{
                    fontSize: 9,
                    fontFamily: 'var(--ln-mono, monospace)',
                    color: 'rgba(255,255,255,0.55)',
                    flexShrink: 0,
                  }}
                >
                  {Math.round(a.confidence * 100)}%
                </span>
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
