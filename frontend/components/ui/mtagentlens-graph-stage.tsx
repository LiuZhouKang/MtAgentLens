'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import ReactForceGraph3D from 'react-force-graph-3d';
import { useAppStore } from '@/lib/store';
import type { GraphData } from '@/lib/types';
import { adaptGraphToLargeGraph } from '@/lib/large-graph-adapter';
import { pickLodLevel, summarizeToLod } from '@/lib/graph-lod';

export type GraphNodeInfo = {
  id: string;
  node_type: string;
  agent_id: string;
  desc: string;
};

type Props = {
  active: boolean;
  onNodeClick?: (node: GraphNodeInfo) => void;
  onBackgroundClick?: () => void;
  /** 时间演化当前帧覆盖：传入时优先于 store.graph（不改全局态，scrubber 只读帧）。 */
  graphOverride?: GraphData;
  /** 图渲染就绪回调：外层据此淡出统一加载覆盖层（消除两阶段加载跳变）。 */
  onReady?: () => void;
};

// ── Alert highlight colors ──
const VIOLATION_COLOR = '#f97316'; // orange — violation path
const CONTEXT_COLOR = '#5e6ad2'; // blue — context edges
const DIM_OPACITY = 0.04; // dimmed nodes/links

export default function MtAgentLensGraphStage({
  active,
  onNodeClick,
  onBackgroundClick,
  graphOverride,
  onReady,
}: Props) {
  // Stable selectors — only re-render when data identity changes
  const storeGraph = useAppStore((s) => s.graph);
  const graph = graphOverride ?? storeGraph;
  const highlightedEdgeIds = useAppStore((s) => s.investigationContext.highlightedPathEdgeIds);
  const fgRef = useRef<any>(undefined);
  const onReadyRef = useRef(onReady);
  useEffect(() => {
    onReadyRef.current = onReady;
  }, [onReady]);
  const containerRef = useRef<HTMLDivElement>(null);
  const [ready, setReady] = useState(false);
  const [size, setSize] = useState({ w: 1, h: 1 });

  // ── LOD input ──
  // Beyond LOD_RAW_LIMIT nodes, the 3D engine (three.js sprites + per-frame
  // force simulation + particles) chokes. Feed it the agent/cluster summary
  // instead — a coarse "mission constellation" that stays interactive.
  const lodLevel = useMemo(() => pickLodLevel(graph.nodes.length), [graph.nodes.length]);
  const isLarge = lodLevel !== 'raw';

  // Build render data — include raw_edge_ids for reverse mapping.
  // Memo depends on `graph` identity (zustand keeps a stable reference) so it
  // never recomputes on unrelated re-renders and never goes stale on content
  // changes with unchanged counts.
  const { graphData, rawIdToLinkIdx } = useMemo<{
    graphData: {
      nodes: {
        id: string;
        node_type: string;
        agent_id: string;
        desc: string;
        raw_node_ids?: string[];
      }[];
      // react-force-graph-3d replaces source/target with node objects at runtime
      links: {
        source: string | { id: string };
        target: string | { id: string };
        edge_type: string;
        raw_edge_ids: string[];
      }[];
    };
    rawIdToLinkIdx: Map<string, number>;
  }>(() => {
    if (isLarge) {
      const lod = summarizeToLod(graph, lodLevel);
      const nodes = lod.nodes.map((n) => ({
        id: n.id,
        node_type: n.node_type,
        agent_id: n.id,
        desc: n.label,
        raw_node_ids: [n.id],
      }));
      const rawMap = new Map<string, number>();
      const links = lod.edges.map((e, i) => {
        rawMap.set(`${e.source}>${e.target}`, i);
        return {
          source: e.source,
          target: e.target,
          edge_type: e.edge_type,
          raw_edge_ids: [`${e.source}>${e.target}`],
        };
      });
      return { graphData: { nodes, links }, rawIdToLinkIdx: rawMap };
    }

    const adapted = adaptGraphToLargeGraph(graph);
    const nodes = adapted.nodes.map((n) => ({
      id: n.id,
      node_type: n.node_type,
      agent_id: n.agent_id,
      desc: n.description,
      raw_node_ids: n.raw_node_ids,
    }));

    // raw edge ID → adapted link index
    const rawMap = new Map<string, number>();
    const links = adapted.links.map((l, i) => {
      const rawIds = l.raw_edge_ids ?? [];
      rawIds.forEach((rid) => rawMap.set(rid, i));
      return {
        source: typeof l.source === 'string' ? l.source : l.source.id,
        target: typeof l.target === 'string' ? l.target : l.target.id,
        edge_type: l.edge_type,
        raw_edge_ids: rawIds,
      };
    });

    return { graphData: { nodes, links }, rawIdToLinkIdx: rawMap };
  }, [graph, isLarge, lodLevel]);

  // Build highlighted link-key Set for O(1) lookup in dynamic accessors
  const highlightedKeys = useMemo(() => {
    const set = new Set<string>();
    for (const rid of highlightedEdgeIds) {
      const idx = rawIdToLinkIdx.get(rid);
      if (idx == null) continue;
      const link = graphData.links[idx];
      const src = typeof link.source === 'string' ? link.source : link.source.id;
      const tgt = typeof link.target === 'string' ? link.target : link.target.id;
      set.add(`${src}|${tgt}|${link.edge_type}`);
    }
    return set;
  }, [highlightedEdgeIds, rawIdToLinkIdx, graphData.links]);

  const hasHighlight = highlightedKeys.size > 0;

  function linkKey(l: any): string {
    return `${l.source?.id ?? l.source}|${l.target?.id ?? l.target}|${l.edge_type}`;
  }

  // Camera fly-to when alert changes
  useEffect(() => {
    if (!hasHighlight || !fgRef.current) return;
    const t = setTimeout(() => {
      fgRef.current?.zoomToFit?.(1200, 80, (node: any) => {
        for (const l of graphData.links) {
          const src = typeof l.source === 'string' ? l.source : l.source.id;
          const tgt = typeof l.target === 'string' ? l.target : l.target.id;
          if (!highlightedKeys.has(`${src}|${tgt}|${l.edge_type}`)) continue;
          if (node.id === src || node.id === tgt) return true;
        }
        return false;
      });
    }, 400);
    return () => clearTimeout(t);
  }, [hasHighlight, highlightedKeys, graphData.links]);

  // Pause the 3D force simulation when the tab is hidden — saves GPU/CPU when
  // the operator is elsewhere and prevents background tab jank.
  useEffect(() => {
    const onVis = () => {
      if (document.hidden) {
        fgRef.current?.pauseAnimation?.();
      } else if (active) {
        fgRef.current?.resumeAnimation?.();
      }
    };
    document.addEventListener('visibilitychange', onVis);
    return () => document.removeEventListener('visibilitychange', onVis);
  }, [active]);

  // Resize — only fires when dimensions actually change
  useEffect(() => {
    if (!active || !containerRef.current) return;
    const el = containerRef.current;
    let prevW = 0,
      prevH = 0;
    const update = () => {
      const r = el.getBoundingClientRect();
      const w = Math.round(r.width);
      const h = Math.round(r.height);
      if (w > 0 && h > 0 && (w !== prevW || h !== prevH)) {
        prevW = w;
        prevH = h;
        setSize({ w, h });
      }
    };
    update();
    const obs = new ResizeObserver(update);
    obs.observe(el);
    // Mark as ready after initial layout settles
    const t = setTimeout(() => setReady(true), 1200);
    return () => {
      obs.disconnect();
      clearTimeout(t);
    };
  }, [active]);

  // 图渲染就绪后通知外层（command-center）：态势页唯一的全屏 Observation Orbit
  // 覆盖层据此平滑淡出。图内不再渲染自己的迷你 Orbit —— 态势页加载统一为一个
  // 全屏品牌动画（Image 8），消除「全屏 Orbit → 图内迷你 Orbit（Image 9）」两段
  // 加载动画的跳变。
  useEffect(() => {
    if (ready) onReadyRef.current?.();
  }, [ready]);

  if (!active) return null;

  return (
    <div ref={containerRef} style={{ position: 'absolute', inset: 0 }}>
      {size.w > 1 && size.h > 1 && (
        <ReactForceGraph3D
          ref={fgRef}
          graphData={graphData}
          width={size.w}
          height={size.h}
          // 纯黑背景：覆盖 ReactForceGraph3D 默认 #000011 深蓝黑，与态势页原生纯黑统一
          backgroundColor="#000000"
          nodeAutoColorBy="agent_id"
          nodeLabel={(n: any) => `${n.agent_id !== 'unassigned' ? n.agent_id : n.id}: ${n.desc}`}
          nodeResolution={isLarge ? 16 : 32}
          nodeOpacity={0.88}
          enableNodeDrag={!isLarge}
          enableNavigationControls={true}
          cooldownTime={isLarge ? 600 : 3000}
          d3VelocityDecay={0.6}
          // 布局质量优先：使用默认 alphaDecay 让力导充分展开（节点分散到最优
          // 位置，避免加速衰减导致布局未展开、节点挤在一起 —— 顶级布局美学）。
          // engine 停止（力导收敛完成）时 stopSimulation 固定布局防抖，
          // 粒子动画属渲染层、不受影响 —— 布局充分展开 + 节点静止兼得。
          onEngineStop={() => {
            // 力导收敛后固定布局防抖（粒子动画属渲染层、不受影响）。
            // 不使用 zoomToFit：与备份的默认视角一致（密集责任图上 fit
            // 全图会把节点缩成尘埃不可辨）。
            (fgRef.current as any)?.stopSimulation?.();
          }}
          onNodeClick={(n: any) =>
            onNodeClick?.({ id: n.id, node_type: n.node_type, agent_id: n.agent_id, desc: n.desc })
          }
          onBackgroundClick={onBackgroundClick}

          // ── Living particles (premium look) ──
          // Default: soft white particles flow along every edge — the classic
          // OSS-demo effect. On alert selection, violation paths get intense
          // colored particles, context edges get blue ones, and unrelated
          // edges go quiet. Large graphs disable particles entirely — they are
          // the dominant per-frame GPU cost in 3d-force-graph.
          linkDirectionalParticles={
            isLarge
              ? 0
              : (link: any) => {
                  if (!hasHighlight) return 1;
                  const k = linkKey(link);
                  if (!highlightedKeys.has(k)) return 0;
                  return link.edge_type === 'PolicyBoundaryCrossing' ||
                    link.edge_type === 'TaintPropagatesTo'
                    ? 4
                    : 2;
                }
          }
          linkDirectionalParticleWidth={
            isLarge
              ? 0
              : (link: any) => {
                  if (!hasHighlight) return 1.2;
                  const k = linkKey(link);
                  if (!highlightedKeys.has(k)) return 0;
                  return link.edge_type === 'PolicyBoundaryCrossing' ? 2.4 : 1.4;
                }
          }
          linkDirectionalParticleSpeed={
            isLarge
              ? 0
              : (link: any) => {
                  if (!hasHighlight) return 0.004;
                  const k = linkKey(link);
                  if (!highlightedKeys.has(k)) return 0.004;
                  return link.edge_type === 'PolicyBoundaryCrossing' ? 0.012 : 0.006;
                }
          }
          linkDirectionalParticleColor={
            isLarge
              ? 'rgba(255,255,255,0)'
              : (link: any) => {
                  if (!hasHighlight) return 'rgba(255,255,255,0.55)';
                  const k = linkKey(link);
                  if (!highlightedKeys.has(k)) return 'rgba(255,255,255,0.55)';
                  return link.edge_type === 'PolicyBoundaryCrossing' ||
                    link.edge_type === 'TaintPropagatesTo'
                    ? VIOLATION_COLOR
                    : CONTEXT_COLOR;
                }
          }

          // ── Link color: crisp white web by default, highlight violation path ──
          linkColor={(link: any) => {
            if (!hasHighlight) return 'rgba(255,255,255,0.18)';
            const k = linkKey(link);
            if (highlightedKeys.has(k)) {
              return link.edge_type === 'PolicyBoundaryCrossing' ||
                link.edge_type === 'TaintPropagatesTo'
                ? VIOLATION_COLOR
                : CONTEXT_COLOR;
            }
            return `rgba(255,255,255,${DIM_OPACITY})`;
          }}
          linkWidth={(link: any) => {
            if (!hasHighlight) return 0.42;
            const k = linkKey(link);
            return highlightedKeys.has(k) ? 0.6 : 0.1;
          }}
          linkOpacity={0.9}
        />
      )}
    </div>
  );
}
