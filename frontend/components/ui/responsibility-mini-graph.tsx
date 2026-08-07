'use client';

import { useCallback, useEffect, useMemo, useRef, useState, useId, type FC } from 'react';
import Link from 'next/link';
import dagre from 'dagre';
import { motion } from 'motion/react';
import { Codicon } from '@/components/ui/codicon';
import { buildTraceHref } from '@/lib/trace-route';

// ═══════════════════════════════════════════════════════════
// MagicUI-inspired design tokens — dark theme
// ═══════════════════════════════════════════════════════════

const NODE_TYPE_COLORS: Record<string, string> = {
  Agent: '#60a5fa',
  Resource: '#34d399',
  PolicyBoundary: '#f43f5e',
  Process: '#fbbf24',
  ToolCall: '#a78bfa',
  Credential: '#fb923c',
  Context: '#22d3ee',
};
const NODE_DEFAULT_COLOR = '#6b7280';

const EDGE_TYPE_COLORS: Record<string, string> = {
  IntentCausesAction: '#60a5fa',
  PolicyBoundaryCrossing: '#f43f5e',
  AgentDelegatesTo: '#a78bfa',
  AuthorityTransfer: '#fbbf24',
  Delegation: '#8b5cf6',
  TaintPropagation: '#fb923c',
  ViolationProvenance: '#ef4444',
  ContextFlowsFrom: '#22d3ee',
  TaintPropagatesTo: '#fb923c',
  CompetingExplanation: '#a78bfa',
  CapabilityUse: '#34d399',
  ContextInheritance: '#22d3ee',
};
const EDGE_DEFAULT_COLOR = '#6b7280';

const NODE_TYPE_ICONS: Record<string, string> = {
  Agent: 'organization',
  Resource: 'file',
  PolicyBoundary: 'shield',
  Process: 'server-process',
  ToolCall: 'tools',
  Credential: 'key',
  Context: 'symbol-namespace',
};
const NODE_DEFAULT_ICON = 'circle-filled';

// ═══════════════════════════════════════════════════════════
// dagre layout engine
// ═══════════════════════════════════════════════════════════

interface LayoutNode {
  id: string; label: string; nodeType: string;
  x: number; y: number; w: number; h: number;
}

const NODE_W = 120;
const NODE_H = 42;
const MARGIN = 32;

function computeLayout(
  nodes: { id: string; label: string; nodeType: string }[],
  edges: { id: string; source: string; target: string }[],
): { layoutNodes: LayoutNode[]; bounds: { w: number; h: number } } {
  if (!nodes.length) {
    return { layoutNodes: [], bounds: { w: 200, h: 100 } };
  }

  const g = new dagre.graphlib.Graph();
  g.setDefaultEdgeLabel(() => ({}));
  g.setGraph({ rankdir: 'LR', nodesep: 56, ranksep: 140, marginx: 24, marginy: 24 });

  const nodeIdSet = new Set(nodes.map((n) => n.id));
  for (const n of nodes) g.setNode(n.id, { width: NODE_W, height: NODE_H });
  for (const e of edges) {
    if (nodeIdSet.has(e.source) && nodeIdSet.has(e.target)) g.setEdge(e.source, e.target);
  }
  dagre.layout(g);

  const layoutNodes: LayoutNode[] = nodes.map((n) => {
    const dn = g.node(n.id);
    return {
      id: n.id, label: n.label, nodeType: n.nodeType,
      x: dn.x - NODE_W / 2, y: dn.y - NODE_H / 2, w: NODE_W, h: NODE_H,
    };
  });

  const allXs = layoutNodes.flatMap((n) => [n.x, n.x + n.w]);
  const allYs = layoutNodes.flatMap((n) => [n.y, n.y + n.h]);
  const minX = Math.min(...allXs);
  const minY = Math.min(...allYs);
  const maxX = Math.max(...allXs);
  const maxY = Math.max(...allYs);

  return {
    layoutNodes: layoutNodes.map((n) => ({
      ...n, x: n.x - minX + MARGIN, y: n.y - minY + MARGIN,
    })),
    bounds: { w: maxX - minX + MARGIN * 2, h: maxY - minY + MARGIN * 2 },
  };
}

function abbreviate(label: string, maxLen = 14): string {
  if (label.length <= maxLen) return label;
  return `${label.slice(0, maxLen - 1)}…`;
}
function clamp(v: number, lo: number, hi: number) { return Math.min(hi, Math.max(lo, v)); }

// ═══════════════════════════════════════════════════════════
// BeamEdge — MagicUI identical motion.linearGradient technique
// Uses dagre logical coords (no getBoundingClientRect),
// so beams stay perfectly aligned inside CSS transform layer.
// ═══════════════════════════════════════════════════════════

interface BeamEdgeProps {
  d: string;
  color: string;
  width: number;
  opacity: number;
  duration: number;
  animate: boolean;
}

const BeamEdge: FC<BeamEdgeProps> = ({ d, color, width, opacity, duration, animate }) => {
  const gradientId = useId();

  return (
    <g>
      {/* static dim backdrop — identical to MagicUI */}
      <path
        d={d}
        fill="none"
        stroke={color}
        strokeWidth={width}
        strokeOpacity={opacity}
        strokeLinecap="round"
      />
      {/* animated gradient beam — identical to MagicUI */}
      <path
        d={d}
        fill="none"
        strokeWidth={width}
        stroke={`url(#${gradientId})`}
        strokeOpacity="1"
        strokeLinecap="round"
      />
      <defs>
        <motion.linearGradient
          className="transform-gpu"
          id={gradientId}
          gradientUnits="userSpaceOnUse"
          initial={{ x1: '0%', x2: '0%', y1: '0%', y2: '0%' }}
          animate={animate
            ? { x1: ['10%', '110%'], x2: ['0%', '100%'], y1: ['0%', '0%'], y2: ['0%', '0%'] }
            : { x1: '10%', x2: '0%', y1: '0%', y2: '0%' }
          }
          transition={{
            duration,
            ease: [0.16, 1, 0.3, 1],
            repeat: animate ? Infinity : 0,
            repeatDelay: 0,
          }}
        >
          <stop stopColor={color} stopOpacity="0" />
          <stop stopColor={color} />
          <stop offset="32.5%" stopColor={color} />
          <stop offset="100%" stopColor={color} stopOpacity="0" />
        </motion.linearGradient>
      </defs>
    </g>
  );
};

// ═══════════════════════════════════════════════════════════
// Props
// ═══════════════════════════════════════════════════════════

export type MiniGraphNode = { id: string; label: string; nodeType: string };
export type MiniGraphEdge = { id: string; source: string; target: string; edgeType: string; confidence: number };

type Props = { nodes: MiniGraphNode[]; edges: MiniGraphEdge[]; alertId?: string };

// ═══════════════════════════════════════════════════════════
// Main component
// ═══════════════════════════════════════════════════════════

const STAGE_W = 540;
const STAGE_H = 320;
const SCALE_MIN = 0.2;
const SCALE_MAX = 6;

export function ResponsibilityMiniGraph({ nodes, edges, alertId }: Props) {
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const { layoutNodes, bounds } = useMemo(
    () => computeLayout(nodes, edges),
    [nodes, edges],
  );

  const nodePosMap = useMemo(() => {
    const m = new Map<string, LayoutNode>();
    for (const n of layoutNodes) m.set(n.id, n);
    return m;
  }, [layoutNodes]);

  const fitScale = useMemo(() => {
    if (bounds.w <= 0 || bounds.h <= 0) return 1;
    return Math.min(STAGE_W / (bounds.w + 16), STAGE_H / (bounds.h + 16), 1.4);
  }, [bounds]);

  const [panX, setPanX] = useState(0);
  const [panY, setPanY] = useState(0);
  const [zoom, setZoom] = useState(fitScale);

  useEffect(() => {
    setPanX(0); setPanY(0); setZoom(fitScale);
  }, [fitScale]);

  const stageRef = useRef<HTMLDivElement>(null);
  const draggingRef = useRef(false);
  const lastPosRef = useRef({ x: 0, y: 0 });

  useEffect(() => {
    const el = stageRef.current;
    if (!el) return;
    const prevent = (e: WheelEvent) => e.preventDefault();
    el.addEventListener('wheel', prevent, { passive: false });
    return () => el.removeEventListener('wheel', prevent);
  }, []);

  // ── pan / zoom handlers ──

  const resetView = useCallback(() => {
    setPanX(0); setPanY(0); setZoom(fitScale);
  }, [fitScale]);

  const zoomBy = useCallback((factor: number) => {
    setZoom((z) => clamp(z * factor, SCALE_MIN, SCALE_MAX));
  }, []);

  const handleWheel = useCallback((e: React.WheelEvent) => {
    e.preventDefault();
    const rect = stageRef.current?.getBoundingClientRect();
    if (!rect) return;
    const mx = e.clientX - rect.left;
    const my = e.clientY - rect.top;
    const delta = e.deltaY > 0 ? 0.9 : 1.1;
    setZoom((prevZ) => {
      const newZ = clamp(prevZ * delta, SCALE_MIN, SCALE_MAX);
      const ratio = newZ / prevZ;
      setPanX((px) => mx - (mx - px) * ratio);
      setPanY((py) => my - (my - py) * ratio);
      return newZ;
    });
  }, []);

  const handlePointerDown = useCallback((e: React.PointerEvent) => {
    if (e.button !== 0) return;
    const target = e.target as HTMLElement;
    if (target.closest('button, a, [role="button"]')) return;
    draggingRef.current = true;
    lastPosRef.current = { x: e.clientX, y: e.clientY };
    (e.target as HTMLElement).setPointerCapture?.(e.pointerId);
  }, []);

  const handlePointerMove = useCallback((e: React.PointerEvent) => {
    if (!draggingRef.current) return;
    const dx = e.clientX - lastPosRef.current.x;
    const dy = e.clientY - lastPosRef.current.y;
    lastPosRef.current = { x: e.clientX, y: e.clientY };
    setPanX((px) => px + dx);
    setPanY((py) => py + dy);
  }, []);

  const handlePointerUp = useCallback(() => { draggingRef.current = false; }, []);

  const handleDoubleClick = useCallback((e: React.MouseEvent) => {
    if ((e.target as HTMLElement).closest('button, a, [role="button"]')) return;
    resetView();
  }, [resetView]);

  // ── selection ──

  const connectedIds = useMemo(() => {
    if (!selectedId) return new Set<string>();
    const s = new Set<string>([selectedId]);
    for (const e of edges) {
      if (e.source === selectedId) s.add(e.target);
      if (e.target === selectedId) s.add(e.source);
    }
    return s;
  }, [edges, selectedId]);

  const connectedEdgeIds = useMemo(() => {
    if (!selectedId) return new Set<string>();
    const s = new Set<string>();
    for (const e of edges) {
      if (e.source === selectedId || e.target === selectedId) s.add(e.id);
    }
    return s;
  }, [edges, selectedId]);

  const onNodeClick = useCallback((id: string) => {
    setSelectedId((prev) => (prev === id ? null : id));
  }, []);

  // ── beam path calculator (logical coords from dagre) ──

  const buildBeamPath = useCallback(
    (fromId: string, toId: string, curvature: number): string => {
      const from = nodePosMap.get(fromId);
      const to = nodePosMap.get(toId);
      if (!from || !to) return '';

      const sx = from.x + from.w / 2;
      const sy = from.y + from.h / 2;
      const ex = to.x + to.w / 2;
      const ey = to.y + to.h / 2;
      const mx = (sx + ex) / 2;
      const my = (sy + ey) / 2;
      const cy = my - Math.abs(curvature);

      return `M ${sx},${sy} Q ${mx},${cy} ${ex},${ey}`;
    },
    [nodePosMap],
  );

  // ── empty state ──

  if (!layoutNodes.length) {
    return (
      <div className="flex items-center justify-center rounded-2xl border border-white/5 bg-white/[0.02] py-8">
        <p className="text-xs font-light text-white/35">暂无责任链路数据</p>
      </div>
    );
  }

  const selectedNode = selectedId ? nodes.find((n) => n.id === selectedId) ?? null : null;
  const nodeCount = nodes.length;
  const edgeCount = edges.length;
  const traceHref = alertId ? buildTraceHref(alertId) : '/traces';
  const isDefaultView = zoom === fitScale && panX === 0 && panY === 0;
  const transform = `translate(${panX}px, ${panY}px) scale(${zoom})`;
  const scalePercent = Math.round(zoom * 100);

  return (
    <div className="flex flex-col gap-3">
      {/* ── header ── */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2.5">
          <span className="text-[10px] font-semibold tracking-[0.15em] text-white/35 uppercase">
            拓扑总览
          </span>
          <span className="inline-flex items-center gap-1 rounded-full bg-white/[0.05] px-2 py-0.5 text-[10px] font-medium text-white/25">
            {nodeCount} 节点
          </span>
          <span className="inline-flex items-center gap-1 rounded-full bg-white/[0.05] px-2 py-0.5 text-[10px] font-medium text-white/25">
            {edgeCount} 边
          </span>
        </div>
        <div className="flex items-center gap-2">
          {!isDefaultView && (
            <button
              type="button"
              onClick={resetView}
              className="rounded-md border border-white/10 bg-white/[0.03] px-2.5 py-1 text-[10px] font-medium text-white/40 transition hover:border-white/20 hover:text-white/70"
            >
              重置视图
            </button>
          )}
          <span className="text-[10px] font-medium tabular-nums text-white/30 w-10 text-right">
            {scalePercent}%
          </span>
        </div>
      </div>

      {/* ── graph stage ── */}
      <div
        ref={stageRef}
        className="relative w-full select-none touch-none overflow-hidden rounded-2xl border border-white/[0.06] bg-[#0a0a0f]"
        style={{ height: `${STAGE_H}px` }}
      >
        {/* subtle dot grid — MagicUI-inspired background texture */}
        <svg className="pointer-events-none absolute inset-0 w-full h-full" xmlns="http://www.w3.org/2000/svg">
          <defs>
            <pattern id="mg-dots" x="0" y="0" width="22" height="22" patternUnits="userSpaceOnUse">
              <circle cx="11" cy="11" r="0.7" fill="#ffffff" opacity="0.04" />
            </pattern>
          </defs>
          <rect width="100%" height="100%" fill="url(#mg-dots)" />
        </svg>

        {/* pan/zoom transform layer */}
        <div
          className="absolute inset-0 origin-top-left"
          style={{ transform }}
          onWheel={handleWheel}
          onPointerDown={handlePointerDown}
          onPointerMove={handlePointerMove}
          onPointerUp={handlePointerUp}
          onPointerLeave={handlePointerUp}
          onDoubleClick={handleDoubleClick}
        >
          {/* ── SVG beam layer ── */}
          <svg
            className="pointer-events-none absolute top-0 left-0 overflow-visible"
            width={bounds.w}
            height={bounds.h}
            viewBox={`0 0 ${bounds.w} ${bounds.h}`}
          >
            {edges.map((e) => {
              const edgeType = e.edgeType ?? '';
              const color = EDGE_TYPE_COLORS[edgeType] ?? EDGE_DEFAULT_COLOR;
              const isBoundary = edgeType === 'PolicyBoundaryCrossing' || edgeType === 'ViolationProvenance';
              const isConnected = !selectedId || connectedEdgeIds.has(e.id);
              const curvature = isBoundary ? 50 : 28;
              const d = buildBeamPath(e.source, e.target, curvature);
              if (!d) return null;

              return (
                <BeamEdge
                  key={e.id}
                  d={d}
                  color={color}
                  width={isBoundary ? 2.4 : 1.6}
                  opacity={isConnected ? 0.25 : 0.05}
                  duration={2 + (1 - e.confidence) * 3}
                  animate={isConnected}
                />
              );
            })}
          </svg>

          {/* ── node cards (MagicUI style: dark card + soft shadow + colored icon) ── */}
          {layoutNodes.map((n) => {
            const color = NODE_TYPE_COLORS[n.nodeType] ?? NODE_DEFAULT_COLOR;
            const iconName = NODE_TYPE_ICONS[n.nodeType] ?? NODE_DEFAULT_ICON;
            const isSelected = n.id === selectedId;
            const isConnected = !selectedId || connectedIds.has(n.id);
            const opacity = isConnected ? 1 : 0.22;

            return (
              <motion.div
                key={n.id}
                role="button"
                tabIndex={0}
                aria-label={`${n.nodeType}: ${n.label}`}
                aria-pressed={isSelected}
                onClick={(evt) => { evt.stopPropagation(); onNodeClick(n.id); }}
                onKeyDown={(evt) => {
                  if (evt.key === 'Enter' || evt.key === ' ') { evt.preventDefault(); onNodeClick(n.id); }
                }}
                className="absolute flex items-center gap-2.5 rounded-xl border cursor-pointer transition-shadow duration-200"
                style={{
                  left: n.x, top: n.y, width: n.w, height: n.h,
                  backgroundColor: isSelected ? `${color}10` : '#141418',
                  borderColor: isSelected ? `${color}50` : 'rgba(255,255,255,0.08)',
                  boxShadow: isSelected
                    ? `0 0 0 1px ${color}30, 0 0 20px ${color}14, 0 2px 4px rgba(0,0,0,0.4)`
                    : '0 1px 2px rgba(0,0,0,0.3), 0 1px 3px rgba(0,0,0,0.2)',
                  opacity,
                  zIndex: isSelected ? 10 : 1,
                }}
                whileHover={{
                  boxShadow: isSelected
                    ? `0 0 0 1px ${color}50, 0 0 24px ${color}20, 0 2px 4px rgba(0,0,0,0.4)`
                    : `0 0 0 1px ${color}30, 0 0 16px ${color}0d, 0 2px 4px rgba(0,0,0,0.3)`,
                }}
              >
                {/* colored icon badge */}
                <span
                  className="flex items-center justify-center w-6 h-6 rounded-lg shrink-0 ml-2"
                  style={{
                    backgroundColor: isSelected ? `${color}20` : `${color}0f`,
                    border: `1px solid ${isSelected ? `${color}45` : `${color}20`}`,
                    color,
                  }}
                >
                  <Codicon name={iconName} size={12} />
                </span>
                {/* label */}
                <span
                  className="text-[11px] font-medium text-white/85 truncate flex-1 pr-2.5 select-none"
                  title={n.label}
                >
                  {abbreviate(n.label)}
                </span>
              </motion.div>
            );
          })}
        </div>

        {/* ── zoom controls ── */}
        <div className="absolute bottom-3 right-3 flex items-center gap-0.5 z-20">
          <button
            type="button"
            onClick={() => zoomBy(1 / 1.3)}
            className="flex items-center justify-center w-7 h-7 rounded-lg border border-white/10 bg-[#141418]/90 text-white/40 transition hover:border-white/20 hover:text-white/70"
            aria-label="缩小"
          >
            <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
              <path d="M3 7h8" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
            </svg>
          </button>
          <button
            type="button"
            onClick={() => zoomBy(1.3)}
            className="flex items-center justify-center w-7 h-7 rounded-lg border border-white/10 bg-[#141418]/90 text-white/40 transition hover:border-white/20 hover:text-white/70"
            aria-label="放大"
          >
            <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
              <path d="M3 7h8M7 3v8" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
            </svg>
          </button>
          <button
            type="button"
            onClick={resetView}
            className="flex items-center justify-center w-7 h-7 rounded-lg border border-white/10 bg-[#141418]/90 text-white/40 transition hover:border-white/20 hover:text-white/70"
            aria-label="重置视图"
          >
            <svg width="12" height="12" viewBox="0 0 12 12" fill="none">
              <path d="M1.5 4.5V1.5H4.5M10.5 7.5V10.5H7.5" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" strokeLinejoin="round" />
              <path d="M2 6.5a4 4 0 0 0 6.8 2.8l1.7 1.7M10 5.5a4 4 0 0 0-6.8-2.8L1.5 1" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </button>
        </div>
      </div>

      {/* ── selected node detail ── */}
      {selectedNode && (
        <div
          className="flex items-center gap-2.5 rounded-xl px-3.5 py-2.5 text-xs border"
          style={{
            backgroundColor: `${NODE_TYPE_COLORS[selectedNode.nodeType] ?? NODE_DEFAULT_COLOR}0c`,
            borderColor: `${NODE_TYPE_COLORS[selectedNode.nodeType] ?? NODE_DEFAULT_COLOR}20`,
          }}
        >
          <span
            className="flex items-center justify-center w-5 h-5 rounded-md shrink-0"
            style={{
              backgroundColor: `${NODE_TYPE_COLORS[selectedNode.nodeType] ?? NODE_DEFAULT_COLOR}18`,
              color: NODE_TYPE_COLORS[selectedNode.nodeType] ?? NODE_DEFAULT_COLOR,
            }}
          >
            <Codicon name={NODE_TYPE_ICONS[selectedNode.nodeType] ?? NODE_DEFAULT_ICON} size={11} />
          </span>
          <span className="text-[10px] font-semibold uppercase tracking-wider text-white/45">
            {selectedNode.nodeType}
          </span>
          <span className="text-xs font-medium text-white/80">{selectedNode.label}</span>
          <span className="text-[10px] text-white/30 ml-auto">
            {edges.filter((e) => e.source === selectedId || e.target === selectedId).length} 条关联
          </span>
        </div>
      )}

      {/* ── legend ── */}
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
        {Array.from(new Set(nodes.map((n) => n.nodeType))).map((type) => (
          <div key={type} className="flex items-center gap-1.5">
            <span
              className="w-2 h-2 rounded-full shrink-0"
              style={{ backgroundColor: NODE_TYPE_COLORS[type] ?? NODE_DEFAULT_COLOR }}
            />
            <span className="text-[9px] font-semibold uppercase tracking-wider text-white/30">
              {type === 'PolicyBoundary' ? 'Boundary' : type}
            </span>
          </div>
        ))}
      </div>

      {/* ── full-page link ── */}
      <Link
        href={traceHref}
        className="group flex items-center justify-center gap-2 rounded-xl border border-white/5 bg-white/[0.02] px-4 py-2.5 text-xs font-medium text-white/45 transition-all hover:border-white/12 hover:bg-white/[0.05] hover:text-white/75"
      >
        <Codicon name="graph-line" size={13} className="text-white/25 group-hover:text-white/50 transition-colors" />
        <span>在完整页面中打开交互式责任网络</span>
        <Codicon name="link-external" size={11} className="text-white/15 group-hover:text-white/40 transition-colors" />
      </Link>
    </div>
  );
}
