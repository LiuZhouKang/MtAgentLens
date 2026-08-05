import forceAtlas2 from 'graphology-layout-forceatlas2';
import Graph from 'graphology';
import type { LayoutRequest, LayoutResult } from './layout-worker-client';

/**
 * Off-main-thread ForceAtlas2 layout.
 *
 * The main thread posts { nodes, edges, iterations, settings }; this worker
 * builds a graphology graph, runs the (Barnes-Hut optimized) layout, and posts
 * back { id, positions }. Falls back is handled by the client (`runLayoutSync`).
 */
self.onmessage = (e: MessageEvent<LayoutRequest>) => {
  const { id, nodes, edges, iterations, settings } = e.data;
  try {
    const g = new Graph({ multi: true });
    for (const n of nodes) g.addNode(n.id, { x: n.x, y: n.y });
    for (const ed of edges) {
      if (g.hasNode(ed.source) && g.hasNode(ed.target)) g.addEdge(ed.source, ed.target);
    }
    if (g.order > 1) {
      forceAtlas2.assign(g, {
        iterations,
        settings: {
          ...(settings ?? {}),
          barnesHutOptimize: true,
          barnesHutTheta: 1.0,
        },
      });
    }
    const positions: LayoutResult['positions'] = {};
    g.forEachNode((nodeId, attr) => {
      positions[nodeId] = { x: attr.x, y: attr.y };
    });
    (self as unknown as Worker).postMessage({ id, positions } satisfies LayoutResult);
  } catch (err) {
    (self as unknown as Worker).postMessage({ id, error: String(err) } satisfies LayoutResult);
  }
};
