import * as THREE from 'three';
import ForceGraph3D, { type ForceGraph3DInstance } from '3d-force-graph';
import { UnrealBloomPass } from 'three/examples/jsm/postprocessing/UnrealBloomPass.js';
import { createStarfield, animateStarfield } from './starfield';
import { AlertPulseOverlay } from './alert-pulse';
import { TimelineRing } from './timeline-ring';
import type { GraphData, ResponsibilityAlert, ResponsibilityEdge, ResponsibilityNode } from '@/lib/types';

export interface EngineAPI {
  setVisualizationMode(mode: string): void;
  flyToAgent(agentId: string, duration?: number): void;
  flyToNode(nodeId: string, duration?: number): void;
  flyToLevel(level: number, duration?: number): void;
  resetCamera(duration?: number): void;
  updateGraph(graph: GraphData): void;
  highlightPath(edgeIds: string[]): void;
  clearHighlights(): void;
  showAlertPulse(alert: ResponsibilityAlert): void;
  clearAlertPulses(): void;
  orbit(deltaX: number, deltaY: number): void;
  zoom(delta: number): void;
  onNodeClicked(cb: (nodeId: string) => void): void;
  onZoomLevelChanged(cb: (level: number) => void): void;
  dispose(): void;
}

type VisualizationMode = 'hero' | 'focus';

const BOOTSTRAP_NODE_ID = '__bootstrap__';

interface ForceNode extends ResponsibilityNode {
  id: string;
  name: string;
  val: number;
  color: string;
  fx?: number;
  fy?: number;
  fz?: number;
  x?: number;
  y?: number;
  z?: number;
}

type ForceLink = Omit<ResponsibilityEdge, 'source' | 'target'> & {
  source: string | ForceNode;
  target: string | ForceNode;
  curvature: number;
  width: number;
  color: string;
  particles: number;
};

const EDGE_COLORS: Record<string, string> = {
  IntentCausesAction: '#dbeafe',
  AgentDelegatesTo: '#8b9dff',
  ContextFlowsFrom: '#67d2ff',
  TaintPropagatesTo: '#f7b955',
  PolicyBoundaryCrossing: '#fb6f92',
  CompetingExplanation: '#7f88a7',
};

function hashToUnit(seed: string): number {
  let hash = 2166136261;
  for (let i = 0; i < seed.length; i += 1) {
    hash ^= seed.charCodeAt(i);
    hash = Math.imul(hash, 16777619);
  }
  return ((hash >>> 0) % 1000) / 1000;
}

function getNodeName(node: ResponsibilityNode): string {
  if (node.node_type === 'Agent') {
    return node.agent_id ?? node.node_id;
  }
  if (node.resource && 'File' in node.resource) {
    return node.resource.File.canonical_path.split('/').pop() ?? node.node_id;
  }
  if (node.resource && 'Network' in node.resource) {
    return node.resource.Network.host;
  }
  return node.node_id;
}

function buildForceNode(node: ResponsibilityNode): ForceNode {
  const hue = hashToUnit(node.node_id);
  const angle = hue * Math.PI * 2;

  if (node.node_type === 'Agent') {
    return {
      ...node,
      id: node.node_id,
      name: getNodeName(node),
      val: 18,
      color: '#9fe8ff',
      fx: Math.cos(angle) * 56,
      fy: (hue - 0.5) * 46,
      fz: Math.sin(angle) * 56,
    };
  }

  if (node.node_type === 'PolicyBoundary') {
    return {
      ...node,
      id: node.node_id,
      name: getNodeName(node),
      val: 12,
      color: '#fb6f92',
      fx: Math.cos(angle) * 188,
      fy: hue > 0.5 ? 84 : -84,
      fz: Math.sin(angle) * 188,
    };
  }

  return {
    ...node,
    id: node.node_id,
    name: getNodeName(node),
    val: 10,
    color: '#78b9ff',
    fx: Math.cos(angle) * 126,
    fy: Math.sin(angle * 1.4) * 42,
    fz: Math.sin(angle) * 126,
  };
}

function buildForceLink(edge: ResponsibilityEdge): ForceLink {
  const tension = hashToUnit(edge.edge_id);
  const highlightedColor = EDGE_COLORS[edge.edge_type] ?? '#dbeafe';

  return {
    ...edge,
    source: edge.source,
    target: edge.target,
    curvature:
      edge.edge_type === 'PolicyBoundaryCrossing'
        ? 0.34
        : edge.edge_type === 'TaintPropagatesTo'
          ? 0.24
          : 0.14 + tension * 0.12,
    width:
      edge.edge_type === 'PolicyBoundaryCrossing'
        ? 1.8
        : edge.edge_type === 'AgentDelegatesTo'
          ? 1.35
          : 0.95,
    color: highlightedColor,
    particles: edge.edge_type === 'PolicyBoundaryCrossing' || edge.edge_type === 'TaintPropagatesTo' ? 3 : 1,
  };
}

export async function initEngine(container: HTMLElement): Promise<EngineAPI> {
  container.replaceChildren();

  const graph = new ForceGraph3D(container, {
    controlType: 'trackball',
    rendererConfig: {
      antialias: true,
      alpha: true,
      powerPreference: 'high-performance',
    },
  }) as unknown as ForceGraph3DInstance<ForceNode, ForceLink>;
  graph.pauseAnimation();

  let currentMode: VisualizationMode = 'hero';
  let currentGraph: GraphData = { nodes: [], edges: [] };
  let currentNodes = new Map<string, ForceNode>();
  let highlightedEdgeIds = new Set<string>();
  let nodeClickCb: ((id: string) => void) | null = null;
  let zoomCb: ((level: number) => void) | null = null;

  graph
    .width(container.clientWidth || window.innerWidth)
    .height(container.clientHeight || window.innerHeight)
    .backgroundColor('rgba(0,0,0,0)')
    .showNavInfo(false)
    .enableNodeDrag(true)
    .linkOpacity(0.82)
    .linkColor((link) => highlightedEdgeIds.has(link.edge_id) ? '#b8f3ff' : link.color)
    .linkWidth((link) => {
      const base = currentMode === 'focus' ? link.width * 1.12 : link.width * 0.9;
      return highlightedEdgeIds.has(link.edge_id) ? base * 1.9 : base;
    })
    .linkCurvature((link) => link.curvature)
    .linkDirectionalParticles((link) => highlightedEdgeIds.has(link.edge_id) ? Math.max(link.particles, 5) : link.particles)
    .linkDirectionalParticleWidth((link) => highlightedEdgeIds.has(link.edge_id) ? 5.4 : 3.1)
    .linkDirectionalParticleSpeed((link) => highlightedEdgeIds.has(link.edge_id) ? 0.012 : 0.006)
    .linkDirectionalParticleColor((link) => highlightedEdgeIds.has(link.edge_id) ? '#ffffff' : link.color)
    .linkDirectionalArrowLength((link) => link.edge_type === 'AgentDelegatesTo' ? 7.5 : 0)
    .linkDirectionalArrowRelPos(0.82)
    .linkDirectionalArrowColor((link) => link.color)
    .nodeVisibility((node) => node.id !== BOOTSTRAP_NODE_ID)
    .nodeRelSize(7)
    .nodeVal((node) => currentMode === 'focus' ? node.val * 1.08 : node.val)
    .nodeColor((node) => node.color)
    .nodeOpacity(0.96)
    .nodeResolution(18)
    .nodeLabel((node) => `${node.node_type}<br/>${node.name}`)
    .onNodeClick((node) => nodeClickCb?.(node.id))
    .onBackgroundClick(() => {
      highlightedEdgeIds = new Set();
      graph.refresh();
    });

  graph.graphData({
    nodes: [
      {
        id: BOOTSTRAP_NODE_ID,
        node_id: BOOTSTRAP_NODE_ID,
        node_type: 'Resource',
        name: 'bootstrap',
        val: 0.001,
        color: '#000000',
        fx: 0,
        fy: 0,
        fz: 0,
      } as unknown as ForceNode,
    ],
    links: [],
  });
  graph.resumeAnimation();

  const scene = graph.scene();
  const camera = graph.camera() as THREE.PerspectiveCamera;
  const renderer = graph.renderer();
  const composer = graph.postProcessingComposer();
  const starfield = createStarfield(scene, 240);
  const alertPulse = new AlertPulseOverlay(scene);
  const timelineRing = new TimelineRing(scene);

  const resizeObserver = typeof ResizeObserver !== 'undefined'
    ? new ResizeObserver(() => {
        graph
          .width(container.clientWidth || window.innerWidth)
          .height(container.clientHeight || window.innerHeight);
      })
    : null;
  resizeObserver?.observe(container);

  const keyLight = new THREE.PointLight(0x8ad8ff, 1.45, 1800);
  keyLight.position.set(180, 220, 320);
  const fillLight = new THREE.PointLight(0x8b7dff, 0.9, 1400);
  fillLight.position.set(-220, -120, 220);
  const warmLight = new THREE.PointLight(0xf59e0b, 0.55, 1200);
  warmLight.position.set(80, -160, 140);
  const sceneLights = [
    new THREE.AmbientLight(0x0f172a, 1.8),
    new THREE.HemisphereLight(0x9fe8ff, 0x020617, 1.1),
    keyLight,
    fillLight,
    warmLight,
  ];
  graph.lights(sceneLights);

  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 1.7));
  camera.position.set(0, 56, 460);
  const controls = graph.controls() as {
    minDistance?: number;
    maxDistance?: number;
    rotateSpeed?: number;
    zoomSpeed?: number;
    panSpeed?: number;
  };
  controls.minDistance = 150;
  controls.maxDistance = 700;
  controls.rotateSpeed = 0.8;
  controls.zoomSpeed = 0.9;
  controls.panSpeed = 0.6;

  const bloomPass = new UnrealBloomPass(
    new THREE.Vector2(container.clientWidth || window.innerWidth, container.clientHeight || window.innerHeight),
    0.72,
    1.1,
    0.18
  );
  composer.addPass(bloomPass);

  function refreshGraph(): void {
    if (!currentGraph.nodes.length) {
      graph.graphData({
        nodes: [
          {
            id: BOOTSTRAP_NODE_ID,
            node_id: BOOTSTRAP_NODE_ID,
            node_type: 'Resource',
            name: 'bootstrap',
            val: 0.001,
            color: '#000000',
            fx: 0,
            fy: 0,
            fz: 0,
          } as unknown as ForceNode,
        ],
        links: [],
      });
      graph.resumeAnimation();
      graph.refresh();
      return;
    }

    const forceNodes = currentGraph.nodes.map(buildForceNode);
    const forceLinks = currentGraph.edges.map(buildForceLink);
    currentNodes = new Map(forceNodes.map((node) => [node.node_id, node]));

    graph.graphData({
      nodes: forceNodes,
      links: forceLinks,
    });

    graph
      .cooldownTicks(currentMode === 'focus' ? 120 : 180)
      .warmupTicks(40)
      .d3VelocityDecay(currentMode === 'focus' ? 0.22 : 0.28)
      .d3AlphaDecay(currentMode === 'focus' ? 0.035 : 0.026);

    const chargeForce = graph.d3Force('charge') as { strength?: (value: number) => unknown } | undefined;
    chargeForce?.strength?.(currentMode === 'focus' ? -280 : -220);
    const linkForce = graph.d3Force('link') as {
      distance?: (fn: (link: ForceLink) => number) => unknown;
      strength?: (fn: (link: ForceLink) => number) => unknown;
    } | undefined;
    linkForce?.distance?.((link) => {
      if (link.edge_type === 'PolicyBoundaryCrossing') return 190;
      if (link.edge_type === 'AgentDelegatesTo') return 132;
      if (link.edge_type === 'ContextFlowsFrom') return 154;
      return 118;
    });
    linkForce?.strength?.((link) => {
      if (link.edge_type === 'PolicyBoundaryCrossing') return 0.4;
      if (link.edge_type === 'AgentDelegatesTo') return 0.28;
      return 0.18;
    });

    graph.d3ReheatSimulation();
    graph.zoomToFit(0, 220);
    graph.resumeAnimation();
    graph.refresh();
  }

  let prevTime = performance.now();
  let rafId = 0;
  function animateExtras(): void {
    rafId = requestAnimationFrame(animateExtras);
    const now = performance.now();
    const delta = Math.min((now - prevTime) / 1000, 0.1);
    prevTime = now;

    animateStarfield(starfield, delta);
    alertPulse.update(delta);
    timelineRing.update(delta);

    const zoomLevel = camera.position.length() < 220 ? 2 : camera.position.length() < 320 ? 1 : 0;
    zoomCb?.(zoomLevel);
  }
  animateExtras();

  (window as any).__mtagentlens_scene = scene;
  (window as any).__mtagentlens_camera = camera;
  (window as any).__mtagentlens_graphRenderer = graph;

  return {
    setVisualizationMode(mode: string) {
      currentMode = mode === 'focus' ? 'focus' : 'hero';
      refreshGraph();
      if (currentMode === 'hero') {
        graph.cameraPosition({ x: 88, y: 38, z: 420 }, { x: 0, y: 0, z: 0 }, 900);
        graph.zoomToFit(900, 220);
      }
    },
    flyToAgent(agentId: string, duration?: number) {
      const node = currentNodes.get(agentId);
      if (!node) return;
      graph.cameraPosition(
        {
          x: (node.x ?? 0) + 60,
          y: (node.y ?? 0) + 30,
          z: (node.z ?? 0) + 120,
        },
        { x: node.x ?? 0, y: node.y ?? 0, z: node.z ?? 0 },
        Math.round((duration ?? 1.2) * 1000)
      );
    },
    flyToNode(nodeId: string, duration?: number) {
      const node = currentNodes.get(nodeId);
      if (!node) return;
      graph.cameraPosition(
        {
          x: (node.x ?? 0) + 45,
          y: (node.y ?? 0) + 24,
          z: (node.z ?? 0) + 94,
        },
        { x: node.x ?? 0, y: node.y ?? 0, z: node.z ?? 0 },
        Math.round((duration ?? 0.8) * 1000)
      );
    },
    flyToLevel(level: number, duration?: number) {
      const presets: Record<number, { position: { x: number; y: number; z: number }; lookAt: { x: number; y: number; z: number } }> = {
        0: { position: { x: 88, y: 38, z: 420 }, lookAt: { x: 0, y: 0, z: 0 } },
        1: { position: { x: 44, y: 22, z: 284 }, lookAt: { x: 0, y: 0, z: 0 } },
        2: { position: { x: 18, y: 10, z: 196 }, lookAt: { x: 0, y: 0, z: 0 } },
      };
      const preset = presets[level] ?? presets[0];
      graph.cameraPosition(preset.position, preset.lookAt, Math.round((duration ?? 1.1) * 1000));
    },
    resetCamera(duration?: number) {
      graph.cameraPosition({ x: 0, y: 56, z: 460 }, { x: 0, y: 0, z: 0 }, Math.round((duration ?? 1.4) * 1000));
    },
    updateGraph(graphData: GraphData) {
      currentGraph = graphData;
      refreshGraph();
    },
    highlightPath(edgeIds: string[]) {
      highlightedEdgeIds = new Set(edgeIds);
      graph.refresh();
    },
    clearHighlights() {
      highlightedEdgeIds = new Set();
      graph.refresh();
    },
    showAlertPulse(alert: ResponsibilityAlert) {
      alertPulse.showPulse(alert);
      timelineRing.setEvents(
        (alert.timeline?.steps ?? []).map((step) => ({
          timestamp_ns: step.timestamp_ns,
          operation: step.operation ?? undefined,
        }))
      );
    },
    clearAlertPulses() {
      alertPulse.clear();
    },
    orbit(deltaX: number, deltaY: number) {
      const controls = graph.controls() as { rotateLeft?: (v: number) => void; rotateUp?: (v: number) => void; update?: () => void };
      controls.rotateLeft?.(deltaX * 0.0028);
      controls.rotateUp?.(deltaY * 0.0028);
      controls.update?.();
    },
    zoom(delta: number) {
      const direction = camera.position.clone().normalize();
      const next = camera.position.clone().addScaledVector(direction, delta * 0.6);
      graph.cameraPosition({ x: next.x, y: next.y, z: next.z });
    },
    onNodeClicked(cb: (nodeId: string) => void) {
      nodeClickCb = cb;
    },
    onZoomLevelChanged(cb: (level: number) => void) {
      zoomCb = cb;
    },
    dispose() {
      cancelAnimationFrame(rafId);
      resizeObserver?.disconnect();
      alertPulse.clear();
      timelineRing.clear();
      starfield.dispose();
      graph._destructor();
      renderer.dispose();
    },
  };
}
