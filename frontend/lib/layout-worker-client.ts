import forceAtlas2 from 'graphology-layout-forceatlas2';
import Graph from 'graphology';

/**
 * ForceAtlas2 layout — worker-first, sync fallback.
 *
 * `requestLayout` runs the layout in a Web Worker (main thread stays free).
 * If workers are unavailable (SSR, static export quirk, old browser) it rejects
 * with `worker-unavailable`; callers fall back to `runLayoutSync` so a graph is
 * never left blank.
 */

export interface LayoutNodeInput {
  id: string;
  x: number;
  y: number;
}

export interface LayoutEdgeInput {
  source: string;
  target: string;
}

export interface LayoutRequest {
  id: number;
  nodes: LayoutNodeInput[];
  edges: LayoutEdgeInput[];
  iterations: number;
  settings?: Record<string, unknown>;
}

export interface LayoutResult {
  id: number;
  positions?: Record<string, { x: number; y: number }>;
  error?: string;
}

let worker: Worker | null = null;
let seq = 0;
const pending = new Map<number, (r: LayoutResult) => void>();

function getWorker(): Worker | null {
  if (worker) return worker;
  if (typeof window === 'undefined' || typeof Worker === 'undefined') return null;
  try {
    worker = new Worker(new URL('./layout.worker.ts', import.meta.url));
    worker.onmessage = (e: MessageEvent<LayoutResult>) => {
      const resolve = pending.get(e.data.id);
      if (resolve) {
        pending.delete(e.data.id);
        resolve(e.data);
      }
    };
    worker.onerror = () => {
      worker?.terminate();
      worker = null;
    };
    return worker;
  } catch {
    return null;
  }
}

export function requestLayout(req: Omit<LayoutRequest, 'id'>): Promise<LayoutResult> {
  const w = getWorker();
  if (!w) return Promise.reject(new Error('worker-unavailable'));
  const id = ++seq;
  return new Promise((resolve) => {
    pending.set(id, resolve);
    w.postMessage({ ...req, id });
  });
}

// Synchronous fallback — never leaves the graph blank if workers are unavailable.
export function runLayoutSync(
  nodes: LayoutNodeInput[],
  edges: LayoutEdgeInput[],
  iterations: number,
  settings?: Record<string, unknown>,
): Record<string, { x: number; y: number }> {
  const g = new Graph({ multi: true });
  for (const n of nodes) g.addNode(n.id, { x: n.x, y: n.y });
  for (const ed of edges) {
    if (g.hasNode(ed.source) && g.hasNode(ed.target)) g.addEdge(ed.source, ed.target);
  }
  if (g.order > 1) {
    forceAtlas2.assign(g, {
      iterations,
      settings: { ...(settings ?? {}), barnesHutOptimize: true, barnesHutTheta: 1.0 },
    });
  }
  const positions: Record<string, { x: number; y: number }> = {};
  g.forEachNode((nodeId, attr) => {
    positions[nodeId] = { x: attr.x, y: attr.y };
  });
  return positions;
}
