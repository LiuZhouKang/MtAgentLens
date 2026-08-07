'use client';

import { useEffect, useMemo } from 'react';
import {
  Background,
  BackgroundVariant,
  Controls,
  MiniMap,
  ReactFlow,
  Position,
  type Edge,
  type Node,
  useEdgesState,
  useNodesState,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';

import { AgentNode } from '@/components/ui/agent-node';
import { AnimatedBeamEdge } from '@/components/ui/animated-beam-edge';

const nodeTypes = {
  agent: AgentNode,
};

const edgeTypes = {
  beam: AnimatedBeamEdge,
};

type TraceNetworkProps = {
  nodes: { id: string; label: string; meta?: string; icon?: string }[];
  edges: { source: string; target: string; label?: string }[];
  height?: number;
};

export function TraceNetwork({ nodes, edges, height = 300 }: TraceNetworkProps) {
  const graphSignature = useMemo(
    () =>
      [
        nodes.map((node) => `${node.id}:${node.label}:${node.meta ?? ''}:${node.icon ?? ''}`).join('|'),
        edges.map((edge) => `${edge.source}->${edge.target}:${edge.label ?? ''}`).join('|'),
      ].join('::'),
    [edges, nodes],
  );

  const initialNodes: Node[] = useMemo(
    () =>
      nodes.map((node, index) => ({
        id: node.id,
        type: 'agent',
        position: { x: index * 280, y: 50 },
        data: {
          label: node.label,
          meta: node.meta,
          icon: node.icon || 'gist-commit',
        },
        sourcePosition: Position.Right,
        targetPosition: Position.Left,
      })),
    [nodes],
  );

  const initialEdges: Edge[] = useMemo(
    () =>
      edges.map((edge, index) => ({
        id: `e-${edge.source}-${edge.target}-${index}`,
        source: edge.source,
        target: edge.target,
        type: 'beam',
        animated: false,
      })),
    [edges],
  );

  const [rfNodes, setRfNodes, onNodesChange] = useNodesState<Node>(initialNodes);
  const [rfEdges, setRfEdges, onEdgesChange] = useEdgesState<Edge>(initialEdges);

  useEffect(() => {
    setRfNodes(initialNodes);
    setRfEdges(initialEdges);
  }, [graphSignature, initialEdges, initialNodes, setRfEdges, setRfNodes]);

  if (nodes.length === 0) {
    return (
      <div className="flex items-center justify-center border border-white/5 bg-white/[0.02] rounded-2xl w-full" style={{ height: `${height}px` }}>
        <p className="text-sm font-light text-white/40">暂无链路数据</p>
      </div>
    );
  }

  return (
    <div
      className="relative w-full rounded-2xl border border-white/5 bg-[#141415]/50 overflow-hidden"
      style={{ height: `${height}px` }}
    >
      <svg aria-hidden="true" focusable="false" style={{ position: 'absolute', width: 0, height: 0 }}>
        <defs>
          <linearGradient id="beam-gradient" x1="0%" y1="0%" x2="100%" y2="0%">
            <stop offset="0%" stopColor="rgba(244, 63, 94, 0)" />
            <stop offset="50%" stopColor="rgba(244, 63, 94, 1)" />
            <stop offset="100%" stopColor="rgba(255, 255, 255, 0.8)" />
          </linearGradient>
        </defs>
      </svg>
      
      <ReactFlow
        nodes={rfNodes}
        edges={rfEdges}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        fitView
        fitViewOptions={{ padding: 0.18 }}
        minZoom={0.2}
        maxZoom={2}
        panOnDrag
        zoomOnScroll
        zoomOnPinch
        selectionOnDrag
        autoPanOnNodeDrag
        autoPanSpeed={24}
        proOptions={{ hideAttribution: true }}
        colorMode="dark"
        className="react-flow-custom-dark"
        nodesDraggable
        nodesConnectable={false}
        elementsSelectable
      >
        <MiniMap
          pannable
          zoomable
          position="bottom-left"
          nodeColor={() => 'rgba(255, 255, 255, 0.22)'}
          maskColor="rgba(0, 0, 0, 0.28)"
        />
        <Controls showZoom showFitView showInteractive={false} position="bottom-right" />
        <Background variant={BackgroundVariant.Dots} gap={24} size={1} color="rgba(255,255,255,0.05)" />
      </ReactFlow>
    </div>
  );
}
