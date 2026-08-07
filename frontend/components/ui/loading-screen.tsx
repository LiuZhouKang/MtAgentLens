'use client';

type Props = {
  show: boolean;
};

/**
 * Full-screen loading overlay — "Observation Orbit" loader.
 *
 * All animations are pure CSS `transform`/`opacity` keyframes → GPU COMPOSITOR
 * thread. Main-thread page rendering never stalls them (60fps guaranteed).
 *
 * Visual language mirrors the MtAgentLens mark: three concentric observation
 * orbits (amber → orange → indigo) rotating at different speeds, a radar scan
 * pulse expanding from the core, a breathing amber nucleus, and a gradient
 * brand wordmark.
 */
export default function LoadingScreen({ show }: Props) {
  return (
    <div
      aria-hidden={!show}
      style={{
        position: 'absolute', inset: 0,
        display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
        background:
          'radial-gradient(circle at 50% 42%, rgba(251,191,36,0.05), transparent 58%), var(--ln-bg, #010102)',
        zIndex: 60,
        opacity: show ? 1 : 0,
        pointerEvents: show ? 'auto' : 'none',
        transition: 'opacity 300ms ease',
        willChange: 'opacity',
      }}
    >
      <div style={{ position: 'relative', width: 72, height: 72 }}>
        {/* Radar scan pulses — expanding rings from the core */}
        <div
          className="mtl-load-pulse"
          style={{
            position: 'absolute', inset: 0, borderRadius: '50%',
            border: '1.5px solid rgba(251,191,36,0.35)',
            animation: 'mtl-pulse-spread 2.2s ease-out infinite',
            willChange: 'transform, opacity',
          }}
        />
        <div
          className="mtl-load-pulse"
          style={{
            position: 'absolute', inset: 0, borderRadius: '50%',
            border: '1.5px solid rgba(249,115,22,0.3)',
            animation: 'mtl-pulse-spread 2.2s ease-out 1.1s infinite',
            willChange: 'transform, opacity',
          }}
        />

        {/* Outer orbit — amber */}
        <div
          style={{
            position: 'absolute', inset: 0, borderRadius: '50%',
            border: '2px solid rgba(251,191,36,0.12)',
            borderTopColor: '#fbbf24',
            boxShadow: '0 0 20px rgba(251,191,36,0.12)',
            animation: 'mtl-ring-rotate 1.4s linear infinite',
            willChange: 'transform',
          }}
        />
        {/* Middle orbit — orange, counter-rotating */}
        <div
          style={{
            position: 'absolute', inset: '7px', borderRadius: '50%',
            border: '2px solid rgba(249,115,22,0.14)',
            borderTopColor: '#f97316',
            boxShadow: '0 0 16px rgba(249,115,22,0.12)',
            animation: 'mtl-ring-rotate 2s linear infinite reverse',
            willChange: 'transform',
          }}
        />
        {/* Inner orbit — indigo */}
        <div
          style={{
            position: 'absolute', inset: '14px', borderRadius: '50%',
            border: '2px solid rgba(94,106,210,0.2)',
            borderTopColor: '#5e6ad2',
            boxShadow: '0 0 14px rgba(94,106,210,0.14)',
            animation: 'mtl-ring-rotate 2.6s linear infinite',
            willChange: 'transform',
          }}
        />

        {/* Breathing nucleus */}
        <div
          style={{
            position: 'absolute', inset: 0,
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            animation: 'mtl-core-pulse 1.6s ease-in-out infinite',
            willChange: 'transform',
          }}
        >
          <div
            style={{
              width: 9, height: 9, borderRadius: '50%',
              background: '#fbbf24',
              boxShadow: '0 0 18px rgba(251,191,36,0.6)',
            }}
          />
        </div>
      </div>

      {/* Gradient brand wordmark — breathing glow */}
      <div
        className="mtl-load-word"
        style={{
          marginTop: 22, fontSize: 12,
          fontFamily: 'var(--ln-mono, monospace)',
          letterSpacing: '0.16em', fontWeight: 500,
          background: 'linear-gradient(90deg, #fbbf24, #f97316)',
          WebkitBackgroundClip: 'text',
          backgroundClip: 'text',
          color: 'transparent',
          filter: 'drop-shadow(0 0 10px rgba(251,191,36,0.25))',
          animation: 'mtl-word-breathe 2.2s ease-in-out infinite',
          willChange: 'opacity',
        }}
      >
        MtAgentLens
      </div>
    </div>
  );
}
