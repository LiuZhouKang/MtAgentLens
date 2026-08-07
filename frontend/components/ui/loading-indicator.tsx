'use client';

type Props = {
  size?: number;
  label?: string;
};

/**
 * Inline loading indicator — compact "observation orbit" spinner.
 * Pure CSS transform/opacity → compositor thread, never stalls on main thread.
 * Used inside panels/graphs while their data mounts.
 */
export default function LoadingIndicator({ size = 40, label }: Props) {
  return (
    <div
      style={{
        display: 'flex', flexDirection: 'column',
        alignItems: 'center', justifyContent: 'center', gap: 10,
      }}
    >
      <div style={{ position: 'relative', width: size, height: size }}>
        <div
          style={{
            position: 'absolute', inset: 0, borderRadius: '50%',
            border: '2px solid rgba(251,191,36,0.12)',
            borderTopColor: '#fbbf24',
            animation: 'mtl-ring-rotate 1s linear infinite',
            willChange: 'transform',
          }}
        />
        <div
          style={{
            position: 'absolute', inset: '22%', borderRadius: '50%',
            background: '#fbbf24',
            boxShadow: '0 0 12px rgba(251,191,36,0.4)',
            animation: 'mtl-core-pulse 1.4s ease-in-out infinite',
            willChange: 'transform, opacity',
          }}
        />
      </div>
      {label && (
        <span
          style={{
            fontSize: 10, color: 'rgba(255,255,255,0.3)',
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
