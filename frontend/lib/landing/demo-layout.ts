/**
 * Deterministic SVG layout for the mini responsibility-graph demo.
 *
 * `demoGraph` (a pollution → propagation → attribution arc) is laid out with
 * fractional positions scaled to any viewport, and every edge becomes a
 * quadratic-bezier path. Pure and side-effect free — same viewport → same
 * layout, so SSR/CSR and repeated renders stay identical.
 */

import { demoGraph } from './landing-copy';

export type DemoNodeKind = 'Agent' | 'Resource' | 'PolicyBoundary';

export interface DemoLayoutNode {
  id: string;
  /** Absolute position in the given viewport. */
  x: number;
  y: number;
  kind: DemoNodeKind;
  label: string;
}

export interface DemoLayoutEdge {
  source: string;
  target: string;
  edgeType: string;
  /** `M x1 y1 Q cx cy x2 y2` — open curve from source to target. */
  path: string;
}

export interface DemoLayout {
  nodes: DemoLayoutNode[];
  edges: DemoLayoutEdge[];
}

/** Fractional positions (0..1) — the visual arc flows left→right→down. */
const POSITIONS: Record<string, [number, number]> = {
  'agent-writer': [0.18, 0.2],
  'res-shadow': [0.5, 0.28],
  'agent-builder': [0.84, 0.48],
  'res-report': [0.68, 0.84],
  'boundary-system': [0.3, 0.82],
};

const LABELS: Record<string, string> = {
  'agent-writer': 'Writer Agent',
  'res-shadow': '/etc/shadow',
  'agent-builder': 'Builder Agent',
  'res-report': '/docs/report.md',
  'boundary-system': 'Policy Boundary',
};

const KIND: Record<string, DemoNodeKind> = {
  'agent-writer': 'Agent',
  'res-shadow': 'Resource',
  'agent-builder': 'Agent',
  'res-report': 'Resource',
  'boundary-system': 'PolicyBoundary',
};

function quadPath(x1: number, y1: number, x2: number, y2: number, bow: number): string {
  const cx = (x1 + x2) / 2;
  const cy = (y1 + y2) / 2 - bow;
  return `M${x1} ${y1} Q${cx} ${cy} ${x2} ${y2}`;
}

/**
 * Compute the demo layout for a viewport of the given size.
 * Positions are fractions of (width, height); edge curves bow upward by a
 * fixed fraction of the height so taint flows read as an arc.
 */
export function computeDemoLayout(width: number, height: number): DemoLayout {
  const nodes: DemoLayoutNode[] = demoGraph.nodes.map((n) => {
    const [fx, fy] = POSITIONS[n.node_id] ?? [0.5, 0.5];
    return {
      id: n.node_id,
      x: fx * width,
      y: fy * height,
      kind: KIND[n.node_id] ?? 'Resource',
      label: LABELS[n.node_id] ?? n.node_id,
    };
  });

  const byId = new Map(nodes.map((n) => [n.id, n]));
  const edges: DemoLayoutEdge[] = demoGraph.edges.map((e) => {
    const s = byId.get(e.source)!;
    const t = byId.get(e.target)!;
    return {
      source: e.source,
      target: e.target,
      edgeType: e.edge_type,
      path: quadPath(s.x, s.y, t.x, t.y, 0.07 * height),
    };
  });

  return { nodes, edges };
}
