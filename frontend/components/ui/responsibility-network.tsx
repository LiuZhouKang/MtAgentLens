'use client';

import { useCallback, useEffect, useMemo, useRef } from 'react';
import {
  Background,
  BackgroundVariant,
  Controls,
  MiniMap,
  ReactFlow,
  Position,
  useEdgesState,
  useNodesState,
  type Edge,
  type Node,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import dagre from 'dagre';
import { motion } from 'motion/react';

import {
  ResponsibilityNode,
  type ResponsibilityNodeData,
} from '@/components/ui/responsibility-node';
import {
  ResponsibilityEdge,
  type ResponsibilityEdgeData,
} from '@/components/ui/responsibility-edge';

// ── stable references for node/edge types ──
const nodeTypes = { responsibility: ResponsibilityNode };
const edgeTypes = { responsibility: ResponsibilityEdge };

// ══════════════════════════════════════════
// dagre auto-layout
// ══════════════════════════════════════════

const NODE_WIDTH = 160;
const NODE_HEIGHT = 60;

function buildDagreLayout(
  nodes: { id: string; label: string; nodeType: string }[],
  edges: { id: string; source: string; target: string }[],
): {
  nodes: Node<ResponsibilityNodeData>[];
  edges: Edge<ResponsibilityEdgeData>[];
} {
  const g = new dagre.graphlib.Graph();
  g.setDefaultEdgeLabel(() => ({}));
  g.setGraph({
    rankdir: 'LR',
    nodesep: 60,
    ranksep: 180,
    marginx: 30,
    marginy: 30,
  });

  nodes.forEach((n) => {
    g.setNode(n.id, { width: NODE_WIDTH, height: NODE_HEIGHT });
  });

  edges.forEach((e) => {
    g.setEdge(e.source, e.target);
  });

  dagre.layout(g);

  const rfNodes: Node<ResponsibilityNodeData>[] = nodes.map((n) => {
    const dagreNode = g.node(n.id);
    return {
      id: n.id,
      type: 'responsibility',
      position: {
        x: dagreNode.x - NODE_WIDTH / 2,
        y: dagreNode.y - NODE_HEIGHT / 2,
      },
      data: { label: n.label, nodeType: n.nodeType },
      sourcePosition: Position.Right,
      targetPosition: Position.Left,
    };
  });

  const rfEdges: Edge<ResponsibilityEdgeData>[] = edges.map((e, i) => ({
    id: e.id || `e-${e.source}-${e.target}-${i}`,
    source: e.source,
    target: e.target,
    type: 'responsibility',
    data: {
      edgeType: (e as { edgeType?: string }).edgeType ?? '',
      confidence: (e as { confidence?: number }).confidence ?? 0,
    },
  }));

  return { nodes: rfNodes, edges: rfEdges };
}

// ══════════════════════════════════════════
// compute a stable identity signature so we
// only reset layout when source data changes
// ══════════════════════════════════════════

function computeDataSig(
  nodes: { id: string; label: string; nodeType: string }[],
  edges: { id: string; source: string; target: string; edgeType: string; confidence: number }[],
): string {
  const n = nodes.map((x) => `${x.id}:${x.label}:${x.nodeType}`).join('|');
  const e = edges
    .map((x) => `${x.id}:${x.source}:${x.target}:${x.edgeType}:${x.confidence}`)
    .join('|');
  return `${n}::${e}`;
}

// ══════════════════════════════════════════
// props
// ══════════════════════════════════════════

export type ResponsibilityNetworkNode = {
  id: string;
  label: string;
  nodeType: string;
};

export type ResponsibilityNetworkEdge = {
  id: string;
  source: string;
  target: string;
  edgeType: string;
  confidence: number;
  evidenceRefs?: string[];
};

type ResponsibilityNetworkProps = {
  nodes: ResponsibilityNetworkNode[];
  edges: ResponsibilityNetworkEdge[];
  height?: number;
};

// ══════════════════════════════════════════
// component
// ══════════════════════════════════════════

export function ResponsibilityNetwork({
  nodes: rawNodes,
  edges: rawEdges,
  height = 360,
}: ResponsibilityNetworkProps) {
  // ── track data identity to know when to reset layout ──
  const dataSig = useMemo(
    () => computeDataSig(rawNodes, rawEdges),
    [rawNodes, rawEdges],
  );
  const prevDataSigRef = useRef(dataSig);

  // ── compute dagre layout when source data changes ──
  const freshLayout = useMemo(() => {
    if (!rawNodes.length) {
      return {
        nodes: [] as Node<ResponsibilityNodeData>[],
        edges: [] as Edge<ResponsibilityEdgeData>[],
      };
    }
    return buildDagreLayout(rawNodes, rawEdges);
  }, [rawNodes, rawEdges]);

  // ── react-flow managed state (uncontrolled: ReactFlow owns drag state) ──
  const [rfNodes, setRfNodes, onNodesChange] = useNodesState(
    freshLayout.nodes,
  );
  const [rfEdges, setRfEdges, onEdgesChange] = useEdgesState(
    freshLayout.edges,
  );

  // Only reset layout when source data identity actually changes
  // (e.g. user selected a different alert). We do NOT reset on highlight
  // changes — that would destroy user-dragged positions.
  useEffect(() => {
    if (prevDataSigRef.current !== dataSig) {
      prevDataSigRef.current = dataSig;
      setRfNodes(freshLayout.nodes);
      setRfEdges(freshLayout.edges);
    }
  }, [dataSig, freshLayout, setRfNodes, setRfEdges]);

  // ── highlight on node click (functional updater preserves positions) ──
  const onNodeClick = useCallback(
    (_event: React.MouseEvent, node: Node<ResponsibilityNodeData>) => {
      setRfNodes((prev) => {
        // detect whether this node is already highlighted
        const currentlyDimmed = prev.find((n) => n.id === node.id)?.style
          ?.opacity === 0.4;
        const targetId = currentlyDimmed ? null : node.id;

        return prev.map((n) => ({
          ...n,
          style: {
            ...n.style,
            opacity: targetId && n.id !== targetId ? 0.4 : 1,
          },
          data: {
            ...n.data,
            selected: targetId ? n.id === targetId : false,
          },
        }));
      });

      setRfEdges((prev) => {
        // check current highlight state from an animated edge
        const anyAnimated = prev.some((e) => e.animated);
        const targetId = anyAnimated ? null : node.id;

        return prev.map((e) => {
          const isConnected =
            targetId
              ? e.source === targetId || e.target === targetId
              : false;
          return {
            ...e,
            style: {
              ...e.style,
              opacity: targetId && !isConnected ? 0.08 : 1,
              stroke: targetId && !isConnected ? '#6b7280' : undefined,
            },
            animated: targetId ? isConnected : false,
          };
        });
      });
    },
    [setRfNodes, setRfEdges],
  );

  const onPaneClick = useCallback(() => {
    setRfNodes((prev) =>
      prev.map((n) => ({
        ...n,
        style: { ...n.style, opacity: 1 },
        data: { ...n.data, selected: false },
      })),
    );
    setRfEdges((prev) =>
      prev.map((e) => ({
        ...e,
        style: { ...e.style, opacity: 1, stroke: undefined },
        animated: false,
      })),
    );
  }, [setRfNodes, setRfEdges]);

  // ── empty state ──
  if (!rawNodes.length) {
    return (
      <motion.div
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        className="flex items-center justify-center border border-white/5 bg-white/[0.02] rounded-2xl w-full"
        style={{ height: `${height}px` }}
      >
        <div className="flex flex-col items-center gap-2">
          <span className="text-2xl opacity-30" aria-hidden="true">
            ⚡
          </span>
          <p className="text-xs font-light text-white/40">
            暂无责任链路数据
          </p>
          <p className="text-[10px] font-light text-white/20">
            等待运行时事件触发归因分析
          </p>
        </div>
      </motion.div>
    );
  }

  return (
    <motion.div
      initial={{ opacity: 0, y: 6, filter: 'blur(4px)' }}
      animate={{ opacity: 1, y: 0, filter: 'blur(0px)' }}
      transition={{ duration: 0.4, ease: [0.22, 1, 0.36, 1] }}
      className="relative w-full rounded-2xl border border-white/5 bg-[#141415]/50 overflow-hidden"
      style={{ height: `${height}px` }}
    >
      <ReactFlow
        nodes={rfNodes}
        edges={rfEdges}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onNodeClick={onNodeClick}
        onPaneClick={onPaneClick}
        fitView
        fitViewOptions={{ padding: 0.2 }}
        minZoom={0.15}
        maxZoom={2.5}
        panOnDrag
        zoomOnScroll
        zoomOnPinch
        autoPanOnNodeDrag
        autoPanSpeed={20}
        proOptions={{ hideAttribution: true }}
        colorMode="dark"
        className="react-flow-custom-dark"
        nodesDraggable
        nodesConnectable={false}
        elementsSelectable={false}
        selectNodesOnDrag={false}
        defaultEdgeOptions={{ type: 'responsibility' }}
      >
        <MiniMap
          pannable
          zoomable
          position="bottom-left"
          nodeColor={(n) => {
            const d = (n as Node<ResponsibilityNodeData>).data;
            const colors: Record<string, string> = {
              Agent: '#60a5fa',
              Resource: '#34d399',
              PolicyBoundary: '#f43f5e',
              Process: '#fbbf24',
              ToolCall: '#a78bfa',
              Credential: '#fb923c',
              Context: '#22d3ee',
            };
            return colors[d?.nodeType ?? ''] ?? '#6b7280';
          }}
          maskColor="rgba(0,0,0,0.3)"
          className="!bg-[#141415]/80 !border-white/5 !rounded-xl !overflow-hidden"
        />
        <Controls
          showZoom
          showFitView
          showInteractive={false}
          position="bottom-right"
          className="!bg-[#141415]/80 !border-white/5 !rounded-xl !overflow-hidden"
        />
        <Background
          variant={BackgroundVariant.Dots}
          gap={20}
          size={1}
          color="rgba(255,255,255,0.04)"
        />
      </ReactFlow>
    </motion.div>
  );
}
