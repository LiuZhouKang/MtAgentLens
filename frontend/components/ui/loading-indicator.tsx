'use client';

type Props = {
  size?: number;
  label?: string;
};

/**
 * Inline loading indicator —— 迷你 Observation Orbit（与全站品牌加载统一）。
 *
 * 三同心轨道（amber → orange → indigo）以不同速度旋转（中轨反向），
 * 呼吸琥珀核心脉冲 —— 与 loading-screen 全屏加载同一视觉语言，全站统一。
 * 纯 CSS transform/opacity → compositor 线程，不阻塞主线程。
 */
export default function LoadingIndicator({ size = 40, label }: Props) {
  const inset = (pct: number) => `${(size * pct) / 100}px`;
  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        gap: 10,
      }}
    >
      <div style={{ position: 'relative', width: size, height: size }}>
        {/* 外轨道 — amber */}
        <div
          style={{
            position: 'absolute',
            inset: 0,
            borderRadius: '50%',
            border: '2px solid rgba(251,191,36,0.12)',
            borderTopColor: '#fbbf24',
            boxShadow: `0 0 ${Math.max(8, size * 0.35)}px rgba(251,191,36,0.12)`,
            animation: 'mtl-ring-rotate 1.4s linear infinite',
            willChange: 'transform',
          }}
        />
        {/* 中轨道 — orange，反向 */}
        <div
          style={{
            position: 'absolute',
            inset: inset(18),
            borderRadius: '50%',
            border: '2px solid rgba(249,115,22,0.14)',
            borderTopColor: '#f97316',
            boxShadow: `0 0 ${Math.max(6, size * 0.28)}px rgba(249,115,22,0.12)`,
            animation: 'mtl-ring-rotate 2s linear infinite reverse',
            willChange: 'transform',
          }}
        />
        {/* 内轨道 — indigo */}
        <div
          style={{
            position: 'absolute',
            inset: inset(36),
            borderRadius: '50%',
            border: '2px solid rgba(94,106,210,0.2)',
            borderTopColor: '#5e6ad2',
            boxShadow: `0 0 ${Math.max(5, size * 0.24)}px rgba(94,106,210,0.14)`,
            animation: 'mtl-ring-rotate 2.6s linear infinite',
            willChange: 'transform',
          }}
        />
        {/* 呼吸核心 — amber */}
        <div
          style={{
            position: 'absolute',
            inset: 0,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            animation: 'mtl-core-pulse 1.6s ease-in-out infinite',
            willChange: 'transform, opacity',
          }}
        >
          <div
            style={{
              width: Math.max(4, size * 0.12),
              height: Math.max(4, size * 0.12),
              borderRadius: '50%',
              background: '#fbbf24',
              boxShadow: `0 0 ${Math.max(8, size * 0.45)}px rgba(251,191,36,0.6)`,
            }}
          />
        </div>
      </div>
      {label && (
        <span
          style={{
            fontSize: 10,
            color: 'rgba(255,255,255,0.3)',
            fontFamily: 'var(--ln-mono, monospace)',
            letterSpacing: '0.05em',
          }}
        >
          {label}
        </span>
      )}
    </div>
  );
}
