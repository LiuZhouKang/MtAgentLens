'use client';

import { useCallback, useMemo, useRef, useState } from 'react';
import { motion } from 'motion/react';
import Link from 'next/link';
import AgentAvatar from '@/components/ui/agent-avatar';
import { Codicon } from '@/components/ui/codicon';
import { buildTraceHref } from '@/lib/trace-route';

// ══════════════════════════════════════════
// type → color / icon mapping
// ══════════════════════════════════════════

const NODE_STYLE: Record<string, { color: string; icon: string; label: string }> = {
  Agent: { color: '#60a5fa', icon: 'organization', label: 'Agent' },
  Resource: { color: '#34d399', icon: 'file', label: 'Resource' },
  PolicyBoundary: { color: '#f43f5e', icon: 'shield', label: 'Boundary' },
  Process: { color: '#fbbf24', icon: 'server-process', label: 'Process' },
  ToolCall: { color: '#a78bfa', icon: 'tools', label: 'Tool' },
  Credential: { color: '#fb923c', icon: 'key', label: 'Cred' },
  Context: { color: '#22d3ee', icon: 'symbol-namespace', label: 'Context' },
};
const DEFAULT_NODE = { color: '#6b7280', icon: 'circle-filled', label: 'Node' };

const EDGE_STYLE: Record<string, { color: string; label: string }> = {
  IntentCausesAction: { color: '#60a5fa', label: '意图→动作' },
  PolicyBoundaryCrossing: { color: '#f43f5e', label: '边界穿越' },
  AgentDelegatesTo: { color: '#a78bfa', label: '委派至' },
  AuthorityTransfer: { color: '#fbbf24', label: '权限转移' },
  Delegation: { color: '#8b5cf6', label: '委派' },
  TaintPropagation: { color: '#fb923c', label: '污点传播' },
  ViolationProvenance: { color: '#ef4444', label: '违规溯源' },
  ContextFlowsFrom: { color: '#22d3ee', label: '上下文流' },
  TaintPropagatesTo: { color: '#fb923c', label: '污点扩散' },
  CompetingExplanation: { color: '#a78bfa', label: '竞争解释' },
  CapabilityUse: { color: '#34d399', label: '能力使用' },
  ContextInheritance: { color: '#22d3ee', label: '上下文继承' },
};
const DEFAULT_EDGE = { color: '#6b7280', label: '关联' };

// ══════════════════════════════════════════
// topological sort to determine chain order
// ══════════════════════════════════════════

function buildChain(
  nodes: { id: string; label: string; nodeType: string }[],
  edges: { id: string; source: string; target: string; edgeType: string; confidence: number }[],
): { orderedNodes: typeof nodes; nodeEdges: Map<string, typeof edges> } {
  if (!nodes.length) return { orderedNodes: [], nodeEdges: new Map() };

  // Build adjacency
  const outEdges = new Map<string, typeof edges>();
  const inDegree = new Map<string, number>();
  const nodeSet = new Set(nodes.map((n) => n.id));

  for (const n of nodes) {
    outEdges.set(n.id, []);
    inDegree.set(n.id, 0);
  }

  for (const e of edges) {
    if (!nodeSet.has(e.source) || !nodeSet.has(e.target)) continue;
    outEdges.get(e.source)?.push(e);
    inDegree.set(e.target, (inDegree.get(e.target) ?? 0) + 1);
  }

  // Kahn's algorithm
  const queue: string[] = [];
  for (const [id, deg] of inDegree) {
    if (deg === 0) queue.push(id);
  }

  const ordered: string[] = [];
  while (queue.length) {
    const id = queue.shift()!;
    ordered.push(id);
    for (const e of outEdges.get(id) ?? []) {
      const newDeg = (inDegree.get(e.target) ?? 1) - 1;
      inDegree.set(e.target, newDeg);
      if (newDeg === 0) queue.push(e.target);
    }
  }

  // Fallback: any remaining nodes (cycles)
  for (const n of nodes) {
    if (!ordered.includes(n.id)) ordered.push(n.id);
  }

  const nodeMap = new Map(nodes.map((n) => [n.id, n]));
  const orderedNodes = ordered.map((id) => nodeMap.get(id)!).filter(Boolean);

  // Build per-edge lookup: for each node, which edges connect FROM it
  const nodeEdges = new Map<string, typeof edges>();
  for (const n of orderedNodes) {
    nodeEdges.set(n.id, outEdges.get(n.id) ?? []);
  }

  return { orderedNodes, nodeEdges };
}

// ══════════════════════════════════════════
// props
// ══════════════════════════════════════════

export type ChainNode = {
  id: string;
  label: string;
  nodeType: string;
};

export type ChainEdge = {
  id: string;
  source: string;
  target: string;
  edgeType: string;
  confidence: number;
};

type ResponsibilityChainPreviewProps = {
  nodes: ChainNode[];
  edges: ChainEdge[];
  alertId?: string;
};

// ══════════════════════════════════════════
// component
// ══════════════════════════════════════════

export function ResponsibilityChainPreview({
  nodes,
  edges,
  alertId,
}: ResponsibilityChainPreviewProps) {
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  const { orderedNodes, nodeEdges } = useMemo(
    () => buildChain(nodes, edges),
    [nodes, edges],
  );

  // Build a set of edge IDs connected to the selected node
  const connectedEdgeIds = useMemo(() => {
    if (!selectedNodeId) return new Set<string>();
    const ids = new Set<string>();
    for (const e of edges) {
      if (e.source === selectedNodeId || e.target === selectedNodeId) {
        ids.add(e.id);
      }
    }
    return ids;
  }, [edges, selectedNodeId]);

  const onNodeClick = useCallback((nodeId: string) => {
    setSelectedNodeId((prev) => (prev === nodeId ? null : nodeId));
  }, []);

  // ── empty state ──
  if (!orderedNodes.length) {
    return (
      <div className="flex items-center justify-center rounded-2xl border border-white/5 bg-white/[0.02] py-10">
        <div className="flex flex-col items-center gap-2">
          <span className="text-xl opacity-30" aria-hidden="true">⚡</span>
          <p className="text-xs font-light text-white/40">暂无责任链路数据</p>
        </div>
      </div>
    );
  }

  const traceHref = alertId ? buildTraceHref(alertId) : '/traces';

  return (
    <div className="flex flex-col gap-3">
      {/* ── scrollable chain ── */}
      <div className="relative">
        {/* right fade mask */}
        <div
          className="pointer-events-none absolute right-0 top-0 bottom-0 w-12 z-10 rounded-r-2xl"
          style={{
            background:
              'linear-gradient(to right, transparent, rgba(20,21,21,0.95))',
          }}
          aria-hidden="true"
        />

        <div
          ref={scrollRef}
          className="flex items-center gap-0 overflow-x-auto scrollbar-none py-2 px-1"
          style={{ scrollBehavior: 'smooth' }}
        >
          {orderedNodes.map((node, idx) => {
            const style = NODE_STYLE[node.nodeType] ?? DEFAULT_NODE;
            const isSelected = node.id === selectedNodeId;
            const outgoingEdges = nodeEdges.get(node.id) ?? [];

            return (
              <div key={node.id} className="flex items-center shrink-0">
                {/* ── node pill ── */}
                <motion.button
                  type="button"
                  whileHover={{ scale: 1.03 }}
                  whileTap={{ scale: 0.97 }}
                  onClick={() => onNodeClick(node.id)}
                  className="flex items-center gap-2 rounded-lg border px-3 py-2 cursor-pointer transition-colors duration-200 shrink-0"
                  style={{
                    borderColor: isSelected
                      ? style.color
                      : 'rgba(255,255,255,0.08)',
                    backgroundColor: isSelected
                      ? `${style.color}10`
                      : 'rgba(20,21,21,0.8)',
                    boxShadow: isSelected
                      ? `0 0 16px ${style.color}18`
                      : 'none',
                    opacity: selectedNodeId && !isSelected ? 0.5 : 1,
                  }}
                  aria-label={`${style.label}: ${node.label}`}
                  aria-pressed={isSelected}
                >
                  {node.nodeType === 'Agent' ? (
                    <AgentAvatar agentId={node.label} size={20} />
                  ) : (
                    <span
                      className="flex items-center justify-center w-6 h-6 rounded-md shrink-0"
                      style={{
                        backgroundColor: `${style.color}18`,
                        border: `1px solid ${style.color}33`,
                      }}
                    >
                      <Codicon name={style.icon} size={12} />
                    </span>
                  )}
                  <span className="text-[11px] font-medium text-white/80 truncate max-w-[80px]">
                    {node.label}
                  </span>
                </motion.button>

                {/* ── outgoing edge badges ── */}
                {outgoingEdges.length > 0 && idx < orderedNodes.length - 1 && (
                  <div className="flex flex-col items-center gap-0.5 mx-0.5 shrink-0">
                    {outgoingEdges.map((edge) => {
                      const eStyle = EDGE_STYLE[edge.edgeType] ?? DEFAULT_EDGE;
                      const isConnected = connectedEdgeIds.has(edge.id);
                      const confPct = Math.round(edge.confidence * 100);

                      return (
                        <div
                          key={edge.id}
                          className="flex items-center gap-1"
                          style={{ opacity: selectedNodeId && !isConnected ? 0.2 : 1 }}
                        >
                          {/* connector line */}
                          <div
                            className="h-px w-3 shrink-0"
                            style={{
                              backgroundColor: isConnected
                                ? eStyle.color
                                : 'rgba(255,255,255,0.1)',
                            }}
                          />
                          {/* edge badge */}
                          <span
                            className="rounded px-1.5 py-0.5 text-[8px] font-semibold uppercase tracking-wider whitespace-nowrap shrink-0"
                            style={{
                              color: eStyle.color,
                              backgroundColor: isConnected
                                ? `${eStyle.color}14`
                                : 'transparent',
                              border: `1px solid ${isConnected ? eStyle.color + '33' : 'rgba(255,255,255,0.06)'}`,
                            }}
                          >
                            {eStyle.label} {confPct}%
                          </span>
                          {/* arrow */}
                          <svg
                            width="12"
                            height="8"
                            viewBox="0 0 12 8"
                            className="shrink-0"
                            aria-hidden="true"
                          >
                            <path
                              d="M0 4h10M7 1l4 3-4 3"
                              stroke={
                                isConnected
                                  ? eStyle.color
                                  : 'rgba(255,255,255,0.15)'
                              }
                              strokeWidth="1"
                              fill="none"
                              strokeLinecap="round"
                              strokeLinejoin="round"
                            />
                          </svg>
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </div>

      {/* ── open in full page ── */}
      <Link
        href={traceHref}
        className="group flex items-center justify-center gap-2 rounded-xl border border-white/5 bg-white/[0.02] px-4 py-2.5 text-xs font-medium tracking-wide text-white/50 transition-all hover:border-white/15 hover:bg-white/[0.05] hover:text-white/80"
      >
        <Codicon name="graph-line" size={13} className="text-white/30 group-hover:text-white/60 transition-colors" />
        <span>在完整页面中打开交互式责任网络</span>
        <Codicon name="link-external" size={11} className="text-white/20 group-hover:text-white/50 transition-colors" />
      </Link>
    </div>
  );
}
