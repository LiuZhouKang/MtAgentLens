import type { GraphData, ResponsibilityEdge, ResponsibilityNode } from '@/lib/types';

export type ForceNode = ResponsibilityNode & {
  id: string;
  name: string;
  val: number;
  color: string;
  semanticGroup: string;
  importance: number;
  satellites: number;
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

export type StageNode = {
  id: string;
  label: string;
  color: string;
  semanticGroup: string;
  realNodeId: string;
  nodeType: ResponsibilityNode['node_type'];
  kind: 'core' | 'satellite';
  val: number;
  importance: number;
  x?: number;
  y?: number;
  z?: number;
  fx?: number;
  fy?: number;
  fz?: number;
};

export type StageLink = {
  id: string;
  source: string | StageNode;
  target: string | StageNode;
  color: string;
  width: number;
  particles: number;
  kind: 'real' | 'structural' | 'bridge';
  edgeType: ResponsibilityEdge['edge_type'] | 'Cluster';
  realEdgeId?: string;
};

export const BOOTSTRAP_NODE_ID = '__bootstrap__';

const EDGE_COLORS: Record<string, string> = {
  IntentCausesAction: '#e4eeff',
  AgentDelegatesTo: '#8abbff',
  ContextFlowsFrom: '#78e5ff',
  TaintPropagatesTo: '#ffc67a',
  PolicyBoundaryCrossing: '#ff8dc1',
  CompetingExplanation: '#b2bddc',
};

const FOREGROUND_NODE_COLORS: Record<ResponsibilityNode['node_type'], string> = {
  Agent: '#aef4ff',
  Resource: '#a4b7ff',
  PolicyBoundary: '#ff98c8',
};

const BACKGROUND_NODE_PALETTE = ['#dde7ff', '#bfd5ff', '#a4c8ff', '#cad7ff'];
const SEMANTIC_GROUP_CENTERS: Record<string, Vec3> = {
  'agent:primary': { x: -124, y: 18, z: -68 },
  'agent:delegated': { x: -22, y: 26, z: 118 },
  'agent:aux': { x: -182, y: -52, z: 76 },
  'resource:file:workspace': { x: 118, y: -22, z: -48 },
  'resource:file:sensitive': { x: 214, y: -26, z: 88 },
  'resource:network': { x: 96, y: 54, z: 166 },
  'resource:other': { x: 152, y: -72, z: 8 },
  'boundary:workspace': { x: -246, y: 108, z: -138 },
  'boundary:system': { x: 252, y: 96, z: 58 },
  'boundary:generic': { x: 12, y: 132, z: -214 },
};
const SEMANTIC_GROUP_COLORS: Record<string, string> = {
  'agent:primary': '#e0fbff',
  'agent:delegated': '#7fb4ff',
  'agent:aux': '#67f1c8',
  'resource:file:workspace': '#aab4ff',
  'resource:file:sensitive': '#ffc980',
  'resource:network': '#64ecff',
  'resource:other': '#cfb7ff',
  'boundary:workspace': '#ff9fd6',
  'boundary:system': '#ff6f96',
  'boundary:generic': '#cb95ff',
};

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

function lightenHex(hex: string, amount: number): string {
  const normalized = hex.replace('#', '');
  const value = normalized.length === 3
    ? normalized.split('').map((char) => `${char}${char}`).join('')
    : normalized;
  const red = parseInt(value.slice(0, 2), 16);
  const green = parseInt(value.slice(2, 4), 16);
  const blue = parseInt(value.slice(4, 6), 16);
  const lift = (channel: number) => Math.max(0, Math.min(255, Math.round(channel + (255 - channel) * amount)));
  return `#${lift(red).toString(16).padStart(2, '0')}${lift(green).toString(16).padStart(2, '0')}${lift(blue).toString(16).padStart(2, '0')}`;
}

function getSemanticGroup(node: ResponsibilityNode): string {
  if (node.node_type === 'Agent') {
    const id = `${node.agent_id ?? node.node_id}`.toLowerCase();
    if (id.includes('security') || id.includes('audit')) return 'agent:delegated';
    if (id.includes('builder') || id.includes('worker') || id.includes('tool')) return 'agent:aux';
    return 'agent:primary';
  }

  if (node.node_type === 'PolicyBoundary') {
    const id = node.node_id.toLowerCase();
    if (id.includes('system') || id.includes('root') || id.includes('prod')) return 'boundary:system';
    if (id.includes('workspace') || id.includes('project')) return 'boundary:workspace';
    return 'boundary:generic';
  }

  if (node.resource && 'File' in node.resource) {
    const path = node.resource.File.canonical_path.toLowerCase();
    if (path.startsWith('/etc') || path.includes('shadow') || path.includes('credential')) return 'resource:file:sensitive';
    if (path.startsWith('/src') || path.startsWith('/home') || path.startsWith('/workspace') || path.includes('/project')) {
      return 'resource:file:workspace';
    }
    return 'resource:other';
  }

  if (node.resource && 'Network' in node.resource) {
    return 'resource:network';
  }

  return 'resource:other';
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
  const semanticGroup = getSemanticGroup(node);
  const center = SEMANTIC_GROUP_CENTERS[semanticGroup]
    ?? (node.node_type === 'Agent'
      ? { x: -96, y: 12, z: 28 }
      : node.node_type === 'PolicyBoundary'
        ? { x: 218, y: 102, z: 26 }
        : { x: 118, y: -24, z: 84 });
  const spread =
    node.node_type === 'PolicyBoundary'
      ? createSeededPoint(`${node.node_id}:boundary`, 16, 54, 0.32)
      : node.node_type === 'Agent'
        ? createSeededPoint(`${node.node_id}:agent`, 14, 46, 0.52)
        : createSeededPoint(`${node.node_id}:resource`, 18, 66, 0.58);
  const position = addVec3(center, spread);
  const importance = node.node_type === 'Agent' ? 1 : node.node_type === 'PolicyBoundary' ? 0.92 : semanticGroup === 'resource:file:sensitive' ? 0.82 : 0.7;

  return {
    ...node,
    id: node.node_id,
    name: getNodeName(node),
    val: node.node_type === 'Agent' ? 3.6 : node.node_type === 'PolicyBoundary' ? 3.05 : 2.35,
    color: SEMANTIC_GROUP_COLORS[semanticGroup] ?? FOREGROUND_NODE_COLORS[node.node_type],
    semanticGroup,
    importance,
    satellites: node.node_type === 'PolicyBoundary' ? 18 : node.node_type === 'Agent' ? 14 : semanticGroup === 'resource:file:sensitive' ? 11 : 8,
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
        ? 0.14
        : edge.edge_type === 'TaintPropagatesTo'
          ? 0.1
          : edge.edge_type === 'AgentDelegatesTo'
            ? 0.08 + tension * 0.05
            : 0.045 + tension * 0.055,
    width:
      edge.edge_type === 'PolicyBoundaryCrossing'
        ? 1.18
        : edge.edge_type === 'AgentDelegatesTo'
          ? 0.86
          : edge.edge_type === 'TaintPropagatesTo'
            ? 0.72
            : 0.46,
    color: EDGE_COLORS[edge.edge_type] ?? '#dbeafe',
    particles:
      edge.edge_type === 'PolicyBoundaryCrossing'
        ? 9
        : edge.edge_type === 'TaintPropagatesTo'
          ? 7
          : edge.edge_type === 'AgentDelegatesTo'
            ? 5
            : edge.edge_type === 'ContextFlowsFrom'
              ? 4
              : edge.edge_type === 'IntentCausesAction'
                ? 3
                : 2,
  };
}

export function buildStageGraph(
  graph: GraphData,
  pinnedPositions: Record<string, { x: number; y: number; z: number }> = {},
): { nodes: StageNode[]; links: StageLink[] } {
  if (!graph.nodes.length) {
    return {
      nodes: [
        {
          id: BOOTSTRAP_NODE_ID,
          label: 'bootstrap',
          color: '#000000',
          semanticGroup: 'resource:other',
          realNodeId: BOOTSTRAP_NODE_ID,
          nodeType: 'Resource',
          kind: 'core',
          val: 0.001,
          importance: 0,
          fx: 0,
          fy: 0,
          fz: 0,
        },
      ],
      links: [],
    };
  }

  const nodes: StageNode[] = [];
  const links: StageLink[] = [];
  const clusterMembers = new Map<string, string[]>();
  const baseNodes = graph.nodes.map(buildForceNode);

  const applyPinnedPosition = (node: StageNode): StageNode => {
    const pinned = pinnedPositions[node.id];
    if (!pinned) return node;
    return {
      ...node,
      x: pinned.x,
      y: pinned.y,
      z: pinned.z,
      fx: pinned.x,
      fy: pinned.y,
      fz: pinned.z,
    };
  };

  const degreeByNodeId = new Map<string, number>();
  graph.edges.forEach((edge) => {
    degreeByNodeId.set(edge.source, (degreeByNodeId.get(edge.source) ?? 0) + 1);
    degreeByNodeId.set(edge.target, (degreeByNodeId.get(edge.target) ?? 0) + 1);
  });

  baseNodes.forEach((node) => {
    const degree = degreeByNodeId.get(node.id) ?? 0;
    const core: StageNode = applyPinnedPosition({
      id: node.id,
      label: node.name,
      color: node.color,
      semanticGroup: node.semanticGroup,
      realNodeId: node.id,
      nodeType: node.node_type,
      kind: 'core',
      val:
        (node.node_type === 'Agent' ? 2.4 : node.node_type === 'PolicyBoundary' ? 2.1 : 1.7)
        + Math.min(0.9, degree * 0.18),
      importance: node.importance,
      x: node.x,
      y: node.y,
      z: node.z,
    });
    nodes.push(core);

    const members: string[] = [];
    const seededRandom = createSeededRandom(hashToInt(`${node.id}:stage-cluster`));
    const clusterCount =
      node.node_type === 'Agent'
        ? 28 + Math.min(18, degree * 4)
        : node.node_type === 'PolicyBoundary'
          ? 20 + Math.min(14, degree * 3)
          : node.semanticGroup === 'resource:file:sensitive'
            ? 18 + Math.min(12, degree * 3)
            : 14 + Math.min(10, degree * 3);

    const localAnchors = Array.from({ length: Math.min(4, Math.max(2, Math.ceil(clusterCount / 10))) }, (_, index) =>
      createSeededPoint(
        `${node.id}:cluster-anchor:${index}`,
        8,
        node.node_type === 'PolicyBoundary' ? 30 : node.node_type === 'Agent' ? 24 : 20,
        0.62,
      ),
    );

    for (let i = 0; i < clusterCount; i += 1) {
      const anchor = localAnchors[i % localAnchors.length] ?? { x: 0, y: 0, z: 0 };
      const offset = addVec3(
        anchor,
        createSeededPoint(
          `${node.id}:stage:${i}`,
          2,
          node.node_type === 'PolicyBoundary' ? 18 : node.node_type === 'Agent' ? 14 : 12,
          0.72,
        ),
      );
      const satelliteId = `${node.id}::sat:${i}`;
      const satellite: StageNode = applyPinnedPosition({
        id: satelliteId,
        label: `${node.name} satellite ${i + 1}`,
        color:
          i % 7 === 0
            ? lightenHex(node.color, 0.42)
            : i % 5 === 0
              ? lightenHex(node.color, 0.22)
              : node.color,
        semanticGroup: node.semanticGroup,
        realNodeId: node.id,
        nodeType: node.node_type,
        kind: 'satellite',
        val: 0.16 + seededRandom() * 0.22,
        importance: Math.max(0.2, node.importance * 0.55),
        x: (node.x ?? 0) + offset.x,
        y: (node.y ?? 0) + offset.y,
        z: (node.z ?? 0) + offset.z,
      });
      nodes.push(satellite);
      members.push(satelliteId);

      links.push({
        id: `${node.id}::struct:${i}`,
        source: i % 5 === 0 || !members[Math.max(0, i - 1)] ? node.id : members[Math.max(0, i - 1)],
        target: satelliteId,
        color: i % 6 === 0 ? '#dcecff' : '#b0c6ff',
        width: 0.08 + seededRandom() * 0.08,
        particles: 0,
        kind: 'structural',
        edgeType: 'Cluster',
      });

      if (i > 1) {
        const previousSatellite = members[Math.max(0, i - 1)];
        const randomSatellite = members[Math.floor(seededRandom() * Math.max(1, i - 1))];
        links.push({
          id: `${node.id}::mesh:${i}`,
          source: seededRandom() > 0.55 ? previousSatellite : randomSatellite,
          target: satelliteId,
          color: '#a4beff',
          width: 0.06 + seededRandom() * 0.05,
          particles: 0,
          kind: 'structural',
          edgeType: 'Cluster',
        });
      }
    }

    clusterMembers.set(node.id, members);
  });

  graph.edges.forEach((edge) => {
    const baseLink = buildForceLink(edge);
    links.push({
      id: edge.edge_id,
      source: edge.source,
      target: edge.target,
      color: baseLink.color,
      width: baseLink.width,
      particles: Math.max(1, baseLink.particles - 1),
      kind: 'real',
      edgeType: edge.edge_type,
      realEdgeId: edge.edge_id,
    });

    const sourceMembers = clusterMembers.get(edge.source) ?? [];
    const targetMembers = clusterMembers.get(edge.target) ?? [];
    const bridgeCount =
      edge.edge_type === 'PolicyBoundaryCrossing'
        ? 4
        : edge.edge_type === 'TaintPropagatesTo'
          ? 3
          : edge.edge_type === 'AgentDelegatesTo'
            ? 2
            : 2;

    for (let i = 0; i < bridgeCount; i += 1) {
      if (!sourceMembers.length || !targetMembers.length) break;
      const sourceSatellite = sourceMembers[(hashToInt(`${edge.edge_id}:source:${i}`)) % sourceMembers.length];
      const targetSatellite = targetMembers[(hashToInt(`${edge.edge_id}:target:${i}`)) % targetMembers.length];
      links.push({
        id: `${edge.edge_id}::bridge:${i}`,
        source: sourceSatellite,
        target: targetSatellite,
        color: baseLink.color,
        width: Math.max(0.08, baseLink.width * 0.22),
        particles: 0,
        kind: 'bridge',
        edgeType: edge.edge_type,
        realEdgeId: edge.edge_id,
      });
    }
  });

  return { nodes, links };
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
        semanticGroup: 'resource:other',
        importance: 0,
        satellites: 0,
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
    const hubPosition = addVec3(scaleVec3(base, anchor.node_type === 'PolicyBoundary' ? 1.38 : 1.74), {
      x: anchor.node_type === 'PolicyBoundary' ? 0 : index % 2 === 0 ? -18 : 18,
      y: anchor.node_type === 'Agent' ? 10 : 0,
      z: index % 3 === 0 ? 14 : -12,
    });
    const hubId = `bg-hub:${anchor.id}`;
    const clusterColor = anchor.color;

    pushNode(hubId, hubPosition, clusterColor, 0.19);

    const random = createSeededRandom(hashToInt(`${anchor.id}:background-cluster`));
    const clusterCount = 22 + Math.floor(random() * 24);
    const clusterIds: string[] = [];

    for (let i = 0; i < clusterCount; i += 1) {
      const offset = createSeededPoint(`${anchor.id}:cluster:${i}`, 16, 108, 0.54);
      const nodeId = `bg-cluster:${anchor.id}:${i}`;
      pushNode(nodeId, addVec3(hubPosition, offset), i % 9 === 0 ? '#eef4ff' : clusterColor, 0.055 + random() * 0.075);
      clusterIds.push(nodeId);

      if (i > 0) {
        const targetId = random() > 0.58 ? hubId : clusterIds[Math.floor(random() * i)];
        pushLink(targetId, nodeId, '#b0c6ff', 0.18 + random() * 0.08);
      }
    }

    if (index > 0) {
      pushLink(`bg-hub:${anchors[index - 1].id}`, hubId, '#a4beff', 0.18);
    }
  });

  const ambientRandom = createSeededRandom(hashToInt(graph.nodes.map((node) => node.node_id).join('|')));
  const ambientClusterCount = Math.max(20, Math.min(42, graph.nodes.length * 5));

  for (let clusterIndex = 0; clusterIndex < ambientClusterCount; clusterIndex += 1) {
    const center = createSeededPoint(`ambient:${clusterIndex}`, 460, 1240, 0.4);
    const clusterSize = 22 + Math.floor(ambientRandom() * 28);
    const clusterColor = BACKGROUND_NODE_PALETTE[clusterIndex % BACKGROUND_NODE_PALETTE.length];
    const localIds: string[] = [];

    for (let i = 0; i < clusterSize; i += 1) {
      const offset = createSeededPoint(`ambient:${clusterIndex}:${i}`, 12, 82, 0.42);
      const nodeId = `bg-ambient:${clusterIndex}:${i}`;
      pushNode(nodeId, addVec3(center, offset), ambientRandom() > 0.9 ? '#f6f9ff' : clusterColor, 0.038 + ambientRandom() * 0.052);
      localIds.push(nodeId);

      if (i > 0) {
        pushLink(localIds[Math.floor(ambientRandom() * i)], nodeId, '#bcd0ff', 0.12);
      }
    }
  }

  return { nodes, links };
}
