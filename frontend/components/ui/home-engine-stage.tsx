'use client';

import * as THREE from 'three';
import ForceGraph3D, { type ForceGraph3DInstance } from '3d-force-graph';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { UnrealBloomPass } from 'three/examples/jsm/postprocessing/UnrealBloomPass.js';
import { AlertPulseOverlay } from '@/engine/alert-pulse';
import { createShieldMaterial, MAX_HITS } from '@/lib/flow-shield-material';
import { useAppStore } from '@/lib/store';
import type { GraphData, ResponsibilityAlert } from '@/lib/types';
import { adaptGraphToLargeGraph, type LargeGraphColorBy, type LargeGraphLink, type LargeGraphNode } from '@/lib/large-graph-adapter';
import {
  BOOTSTRAP_NODE_ID,
  buildStageGraph,
  type StageLink,
  type StageNode,
} from '@/components/ui/home-engine-stage-config';
import type { ResponsibilityNode } from '@/lib/types';

interface HomeEngineStageProps {
  active: boolean;
  featuredAlert: ResponsibilityAlert | null;
  theme: 'light' | 'dark';
  panelLevel: number;
  dataMode?: StageDataMode;
  colorBy?: LargeGraphColorBy;
  focusEdgeIds?: string[];
  onReady?: () => void;
  onError?: (error: Error) => void;
  onNodeSelected?: (selection: StageNodeSelection) => void;
  onEdgeSelected?: (selection: StageEdgeSelection) => void;
}

type StageDataMode = 'oss-large-graph' | 'oss-demo' | 'semantic';
type VisualizationMode = 'hero' | 'focus';
type EngineAPI = NonNullable<ReturnType<typeof useAppStore.getState>['engineRef']['current']>;

export type StageNodeSelection = {
  nodeId: string;
  rawNodeIds?: string[];
  nodeType: string;
  label: string;
  description: string;
  agentId?: string;
  clusterId?: string;
  boundaryKey?: string;
  boundaryCount?: number;
};

export type StageEdgeSelection = {
  edgeId: string;
  rawEdgeIds?: string[];
  edgeType: string;
  sourceId: string;
  targetId: string;
  sourceLabel: string;
  targetLabel: string;
  sourceType: string;
  targetType: string;
  count?: number;
  confidence: number;
  boundaryKey?: string;
};

function toRgba(color: string, alpha: number): string {
  if (color.startsWith('rgba(')) {
    return color.replace(/rgba\(([^,]+),\s*([^,]+),\s*([^,]+),\s*([^)]+)\)/, `rgba($1, $2, $3, ${alpha})`);
  }
  if (color.startsWith('rgb(')) {
    return color.replace(/rgb\(([^,]+),\s*([^,]+),\s*([^)]+)\)/, `rgba($1, $2, $3, ${alpha})`);
  }

  const normalized = color.replace('#', '');
  const value = normalized.length === 3
    ? normalized.split('').map((char) => `${char}${char}`).join('')
    : normalized;
  const red = parseInt(value.slice(0, 2), 16);
  const green = parseInt(value.slice(2, 4), 16);
  const blue = parseInt(value.slice(4, 6), 16);
  return `rgba(${red}, ${green}, ${blue}, ${alpha})`;
}

function blendHex(hex: string, target: string, amount: number): string {
  const sourceColor = new THREE.Color(hex);
  const targetColor = new THREE.Color(target);
  sourceColor.lerp(targetColor, amount);
  return `#${sourceColor.getHexString()}`;
}

function hashString(input: string): number {
  let hash = 2166136261;
  for (let i = 0; i < input.length; i += 1) {
    hash ^= input.charCodeAt(i);
    hash = Math.imul(hash, 16777619);
  }
  return hash >>> 0;
}

function colorFromSeed(seed: string, saturation = 0.74, lightness = 0.62): string {
  const hue = (hashString(seed) % 360) / 360;
  return `#${new THREE.Color().setHSL(hue, saturation, lightness).getHexString()}`;
}

function getStageNodeColor(node: LargeGraphNode, mode: LargeGraphColorBy): string {
  if (mode === 'agent_id') {
    const seed = node.agent_id !== 'unassigned' ? node.agent_id : `${node.node_type}:${node.id}`;
    return colorFromSeed(seed, 0.76, 0.62);
  }

  if (mode === 'cluster_id') {
    return colorFromSeed(node.cluster_id ?? node.id, 0.72, 0.6);
  }

  switch (node.node_type) {
    case 'Agent':
      return blendHex('#67e8f9', colorFromSeed(node.agent_id || node.id, 0.68, 0.58), 0.24);
    case 'Resource':
      return blendHex('#a78bfa', colorFromSeed(node.id, 0.72, 0.64), 0.42);
    case 'PolicyBoundary':
      return blendHex('#fb7185', colorFromSeed(node.boundary_key ?? node.id, 0.78, 0.64), 0.18);
    default:
      return colorFromSeed(node.id, 0.7, 0.62);
  }
}

type StageRenderData = {
  nodes: Array<LargeGraphNode & { color: string }>;
  links: LargeGraphLink[];
};

type ShieldHandle = {
  obj: THREE.Object3D;
  material: THREE.ShaderMaterial;
  hitIndex: number;
  baseOpacity: number;
  baseFresnelStrength: number;
  targetOpacity: number;
  targetFresnelStrength: number;
};

function createStageRenderData(data: ReturnType<typeof adaptGraphToLargeGraph>, mode: LargeGraphColorBy): StageRenderData {
  return {
    nodes: data.nodes.map((node) => ({
      id: node.id,
      raw_node_ids: node.raw_node_ids,
      node_type: node.node_type,
      agent_id: node.agent_id,
      cluster_id: node.cluster_id,
      description: node.description,
      boundary_key: node.boundary_key,
      boundary_count: node.boundary_count,
      x: node.x,
      y: node.y,
      z: node.z,
      fx: node.fx,
      fy: node.fy,
      fz: node.fz,
      color: getStageNodeColor(node, mode),
    })),
    links: data.links.map((link) => ({
      id: link.id,
      raw_edge_ids: link.raw_edge_ids,
      source: typeof link.source === 'string' ? link.source : link.source.id,
      target: typeof link.target === 'string' ? link.target : link.target.id,
      source_id: link.source_id,
      target_id: link.target_id,
      source_label: link.source_label,
      target_label: link.target_label,
      source_type: link.source_type,
      target_type: link.target_type,
      edge_type: link.edge_type,
      confidence: link.confidence,
      count: link.count,
      boundary_key: link.boundary_key,
    })),
  };
}

function createBoundaryShieldHandle(seed: string, baseColor: string): ShieldHandle {
  const geometry = new THREE.SphereGeometry(1.8, 64, 64);
  const material = createShieldMaterial();
  (material.uniforms.uColor.value as THREE.Color).set(baseColor);
  (material.uniforms.uNoiseEdgeColor.value as THREE.Color).set(baseColor);
  material.uniforms.uReveal.value = 0;
  material.uniforms.uFadeStart.value = -1;
  material.depthTest = false;

  const mesh = new THREE.Mesh(geometry, material);
  mesh.userData = { seed };

  const group = new THREE.Group();
  group.add(mesh);
  group.scale.setScalar(4);

  return {
    obj: group,
    material,
    hitIndex: 0,
    baseOpacity: material.uniforms.uOpacity.value as number,
    baseFresnelStrength: material.uniforms.uFresnelStrength.value as number,
    targetOpacity: material.uniforms.uOpacity.value as number,
    targetFresnelStrength: material.uniforms.uFresnelStrength.value as number,
  };
}

function getResourceIdentity(node: ResponsibilityNode): string | null {
  if (!node.resource) return null;
  if ('File' in node.resource) {
    return node.resource.File.canonical_path.toLowerCase();
  }
  if ('Network' in node.resource) {
    return `${node.resource.Network.host}:${node.resource.Network.port}`.toLowerCase();
  }
  return null;
}

export function deriveAlertFocus(alert: ResponsibilityAlert | null): { nodeIds: Set<string>; edgeIds: Set<string> } {
  if (!alert) {
    return { nodeIds: new Set(), edgeIds: new Set() };
  }

  const nodeIds = new Set<string>();
  const edgeIds = new Set<string>();
  const graphNodes = alert.graph_snapshot.nodes;
  const graphEdges = alert.graph_snapshot.edges;
  const agentIds = new Set([
    alert.primary_agent.agent_id,
    ...alert.involved_agents.map((agent) => agent.agent_id),
  ]);
  const resourceHints = new Set(
    alert.timeline.steps
      .map((step) => step.resource?.toLowerCase())
      .filter((value): value is string => Boolean(value)),
  );

  graphNodes.forEach((node) => {
    if (node.agent_id && agentIds.has(node.agent_id)) {
      nodeIds.add(node.node_id);
      return;
    }

    const resourceIdentity = getResourceIdentity(node);
    if (resourceIdentity && Array.from(resourceHints).some((hint) => resourceIdentity.includes(hint) || hint.includes(resourceIdentity))) {
      nodeIds.add(node.node_id);
      return;
    }

    if (node.node_type === 'PolicyBoundary' && alert.timeline.steps.some((step) => step.event_type === 'boundary_cross')) {
      nodeIds.add(node.node_id);
    }
  });

  graphEdges.forEach((edge) => {
    const isRiskEdge = edge.edge_type === 'PolicyBoundaryCrossing'
      || edge.edge_type === 'TaintPropagatesTo'
      || edge.edge_type === 'AgentDelegatesTo';
    if (isRiskEdge) {
      edgeIds.add(edge.edge_id);
      nodeIds.add(edge.source);
      nodeIds.add(edge.target);
      return;
    }
    if (nodeIds.has(edge.source) || nodeIds.has(edge.target)) {
      edgeIds.add(edge.edge_id);
    }
  });

  return { nodeIds, edgeIds };
}

function SemanticHomeEngineStage({
  active,
  featuredAlert,
  theme,
  panelLevel,
  onReady,
  onError,
  onNodeSelected,
}: HomeEngineStageProps) {
  const graph = useAppStore((state) => state.graph);
  const engineRef = useAppStore((state) => state.engineRef);
  const [mounted, setMounted] = useState(false);
  const [mode, setMode] = useState<VisualizationMode>('hero');
  const [graphState, setGraphState] = useState<GraphData>(graph);
  const [stageGraph, setStageGraph] = useState(() => buildStageGraph(graph, {}));
  const [size, setSize] = useState({ width: 1200, height: 900 });

  const containerRef = useRef<HTMLDivElement | null>(null);
  const graphRef = useRef<ForceGraph3DInstance<StageNode, StageLink> | null>(null);
  const selectionCbRef = useRef<typeof onNodeSelected>(onNodeSelected);
  const engineNodeClickCbRef = useRef<((nodeId: string) => void) | null>(null);
  const zoomCbRef = useRef<((level: number) => void) | null>(null);
  const sceneReadyRef = useRef(false);
  const initializedRef = useRef(false);
  const emphasizedNodeIdsRef = useRef(new Set<string>());
  const emphasizedLinkIdsRef = useRef(new Set<string>());
  const highlightedEdgeIdsRef = useRef(new Set<string>());
  const hoveredNodeIdRef = useRef<string | null>(null);
  const hoveredLinkIdRef = useRef<string | null>(null);
  const stageGraphRef = useRef<{ nodes: StageNode[]; links: StageLink[] }>({ nodes: [], links: [] });
  const clusterMembersRef = useRef<Map<string, string[]>>(new Map());
  const alertFocusRef = useRef<{ nodeIds: Set<string>; edgeIds: Set<string> }>({ nodeIds: new Set(), edgeIds: new Set() });
  const pinnedNodePositionsRef = useRef<Record<string, { x: number; y: number; z: number }>>({});
  const lastAppliedStageGraphRef = useRef<{ nodes: StageNode[]; links: StageLink[] } | null>(null);
  const previousGraphStateRef = useRef(graphState);
  const extrasRef = useRef<{
    alertPulse: AlertPulseOverlay | null;
    backdrop: THREE.Group | null;
    rafId: number | null;
  }>({ alertPulse: null, backdrop: null, rafId: null });

  const coreNodeLookup = useMemo(() => {
    const entries = stageGraph.nodes
      .filter((node) => node.kind === 'core')
      .map((node) => [node.realNodeId, node] as const);
    return new Map(entries);
  }, [stageGraph.nodes]);

  const refreshHighlight = useCallback(() => {
    const graphInstance = graphRef.current;
    if (!graphInstance) return;
    graphInstance
      .nodeColor(graphInstance.nodeColor())
      .nodeThreeObject(graphInstance.nodeThreeObject())
      .linkColor(graphInstance.linkColor())
      .linkWidth(graphInstance.linkWidth())
      .linkDirectionalParticles(graphInstance.linkDirectionalParticles())
      .linkDirectionalParticleWidth(graphInstance.linkDirectionalParticleWidth())
      .linkDirectionalParticleSpeed(graphInstance.linkDirectionalParticleSpeed())
      .linkDirectionalParticleColor(graphInstance.linkDirectionalParticleColor())
      .linkDirectionalParticleOffset(graphInstance.linkDirectionalParticleOffset())
      .linkDirectionalParticleThreeObject(graphInstance.linkDirectionalParticleThreeObject())
      .linkDirectionalArrowLength(graphInstance.linkDirectionalArrowLength())
      .linkDirectionalArrowColor(graphInstance.linkDirectionalArrowColor());
  }, []);

  const recomputeHighlightState = useCallback(() => {
    const nodes = stageGraphRef.current.nodes;
    const links = stageGraphRef.current.links;
    const hoveredNodeId = hoveredNodeIdRef.current;
    const hoveredLinkId = hoveredLinkIdRef.current;
    const highlightedEdgeIds = highlightedEdgeIdsRef.current;

    const nodeById = new Map(nodes.map((node) => [node.id, node]));
    const emphasizedNodes = new Set<string>();
    const emphasizedLinks = new Set<string>();

    const addCluster = (nodeId: string) => {
      const node = nodeById.get(nodeId);
      if (!node) return;
      const clusterMembers = clusterMembersRef.current.get(node.realNodeId) ?? [];
      clusterMembers.forEach((memberId) => emphasizedNodes.add(memberId));
    };

    if (hoveredNodeId) {
      addCluster(hoveredNodeId);
      links.forEach((link) => {
        const sourceId = typeof link.source === 'string' ? link.source : link.source.id;
        const targetId = typeof link.target === 'string' ? link.target : link.target.id;
        if (sourceId === hoveredNodeId || targetId === hoveredNodeId) {
          emphasizedLinks.add(link.id);
          addCluster(sourceId);
          addCluster(targetId);
        }
      });
    }

    if (hoveredLinkId) {
      const hoveredLink = links.find((entry) => entry.id === hoveredLinkId);
      if (hoveredLink) {
        emphasizedLinks.add(hoveredLink.id);
        addCluster(typeof hoveredLink.source === 'string' ? hoveredLink.source : hoveredLink.source.id);
        addCluster(typeof hoveredLink.target === 'string' ? hoveredLink.target : hoveredLink.target.id);
      }
    }

    if (highlightedEdgeIds.size) {
      links.forEach((link) => {
        const key = link.realEdgeId ?? link.id;
        if (!highlightedEdgeIds.has(key)) return;
        emphasizedLinks.add(link.id);
        addCluster(typeof link.source === 'string' ? link.source : link.source.id);
        addCluster(typeof link.target === 'string' ? link.target : link.target.id);
      });
    }

    if (alertFocusRef.current.nodeIds.size || alertFocusRef.current.edgeIds.size) {
      alertFocusRef.current.nodeIds.forEach((nodeId) => addCluster(nodeId));
      links.forEach((link) => {
        const key = link.realEdgeId ?? link.id;
        const sourceId = typeof link.source === 'string' ? link.source : link.source.id;
        const targetId = typeof link.target === 'string' ? link.target : link.target.id;
        if (
          alertFocusRef.current.edgeIds.has(key)
          || alertFocusRef.current.nodeIds.has(sourceId)
          || alertFocusRef.current.nodeIds.has(targetId)
        ) {
          emphasizedLinks.add(link.id);
          addCluster(sourceId);
          addCluster(targetId);
        }
      });
    }

    emphasizedNodeIdsRef.current = emphasizedNodes;
    emphasizedLinkIdsRef.current = emphasizedLinks;
    refreshHighlight();
  }, [refreshHighlight]);

  const persistNodePosition = useCallback((node: Partial<StageNode> & { id?: string }, commit = false) => {
    if (!node.id) return;
    const x = typeof node.x === 'number' ? node.x : node.fx;
    const y = typeof node.y === 'number' ? node.y : node.fy;
    const z = typeof node.z === 'number' ? node.z : node.fz;
    if (typeof x !== 'number' || typeof y !== 'number' || typeof z !== 'number') return;

    node.fx = x;
    node.fy = y;
    node.fz = z;

    if (commit) {
      const current = pinnedNodePositionsRef.current;
      const existing = current[node.id as string];
      if (existing && existing.x === x && existing.y === y && existing.z === z) {
        return;
      }
      pinnedNodePositionsRef.current = {
        ...current,
        [node.id as string]: { x, y, z },
      };
    }
  }, []);

  const applyGraphAccessors = useCallback(() => {
    const graphInstance = graphRef.current;
    if (!graphInstance) return;

    graphInstance
      .nodeId('id')
      .nodeVal((node) => {
        const emphasized = emphasizedNodeIdsRef.current.has(node.id);
        const focusScale = emphasized && node.kind === 'core' ? 1.16 : emphasized ? 1.08 : 1;
        const modeScale = mode === 'focus' && node.kind === 'core' ? 1.03 : 1;
        return node.val * focusScale * modeScale;
      })
      .nodeRelSize(0.58)
      .nodeResolution(6)
      .nodeLabel((node) => node.kind === 'core'
        ? `${node.nodeType}<br/>${node.label}<br/>${node.semanticGroup}`
        : `${node.semanticGroup}<br/>${node.label}`)
      .nodeColor((node) => {
        const emphasized = emphasizedNodeIdsRef.current.has(node.id);
        if (emphasized) {
          return node.kind === 'core' ? blendHex(node.color, '#ffffff', 0.28) : blendHex(node.color, '#ffffff', 0.18);
        }
        return node.color;
      })
      .linkOpacity(0.22)
      .linkColor((link) => {
        if (emphasizedLinkIdsRef.current.has(link.id)) {
          return '#ffffff';
        }
        if (link.kind === 'real') {
          return toRgba(link.color, 0.5);
        }
        if (link.kind === 'bridge') {
          return toRgba(link.color, 0.14);
        }
        return toRgba(link.color, 0.08);
      })
      .linkWidth((link) => {
        const base = mode === 'focus' ? link.width : link.width * 0.96;
        if (emphasizedLinkIdsRef.current.has(link.id)) return Math.max(0.58, base * 0.74);
        if (link.kind === 'real') return Math.max(0.2, base * 0.24);
        if (link.kind === 'bridge') return Math.max(0.1, base * 0.12);
        return Math.max(0.06, base * 0.06);
      })
      .linkCurvature(() => 0)
      .linkDirectionalParticles(() => 0)
      .linkDirectionalParticleWidth(() => 0)
      .linkDirectionalParticleSpeed(() => 0)
      .linkDirectionalParticleColor((link) => link.color)
      .linkDirectionalParticleOffset(() => 0)
      .linkDirectionalParticleThreeObject(() => null as unknown as THREE.Object3D)
      .linkDirectionalArrowLength(() => 0)
      .linkDirectionalArrowRelPos(0.78)
      .linkDirectionalArrowColor((link) => emphasizedLinkIdsRef.current.has(link.id) ? '#ffffff' : link.color)
      .enableNodeDrag(panelLevel === 0)
      .enableNavigationControls(panelLevel === 0)
      .showPointerCursor(() => panelLevel === 0);
  }, [mode, panelLevel]);

  useEffect(() => {
    setMounted(true);
  }, []);

  useEffect(() => {
    selectionCbRef.current = onNodeSelected;
  }, [onNodeSelected]);

  useEffect(() => {
    setGraphState(graph);
  }, [graph]);

  useEffect(() => {
    if (previousGraphStateRef.current === graphState) {
      return;
    }
    previousGraphStateRef.current = graphState;
    setStageGraph(buildStageGraph(graphState, pinnedNodePositionsRef.current));
  }, [graphState]);

  useEffect(() => {
    setMode(panelLevel === 0 ? 'hero' : 'focus');
  }, [panelLevel]);

  useEffect(() => {
    stageGraphRef.current = stageGraph;
    const clusterMembers = new Map<string, string[]>();
    stageGraph.nodes.forEach((node) => {
      const list = clusterMembers.get(node.realNodeId) ?? [];
      list.push(node.id);
      clusterMembers.set(node.realNodeId, list);
    });
    clusterMembersRef.current = clusterMembers;
    (window as any).__mtagentlens_graphStats = {
      nodes: stageGraph.nodes.length,
      links: stageGraph.links.length,
      coreNodes: stageGraph.nodes.filter((node) => node.kind === 'core').length,
    };
  }, [stageGraph]);

  useEffect(() => {
    if (!mounted || !active || !containerRef.current) return;
    const container = containerRef.current;

    const updateSize = () => {
      const rect = container.getBoundingClientRect();
      setSize({
        width: Math.max(Math.round(rect.width), 1),
        height: Math.max(Math.round(rect.height), 1),
      });
    };

    updateSize();
    const observer = new ResizeObserver(updateSize);
    observer.observe(container);
    return () => observer.disconnect();
  }, [active, mounted]);

  useEffect(() => {
    if (!mounted || !active || !containerRef.current || initializedRef.current) return;

    try {
      const graphInstance = new ForceGraph3D(containerRef.current, {
        controlType: 'trackball',
        rendererConfig: {
          antialias: true,
          alpha: true,
          powerPreference: 'high-performance',
        },
      }) as unknown as ForceGraph3DInstance<StageNode, StageLink>;

      graphRef.current = graphInstance;
      initializedRef.current = true;

      graphInstance
        .backgroundColor('rgba(0,0,0,0)')
        .showNavInfo(false)
        .graphData(stageGraph)
        .onNodeClick((node) => {
          selectionCbRef.current?.({
            nodeId: node.realNodeId,
            nodeType: node.nodeType,
            label: node.label,
            description: `${node.nodeType} • ${node.semanticGroup}`,
          });
          engineNodeClickCbRef.current?.(node.realNodeId);
        })
        .onNodeHover((node) => {
          hoveredNodeIdRef.current = node?.id ?? null;
          if (node) hoveredLinkIdRef.current = null;
          recomputeHighlightState();
        })
        .onLinkHover((link) => {
          hoveredLinkIdRef.current = link?.id ?? null;
          if (link) hoveredNodeIdRef.current = null;
          recomputeHighlightState();
        })
        .onBackgroundClick(() => {
          hoveredNodeIdRef.current = null;
          hoveredLinkIdRef.current = null;
          highlightedEdgeIdsRef.current = new Set();
          recomputeHighlightState();
        })
        .onNodeDrag((node) => {
          persistNodePosition(node, false);
        })
        .onNodeDragEnd((node) => {
          persistNodePosition(node, true);
          graphInstance.d3ReheatSimulation();
          graphInstance.resumeAnimation();
        });

      applyGraphAccessors();
      lastAppliedStageGraphRef.current = stageGraph;

      const scene = graphInstance.scene();
      const camera = graphInstance.camera() as THREE.PerspectiveCamera;
      const renderer = graphInstance.renderer();
      const composer = graphInstance.postProcessingComposer?.();
      const alertPulse = new AlertPulseOverlay(scene);
      const backdrop = null;
      const bloomPass = composer
        ? new UnrealBloomPass(
            new THREE.Vector2(size.width || window.innerWidth, size.height || window.innerHeight),
            0.28,
            0.72,
            0.34,
          )
        : null;

      graphInstance.lights([
        new THREE.AmbientLight(0xffffff, 0.95),
        new THREE.HemisphereLight(0x88bbff, 0x02050f, 0.24),
      ]);
      renderer.setPixelRatio(Math.min(window.devicePixelRatio, 1.6));
      renderer.toneMapping = THREE.ACESFilmicToneMapping;
      renderer.toneMappingExposure = 1.08;
      if (composer && bloomPass) {
        composer.addPass(bloomPass);
      }
      scene.fog = new THREE.FogExp2(0x000000, 0.000028);
      camera.position.set(0, 0, 720);

      const controls = graphInstance.controls() as {
        minDistance?: number;
        maxDistance?: number;
        rotateSpeed?: number;
        zoomSpeed?: number;
        panSpeed?: number;
        update?: () => void;
        rotateLeft?: (value: number) => void;
        rotateUp?: (value: number) => void;
      };
      controls.minDistance = 180;
      controls.maxDistance = 1080;
      controls.rotateSpeed = 0.72;
      controls.zoomSpeed = 0.88;
      controls.panSpeed = 0.54;

      let previous = performance.now();
      const tick = () => {
        const now = performance.now();
        const delta = Math.min((now - previous) / 1000, 0.1);
        previous = now;
        alertPulse.update(delta);

        const distance = camera.position.length();
        zoomCbRef.current?.(distance < 260 ? 2 : distance < 420 ? 1 : 0);
        extrasRef.current.rafId = window.requestAnimationFrame(tick);
      };

      extrasRef.current = {
        alertPulse,
        backdrop,
        rafId: window.requestAnimationFrame(tick),
      };

      sceneReadyRef.current = true;
      onReady?.();
      (window as any).__mtagentlens_scene = scene;
      (window as any).__mtagentlens_camera = camera;
      (window as any).__mtagentlens_graphRenderer = graphInstance;
    } catch (error) {
      onError?.(error instanceof Error ? error : new Error('Failed to initialize home 3D stage'));
    }

    return () => {
      if (extrasRef.current.rafId != null) {
        cancelAnimationFrame(extrasRef.current.rafId);
      }
      extrasRef.current.alertPulse?.clear();
      if (extrasRef.current.backdrop) {
        extrasRef.current.backdrop.parent?.remove(extrasRef.current.backdrop);
      }
      extrasRef.current = { alertPulse: null, backdrop: null, rafId: null };
      sceneReadyRef.current = false;
      graphRef.current?._destructor?.();
      graphRef.current = null;
      initializedRef.current = false;
      lastAppliedStageGraphRef.current = null;
    };
  }, [active, applyGraphAccessors, mounted, onError, onReady, persistNodePosition, recomputeHighlightState, size.height, size.width, stageGraph]);

  useEffect(() => {
    const graphInstance = graphRef.current;
    if (!graphInstance) return;

    if (lastAppliedStageGraphRef.current !== stageGraph) {
      graphInstance.pauseAnimation();
      graphInstance.graphData(stageGraph);
      lastAppliedStageGraphRef.current = stageGraph;
    }
    applyGraphAccessors();

    graphInstance
      .d3AlphaDecay(mode === 'focus' ? 0.03 : 0.018)
      .d3VelocityDecay(mode === 'focus' ? 0.22 : 0.16)
      .cooldownTicks(mode === 'focus' ? 160 : 320)
      .warmupTicks(120);

    const chargeForce = graphInstance.d3Force('charge') as { strength?: (value: number) => unknown } | undefined;
    chargeForce?.strength?.(mode === 'focus' ? -88 : -112);
    const linkForce = graphInstance.d3Force('link') as {
      distance?: (fn: (link: StageLink) => number) => unknown;
      strength?: (fn: (link: StageLink) => number) => unknown;
    } | undefined;
    linkForce?.distance?.((link) => {
      if (link.kind === 'structural') return 8;
      if (link.kind === 'bridge') return 14;
      if (link.edgeType === 'PolicyBoundaryCrossing') return 74;
      if (link.edgeType === 'TaintPropagatesTo') return 68;
      if (link.edgeType === 'AgentDelegatesTo') return 62;
      return 56;
    });
    linkForce?.strength?.((link) => {
      if (link.kind === 'structural') return 0.2;
      if (link.kind === 'bridge') return 0.1;
      return link.edgeType === 'PolicyBoundaryCrossing' ? 0.13 : 0.1;
    });
    graphInstance.d3ReheatSimulation();
    graphInstance.resumeAnimation();
    graphInstance.zoomToFit(0, 60, (node) => node.id !== BOOTSTRAP_NODE_ID);
    recomputeHighlightState();
  }, [applyGraphAccessors, mode, recomputeHighlightState, stageGraph]);

  useEffect(() => {
    const graphInstance = graphRef.current;
    if (!graphInstance) return;
    graphInstance.width(size.width).height(size.height);
  }, [size.height, size.width]);

  useEffect(() => {
    if (!featuredAlert) return;
    extrasRef.current.alertPulse?.showPulse(featuredAlert);
  }, [featuredAlert]);

  useEffect(() => {
    alertFocusRef.current = deriveAlertFocus(featuredAlert);
    recomputeHighlightState();
  }, [featuredAlert, recomputeHighlightState]);

  useEffect(() => {
    if (!mounted || !active) return;

    const engine: EngineAPI = {
      setVisualizationMode(nextMode) {
        setMode(nextMode === 'focus' ? 'focus' : 'hero');
      },
      flyToAgent(agentId, duration) {
        const node = coreNodeLookup.get(agentId);
        if (!node || !graphRef.current) return;
        graphRef.current.cameraPosition(
          { x: (node.x ?? node.fx ?? 0) + 58, y: (node.y ?? node.fy ?? 0) + 28, z: (node.z ?? node.fz ?? 0) + 110 },
          { x: node.x ?? node.fx ?? 0, y: node.y ?? node.fy ?? 0, z: node.z ?? node.fz ?? 0 },
          Math.round((duration ?? 1.2) * 1000),
        );
      },
      flyToNode(nodeId, duration) {
        const node = coreNodeLookup.get(nodeId) ?? stageGraph.nodes.find((entry) => entry.id === nodeId);
        if (!node || !graphRef.current) return;
        graphRef.current.cameraPosition(
          { x: (node.x ?? node.fx ?? 0) + 44, y: (node.y ?? node.fy ?? 0) + 24, z: (node.z ?? node.fz ?? 0) + 94 },
          { x: node.x ?? node.fx ?? 0, y: node.y ?? node.fy ?? 0, z: node.z ?? node.fz ?? 0 },
          Math.round((duration ?? 0.9) * 1000),
        );
      },
      flyToLevel(level, duration) {
        const presets: Record<number, { position: { x: number; y: number; z: number }; lookAt: { x: number; y: number; z: number } }> = {
          0: { position: { x: 0, y: 12, z: 560 }, lookAt: { x: 0, y: 0, z: 0 } },
          1: { position: { x: 12, y: 8, z: 360 }, lookAt: { x: 0, y: 0, z: 0 } },
          2: { position: { x: 6, y: 2, z: 228 }, lookAt: { x: 0, y: 0, z: 0 } },
        };
        const preset = presets[level] ?? presets[0];
        graphRef.current?.cameraPosition(preset.position, preset.lookAt, Math.round((duration ?? 1.1) * 1000));
      },
      resetCamera(duration) {
        graphRef.current?.cameraPosition({ x: 0, y: 12, z: 560 }, { x: 0, y: 0, z: 0 }, Math.round((duration ?? 1.3) * 1000));
      },
      updateGraph(nextGraph) {
        setGraphState(nextGraph);
      },
      highlightPath(edgeIds) {
        highlightedEdgeIdsRef.current = new Set(edgeIds);
        recomputeHighlightState();
      },
      clearHighlights() {
        highlightedEdgeIdsRef.current = new Set();
        recomputeHighlightState();
      },
      showAlertPulse(alert) {
        extrasRef.current.alertPulse?.showPulse(alert);
      },
      clearAlertPulses() {
        extrasRef.current.alertPulse?.clear();
      },
      orbit(deltaX, deltaY) {
        const controls = graphRef.current?.controls() as {
          rotateLeft?: (value: number) => void;
          rotateUp?: (value: number) => void;
          update?: () => void;
        } | undefined;
        controls?.rotateLeft?.(deltaX * 0.0028);
        controls?.rotateUp?.(deltaY * 0.0028);
        controls?.update?.();
      },
      zoom(delta) {
        const camera = graphRef.current?.camera() as THREE.PerspectiveCamera | undefined;
        if (!camera || !graphRef.current) return;
        const direction = camera.position.clone().normalize();
        const next = camera.position.clone().addScaledVector(direction, delta * 0.6);
        graphRef.current.cameraPosition({ x: next.x, y: next.y, z: next.z });
      },
      onNodeClicked(cb) {
        engineNodeClickCbRef.current = cb;
      },
      onZoomLevelChanged(cb) {
        zoomCbRef.current = cb;
      },
      dispose() {
        if (extrasRef.current.rafId != null) {
          cancelAnimationFrame(extrasRef.current.rafId);
        }
        extrasRef.current.alertPulse?.clear();
        extrasRef.current.backdrop?.parent?.remove(extrasRef.current.backdrop);
        extrasRef.current = { alertPulse: null, backdrop: null, rafId: null };
      },
    };

    engineRef.current = engine;
    return () => {
      engine.dispose();
      if (engineRef.current === engine) {
        engineRef.current = null;
      }
    };
  }, [active, coreNodeLookup, engineRef, mounted, recomputeHighlightState, stageGraph.nodes]);

  if (!mounted || !active) return null;

  return (
    <div
      id="home-engine-stage"
      aria-hidden="true"
      style={{
        position: 'absolute',
        inset: '0',
        zIndex: 0,
        pointerEvents: panelLevel > 0 ? 'none' : 'auto',
        opacity: panelLevel === 0 ? 1 : panelLevel === 1 ? 0.72 : 0.38,
        transform: 'scale(1)',
        filter: theme === 'light'
          ? 'brightness(0.84) saturate(0.46)'
          : 'brightness(1.08) saturate(1.02) contrast(1.08)',
        transition: 'opacity 240ms ease, transform 320ms ease, filter 240ms ease',
      }}
    >
      <div
        style={{
          position: 'absolute',
          inset: 0,
          background: theme === 'light'
            ? 'transparent'
            : 'radial-gradient(circle at 50% 46%, rgba(255,255,255,0.03) 0%, rgba(2,5,13,0) 58%)',
          pointerEvents: 'none',
        }}
      />
      <div ref={containerRef} style={{ position: 'absolute', inset: 0 }} />
    </div>
  );
}

function OSSLargeGraphStage({
  active,
  theme,
  panelLevel,
  dataMode,
  colorBy,
  focusEdgeIds,
  onReady,
  onError,
  onNodeSelected,
  onEdgeSelected,
}: HomeEngineStageProps) {
  const graph = useAppStore((state) => state.graph);
  const [mounted, setMounted] = useState(false);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const graphRef = useRef<ForceGraph3DInstance<LargeGraphNode, LargeGraphLink> | null>(null);
  const initializedRef = useRef(false);
  const pinnedNodePositionsRef = useRef<Record<string, { x: number; y: number; z: number }>>({});
  const lastAppliedGraphRef = useRef<unknown>(null);
  const [size, setSize] = useState({ width: 1200, height: 900 });
  const appliedColorByRef = useRef<string | null>(null);
  const [stageData, setStageData] = useState<ReturnType<typeof adaptGraphToLargeGraph> | null>(null);
  const nodeIndexRef = useRef(new Map<string, LargeGraphNode>());
  const shieldRef = useRef(new Map<string, ShieldHandle>());
  const hoveredBoundaryRef = useRef<string | null>(null);
  const focusedRawEdgeIdsRef = useRef(new Set<string>());
  const focusedLinkIdsRef = useRef(new Set<string>());
  const focusedNodeIdsRef = useRef(new Set<string>());
  const rafRef = useRef<number | null>(null);
  const focusRefreshKey = useMemo(
    () => `${dataMode ?? 'oss-large-graph'}:${(focusEdgeIds ?? []).join('|')}`,
    [dataMode, focusEdgeIds],
  );

  useEffect(() => {
    setMounted(true);
  }, []);

  useEffect(() => {
    if (dataMode === 'oss-demo') {
      setStageData(null);
      return;
    }
    const adapted = adaptGraphToLargeGraph(graph, pinnedNodePositionsRef.current);
    nodeIndexRef.current = new Map(adapted.nodes.map((node) => [node.id, node]));
    setStageData(adapted);
  }, [dataMode, graph]);

  useEffect(() => {
    const nextRawEdgeIds = new Set(focusEdgeIds ?? []);
    focusedRawEdgeIdsRef.current = nextRawEdgeIds;

    if (!stageData || !nextRawEdgeIds.size) {
      focusedLinkIdsRef.current = new Set();
      focusedNodeIdsRef.current = new Set();
      return;
    }

    const linkIds = new Set<string>();
    const nodeIds = new Set<string>();
    stageData.links.forEach((link) => {
      const rawEdgeIds = link.raw_edge_ids ?? [];
      if (!rawEdgeIds.some((edgeId) => nextRawEdgeIds.has(edgeId))) return;
      linkIds.add(link.id);
      nodeIds.add(typeof link.source === 'string' ? link.source : link.source.id);
      nodeIds.add(typeof link.target === 'string' ? link.target : link.target.id);
    });
    focusedLinkIdsRef.current = linkIds;
    focusedNodeIdsRef.current = nodeIds;
  }, [focusEdgeIds, stageData]);

  useEffect(() => {
    if (!mounted || !active || !containerRef.current) return;
    const container = containerRef.current;

    const updateSize = () => {
      const rect = container.getBoundingClientRect();
      setSize({
        width: Math.max(Math.round(rect.width), 1),
        height: Math.max(Math.round(rect.height), 1),
      });
    };

    updateSize();
    const observer = new ResizeObserver(updateSize);
    observer.observe(container);
    return () => observer.disconnect();
  }, [active, mounted]);

  useEffect(() => {
    const graphInstance = graphRef.current;
    if (!graphInstance) return;
    graphInstance.width(size.width).height(size.height);
  }, [size.height, size.width]);

  useEffect(() => {
    const graphInstance = graphRef.current;
    if (!graphInstance || !initializedRef.current || focusRefreshKey.startsWith('oss-demo:')) return;
    const linkPositionUpdate = graphInstance.linkPositionUpdate();
    graphInstance
      .nodeColor(graphInstance.nodeColor())
      .nodeThreeObject(graphInstance.nodeThreeObject())
      .linkColor(graphInstance.linkColor())
      .linkMaterial(graphInstance.linkMaterial())
      .linkThreeObject(graphInstance.linkThreeObject())
      .linkWidth(graphInstance.linkWidth())
      .linkDirectionalParticles(graphInstance.linkDirectionalParticles())
      .linkDirectionalParticleWidth(graphInstance.linkDirectionalParticleWidth());
    if (linkPositionUpdate) {
      graphInstance.linkPositionUpdate(linkPositionUpdate);
    }
  }, [focusRefreshKey, stageData]);

  useEffect(() => {
    if (!mounted || !active || !containerRef.current || initializedRef.current) return;
    const shields = shieldRef.current;
    const sourceLineMaterial = new THREE.LineBasicMaterial({
      color: 0xffffff,
      opacity: 1,
      transparent: true,
      fog: false,
      toneMapped: false,
      linewidth: 1,
    });
    try {
      const graphInstance = new ForceGraph3D(containerRef.current, {
        controlType: 'trackball',
        rendererConfig: {
          antialias: true,
          alpha: true,
          powerPreference: 'high-performance',
        },
      }) as unknown as ForceGraph3DInstance<LargeGraphNode, LargeGraphLink>;

      graphRef.current = graphInstance;
      initializedRef.current = true;

      graphInstance
        .backgroundColor('rgba(0,0,0,0)')
        .showNavInfo(false)
        .nodeLabel((node) => node.description)
        .nodeOpacity(0.82)
        .nodeColor((node) => {
          const base = ((node as any).color as string | undefined) ?? '#9fb4ff';
          if (!focusedNodeIdsRef.current.size) return base;
          return focusedNodeIdsRef.current.has(node.id) ? blendHex(base, '#ffffff', 0.18) : toRgba(base, 0.26);
        })
        .linkColor(() => '#ffffff')
        .linkOpacity(1)
        .linkMaterial(() => sourceLineMaterial)
        .linkThreeObject(() => {
          const geometry = new THREE.BufferGeometry();
          geometry.setAttribute('position', new THREE.BufferAttribute(new Float32Array(6), 3));
          const line = new THREE.Line(geometry, sourceLineMaterial);
          line.frustumCulled = false;
          line.renderOrder = 12;
          return line;
        })
        .linkPositionUpdate((lineObject, { start, end }) => {
          const line = lineObject as THREE.Line<THREE.BufferGeometry, THREE.LineBasicMaterial>;
          const position = line.geometry.getAttribute('position') as THREE.BufferAttribute;
          position.setXYZ(0, start.x, start.y, start.z);
          position.setXYZ(1, end.x, end.y, end.z);
          position.needsUpdate = true;
          line.geometry.computeBoundingSphere();
          return true;
        })
        .linkWidth(() => 0)
        .linkDirectionalParticles(() => 0)
        .linkDirectionalParticleWidth(() => 0)
        .linkDirectionalParticleSpeed(() => 0.0045)
        .nodeThreeObjectExtend(true)
        .nodeThreeObject((node) => {
          if (node.node_type !== 'PolicyBoundary') return new THREE.Object3D();
          const key = node.boundary_key ?? node.id;
          const existing = shields.get(node.id);
          if (existing) return existing.obj;
          const seed = `${node.id}:${key}`;
          const base = blendHex('#7dd3fc', '#a78bfa', (Math.abs(seed.length * 13) % 100) / 1000);
          const handle = createBoundaryShieldHandle(seed, base);
          const count = node.boundary_count ?? 1;
          const scale = Math.min(2.2, 1.15 + Math.log1p(count) * 0.42);
          handle.obj.scale.setScalar(4 * scale);
          handle.targetOpacity = handle.baseOpacity;
          handle.targetFresnelStrength = handle.baseFresnelStrength;
          shields.set(node.id, handle);
          return handle.obj;
        })
        .enableNodeDrag(panelLevel === 0)
        .enableNavigationControls(panelLevel === 0)
        .showPointerCursor(() => panelLevel === 0)
        .onNodeHover((node) => {
          if (!node || node.node_type !== 'PolicyBoundary') {
            hoveredBoundaryRef.current = null;
            return;
          }
          hoveredBoundaryRef.current = node.id;
        })
        .onLinkHover((link) => {
          if (!link || link.edge_type !== 'PolicyBoundaryCrossing') return;
          const boundaryId = typeof link.target === 'string' ? link.target : link.target.id;
          const boundaryNode = nodeIndexRef.current.get(boundaryId);
          if (!boundaryNode || boundaryNode.node_type !== 'PolicyBoundary') return;
          const handle = shields.get(boundaryId);
          if (!handle) return;
          const sourceId = typeof link.source === 'string' ? link.source : link.source.id;
          const sourceNode = nodeIndexRef.current.get(sourceId);
          const sourcePos = sourceNode && typeof (sourceNode as any).x === 'number'
            ? new THREE.Vector3((sourceNode as any).x, (sourceNode as any).y, (sourceNode as any).z)
            : null;
          const targetPos = boundaryNode && typeof (boundaryNode as any).x === 'number'
            ? new THREE.Vector3((boundaryNode as any).x, (boundaryNode as any).y, (boundaryNode as any).z)
            : null;
          if (!sourcePos || !targetPos) return;

          const dir = sourcePos.sub(targetPos).normalize();
          const localPoint = dir.multiplyScalar(1.8);
          const idx = handle.hitIndex % MAX_HITS;
          handle.hitIndex += 1;
          (handle.material.uniforms.uHitPos.value as THREE.Vector3[])[idx].copy(localPoint);
          (handle.material.uniforms.uHitTime.value as number[])[idx] = handle.material.uniforms.uTime.value as number;
        })
        .onLinkClick((link) => {
          if (!link) return;
          onEdgeSelected?.({
            edgeId: link.id,
            rawEdgeIds: link.raw_edge_ids,
            edgeType: link.edge_type,
            sourceId: link.source_id,
            targetId: link.target_id,
            sourceLabel: link.source_label,
            targetLabel: link.target_label,
            sourceType: link.source_type,
            targetType: link.target_type,
            count: link.count,
            confidence: link.confidence,
            boundaryKey: link.boundary_key,
          });
        })
        .onNodeClick((node) => {
          onNodeSelected?.({
            nodeId: node.id,
            rawNodeIds: node.raw_node_ids,
            nodeType: node.node_type,
            label: node.node_type === 'PolicyBoundary'
              ? 'Policy Boundary'
              : node.agent_id !== 'unassigned'
                ? node.agent_id
                : node.id,
            description: node.description,
            agentId: node.agent_id !== 'unassigned' ? node.agent_id : undefined,
            clusterId: node.cluster_id,
            boundaryKey: node.boundary_key,
            boundaryCount: node.boundary_count,
          });
        })
        .onNodeDragEnd((node) => {
          const x = typeof node.x === 'number' ? node.x : node.fx;
          const y = typeof node.y === 'number' ? node.y : node.fy;
          const z = typeof node.z === 'number' ? node.z : node.fz;
          if (typeof x === 'number') node.fx = x;
          if (typeof y === 'number') node.fy = y;
          if (typeof z === 'number') node.fz = z;
          pinnedNodePositionsRef.current = {
            ...pinnedNodePositionsRef.current,
            [node.id]: { x: node.fx ?? x ?? 0, y: node.fy ?? y ?? 0, z: node.fz ?? z ?? 0 },
          };
          graphInstance.d3ReheatSimulation();
          graphInstance.resumeAnimation();
        });

      const scene = graphInstance.scene();
      const camera = graphInstance.camera() as THREE.PerspectiveCamera;
      const renderer = graphInstance.renderer();
      const composer = graphInstance.postProcessingComposer?.();
      const bloomPass = composer
        ? new UnrealBloomPass(
            new THREE.Vector2(size.width || window.innerWidth, size.height || window.innerHeight),
            0.28,
            0.72,
            0.34,
          )
        : null;

      graphInstance.lights([
        new THREE.AmbientLight(0xffffff, 0.95),
        new THREE.HemisphereLight(0x88bbff, 0x02050f, 0.24),
      ]);
      renderer.setPixelRatio(Math.min(window.devicePixelRatio, 1.6));
      renderer.toneMapping = THREE.ACESFilmicToneMapping;
      renderer.toneMappingExposure = 1.08;
      if (composer && bloomPass) {
        composer.addPass(bloomPass);
      }
      scene.fog = new THREE.FogExp2(0x000000, dataMode === 'oss-demo' ? 0.00011 : 0.000028);
      camera.position.set(0, dataMode === 'oss-demo' ? 32 : 0, dataMode === 'oss-demo' ? 1480 : 720);

      const tick = () => {
        const now = performance.now() / 1000;
        shields.forEach((handle, boundaryId) => {
          const isHovered = hoveredBoundaryRef.current === boundaryId;
          handle.material.uniforms.uTime.value = now;

          const desiredOpacity = isHovered ? Math.min(0.92, handle.baseOpacity + 0.14) : handle.baseOpacity;
          const desiredFresnel = isHovered ? handle.baseFresnelStrength * 1.2 : handle.baseFresnelStrength;

          handle.targetOpacity += (desiredOpacity - handle.targetOpacity) * 0.12;
          handle.targetFresnelStrength += (desiredFresnel - handle.targetFresnelStrength) * 0.12;

          handle.material.uniforms.uOpacity.value += (handle.targetOpacity - (handle.material.uniforms.uOpacity.value as number)) * 0.18;
          handle.material.uniforms.uFresnelStrength.value += (handle.targetFresnelStrength - (handle.material.uniforms.uFresnelStrength.value as number)) * 0.18;
        });
        rafRef.current = requestAnimationFrame(tick);
      };
      rafRef.current = requestAnimationFrame(tick);

      if (dataMode === 'oss-demo') {
        graphInstance
          .backgroundColor('#02050d')
          .jsonUrl('/oss/blocks.json')
          .nodeRelSize(4.4)
          .linkOpacity(0.18)
          .linkWidth(() => 0.18)
          .linkDirectionalParticles(() => 0)
          .nodeAutoColorBy('user');
        const chargeForce = graphInstance.d3Force('charge') as { strength?: (value: number) => unknown } | undefined;
        chargeForce?.strength?.(-48);
        const linkForce = graphInstance.d3Force('link') as { distance?: (value: number) => unknown } | undefined;
        linkForce?.distance?.(24);
        graphInstance.d3VelocityDecay(0.22);
        setTimeout(() => {
          if (!graphRef.current || dataMode !== 'oss-demo') return;
          graphRef.current.zoomToFit(800, 90);
        }, 900);
        appliedColorByRef.current = 'user';
      } else if (stageData) {
        const mode = colorBy ?? 'node_type';
        const renderData = createStageRenderData(stageData, mode);
        graphInstance
          .graphData(renderData);
        appliedColorByRef.current = mode;
        lastAppliedGraphRef.current = stageData;
      } else {
        const mode = colorBy ?? 'node_type';
        const initialData = adaptGraphToLargeGraph(graph, pinnedNodePositionsRef.current);
        const renderData = createStageRenderData(initialData, mode);
        graphInstance
          .graphData(renderData);
        appliedColorByRef.current = mode;
        lastAppliedGraphRef.current = initialData;
        setStageData(initialData);
      }

      onReady?.();
    } catch (error) {
      onError?.(error instanceof Error ? error : new Error('Failed to initialize home 3D stage'));
    }

    return () => {
      if (rafRef.current !== null) {
        cancelAnimationFrame(rafRef.current);
        rafRef.current = null;
      }
      sourceLineMaterial.dispose();
      shields.forEach((handle) => {
        handle.obj.traverse((child) => {
          const mesh = child as THREE.Mesh;
          if (mesh.geometry) mesh.geometry.dispose();
          const material = (mesh as any).material as THREE.Material | undefined;
          material?.dispose?.();
        });
      });
      shields.clear();
      graphRef.current?._destructor?.();
      graphRef.current = null;
      initializedRef.current = false;
      lastAppliedGraphRef.current = null;
      appliedColorByRef.current = null;
    };
  }, [active, colorBy, dataMode, graph, mounted, onEdgeSelected, onError, onNodeSelected, onReady, panelLevel, size.height, size.width, stageData]);

  useEffect(() => {
    const graphInstance = graphRef.current;
    if (!graphInstance || !initializedRef.current) return;

    if (dataMode === 'oss-demo') {
      if (appliedColorByRef.current !== 'user') {
        graphInstance.nodeAutoColorBy('user');
        appliedColorByRef.current = 'user';
      }
      return;
    }

    if (!stageData) return;

    const nextColorBy = colorBy ?? 'node_type';
    const shouldUpdateData = lastAppliedGraphRef.current !== stageData;
    const shouldUpdateColor = appliedColorByRef.current !== nextColorBy;

    if (!shouldUpdateData && !shouldUpdateColor) return;

    graphInstance.pauseAnimation();
    const renderData = createStageRenderData(stageData, nextColorBy);
    graphInstance.graphData(renderData);
    lastAppliedGraphRef.current = stageData;
    appliedColorByRef.current = nextColorBy;
    graphInstance.d3ReheatSimulation();
    graphInstance.resumeAnimation();
  }, [colorBy, dataMode, stageData]);

  if (!mounted || !active) return null;

  return (
    <div
      id="home-engine-stage"
      aria-hidden="true"
      style={{
        position: 'absolute',
        inset: '0',
        zIndex: 0,
        pointerEvents: panelLevel > 0 ? 'none' : 'auto',
        opacity: panelLevel === 0 ? 1 : panelLevel === 1 ? 0.72 : 0.38,
        transform: 'scale(1)',
        filter: theme === 'light'
          ? 'brightness(0.84) saturate(0.46)'
          : 'brightness(1.08) saturate(1.02) contrast(1.08)',
        transition: 'opacity 240ms ease, transform 320ms ease, filter 240ms ease',
      }}
    >
      <div
        style={{
          position: 'absolute',
          inset: 0,
          background: theme === 'light'
            ? 'transparent'
            : 'radial-gradient(circle at 50% 46%, rgba(24,44,120,0.04) 0%, rgba(0,0,0,0) 58%)',
          pointerEvents: 'none',
        }}
      />
      <div ref={containerRef} style={{ position: 'absolute', inset: 0 }} />
    </div>
  );
}

export default function HomeEngineStage(props: HomeEngineStageProps) {
  const mode = props.dataMode ?? 'oss-large-graph';
  if (mode === 'semantic') {
    return <SemanticHomeEngineStage {...props} />;
  }
  return <OSSLargeGraphStage {...props} />;
}
