'use client';

import * as THREE from 'three';
import ForceGraph3D, { type ForceGraphMethods } from 'react-force-graph-3d';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { AlertPulseOverlay } from '@/engine/alert-pulse';
import OSSStarfield from '@/components/ui/oss-starfield';
import { useAppStore } from '@/lib/store';
import type { GraphData, ResponsibilityAlert } from '@/lib/types';
import {
  BOOTSTRAP_NODE_ID,
  bootstrapGraph,
  buildForceLink,
  buildForceNode,
  createBackgroundGraph,
  type BackgroundLink,
  type BackgroundNode,
  type ForceLink,
  type ForceNode,
} from '@/components/ui/home-engine-stage-config';

interface HomeEngineStageProps {
  active: boolean;
  featuredAlert: ResponsibilityAlert | null;
  theme: 'light' | 'dark';
  panelLevel: number;
  onReady?: () => void;
  onError?: (error: Error) => void;
  onNodeSelected?: (nodeId: string) => void;
}

type VisualizationMode = 'hero' | 'focus';

type EngineAPI = NonNullable<ReturnType<typeof useAppStore.getState>['engineRef']['current']>;

export default function HomeEngineStage({
  active,
  featuredAlert,
  theme,
  panelLevel,
  onReady,
  onError: _onError,
  onNodeSelected,
}: HomeEngineStageProps) {
  const graph = useAppStore((state) => state.graph);
  const engineRef = useAppStore((state) => state.engineRef);
  const [mounted, setMounted] = useState(false);
  const [mode, setMode] = useState<VisualizationMode>('hero');
  const [graphState, setGraphState] = useState<GraphData>(graph);
  const [highlightedEdgeIds, setHighlightedEdgeIds] = useState<string[]>([]);
  const [size, setSize] = useState({ width: 1200, height: 900 });
  const containerRef = useRef<HTMLDivElement | null>(null);
  const backgroundRef = useRef<ForceGraphMethods<BackgroundNode, BackgroundLink> | undefined>(undefined);
  const fgRef = useRef<ForceGraphMethods<ForceNode, ForceLink> | undefined>(undefined);
  const sceneReadyRef = useRef(false);
  const extrasRef = useRef<{
    alertPulse: AlertPulseOverlay | null;
    rafId: number | null;
  }>({ alertPulse: null, rafId: null });
  const nodeClickCbRef = useRef<typeof onNodeSelected>(onNodeSelected);
  const zoomCbRef = useRef<((level: number) => void) | null>(null);
  const highlightedSet = useMemo(() => new Set(highlightedEdgeIds), [highlightedEdgeIds]);

  const renderedGraph = useMemo(() => {
    if (!graphState.nodes.length) {
      return bootstrapGraph();
    }
    return {
      nodes: graphState.nodes.map(buildForceNode),
      links: graphState.edges.map(buildForceLink),
    };
  }, [graphState]);
  const denseGraph = renderedGraph.nodes.length > 120;
  const backgroundGraph = useMemo(() => createBackgroundGraph(graphState), [graphState]);

  useEffect(() => {
    setMounted(true);
  }, []);

  useEffect(() => {
    nodeClickCbRef.current = onNodeSelected;
  }, [onNodeSelected]);

  useEffect(() => {
    setGraphState(graph);
  }, [graph]);

  useEffect(() => {
    (window as any).__agentsight_graphStats = {
      nodes: renderedGraph.nodes.length,
      links: renderedGraph.links.length,
      backgroundNodes: backgroundGraph.nodes.length,
      backgroundLinks: backgroundGraph.links.length,
    };
  }, [backgroundGraph.links.length, backgroundGraph.nodes.length, renderedGraph.links.length, renderedGraph.nodes.length]);

  useEffect(() => {
    setMode(panelLevel === 0 ? 'hero' : 'focus');
  }, [panelLevel]);

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

  const setupSceneExtras = useCallback(() => {
    if (!fgRef.current || sceneReadyRef.current) return;
    const scene = fgRef.current.scene();
    const camera = fgRef.current.camera() as THREE.PerspectiveCamera;
    const renderer = fgRef.current.renderer();
    const alertPulse = new AlertPulseOverlay(scene);

    const keyLight = new THREE.PointLight(0xa6deff, 1.1, 1800);
    keyLight.position.set(160, 180, 280);
    const fillLight = new THREE.PointLight(0x9fb8ff, 0.55, 1600);
    fillLight.position.set(-240, -90, 220);
    fgRef.current.lights([
      new THREE.AmbientLight(0x102033, 1.22),
      new THREE.HemisphereLight(0xaedbff, 0x020617, 0.72),
      keyLight,
      fillLight,
    ]);

    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 1.5));
    camera.position.set(0, 42, 560);
    const controls = fgRef.current.controls() as {
      minDistance?: number;
      maxDistance?: number;
      rotateSpeed?: number;
      zoomSpeed?: number;
      panSpeed?: number;
      update?: () => void;
    };
    controls.minDistance = 180;
    controls.maxDistance = 840;
    controls.rotateSpeed = 0.62;
    controls.zoomSpeed = 0.86;
    controls.panSpeed = 0.46;

    let previous = performance.now();
    const tick = () => {
      const now = performance.now();
      const delta = Math.min((now - previous) / 1000, 0.1);
      previous = now;
      alertPulse.update(delta);

      const distance = camera.position.length();
      zoomCbRef.current?.(distance < 260 ? 2 : distance < 390 ? 1 : 0);
      const backgroundCamera = backgroundRef.current?.camera() as THREE.PerspectiveCamera | undefined;
      if (backgroundCamera) {
        backgroundCamera.position.copy(camera.position).multiplyScalar(1.12);
        backgroundCamera.lookAt(0, 0, 0);
        backgroundCamera.updateProjectionMatrix();
      }
      const backgroundScene = backgroundRef.current?.scene();
      if (backgroundScene) {
        backgroundScene.rotation.y += delta * 0.0011;
      }
      extrasRef.current.rafId = window.requestAnimationFrame(tick);
    };

    extrasRef.current = {
      alertPulse,
      rafId: window.requestAnimationFrame(tick),
    };

    sceneReadyRef.current = true;
    onReady?.();

    (window as any).__agentsight_scene = scene;
    (window as any).__agentsight_camera = camera;
    (window as any).__agentsight_graphRenderer = fgRef.current;

    return () => {
      if (extrasRef.current.rafId != null) {
        cancelAnimationFrame(extrasRef.current.rafId);
      }
      extrasRef.current.alertPulse?.clear();
      extrasRef.current = { alertPulse: null, rafId: null };
      sceneReadyRef.current = false;
    };
  }, [onReady]);

  useEffect(() => {
    if (!mounted || !active) return;
    const cleanup = setupSceneExtras();
    return () => cleanup?.();
  }, [active, mounted, setupSceneExtras]);

  useEffect(() => {
    if (!mounted || !active) return;

    const engine: EngineAPI = {
      setVisualizationMode(nextMode) {
        setMode(nextMode === 'focus' ? 'focus' : 'hero');
        if (nextMode !== 'focus') {
          fgRef.current?.cameraPosition({ x: 0, y: 42, z: 560 }, { x: 0, y: 0, z: 0 }, 900);
          backgroundRef.current?.cameraPosition({ x: 0, y: 47.04, z: 627.2 }, { x: 0, y: 0, z: 0 }, 900);
          fgRef.current?.zoomToFit(900, 180, (node) => node.id !== BOOTSTRAP_NODE_ID);
        }
      },
      flyToAgent(agentId, duration) {
        const node = renderedGraph.nodes.find((item) => item.node_id === agentId);
        if (!node || !fgRef.current) return;
        fgRef.current.cameraPosition(
          { x: (node.x ?? node.fx ?? 0) + 60, y: (node.y ?? node.fy ?? 0) + 30, z: (node.z ?? node.fz ?? 0) + 120 },
          { x: node.x ?? node.fx ?? 0, y: node.y ?? node.fy ?? 0, z: node.z ?? node.fz ?? 0 },
          Math.round((duration ?? 1.2) * 1000)
        );
      },
      flyToNode(nodeId, duration) {
        const node = renderedGraph.nodes.find((item) => item.node_id === nodeId);
        if (!node || !fgRef.current) return;
        fgRef.current.cameraPosition(
          { x: (node.x ?? node.fx ?? 0) + 45, y: (node.y ?? node.fy ?? 0) + 24, z: (node.z ?? node.fz ?? 0) + 94 },
          { x: node.x ?? node.fx ?? 0, y: node.y ?? node.fy ?? 0, z: node.z ?? node.fz ?? 0 },
          Math.round((duration ?? 0.8) * 1000)
        );
      },
      flyToLevel(level, duration) {
        const presets: Record<number, { position: { x: number; y: number; z: number }; lookAt: { x: number; y: number; z: number } }> = {
          0: { position: { x: 0, y: 42, z: 560 }, lookAt: { x: 0, y: 0, z: 0 } },
          1: { position: { x: 18, y: 18, z: 360 }, lookAt: { x: 0, y: 0, z: 0 } },
          2: { position: { x: 8, y: 8, z: 250 }, lookAt: { x: 0, y: 0, z: 0 } },
        };
        const preset = presets[level] ?? presets[0];
        fgRef.current?.cameraPosition(preset.position, preset.lookAt, Math.round((duration ?? 1.1) * 1000));
        backgroundRef.current?.cameraPosition(
          { x: preset.position.x * 1.08, y: preset.position.y * 1.08, z: preset.position.z * 1.08 },
          preset.lookAt,
          Math.round((duration ?? 1.1) * 1000)
        );
      },
      resetCamera(duration) {
        fgRef.current?.cameraPosition({ x: 0, y: 42, z: 560 }, { x: 0, y: 0, z: 0 }, Math.round((duration ?? 1.4) * 1000));
        backgroundRef.current?.cameraPosition({ x: 0, y: 47.04, z: 627.2 }, { x: 0, y: 0, z: 0 }, Math.round((duration ?? 1.4) * 1000));
      },
      updateGraph(nextGraph) {
        setGraphState(nextGraph);
      },
      highlightPath(edgeIds) {
        setHighlightedEdgeIds(edgeIds);
      },
      clearHighlights() {
        setHighlightedEdgeIds([]);
      },
      showAlertPulse(alert) {
        extrasRef.current.alertPulse?.showPulse(alert);
      },
      clearAlertPulses() {
        extrasRef.current.alertPulse?.clear();
      },
      orbit(deltaX, deltaY) {
        const controls = fgRef.current?.controls() as {
          rotateLeft?: (v: number) => void;
          rotateUp?: (v: number) => void;
          update?: () => void;
        } | undefined;
        controls?.rotateLeft?.(deltaX * 0.0028);
        controls?.rotateUp?.(deltaY * 0.0028);
        controls?.update?.();
      },
      zoom(delta) {
        const camera = fgRef.current?.camera() as THREE.PerspectiveCamera | undefined;
        if (!camera || !fgRef.current) return;
        const direction = camera.position.clone().normalize();
        const next = camera.position.clone().addScaledVector(direction, delta * 0.6);
        fgRef.current.cameraPosition({ x: next.x, y: next.y, z: next.z });
        backgroundRef.current?.cameraPosition({ x: next.x * 1.08, y: next.y * 1.08, z: next.z * 1.08 });
      },
      onNodeClicked(cb) {
        nodeClickCbRef.current = cb;
      },
      onZoomLevelChanged(cb) {
        zoomCbRef.current = cb;
      },
      dispose() {
        if (extrasRef.current.rafId != null) {
          cancelAnimationFrame(extrasRef.current.rafId);
        }
        extrasRef.current.alertPulse?.clear();
        extrasRef.current = { alertPulse: null, rafId: null };
      },
    };

    engineRef.current = engine;
    return () => {
      engine.dispose();
      if (engineRef.current === engine) {
        engineRef.current = null;
      }
    };
  }, [active, engineRef, mounted, renderedGraph.nodes, size.height, size.width]);

  useEffect(() => {
    if (!fgRef.current) return;
    fgRef.current
      .pauseAnimation()
      .d3AlphaDecay(mode === 'focus' ? 0.038 : 0.03)
      .d3VelocityDecay(mode === 'focus' ? 0.24 : denseGraph ? 0.36 : 0.3)
      .cooldownTicks(mode === 'focus' ? 110 : denseGraph ? 95 : 160)
      .warmupTicks(denseGraph ? 14 : 24);

    const chargeForce = fgRef.current.d3Force('charge') as { strength?: (value: number) => unknown } | undefined;
    chargeForce?.strength?.(mode === 'focus' ? -160 : denseGraph ? -104 : -132);
    const linkForce = fgRef.current.d3Force('link') as {
      distance?: (fn: (link: ForceLink) => number) => unknown;
      strength?: (fn: (link: ForceLink) => number) => unknown;
    } | undefined;
    linkForce?.distance?.((link) => {
      if (link.edge_type === 'PolicyBoundaryCrossing') return 170;
      if (link.edge_type === 'AgentDelegatesTo') return 120;
      if (link.edge_type === 'ContextFlowsFrom') return 132;
      return 108;
    });
    linkForce?.strength?.((link) => {
      if (link.edge_type === 'PolicyBoundaryCrossing') return 0.28;
      if (link.edge_type === 'AgentDelegatesTo') return 0.2;
      return 0.14;
    });
    fgRef.current.d3ReheatSimulation();
    fgRef.current.resumeAnimation();
    fgRef.current.zoomToFit(0, 180, (node) => node.id !== BOOTSTRAP_NODE_ID);
  }, [denseGraph, mode, renderedGraph]);

  useEffect(() => {
    if (!backgroundRef.current) return;
    backgroundRef.current
      .pauseAnimation()
      .cooldownTicks(0)
      .warmupTicks(0)
      .d3AlphaDecay(1)
      .d3VelocityDecay(1)
      .resumeAnimation();
    const chargeForce = backgroundRef.current.d3Force('charge') as { strength?: (value: number) => unknown } | undefined;
    chargeForce?.strength?.(0);
    const linkForce = backgroundRef.current.d3Force('link') as { strength?: (value: number) => unknown; distance?: (value: number) => unknown } | undefined;
    linkForce?.strength?.(0);
    linkForce?.distance?.(0);
    backgroundRef.current.cameraPosition({ x: 0, y: 47.04, z: 627.2 }, { x: 0, y: 0, z: 0 }, 0);
  }, [backgroundGraph]);

  useEffect(() => {
    if (!featuredAlert) return;
    extrasRef.current.alertPulse?.showPulse(featuredAlert);
  }, [featuredAlert]);

  if (!mounted || !active) return null;

  return (
    <div
      id="home-engine-stage"
      ref={containerRef}
      aria-hidden="true"
      style={{
        position: 'absolute',
        inset: '-6% -2% -8% 5%',
        zIndex: 0,
        pointerEvents: panelLevel > 0 ? 'none' : 'auto',
        opacity: panelLevel === 0 ? (theme === 'light' ? 0.16 : 1) : panelLevel === 1 ? 0.58 : 0.32,
        transform: theme === 'light'
          ? 'scale(1) translateX(2.8%) translateY(-0.8%)'
          : 'scale(1.1) translateX(2.8%) translateY(0%)',
        filter: theme === 'light'
          ? 'brightness(0.78) saturate(0.42) blur(0.15px)'
          : 'brightness(1.08) saturate(0.92) contrast(1.04) blur(0px)',
        mixBlendMode: theme === 'light' ? 'multiply' : 'normal',
        maskImage: 'linear-gradient(90deg, transparent 0%, rgba(0, 0, 0, 0.28) 6%, rgba(0, 0, 0, 0.82) 14%, #000 24%, #000 100%)',
        WebkitMaskImage: 'linear-gradient(90deg, transparent 0%, rgba(0, 0, 0, 0.28) 6%, rgba(0, 0, 0, 0.82) 14%, #000 24%, #000 100%)',
        transition: 'opacity 240ms ease, transform 360ms ease, filter 240ms ease',
      }}
    >
      <OSSStarfield theme={theme} panelLevel={panelLevel} />
      <div
        style={{
          position: 'absolute',
          inset: 0,
          opacity: theme === 'light' ? 0.08 : panelLevel === 0 ? 0.3 : 0.16,
          filter: theme === 'light' ? 'blur(0.45px)' : 'blur(0.24px)',
          transform: 'scale(1.04)',
        }}
      >
        <ForceGraph3D<BackgroundNode, BackgroundLink>
          ref={backgroundRef}
          width={size.width}
          height={size.height}
          graphData={backgroundGraph}
          forceEngine="d3"
          backgroundColor="rgba(0,0,0,0)"
          showNavInfo={false}
          enableNodeDrag={false}
          enableNavigationControls={false}
          nodeId="id"
          nodeRelSize={0.22}
          nodeVal={(node) => node.val}
          nodeColor={(node) => node.color}
          nodeOpacity={0.58}
          nodeResolution={3}
          linkOpacity={0.12}
          linkColor={(link) => link.color}
          linkWidth={(link) => link.width}
          linkDirectionalParticles={0}
          onEngineStop={() => {
            backgroundRef.current?.cameraPosition({ x: 0, y: 47.04, z: 627.2 }, { x: 0, y: 0, z: 0 }, 0);
          }}
        />
      </div>
      <ForceGraph3D<ForceNode, ForceLink>
        ref={fgRef}
        width={size.width}
        height={size.height}
        graphData={renderedGraph}
        forceEngine="d3"
        backgroundColor="rgba(0,0,0,0)"
        showNavInfo={false}
        enableNodeDrag={false}
        enableNavigationControls={panelLevel === 0}
        nodeId="id"
        nodeVisibility={(node) => node.id !== BOOTSTRAP_NODE_ID}
        nodeRelSize={1.18}
        nodeVal={(node) => mode === 'focus' ? node.val * 1.05 : node.val}
        nodeColor={(node) => node.color}
        nodeOpacity={denseGraph ? 0.82 : 0.9}
        nodeResolution={denseGraph ? 5 : 8}
        nodeLabel={(node) => `${node.node_type}<br/>${node.name}`}
        linkOpacity={0.28}
        linkColor={(link) => highlightedSet.has(link.edge_id) ? '#b8f3ff' : link.color}
        linkWidth={(link) => {
          const base = mode === 'focus' ? link.width * 1.06 : link.width;
          return highlightedSet.has(link.edge_id) ? base * 2.1 : base;
        }}
        linkCurvature={(link) => link.curvature}
        linkDirectionalParticles={(link) => highlightedSet.has(link.edge_id) ? Math.max(link.particles, 3) : link.particles}
        linkDirectionalParticleWidth={(link) => highlightedSet.has(link.edge_id) ? 2.6 : 1.4}
        linkDirectionalParticleSpeed={(link) => highlightedSet.has(link.edge_id) ? 0.008 : 0.004}
        linkDirectionalParticleColor={(link) => highlightedSet.has(link.edge_id) ? '#ffffff' : link.color}
        linkDirectionalArrowLength={0}
        linkDirectionalArrowRelPos={0.82}
        linkDirectionalArrowColor={(link) => link.color}
        onNodeClick={(node) => nodeClickCbRef.current?.(node.id)}
        onBackgroundClick={() => setHighlightedEdgeIds([])}
        onEngineStop={() => {
          fgRef.current?.cameraPosition({ x: 0, y: 42, z: 560 }, { x: 0, y: 0, z: 0 }, 0);
        }}
      />
    </div>
  );
}
