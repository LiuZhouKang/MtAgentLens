'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import Graph from 'graphology';
import Sigma from 'sigma';
import NetworkLoading from '@/components/ui/network-loading';
import { pickLodLevel, summarizeToLod } from '@/lib/graph-lod';
import { requestLayout, runLayoutSync } from '@/lib/layout-worker-client';
import type { GraphData } from '@/lib/types';

const NODE_COLORS: Record<string, string> = {
  Agent: '#60a5fa',
  Resource: '#4ade80',
  PolicyBoundary: '#f87171',
  Process: '#fbbf24',
  ToolCall: '#a78bfa',
  Credential: '#fb923c',
  Context: '#22d3ee',
};
const NODE_DEFAULT = '#5c5c5f';

const EDGE_COLORS: Record<string, string> = {
  IntentCausesAction: '#60a5fa',
  PolicyBoundaryCrossing: '#f87171',
  AgentDelegatesTo: '#a78bfa',
  AuthorityTransfer: '#fbbf24',
  Delegation: '#8b5cf6',
  TaintPropagation: '#fb923c',
  ViolationProvenance: '#ef4444',
  ContextFlowsFrom: '#22d3ee',
  TaintPropagatesTo: '#fb923c',
  CompetingExplanation: '#a78bfa',
  CapabilityUse: '#4ade80',
  ContextInheritance: '#22d3ee',
};
const EDGE_DEFAULT = '#3a3a3d';

const NODE_SIZE: Record<string, number> = {
  Agent: 8,
  PolicyBoundary: 9,
  Resource: 5,
  Process: 5,
  ToolCall: 5,
  Credential: 4,
  Context: 4,
};

// Rendering budgets — beyond these, the WebGL canvas chokes and the main thread
// stalls. We sample edges by importance and skip labels/edge-events instead.
const EDGE_RENDER_BUDGET = 20_000;
const NODE_LABEL_LIMIT = 60;

// Blend a hex color toward white (amount 0..1) for neighbor highlight
function lightenColor(hex: string, amount: number): string {
  const c = hex.replace('#', '');
  const full =
    c.length === 3
      ? c
          .split('')
          .map((x) => x + x)
          .join('')
      : c;
  const r = parseInt(full.slice(0, 2), 16);
  const g = parseInt(full.slice(2, 4), 16);
  const b = parseInt(full.slice(4, 6), 16);
  const mix = (v: number) => Math.round(v + (255 - v) * amount);
  return `rgb(${mix(r)},${mix(g)},${mix(b)})`;
}

// Blend a hex color toward black (amount 0..1) for non-neighbor dim
function dimColor(hex: string, amount: number): string {
  const c = hex.replace('#', '');
  const full =
    c.length === 3
      ? c
          .split('')
          .map((x) => x + x)
          .join('')
      : c;
  const r = parseInt(full.slice(0, 2), 16);
  const g = parseInt(full.slice(2, 4), 16);
  const b = parseInt(full.slice(4, 6), 16);
  const mix = (v: number) => Math.round(v * (1 - amount));
  return `rgb(${mix(r)},${mix(g)},${mix(b)})`;
}

// Minimal render shape — raw graph and LOD summary both convert into it, so the
// builder never depends on the (heavier) full ResponsibilityNode type.
type RenderNode = { id: string; node_type: string };
type RenderEdge = {
  source: string;
  target: string;
  edge_type: string;
  edge_id: string;
  confidence: number;
};
type RenderGraph = { nodes: RenderNode[]; edges: RenderEdge[] };

function toRenderGraph(graph: GraphData): RenderGraph {
  return {
    nodes: graph.nodes.map((n) => ({ id: n.node_id, node_type: n.node_type })),
    edges: graph.edges.map((e) => ({
      source: e.source,
      target: e.target,
      edge_type: e.edge_type,
      edge_id: e.edge_id,
      confidence: e.confidence,
    })),
  };
}

function toRenderGraphLod(graph: GraphData): RenderGraph {
  const lod = summarizeToLod(graph, 'cluster');
  return {
    nodes: lod.nodes.map((n) => ({ id: n.id, node_type: n.node_type })),
    edges: lod.edges.map((e, i) => ({
      source: e.source,
      target: e.target,
      edge_type: e.edge_type,
      edge_id: `lod-e${i}`,
      confidence: 1,
    })),
  };
}

type Props = {
  graph: GraphData;
  height?: number;
  onNodeClick?: (nodeId: string) => void;
  onNodeHover?: (nodeId: string | null) => void;
  highlightedEdgeIds?: string[];
};

export function SigmaNetwork({
  graph,
  height = 360,
  onNodeClick,
  onNodeHover,
  highlightedEdgeIds = [],
}: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const sigmaRef = useRef<Sigma | null>(null);
  const graphologyRef = useRef<Graph | null>(null);
  const callbacksRef = useRef({ onNodeClick, onNodeHover });
  const [built, setBuilt] = useState(false);
  // Hover tooltip state — filled by the renderer's enterNode/mousemove events.
  const [hoverInfo, setHoverInfo] = useState<{
    x: number;
    y: number;
    label: string;
    type: string;
  } | null>(null);

  // Keep latest highlight list in a ref so it never re-triggers the build effect.
  // (`= []` default param is a NEW array every render — putting it in deps would
  // cancel the idle build on every render and the network would never appear.)
  const highlightedRef = useRef(highlightedEdgeIds);

  // Mirror freshest props into refs AFTER render — the idle build (wired once)
  // reads them, so it always sees current callbacks/highlights. Never write refs
  // during render (React 19 hook rules).
  useEffect(() => {
    callbacksRef.current = { onNodeClick, onNodeHover };
    highlightedRef.current = highlightedEdgeIds;
  });

  // ── LOD input ──
  // Raw graph renders fully up to LOD_RAW_LIMIT nodes; beyond that we feed the
  // builder the cluster-level summary so the canvas never sees a 100k-node graph.
  const lodLevel = useMemo(() => pickLodLevel(graph.nodes.length), [graph.nodes.length]);
  const renderGraph: RenderGraph = useMemo(
    () => (lodLevel === 'raw' ? toRenderGraph(graph) : toRenderGraphLod(graph)),
    [graph, lodLevel],
  );

  // Cache data identity — avoid O(n log n) sort on every render
  const dataKey = useMemo(
    () =>
      renderGraph.nodes
        .map((n) => n.id)
        .sort()
        .join(',') +
      '|' +
      renderGraph.edges
        .map((e) => e.edge_id)
        .sort()
        .join(','),
    [renderGraph],
  );
  const dataKeyRef = useRef(dataKey);

  // Defer heavy build (layout + WebGL Sigma) to idle time so it never blocks
  // page transitions. First paint is a lightweight skeleton.
  // Dependencies are STABLE (dataKey, height) — never include props that are
  // fresh arrays/objects each render, or the effect cancels itself infinitely.
  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    // Only rebuild when the actual data identity changes
    const dataChanged = dataKeyRef.current !== dataKey;
    if (dataChanged) {
      dataKeyRef.current = dataKey;
      setBuilt(false); // show skeleton while new graph builds
    } else if (sigmaRef.current) {
      return; // already built for this data — nothing to do
    }

    const buildStart = Date.now(); // minimum loading-animation display timer
    let cancelled = false;
    let builtTimer: ReturnType<typeof setTimeout> | null = null;

    const buildGraph = async () => {
      // cleanup previous renderer
      if (sigmaRef.current) {
        sigmaRef.current.kill();
        sigmaRef.current = null;
      }
      if (graphologyRef.current) {
        graphologyRef.current.clear();
        graphologyRef.current = null;
      }

      // build graphology graph (multi: true — real data has duplicate source→target edges)
      const g = new Graph({ multi: true });
      const n = renderGraph.nodes.length;

      renderGraph.nodes.forEach((node, i) => {
        const angle = (i / Math.max(n, 1)) * Math.PI * 2;
        g.addNode(node.id, {
          label: node.id.length > 18 ? node.id.slice(0, 16) + '…' : node.id,
          color: NODE_COLORS[node.node_type] ?? NODE_DEFAULT,
          size: NODE_SIZE[node.node_type] ?? 5,
          nodeType: node.node_type,
          x: Math.cos(angle) * 0.5,
          y: Math.sin(angle) * 0.5,
        });
      });

      // ── Edge render budget ──
      // Keep critical edge types whole, then top-confidence edges, capped so the
      // WebGL renderer never chokes on a million edges.
      let edgeSource = renderGraph.edges;
      if (edgeSource.length > EDGE_RENDER_BUDGET) {
        const critical = edgeSource.filter(
          (e) => e.edge_type === 'PolicyBoundaryCrossing' || e.edge_type === 'ViolationProvenance',
        );
        const rest = edgeSource
          .filter(
            (e) =>
              e.edge_type !== 'PolicyBoundaryCrossing' && e.edge_type !== 'ViolationProvenance',
          )
          .sort((a, b) => b.confidence - a.confidence);
        edgeSource = [...critical, ...rest].slice(0, EDGE_RENDER_BUDGET);
      }

      for (const edge of edgeSource) {
        if (!g.hasNode(edge.source) || !g.hasNode(edge.target)) continue;
        g.addEdge(edge.source, edge.target, {
          originalId: edge.edge_id,
          color: EDGE_COLORS[edge.edge_type] ?? EDGE_DEFAULT,
          size:
            edge.edge_type === 'PolicyBoundaryCrossing' || edge.edge_type === 'ViolationProvenance'
              ? 2
              : 1,
          edgeType: edge.edge_type,
        });
      }

      // ── Layout in a Web Worker (main thread stays interactive) ──
      const nodeInputs: { id: string; x: number; y: number }[] = [];
      g.forEachNode((id, attr) => nodeInputs.push({ id, x: attr.x, y: attr.y }));
      const edgeInputs: { source: string; target: string }[] = [];
      g.forEachEdge((_e, _a, src, tgt) => edgeInputs.push({ source: src, target: tgt }));

      try {
        const res = await requestLayout({ nodes: nodeInputs, edges: edgeInputs, iterations: 40 });
        if (res.error || !res.positions) throw new Error(res.error ?? 'no positions');
        for (const [id, pos] of Object.entries(res.positions)) {
          g.setNodeAttribute(id, 'x', pos.x);
          g.setNodeAttribute(id, 'y', pos.y);
        }
      } catch {
        // Synchronous fallback — never leave the network blank.
        const positions = runLayoutSync(nodeInputs, edgeInputs, 40);
        for (const [id, pos] of Object.entries(positions)) {
          g.setNodeAttribute(id, 'x', pos.x);
          g.setNodeAttribute(id, 'y', pos.y);
        }
      }

      if (cancelled) return;

      // guarantee coords
      g.forEachNode((node, attr) => {
        if (typeof attr.x !== 'number' || !Number.isFinite(attr.x))
          attr.x = (Math.random() - 0.5) * 0.5;
        if (typeof attr.y !== 'number' || !Number.isFinite(attr.y))
          attr.y = (Math.random() - 0.5) * 0.5;
      });

      // normalize to [-1, 1]
      let minX = Infinity,
        minY = Infinity,
        maxX = -Infinity,
        maxY = -Infinity;
      g.forEachNode((_n, attr) => {
        minX = Math.min(minX, attr.x);
        maxX = Math.max(maxX, attr.x);
        minY = Math.min(minY, attr.y);
        maxY = Math.max(maxY, attr.y);
      });
      const rx = Math.max(maxX - minX, 1),
        ry = Math.max(maxY - minY, 1);
      g.forEachNode((_n, attr) => {
        attr.x = ((attr.x - minX) / rx - 0.5) * 2;
        attr.y = ((attr.y - minY) / ry - 0.5) * 2;
      });

      // highlight edges by original edge_id (multi-graph keys differ)
      const highlightList = highlightedRef.current;
      if (highlightList.length > 0) {
        const hs = new Set(highlightList);
        g.forEachEdge((_key, attr) => {
          const isHighlighted = typeof attr.originalId === 'string' && hs.has(attr.originalId);
          attr.color = isHighlighted ? '#f87171' : 'rgba(255,255,255,0.06)';
          attr.size = isHighlighted ? 2.5 : 0.5;
        });
      }

      // ── Drag + hover state (closure-scoped) ──
      let draggedNode: string | null = null;
      let isDragging = false;
      let hoveredNode: string | null = null;
      const neighborSet = new Set<string>();
      const hoverNeighborSet = new Set<string>();
      const connectedEdgeSet = new Set<string>();

      // Recompute highlight sets for a dragged node's neighborhood
      const setNeighborhood = (nodeId: string | null) => {
        neighborSet.clear();
        connectedEdgeSet.clear();
        if (nodeId) {
          g.forEachNeighbor(nodeId, (n) => neighborSet.add(n));
          g.forEachEdge(nodeId, (edge) => connectedEdgeSet.add(edge));
        }
      };

      // create sigma renderer — budgets keep the WebGL canvas responsive on
      // large graphs: no labels, no edge events, and edges hidden while moving.
      const renderer = new Sigma(g, container, {
        renderLabels: g.order < NODE_LABEL_LIMIT,
        hideEdgesOnMove: g.size > EDGE_RENDER_BUDGET / 2,
        hideLabelsOnMove: true,
        defaultNodeColor: NODE_DEFAULT,
        defaultEdgeColor: EDGE_DEFAULT,
        labelColor: { color: '#8a8a8e' },
        labelFont: 'Inter, system-ui, sans-serif',
        labelSize: 10,
        minEdgeThickness: 0.3,
        zoomingRatio: 0.8,
        zoomDuration: 200,
        enableEdgeEvents: g.size < EDGE_RENDER_BUDGET,
        stagePadding: 16,
        autoRescale: true,
        autoCenter: true,
      });

      // ── Top-tier drag visuals via reducer (pure render-time, no graph mutation) ──
      // Dragged node: bright white + enlarged. Neighbors: lightened + labeled.
      // Everything else: dimmed. Connected edges stay visible; others fade.
      renderer.setSetting('nodeReducer', (node, data) => {
        if (node === draggedNode) {
          return { ...data, color: '#ffffff', size: (data.size ?? 5) * 1.7, label: data.label };
        }
        if (hoveredNode && !isDragging) {
          if (node === hoveredNode) {
            return { ...data, color: '#ffffff', size: (data.size ?? 5) * 1.4, label: data.label };
          }
          if (hoverNeighborSet.has(node)) {
            return {
              ...data,
              color: lightenColor(data.color ?? NODE_DEFAULT, 0.22),
              size: (data.size ?? 5) * 1.1,
            };
          }
        }
        if (neighborSet.has(node)) {
          return {
            ...data,
            color: lightenColor(data.color ?? NODE_DEFAULT, 0.35),
            size: (data.size ?? 5) * 1.15,
            label: data.label,
          };
        }
        if (isDragging) {
          return {
            ...data,
            color: dimColor(data.color ?? NODE_DEFAULT, 0.35),
            size: (data.size ?? 5) * 0.85,
            label: '',
          };
        }
        return data;
      });

      renderer.setSetting('edgeReducer', (edge, data) => {
        if (connectedEdgeSet.has(edge) && isDragging) {
          return { ...data, color: '#f8fafc', size: Math.max(data.size ?? 1, 1.6) };
        }
        if (isDragging) {
          return { ...data, color: 'rgba(255,255,255,0.04)', size: 0.2 };
        }
        return data;
      });

      // ── Drag interaction (Sigma official pattern) ──
      // downNode starts the drag; moveBody tracks the cursor (Sigma manages
      // coordinates — works even when the cursor leaves the container);
      // upNode ends it. viewportToGraph converts container pixels → graph coords.
      //
      // Camera lock: Sigma sets isMouseDown BEFORE downNode fires, so
      // preventSigmaDefault in downNode can't stop the camera from panning.
      // Instead we intercept the mouse captor's move events while dragging and
      // preventSigmaDefault there — Sigma skips its camera handling and ONLY
      // the node moves, not the whole view.
      const mouseCaptor = renderer.getMouseCaptor();
      const lockCameraDuringDrag = (coords: any) => {
        if (isDragging) coords.preventSigmaDefault();
      };
      mouseCaptor.on('mousemove', lockCameraDuringDrag);
      mouseCaptor.on('mousemovebody', lockCameraDuringDrag);

      renderer.on('downNode', ({ node, event }) => {
        event.preventSigmaDefault();
        isDragging = true;
        draggedNode = node;
        setNeighborhood(node);
        renderer.refresh();
      });

      renderer.on('moveBody', ({ event }) => {
        if (!isDragging || !draggedNode) return;
        const pos = renderer.viewportToGraph({ x: event.x, y: event.y });
        g.setNodeAttribute(draggedNode, 'x', pos.x);
        g.setNodeAttribute(draggedNode, 'y', pos.y);
      });

      renderer.on('upNode', () => {
        if (!isDragging) return;
        isDragging = false;
        draggedNode = null;
        setNeighborhood(null);
        renderer.refresh();
      });

      // wire events
      renderer.on('clickNode', ({ node }) => callbacksRef.current.onNodeClick?.(node));

      // ── Hover: neighbor highlight + floating tooltip ──
      renderer.on('enterNode', ({ node }) => {
        callbacksRef.current.onNodeHover?.(node);
        hoveredNode = node;
        hoverNeighborSet.clear();
        g.forEachNeighbor(node, (n) => hoverNeighborSet.add(n));
        const attrs = g.getNodeAttributes(node);
        const pos = renderer.graphToViewport({ x: attrs.x as number, y: attrs.y as number });
        setHoverInfo({
          x: pos.x,
          y: pos.y,
          label: (attrs.label as string) ?? node,
          type: (attrs.nodeType as string) ?? '',
        });
        renderer.refresh();
      });
      renderer.on('leaveNode', () => {
        callbacksRef.current.onNodeHover?.(null);
        hoveredNode = null;
        hoverNeighborSet.clear();
        setHoverInfo(null);
        renderer.refresh();
      });
      // Track the cursor while hovering so the tooltip follows the mouse.
      const trackHoverPos = (coords: { x: number; y: number }) => {
        if (hoveredNode) {
          setHoverInfo((prev) => (prev ? { ...prev, x: coords.x + 10, y: coords.y - 8 } : prev));
        }
      };
      mouseCaptor.on('mousemove', trackHoverPos);
      mouseCaptor.on('mousemovebody', trackHoverPos);

      sigmaRef.current = renderer;
      graphologyRef.current = g;
      // Minimum 600ms display time — the loading skeleton always has a chance to
      // animate before the network replaces it (no invisible flash).
      const elapsed = Date.now() - buildStart;
      const remaining = Math.max(0, 600 - elapsed);
      builtTimer = setTimeout(() => setBuilt(true), remaining);
    };

    // Defer heavy work to idle — never block the main thread on mount
    const idle = (window as any).requestIdleCallback ?? ((cb: () => void) => setTimeout(cb, 50));
    const idleHandle = idle(
      () => {
        buildGraph();
      },
      { timeout: 400 },
    );

    return () => {
      cancelled = true;
      // Cancel pending idle build + the built-delay timer
      if ((window as any).cancelIdleCallback) {
        (window as any).cancelIdleCallback(idleHandle);
      } else {
        clearTimeout(idleHandle);
      }
      if (builtTimer) clearTimeout(builtTimer);
      if (sigmaRef.current) {
        sigmaRef.current.kill();
        sigmaRef.current = null;
      }
      if (graphologyRef.current) {
        graphologyRef.current.clear();
        graphologyRef.current = null;
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dataKey, height]);

  // The container div is ALWAYS rendered so containerRef is always valid —
  // otherwise the idle build effect would bail out on mount (ref === null) and
  // the network would never appear. The skeleton is an overlay on top, removed
  // once the graph finishes building.
  return (
    <div
      ref={containerRef}
      style={{
        height,
        borderRadius: 12,
        background: 'radial-gradient(circle at 50% 40%, rgba(94,106,210,0.06), transparent 60%)',
        border: '1px solid rgba(255,255,255,0.04)',
        position: 'relative',
        overflow: 'hidden',
      }}
    >
      {!built && (
        <div
          style={{
            position: 'absolute',
            inset: 0,
            zIndex: 2,
          }}
        >
          <NetworkLoading nodes={graph.nodes.length} edges={graph.edges.length} />
        </div>
      )}
      {/* ── Hover tooltip (follows the cursor) ── */}
      {hoverInfo && built && (
        <div
          style={{
            position: 'absolute',
            left: hoverInfo.x,
            top: hoverInfo.y,
            zIndex: 5,
            pointerEvents: 'none',
            background: 'rgba(10,12,16,0.92)',
            border: '1px solid rgba(255,255,255,0.08)',
            borderRadius: 6,
            padding: '5px 9px',
            boxShadow: '0 6px 24px rgba(0,0,0,0.4)',
            maxWidth: 220,
          }}
        >
          <div
            style={{
              color: '#fafafa',
              fontWeight: 500,
              fontSize: 11,
              whiteSpace: 'nowrap',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
            }}
          >
            {hoverInfo.label}
          </div>
          <div
            style={{
              color: 'rgba(255,255,255,0.45)',
              fontSize: 10,
              fontFamily: 'var(--ln-mono, monospace)',
              marginTop: 2,
            }}
          >
            {hoverInfo.type}
          </div>
        </div>
      )}
    </div>
  );
}
