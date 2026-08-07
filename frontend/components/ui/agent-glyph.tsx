'use client';

import type { ReactNode } from 'react';
import type { AgentGlyphSpec, GlyphKind } from '@/lib/agent-identity';

/**
 * The geometric mark rendered inside an AgentAvatar for opaque (uuid) agent
 * ids. Eight shape families are drawn as white strokes on a 24×24 canvas so
 * they stay crisp at 14px and read distinctly from one another at a glance.
 * All geometry is composed around center (12,12) and rotated by the spec.
 */

const CENTER = 12;
const STROKE = 1.6;

type Point = [number, number];

function polar(cx: number, cy: number, r: number, deg: number): Point {
  const rad = (deg * Math.PI) / 180;
  return [cx + r * Math.cos(rad), cy + r * Math.sin(rad)];
}

function fmt(p: Point): string {
  return `${p[0].toFixed(2)} ${p[1].toFixed(2)}`;
}

function line(from: Point, to: Point): string {
  return `M ${fmt(from)} L ${fmt(to)}`;
}

/** Circular arc of `spanDeg` starting at `fromDeg`, radius `r`. */
function arc(r: number, fromDeg: number, spanDeg: number): string {
  const [x0, y0] = polar(CENTER, CENTER, r, fromDeg);
  const [x1, y1] = polar(CENTER, CENTER, r, fromDeg + spanDeg);
  const large = spanDeg > 180 ? 1 : 0;
  const sweep = spanDeg > 0 ? 1 : 0;
  return `M ${fmt([x0, y0])} A ${r} ${r} 0 ${large} ${sweep} ${fmt([x1, y1])}`;
}

function polygon(pts: Point[]): string {
  return `M ${fmt(pts[0]!)} L ${pts.slice(1).map(fmt).join(' L ')} Z`;
}

function regularPolygon(sides: number, r: number, startDeg: number): string {
  const pts = Array.from({ length: sides }, (_, i) => polar(CENTER, CENTER, r, startDeg + (i * 360) / sides));
  return polygon(pts);
}

// ─── shape families (rotation applied by the outer <g>) ────────────────

function ringPaths(): string[] {
  return [arc(6.4, 20, 320)]; // open ring — leave a 40° gap
}

function arcsPaths(rotation: number): string[] {
  return [arc(5.2, rotation, 100), arc(8, rotation + 170, 100)];
}

function diamondPaths(): string[] {
  return [polygon([[CENTER, 4.6], [18.4, CENTER], [CENTER, 19.4], [5.6, CENTER]])];
}

function barsPaths(): string[] {
  return [
    line([8.6, CENTER - 4.2], [8.6, CENTER + 4.2]),
    line([15.4, CENTER - 2.6], [15.4, CENTER + 2.6]),
  ];
}

function trianglePaths(): string[] {
  return [polygon([[CENTER, 4.6], [17.6, 17], [6.4, 17]])];
}

function hexPaths(): string[] {
  return [regularPolygon(6, 6.6, 30)];
}

function crosshairPaths(): string[] {
  return [
    line([CENTER, 4.2], [CENTER, 7.6]),
    line([CENTER, 16.4], [CENTER, 19.8]),
    line([4.2, CENTER], [7.6, CENTER]),
    line([16.4, CENTER], [19.8, CENTER]),
  ];
}

function dotgridPaths(): Point[] {
  const d = 2.9;
  return [
    [CENTER - d, CENTER - d],
    [CENTER + d, CENTER - d],
    [CENTER - d, CENTER + d],
    [CENTER + d, CENTER + d],
  ];
}

type Geometry = { stroke: string[]; dots?: Point[] };

const KIND_GEOMETRY: Record<GlyphKind, (rotation: number) => Geometry> = {
  ring: () => ({ stroke: ringPaths() }),
  arcs: (r) => ({ stroke: arcsPaths(r) }),
  diamond: () => ({ stroke: diamondPaths() }),
  bars: () => ({ stroke: barsPaths() }),
  triangle: () => ({ stroke: trianglePaths() }),
  hex: () => ({ stroke: hexPaths() }),
  crosshair: () => ({ stroke: crosshairPaths(), dots: [[CENTER, CENTER]] }),
  dotgrid: () => ({ stroke: [], dots: dotgridPaths() }),
};

export default function AgentGlyph({ spec, size }: { spec: AgentGlyphSpec; size: number }) {
  const { stroke, dots } = KIND_GEOMETRY[spec.kind](spec.rotation);
  const children: ReactNode[] = [];
  stroke.forEach((d, i) => children.push(<path key={`s${i}`} d={d} />));
  dots?.forEach(([x, y], i) =>
    children.push(<circle key={`d${i}`} cx={x} cy={y} r={1.4} fill="currentColor" stroke="none" />),
  );

  return (
    <svg
      viewBox="0 0 24 24"
      width={size}
      height={size}
      data-agent-glyph
      data-kind={spec.kind}
      aria-hidden="true"
      fill="none"
      stroke="currentColor"
      strokeWidth={STROKE}
      strokeLinecap="round"
      strokeLinejoin="round"
      style={{ display: 'block', color: 'rgba(255, 255, 255, 0.92)' }}
    >
      <g transform={`rotate(${spec.rotation} 12 12)`}>
        {children}
      </g>
    </svg>
  );
}
