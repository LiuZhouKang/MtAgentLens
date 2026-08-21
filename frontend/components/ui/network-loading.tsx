'use client';

/**
 * "Graph Pulse" — skeleton-style loading state for the Sigma responsibility network.
 *
 * Inspired by Datadog / Grafana dashboard skeleton screens:
 * an abstract, minimal arrangement of nodes and edges that subtly pulses —
 * telegraphing "a graph is being built here" without literal animation clutter.
 */
export default function NetworkLoading({ nodes, edges }: { nodes: number; edges: number }) {
  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        minHeight: 280,
        gap: 16,
        background: 'radial-gradient(circle at 50% 44%, rgba(94,106,210,0.04), transparent 60%)',
      }}
    >
      {/* ── Abstract skeleton graph ── */}
      <div style={{ position: 'relative', width: 120, height: 88 }}>
        {/* Edges — thin, subtle lines */}
        {[
          [20, 35, 60, 22],
          [60, 22, 100, 35],
          [30, 40, 55, 66],
          [65, 62, 90, 52],
          [20, 35, 40, 50],
          [85, 38, 75, 58],
        ].map(([x1, y1, x2, y2], i) => (
          <svg
            key={`e${i}`}
            style={{
              position: 'absolute',
              left: 0,
              top: 0,
              width: 120,
              height: 88,
              overflow: 'visible',
              opacity: 0.18,
              animation: `mtl-edge-fill 2.2s ease-in-out ${i * 0.2}s infinite`,
              willChange: 'opacity',
            }}
          >
            <line x1={x1} y1={y1} x2={x2} y2={y2} stroke="#5e6ad2" strokeWidth="1.5" />
          </svg>
        ))}

        {/* Nodes — small dots at connection points */}
        {[
          [18, 34],
          [40, 18],
          [62, 20],
          [88, 33],
          [98, 48],
          [82, 60],
          [62, 68],
          [40, 52],
          [30, 58],
        ].map(([x, y], i) => (
          <div
            key={`n${i}`}
            style={{
              position: 'absolute',
              left: x - 3,
              top: y - 3,
              width: 6,
              height: 6,
              borderRadius: '50%',
              background: i % 3 === 0 ? '#fbbf24' : i % 3 === 1 ? '#5e6ad2' : '#f97316',
              boxShadow:
                i % 3 === 0
                  ? '0 0 8px rgba(251,191,36,0.35)'
                  : i % 3 === 1
                    ? '0 0 6px rgba(94,106,210,0.3)'
                    : '0 0 6px rgba(249,115,22,0.3)',
              animation: `mtl-node-fade 2.2s ease-in-out ${i * 0.15}s infinite`,
              willChange: 'transform, opacity',
            }}
          />
        ))}
      </div>

      {/* ── Labels ── */}
      <div
        style={{
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          gap: 6,
        }}
      >
        <span
          style={{
            fontSize: 13,
            fontWeight: 500,
            color: 'rgba(255,255,255,0.45)',
            letterSpacing: '-0.01em',
          }}
        >
          责任网络构建中
        </span>
        <span
          style={{
            fontSize: 10,
            fontFamily: 'var(--ln-mono, monospace)',
            color: 'rgba(255,255,255,0.22)',
          }}
        >
          {nodes} 节点 · {edges} 边
        </span>
      </div>
    </div>
  );
}
