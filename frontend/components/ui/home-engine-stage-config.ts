import type { GraphData, ResponsibilityEdge, ResponsibilityNode } from '@/lib/types';

export type ForceNode = ResponsibilityNode & {
  id: string;
  name: string;
  val: number;
  color: string;
  x?: number;
  y?: number;
  z?: number;
  fx?: number;
  fy?: number;
  fz?: number;
};

export type ForceLink = Omit<ResponsibilityEdge, 'source' | 'target'> & {
  source: string | ForceNode;
  target: string | ForceNode;
  curvature: number;
  width: number;
  color: string;
  particles: number;
};

export type BackgroundNode = {
  id: string;
  val: number;
  color: string;
  x: number;
  y: number;
  z: number;
  fx: number;
  fy: number;
  fz: number;
};

export type BackgroundLink = {
  source: string | BackgroundNode;
  target: string | BackgroundNode;
  color: string;
  width: number;
};

export const BOOTSTRAP_NODE_ID = '__bootstrap__';

const EDGE_COLORS: Record<string, string> = {
  IntentCausesAction: 'rgba(215,229,255,0.85)',
  AgentDelegatesTo: 'rgba(144,176,255,0.92)',
  ContextFlowsFrom: 'rgba(116,216,255,0.88)',
  TaintPropagatesTo: 'rgba(247,185,85,0.86)',
  PolicyBoundaryCrossing: 'rgba(255,132,164,0.92)',
  CompetingExplanation: 'rgba(138,149,176,0.72)',
};

const FOREGROUND_NODE_COLORS: Record<ResponsibilityNode['node_type'], string> = {
  Agent: '#92dcff',
  Resource: '#9eb8ff',
  PolicyBoundary: '#ff8ca8',
};

const BACKGROUND_NODE_PALETTE = ['#dde7ff', '#bfd5ff', '#a4c8ff', '#cad7ff'];

type Vec3 = { x: number; y: number; z: number };

export function hashToUnit(seed: string): number {
  let hash = 2166136261;
  for (let i = 0; i < seed.length; i += 1) {
    hash ^= seed.charCodeAt(i);
    hash = Math.imul(hash, 16777619);
  }
  return ((hash >>> 0) % 1000) / 1000;
}

export function hashToInt(seed: string): number {
  let hash = 0;
  for (let i = 0; i < seed.length; i += 1) {
    hash = (hash << 5) - hash + seed.charCodeAt(i);
    hash |= 0;
  }
  return Math.abs(hash);
}

function createSeededRandom(seed: number): () => number {
  let value = seed || 1;
  return () => {
    value ^= value << 13;
    value ^= value >> 17;
    value ^= value << 5;
    return ((value >>> 0) % 1000) / 1000;
  };
}

function createSeededPoint(seed: string, radiusMin: number, radiusMax: number, squash = 0.58): Vec3 {
  const random = createSeededRandom(hashToInt(seed));
  const theta = random() * Math.PI * 2;
  const phi = Math.acos(2 * random() - 1);
  const radius = radiusMin + (radiusMax - radiusMin) * random();
  return {
    x: radius * Math.sin(phi) * Math.cos(theta),
    y: radius * Math.cos(phi) * squash,
    z: radius * Math.sin(phi) * Math.sin(theta),
  };
}

function addVec3(a: Vec3, b: Vec3): Vec3 {
  return { x: a.x + b.x, y: a.y + b.y, z: a.z + b.z };
}

function scaleVec3(value: Vec3, scale: number): Vec3 {
  return { x: value.x * scale, y: value.y * scale, z: value.z * scale };
}

export function getNodeName(node: ResponsibilityNode): string {
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

export function buildForceNode(node: ResponsibilityNode): ForceNode {
  const position =
    node.node_type === 'Agent'
      ? createSeededPoint(`${node.node_id}:agent`, 34, 92, 0.46)
      : node.node_type === 'PolicyBoundary'
        ? createSeededPoint(`${node.node_id}:boundary`, 210, 320, 0.3)
        : createSeededPoint(`${node.node_id}:resource`, 96, 210, 0.62);

  return {
    ...node,
    id: node.node_id,
    name: getNodeName(node),
    val: node.node_type === 'Agent' ? 2.8 : node.node_type === 'PolicyBoundary' ? 2.2 : 1.75,
    color: FOREGROUND_NODE_COLORS[node.node_type],
    x: position.x,
    y: position.y,
    z: position.z,
  };
}

export function buildForceLink(edge: ResponsibilityEdge): ForceLink {
  const tension = hashToUnit(edge.edge_id);
  return {
    ...edge,
    source: edge.source,
    target: edge.target,
    curvature:
      edge.edge_type === 'PolicyBoundaryCrossing'
        ? 0.08
        : edge.edge_type === 'TaintPropagatesTo'
          ? 0.06
          : 0.015 + tension * 0.03,
    width:
      edge.edge_type === 'PolicyBoundaryCrossing'
        ? 0.84
        : edge.edge_type === 'AgentDelegatesTo'
          ? 0.56
          : edge.edge_type === 'TaintPropagatesTo'
            ? 0.48
            : 0.34,
    color: EDGE_COLORS[edge.edge_type] ?? 'rgba(219,234,254,0.85)',
    particles: 0,
  };
}

export function bootstrapGraph(): { nodes: ForceNode[]; links: ForceLink[] } {
  return {
    nodes: [
      {
        node_id: BOOTSTRAP_NODE_ID,
        node_type: 'Resource',
        agent_id: null,
        resource: null,
        evidence_refs: [],
        timestamp_ns: 0,
        phase: { Provisional: { timestamp_ns: 0 } },
        id: BOOTSTRAP_NODE_ID,
        name: 'bootstrap',
        val: 0.001,
        color: '#000000',
        fx: 0,
        fy: 0,
        fz: 0,
      },
    ],
    links: [],
  };
}

export function createBackgroundGraph(graph: GraphData): { nodes: BackgroundNode[]; links: BackgroundLink[] } {
  const nodes: BackgroundNode[] = [];
  const links: BackgroundLink[] = [];

  const pushNode = (id: string, position: Vec3, color: string, val: number) => {
    nodes.push({
      id,
      val,
      color,
      x: position.x,
      y: position.y,
      z: position.z,
      fx: position.x,
      fy: position.y,
      fz: position.z,
    });
  };

  const pushLink = (source: string, target: string, color: string, width: number) => {
    links.push({ source, target, color, width });
  };

  if (!graph.nodes.length) {
    pushNode(BOOTSTRAP_NODE_ID, { x: 0, y: 0, z: 0 }, '#000000', 0.001);
    return { nodes, links };
  }

  const anchorCandidates = graph.nodes.map(buildForceNode);
  const anchorLimit = graph.nodes.length > 96 ? 72 : graph.nodes.length;
  const priorityAnchors = anchorCandidates.filter((node) => node.node_type !== 'Resource');
  const resourceAnchors = anchorCandidates.filter((node) => node.node_type === 'Resource');
  const resourceStride = Math.max(1, Math.ceil(resourceAnchors.length / Math.max(1, anchorLimit - priorityAnchors.length)));
  const anchors = graph.nodes.length <= anchorLimit
    ? anchorCandidates
    : [
        ...priorityAnchors,
        ...resourceAnchors.filter((_, index) => index % resourceStride === 0),
      ].slice(0, anchorLimit);

  anchors.forEach((anchor, index) => {
    const base = { x: anchor.x ?? 0, y: anchor.y ?? 0, z: anchor.z ?? 0 };
    const hubPosition = scaleVec3(base, anchor.node_type === 'PolicyBoundary' ? 1.85 : 2.15);
    const hubId = `bg-hub:${anchor.id}`;
    const clusterColor =
      anchor.node_type === 'PolicyBoundary' ? '#f3b2c6' : anchor.node_type === 'Agent' ? '#cfe7ff' : '#b7ccff';

    pushNode(hubId, hubPosition, clusterColor, 0.17);

    const random = createSeededRandom(hashToInt(`${anchor.id}:background-cluster`));
    const clusterCount = 18 + Math.floor(random() * 18);
    const clusterIds: string[] = [];

    for (let i = 0; i < clusterCount; i += 1) {
      const offset = createSeededPoint(`${anchor.id}:cluster:${i}`, 14, 94, 0.54);
      const nodeId = `bg-cluster:${anchor.id}:${i}`;
      pushNode(nodeId, addVec3(hubPosition, offset), i % 9 === 0 ? '#eef4ff' : clusterColor, 0.05 + random() * 0.06);
      clusterIds.push(nodeId);

      if (i > 0) {
        const targetId = random() > 0.58 ? hubId : clusterIds[Math.floor(random() * i)];
        pushLink(targetId, nodeId, 'rgba(176,198,255,0.12)', 0.16 + random() * 0.06);
      }
    }

    if (index > 0) {
      pushLink(`bg-hub:${anchors[index - 1].id}`, hubId, 'rgba(164,190,255,0.08)', 0.14);
    }
  });

  const ambientRandom = createSeededRandom(hashToInt(graph.nodes.map((node) => node.node_id).join('|')));
  const ambientClusterCount = Math.max(16, Math.min(30, graph.nodes.length * 4));

  for (let clusterIndex = 0; clusterIndex < ambientClusterCount; clusterIndex += 1) {
    const center = createSeededPoint(`ambient:${clusterIndex}`, 460, 1240, 0.4);
    const clusterSize = 18 + Math.floor(ambientRandom() * 24);
    const clusterColor = BACKGROUND_NODE_PALETTE[clusterIndex % BACKGROUND_NODE_PALETTE.length];
    const localIds: string[] = [];

    for (let i = 0; i < clusterSize; i += 1) {
      const offset = createSeededPoint(`ambient:${clusterIndex}:${i}`, 10, 72, 0.42);
      const nodeId = `bg-ambient:${clusterIndex}:${i}`;
      pushNode(nodeId, addVec3(center, offset), ambientRandom() > 0.9 ? '#f6f9ff' : clusterColor, 0.035 + ambientRandom() * 0.045);
      localIds.push(nodeId);

      if (i > 0) {
        pushLink(localIds[Math.floor(ambientRandom() * i)], nodeId, 'rgba(188,208,255,0.06)', 0.1);
      }
    }
  }

  return { nodes, links };
}
