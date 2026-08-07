interface LayoutNode {
  id: string;
  type: 'Agent' | 'Resource' | 'PolicyBoundary' | 'Process' | 'ToolCall' | 'Credential' | 'Context';
  x: number;
  y: number;
  z: number;
  vx?: number;
  vy?: number;
  vz?: number;
}

interface LayoutEdge {
  source: string;
  target: string;
  edge_type: string;
}

const STIFFNESS: Record<string, number> = {
  PolicyBoundaryCrossing: 2.0,
  ViolationProvenance: 2.0,
  TaintPropagatesTo: 1.5,
  TaintPropagation: 1.5,
  IntentCausesAction: 1.0,
  AgentDelegatesTo: 0.8,
  Delegation: 0.8,
  AuthorityTransfer: 0.9,
  CapabilityUse: 1.0,
  ContextFlowsFrom: 0.6,
  ContextInheritance: 0.6,
  CompetingExplanation: 0.3,
};

const REPEL = 5000;
const CENTER = 0.001;
const MAX_VELOCITY = 14;
const MAX_COORDINATE = 220;
const LAYER_Z: Record<string, number> = {
  Agent: -30,
  Resource: 0,
  Process: 0,
  ToolCall: 0,
  Credential: 0,
  Context: 0,
  PolicyBoundary: 30,
};

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

function finite(value: number, fallback = 0): number {
  return Number.isFinite(value) ? value : fallback;
}

export function forceLayout3D(
  nodes: LayoutNode[],
  edges: LayoutEdge[],
  iterations: number,
): LayoutNode[] {
  if (nodes.length === 0) return [];

  const result = nodes.map((n) => ({
    ...n,
    x: clamp(finite(n.x), -MAX_COORDINATE, MAX_COORDINATE),
    y: clamp(finite(n.y), -MAX_COORDINATE, MAX_COORDINATE),
    z: clamp(finite(n.z), -MAX_COORDINATE, MAX_COORDINATE),
    vx: 0,
    vy: 0,
    vz: 0,
  }));
  const nodeMap = new Map(result.map((n) => [n.id, n]));
  const adjacency = new Map<string, string[]>();
  for (const n of result) adjacency.set(n.id, []);
  for (const e of edges) {
    adjacency.get(e.source)?.push(e.target);
    adjacency.get(e.target)?.push(e.source);
  }

  let temp = 1.0;

  for (let iter = 0; iter < iterations; iter++) {
    // Repulsion (all pairs)
    for (let i = 0; i < result.length; i++) {
      for (let j = i + 1; j < result.length; j++) {
        const dx = result[i].x - result[j].x;
        const dy = result[i].y - result[j].y;
        const dz = result[i].z - result[j].z;
        const dist = Math.sqrt(dx * dx + dy * dy + dz * dz) + 0.01;
        const force = REPEL / (dist * dist);
        const fx = (dx / dist) * force;
        const fy = (dy / dist) * force;
        const fz = (dz / dist) * force;
        result[i].vx! += fx;
        result[i].vy! += fy;
        result[i].vz! += fz;
        result[j].vx! -= fx;
        result[j].vy! -= fy;
        result[j].vz! -= fz;
      }
    }

    // Attraction (edges)
    for (const e of edges) {
      const s = nodeMap.get(e.source);
      const t = nodeMap.get(e.target);
      if (!s || !t) continue;
      const dx = t.x - s.x;
      const dy = t.y - s.y;
      const dz = t.z - s.z;
      const dist = Math.sqrt(dx * dx + dy * dy + dz * dz) + 0.01;
      const k = STIFFNESS[e.edge_type] ?? 0.5;
      const force = k * dist;
      const fx = (dx / dist) * force;
      const fy = (dy / dist) * force;
      const fz = (dz / dist) * force;
      s.vx! += fx;
      s.vy! += fy;
      s.vz! += fz;
      t.vx! -= fx;
      t.vy! -= fy;
      t.vz! -= fz;
    }

    // Center + layer forces
    for (const n of result) {
      n.vx! -= n.x * CENTER;
      n.vy! -= n.y * CENTER;
      const layerZ = LAYER_Z[n.type] ?? 0;
      n.vz! += (layerZ - n.z) * 0.05;
    }

    // Apply velocities with temperature
    for (const n of result) {
      n.vx = clamp(finite(n.vx ?? 0), -MAX_VELOCITY, MAX_VELOCITY);
      n.vy = clamp(finite(n.vy ?? 0), -MAX_VELOCITY, MAX_VELOCITY);
      n.vz = clamp(finite(n.vz ?? 0), -MAX_VELOCITY, MAX_VELOCITY);
      n.x = clamp(n.x + n.vx * temp, -MAX_COORDINATE, MAX_COORDINATE);
      n.y = clamp(n.y + n.vy * temp, -MAX_COORDINATE, MAX_COORDINATE);
      n.z = clamp(n.z + n.vz * temp, -MAX_COORDINATE, MAX_COORDINATE);
      n.vx = 0;
      n.vy = 0;
      n.vz = 0;
    }

    temp *= 0.995;
  }

  return result.map((node) => ({
    ...node,
    x: clamp(finite(node.x), -MAX_COORDINATE, MAX_COORDINATE),
    y: clamp(finite(node.y), -MAX_COORDINATE, MAX_COORDINATE),
    z: clamp(finite(node.z), -MAX_COORDINATE, MAX_COORDINATE),
  }));
}
