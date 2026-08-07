'use client';

import * as THREE from 'three';
import ForceGraph3D from 'react-force-graph-3d';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  buildStageGraph,
  getNodeName,
  type StageLink,
  type StageNode,
} from '@/components/ui/home-engine-stage-config';
import { useAppStore } from '@/lib/store';
import type { ResponsibilityAlert, ResponsibilityEdge, ResponsibilityNode } from '@/lib/types';

type StageNodeSelection = {
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

type StageEdgeSelection = {
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

interface RuntimeTopologyStageProps {
  active: boolean;
  featuredAlert: ResponsibilityAlert | null;
  theme: 'light' | 'dark';
  panelLevel: number;
  focusEdgeIds?: string[];
  onReady?: () => void;
  onError?: (error: Error) => void;
  onNodeSelected?: (selection: StageNodeSelection) => void;
  onEdgeSelected?: (selection: StageEdgeSelection) => void;
}

type EngineAPI = NonNullable<ReturnType<typeof useAppStore.getState>['engineRef']['current']>;

type GraphHandle = {
  cameraPosition: (
    position: Partial<{ x: number; y: number; z: number }>,
    lookAt?: { x: number; y: number; z: number },
    ms?: number,
  ) => void;
  zoomToFit: (ms?: number, padding?: number, nodeFilter?: (node: StageNode) => boolean) => void;
  d3Force: (
    name: string,
  ) => { strength: (value: number) => unknown; distance?: (value: number) => unknown } | undefined;
  d3ReheatSimulation: () => void;
  pauseAnimation?: () => void;
  resumeAnimation?: () => void;
  scene: () => THREE.Scene;
  renderer: () => { setPixelRatio: (ratio: number) => void };
} | null;

const EDGE_TONES: Record<ResponsibilityEdge['edge_type'], { color: string; particle: string }> = {
  IntentCausesAction: { color: '#92c5ff', particle: '#d8e8ff' },
  AgentDelegatesTo: { color: '#6ee7ff', particle: '#c8fbff' },
  ContextFlowsFrom: { color: '#7dd3fc', particle: '#bff5ff' },
  TaintPropagatesTo: { color: '#fbbf24', particle: '#fde68a' },
  PolicyBoundaryCrossing: { color: '#fb7185', particle: '#fda4af' },
  CompetingExplanation: { color: '#94a3b8', particle: '#cbd5e1' },
  Delegation: { color: '#6ee7ff', particle: '#c8fbff' },
  AuthorityTransfer: { color: '#9eb7ff', particle: '#dbeafe' },
  CapabilityUse: { color: '#c084fc', particle: '#e9d5ff' },
  ContextInheritance: { color: '#7dd3fc', particle: '#bff5ff' },
  TaintPropagation: { color: '#fbbf24', particle: '#fde68a' },
  ViolationProvenance: { color: '#fb7185', particle: '#fda4af' },
};

function getResourceIdentity(node: ResponsibilityNode): string | null {
  if (!node.resource) return null;
  if ('File' in node.resource) return node.resource.File.canonical_path.toLowerCase();
  if ('Network' in node.resource)
    return `${node.resource.Network.host}:${node.resource.Network.port}`.toLowerCase();
  return null;
}

function deriveAlertFocusLocal(alert: ResponsibilityAlert | null): {
  nodeIds: Set<string>;
  edgeIds: Set<string>;
} {
  if (!alert) {
    return { nodeIds: new Set(), edgeIds: new Set() };
  }

  const nodeIds = new Set<string>();
  const edgeIds = new Set<string>();
  const agentIds = new Set([
    alert.primary_agent.agent_id,
    ...alert.involved_agents.map((item) => item.agent_id),
  ]);
  const resourceHints = new Set(
    alert.timeline.steps
      .map((step) => step.resource?.toLowerCase())
      .filter((value): value is string => Boolean(value)),
  );

  alert.graph_snapshot.nodes.forEach((node) => {
    if (node.agent_id && agentIds.has(node.agent_id)) {
      nodeIds.add(node.node_id);
      return;
    }

    const resourceIdentity = getResourceIdentity(node);
    if (
      resourceIdentity &&
      Array.from(resourceHints).some(
        (hint) => resourceIdentity.includes(hint) || hint.includes(resourceIdentity),
      )
    ) {
      nodeIds.add(node.node_id);
      return;
    }

    if (
      node.node_type === 'PolicyBoundary' &&
      alert.timeline.steps.some((step) => step.event_type === 'boundary_cross')
    ) {
      nodeIds.add(node.node_id);
    }
  });

  alert.graph_snapshot.edges.forEach((edge) => {
    const isRiskEdge =
      edge.edge_type === 'PolicyBoundaryCrossing' ||
      edge.edge_type === 'ViolationProvenance' ||
      edge.edge_type === 'TaintPropagatesTo' ||
      edge.edge_type === 'TaintPropagation' ||
      edge.edge_type === 'AgentDelegatesTo' ||
      edge.edge_type === 'Delegation' ||
      edge.edge_type === 'AuthorityTransfer';
    if (isRiskEdge || nodeIds.has(edge.source) || nodeIds.has(edge.target)) {
      edgeIds.add(edge.edge_id);
      nodeIds.add(edge.source);
      nodeIds.add(edge.target);
    }
  });

  return { nodeIds, edgeIds };
}

function describeNode(node: ResponsibilityNode): string {
  if (node.node_type === 'Agent') {
    return `${node.agent_id ?? node.node_id} is active in the current responsibility topology.`;
  }
  if (node.resource && 'File' in node.resource) {
    return `Resource ${node.resource.File.canonical_path} participates in the current runtime path.`;
  }
  if (node.resource && 'Network' in node.resource) {
    return `Network target ${node.resource.Network.host}:${node.resource.Network.port} is linked to the current runtime path.`;
  }
  return `${getNodeName(node)} is part of the current runtime topology.`;
}

function buildNodeSelection(
  node: StageNode,
  rawNode: ResponsibilityNode | undefined,
): StageNodeSelection {
  return {
    nodeId: node.realNodeId,
    rawNodeIds: [node.id],
    nodeType: node.nodeType,
    label: rawNode ? getNodeName(rawNode) : node.label,
    description: rawNode
      ? describeNode(rawNode)
      : `${node.label} is part of the current runtime topology.`,
    agentId: rawNode?.agent_id ?? undefined,
    clusterId: node.semanticGroup,
    boundaryKey: rawNode?.node_type === 'PolicyBoundary' ? rawNode.node_id : undefined,
    boundaryCount:
      rawNode?.node_type === 'PolicyBoundary' ? rawNode.evidence_refs.length : undefined,
  };
}

function buildEdgeSelection(
  link: StageLink,
  rawEdge: ResponsibilityEdge | undefined,
  sourceNode: ResponsibilityNode | undefined,
  targetNode: ResponsibilityNode | undefined,
): StageEdgeSelection {
  return {
    edgeId: rawEdge?.edge_id ?? link.realEdgeId ?? link.id,
    rawEdgeIds: rawEdge ? [rawEdge.edge_id] : undefined,
    edgeType: rawEdge?.edge_type ?? link.edgeType,
    sourceId:
      sourceNode?.node_id ?? (typeof link.source === 'string' ? link.source : link.source.id),
    targetId:
      targetNode?.node_id ?? (typeof link.target === 'string' ? link.target : link.target.id),
    sourceLabel: sourceNode
      ? getNodeName(sourceNode)
      : typeof link.source === 'string'
        ? link.source
        : link.source.label,
    targetLabel: targetNode
      ? getNodeName(targetNode)
      : typeof link.target === 'string'
        ? link.target
        : link.target.label,
    sourceType: sourceNode?.node_type ?? 'Resource',
    targetType: targetNode?.node_type ?? 'Resource',
    count: rawEdge?.evidence_refs.length ?? undefined,
    confidence: rawEdge?.confidence ?? 0.6,
    boundaryKey: rawEdge?.edge_type === 'PolicyBoundaryCrossing' ? rawEdge.target : undefined,
  };
}

function createCoreNodeObject(
  node: StageNode,
  emphasis: 'muted' | 'default' | 'focus',
): THREE.Object3D {
  const group = new THREE.Group();
  const baseRadius =
    node.nodeType === 'Agent' ? 4.2 : node.nodeType === 'PolicyBoundary' ? 3.8 : 3.3;
  const radius = emphasis === 'focus' ? baseRadius * 1.2 : baseRadius;
  const color = new THREE.Color(node.color);

  const sphere = new THREE.Mesh(
    new THREE.SphereGeometry(radius, 24, 24),
    new THREE.MeshStandardMaterial({
      color,
      emissive: color.clone().multiplyScalar(emphasis === 'focus' ? 0.72 : 0.34),
      emissiveIntensity: emphasis === 'focus' ? 1.05 : 0.56,
      metalness: 0.14,
      roughness: 0.34,
      transparent: true,
      opacity: emphasis === 'muted' ? 0.34 : 0.94,
    }),
  );
  group.add(sphere);

  const halo = new THREE.Mesh(
    new THREE.RingGeometry(radius * 1.65, radius * 1.9, 48),
    new THREE.MeshBasicMaterial({
      color,
      transparent: true,
      opacity: emphasis === 'focus' ? 0.22 : 0.08,
      side: THREE.DoubleSide,
    }),
  );
  halo.rotation.x = Math.PI / 2;
  group.add(halo);

  if (node.nodeType === 'PolicyBoundary') {
    const shell = new THREE.Mesh(
      new THREE.TorusGeometry(radius * 2.1, 0.14, 16, 64),
      new THREE.MeshBasicMaterial({
        color: color.clone().lerp(new THREE.Color('#ffffff'), 0.28),
        transparent: true,
        opacity: emphasis === 'focus' ? 0.26 : 0.12,
      }),
    );
    shell.rotation.y = Math.PI / 3;
    group.add(shell);
  }

  if (node.nodeType === 'Agent') {
    const crown = new THREE.Mesh(
      new THREE.OctahedronGeometry(radius * 0.28),
      new THREE.MeshBasicMaterial({
        color: color.clone().lerp(new THREE.Color('#ffffff'), 0.36),
        transparent: true,
        opacity: emphasis === 'muted' ? 0.34 : 0.76,
      }),
    );
    crown.position.y = radius * 1.48;
    group.add(crown);
  }

  return group;
}

function createSatelliteObject(
  node: StageNode,
  emphasis: 'muted' | 'default' | 'focus',
): THREE.Object3D {
  return new THREE.Mesh(
    new THREE.SphereGeometry(emphasis === 'focus' ? 0.72 : 0.46, 12, 12),
    new THREE.MeshBasicMaterial({
      color: new THREE.Color(node.color),
      transparent: true,
      opacity: emphasis === 'muted' ? 0.12 : emphasis === 'focus' ? 0.84 : 0.34,
    }),
  );
}

export default function RuntimeTopologyStage({
  active,
  featuredAlert,
  theme,
  panelLevel,
  focusEdgeIds = [],
  onReady,
  onError,
  onNodeSelected,
  onEdgeSelected,
}: RuntimeTopologyStageProps) {
  const graph = useAppStore((state) => state.graph);
  const engineRef = useAppStore((state) => state.engineRef);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const graphRef = useRef<GraphHandle>(null);
  const [size, setSize] = useState({ width: 1280, height: 860 });
  const [mounted, setMounted] = useState(false);
  const [manualHighlightEdgeIds, setManualHighlightEdgeIds] = useState<string[]>([]);

  useEffect(() => {
    setMounted(true);
  }, []);

  useEffect(() => {
    if (!containerRef.current) return undefined;
    const element = containerRef.current;
    const observer = new ResizeObserver((entries) => {
      const entry = entries[0];
      if (!entry) return;
      setSize({
        width: Math.max(320, Math.round(entry.contentRect.width)),
        height: Math.max(360, Math.round(entry.contentRect.height)),
      });
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  const stageGraph = useMemo(() => buildStageGraph(graph), [graph]);
  const rawNodeMap = useMemo(
    () => new Map(graph.nodes.map((node) => [node.node_id, node])),
    [graph.nodes],
  );
  const rawEdgeMap = useMemo(
    () => new Map(graph.edges.map((edge) => [edge.edge_id, edge])),
    [graph.edges],
  );
  const localAlertFocus = useMemo(() => deriveAlertFocusLocal(featuredAlert), [featuredAlert]);

  const effectiveFocusEdgeIds = manualHighlightEdgeIds.length
    ? manualHighlightEdgeIds
    : focusEdgeIds;
  const highlightEdgeIds = useMemo(
    () => new Set(effectiveFocusEdgeIds.length ? effectiveFocusEdgeIds : localAlertFocus.edgeIds),
    [effectiveFocusEdgeIds, localAlertFocus.edgeIds],
  );
  const highlightNodeIds = useMemo(() => {
    const ids = new Set(localAlertFocus.nodeIds);
    highlightEdgeIds.forEach((edgeId) => {
      const edge = rawEdgeMap.get(edgeId);
      if (!edge) return;
      ids.add(edge.source);
      ids.add(edge.target);
    });
    return ids;
  }, [highlightEdgeIds, localAlertFocus.nodeIds, rawEdgeMap]);

  const coreNodesById = useMemo(() => {
    const map = new Map<string, StageNode>();
    stageGraph.nodes.forEach((node) => {
      if (node.kind === 'core') {
        map.set(node.realNodeId, node);
      }
    });
    return map;
  }, [stageGraph.nodes]);

  const focusNode = useCallback(
    (realNodeId: string, duration = 900) => {
      const node = coreNodesById.get(realNodeId);
      if (!node || !graphRef.current) return;
      const target = {
        x: node.x ?? 0,
        y: node.y ?? 0,
        z: (node.z ?? 0) + 132,
      };
      graphRef.current.cameraPosition(
        target,
        { x: node.x ?? 0, y: node.y ?? 0, z: node.z ?? 0 },
        duration,
      );
    },
    [coreNodesById],
  );

  useEffect(() => {
    if (!mounted || !graphRef.current) return;

    try {
      const forceGraph = graphRef.current;
      const renderer = forceGraph.renderer() as {
        setPixelRatio: (ratio: number) => void;
        debug?: { checkShaderErrors?: boolean };
      };
      renderer.setPixelRatio(Math.min(window.devicePixelRatio, 1.75));
      if (renderer.debug) {
        renderer.debug.checkShaderErrors = false;
      }
      const scene = forceGraph.scene();
      scene.background = null;
      const ambient = new THREE.AmbientLight(
        theme === 'dark' ? '#c7dcff' : '#dbeafe',
        theme === 'dark' ? 1.08 : 0.9,
      );
      const hemisphere = new THREE.HemisphereLight(
        '#93c5fd',
        '#020617',
        theme === 'dark' ? 0.54 : 0.42,
      );
      const keyLight = new THREE.PointLight('#7dd3fc', theme === 'dark' ? 1.18 : 0.92, 960);
      keyLight.position.set(-260, 180, 240);
      const rimLight = new THREE.PointLight('#1d4ed8', theme === 'dark' ? 0.88 : 0.62, 900);
      rimLight.position.set(260, -120, 260);
      scene.add(ambient);
      scene.add(hemisphere);
      scene.add(keyLight);
      scene.add(rimLight);

      const charge = forceGraph.d3Force('charge');
      charge?.strength(-62);
      const link = forceGraph.d3Force('link');
      link?.distance?.(104);
      forceGraph.d3ReheatSimulation();
      forceGraph.zoomToFit(0, 40);
      onReady?.();
    } catch (error) {
      onError?.(error instanceof Error ? error : new Error(String(error)));
    }
  }, [mounted, onError, onReady, theme]);

  useEffect(() => {
    if (!featuredAlert) return;
    const primaryId = featuredAlert.primary_agent.agent_id;
    if (primaryId) {
      focusNode(primaryId, 780);
      return;
    }
    const focusEdge = effectiveFocusEdgeIds[0];
    if (!focusEdge) return;
    const edge = rawEdgeMap.get(focusEdge);
    if (edge) {
      focusNode(edge.source, 780);
    }
  }, [effectiveFocusEdgeIds, featuredAlert, focusNode, rawEdgeMap]);

  useEffect(() => {
    const engine: EngineAPI = {
      setVisualizationMode: () => {},
      flyToAgent: (agentId) => {
        const node = graph.nodes.find((entry) => entry.agent_id === agentId);
        if (node) focusNode(node.node_id);
      },
      flyToNode: (nodeId) => focusNode(nodeId),
      flyToLevel: (level) => {
        if (!graphRef.current) return;
        const z = level === 0 ? 320 : level === 1 ? 262 : level === 2 ? 214 : 184;
        graphRef.current.cameraPosition({ z }, undefined, 640);
      },
      resetCamera: (duration = 780) => {
        graphRef.current?.zoomToFit(duration, 110);
      },
      updateGraph: () => {
        graphRef.current?.d3ReheatSimulation();
      },
      highlightPath: (edgeIds) => setManualHighlightEdgeIds(edgeIds),
      clearHighlights: () => setManualHighlightEdgeIds([]),
      showAlertPulse: () => {},
      clearAlertPulses: () => {},
      orbit: () => {},
      zoom: (delta) => {
        if (!graphRef.current) return;
        graphRef.current.cameraPosition({ z: Math.max(140, 340 - delta * 40) }, undefined, 260);
      },
      onNodeClicked: () => {},
      onZoomLevelChanged: () => {},
      dispose: () => {},
    };

    engineRef.current = engine;
    return () => {
      if (engineRef.current === engine) {
        engineRef.current = null;
      }
    };
  }, [engineRef, focusNode, graph.nodes]);

  const handleNodeClick = useCallback(
    (node: StageNode) => {
      const rawNode = rawNodeMap.get(node.realNodeId);
      onNodeSelected?.(buildNodeSelection(node, rawNode));
      focusNode(node.realNodeId, 640);
    },
    [focusNode, onNodeSelected, rawNodeMap],
  );

  const handleLinkClick = useCallback(
    (link: StageLink) => {
      if (link.kind === 'structural' && !link.realEdgeId) return;
      const realEdgeId = link.realEdgeId ?? link.id;
      const rawEdge = rawEdgeMap.get(realEdgeId);
      if (!rawEdge) return;
      onEdgeSelected?.(
        buildEdgeSelection(
          link,
          rawEdge,
          rawNodeMap.get(rawEdge.source),
          rawNodeMap.get(rawEdge.target),
        ),
      );
      setManualHighlightEdgeIds([realEdgeId]);
      focusNode(rawEdge.source, 640);
    },
    [focusNode, onEdgeSelected, rawEdgeMap, rawNodeMap],
  );

  const nodeThreeObject = useCallback(
    (node: StageNode) => {
      const emphasized = highlightNodeIds.has(node.realNodeId);
      const emphasis = highlightNodeIds.size ? (emphasized ? 'focus' : 'muted') : 'default';
      return node.kind === 'core'
        ? createCoreNodeObject(node, emphasis)
        : createSatelliteObject(node, emphasis);
    },
    [highlightNodeIds],
  );

  const linkColor = useCallback(
    (link: StageLink) => {
      if (link.kind === 'structural') {
        return highlightNodeIds.size ? 'rgba(148, 163, 184, 0.08)' : 'rgba(148, 163, 184, 0.18)';
      }
      const tone =
        EDGE_TONES[
          (link.edgeType === 'Cluster'
            ? 'ContextFlowsFrom'
            : link.edgeType) as ResponsibilityEdge['edge_type']
        ];
      const color = tone?.color ?? link.color;
      if (!highlightNodeIds.size) return color;
      return highlightEdgeIds.has(link.realEdgeId ?? link.id) ? color : 'rgba(86, 101, 128, 0.18)';
    },
    [highlightEdgeIds, highlightNodeIds.size],
  );

  const linkWidth = useCallback(
    (link: StageLink) => {
      if (link.kind === 'structural') return highlightNodeIds.size ? 0.08 : 0.16;
      if (highlightEdgeIds.has(link.realEdgeId ?? link.id)) return Math.max(2.2, link.width * 2.6);
      if (highlightNodeIds.size) return Math.max(0.12, link.width * 0.44);
      return Math.max(0.28, link.width * 1.5);
    },
    [highlightEdgeIds, highlightNodeIds.size],
  );

  const linkParticles = useCallback(
    (link: StageLink) => {
      if (link.kind !== 'real') return 0;
      if (highlightEdgeIds.has(link.realEdgeId ?? link.id)) return 6;
      return highlightNodeIds.size ? 1 : Math.min(3, Math.max(1, link.particles));
    },
    [highlightEdgeIds, highlightNodeIds.size],
  );

  const linkParticleWidth = useCallback(
    (link: StageLink) => (highlightEdgeIds.has(link.realEdgeId ?? link.id) ? 3.4 : 1.6),
    [highlightEdgeIds],
  );

  const linkParticleSpeed = useCallback(
    (link: StageLink) => {
      if (highlightEdgeIds.has(link.realEdgeId ?? link.id)) return 0.014;
      return link.edgeType === 'PolicyBoundaryCrossing' ? 0.009 : 0.006;
    },
    [highlightEdgeIds],
  );

  const linkParticleColor = useCallback((link: StageLink) => {
    const tone =
      EDGE_TONES[
        (link.edgeType === 'Cluster'
          ? 'ContextFlowsFrom'
          : link.edgeType) as ResponsibilityEdge['edge_type']
      ];
    return tone?.particle ?? '#dbeafe';
  }, []);

  if (!mounted || !active) return null;

  return (
    <div
      id="home-engine-stage"
      aria-hidden="true"
      style={{
        position: 'absolute',
        inset: 0,
        zIndex: 0,
        opacity: panelLevel === 0 ? 1 : panelLevel === 1 ? 0.88 : 0.72,
        filter:
          theme === 'light' ? 'saturate(0.88) brightness(0.84)' : 'saturate(1.04) brightness(1.04)',
        transition: 'opacity 220ms ease, filter 220ms ease',
      }}
    >
      <div
        style={{
          position: 'absolute',
          inset: 0,
          background:
            theme === 'dark'
              ? 'radial-gradient(circle at 52% 54%, rgba(56, 189, 248, 0.12), transparent 24%), radial-gradient(circle at 64% 50%, rgba(37, 99, 235, 0.08), transparent 28%)'
              : 'radial-gradient(circle at 50% 50%, rgba(59, 130, 246, 0.08), transparent 28%)',
          pointerEvents: 'none',
        }}
      />
      <div ref={containerRef} style={{ position: 'absolute', inset: 0 }}>
        <ForceGraph3D
          ref={graphRef as never}
          graphData={stageGraph}
          width={size.width}
          height={size.height}
          backgroundColor="rgba(0,0,0,0)"
          showNavInfo={false}
          nodeRelSize={2.2}
          linkOpacity={0.78}
          linkDirectionalArrowLength={0}
          enableNodeDrag={false}
          enableNavigationControls
          controlType="orbit"
          warmupTicks={20}
          cooldownTicks={0}
          nodeThreeObject={nodeThreeObject as never}
          nodeThreeObjectExtend={false}
          nodeLabel={(node: StageNode) => `${node.label} · ${node.nodeType}`}
          linkColor={linkColor as never}
          linkWidth={linkWidth as never}
          linkCurvature={(link: StageLink) =>
            link.kind === 'structural'
              ? 0.04
              : link.edgeType === 'PolicyBoundaryCrossing'
                ? 0.12
                : link.edgeType === 'TaintPropagatesTo'
                  ? 0.09
                  : 0.06
          }
          linkDirectionalParticles={linkParticles as never}
          linkDirectionalParticleWidth={linkParticleWidth as never}
          linkDirectionalParticleSpeed={linkParticleSpeed as never}
          linkDirectionalParticleColor={linkParticleColor as never}
          onNodeClick={handleNodeClick as never}
          onLinkClick={handleLinkClick as never}
        />
      </div>
    </div>
  );
}
