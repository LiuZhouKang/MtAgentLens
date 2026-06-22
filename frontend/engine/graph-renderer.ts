import * as THREE from 'three';
import type { GraphData } from '@/lib/types';
import { forceLayout3D } from './graph-layout';

interface GraphNode3D {
  mesh: THREE.Mesh;
  data: { node_id: string; node_type: string };
  targetPosition: THREE.Vector3;
  birthTime: number;
  dissolving: boolean;
  dissolveStart: number;
  phase: number;
}

interface GraphEdge3D {
  line: THREE.Line;
  data: { edge_id: string; edge_type: string; source: string; target: string };
  birthTime: number;
  dissolving: boolean;
  dissolveStart: number;
  phase: number;
}

type VisualizationMode = 'hero' | 'focus';

const EDGE_COLORS: Record<string, number> = {
  IntentCausesAction: 0xffffff,
  AgentDelegatesTo: 0x818cf8,
  ContextFlowsFrom: 0xa78bfa,
  TaintPropagatesTo: 0xf59e0b,
  PolicyBoundaryCrossing: 0xf43f5e,
  CompetingExplanation: 0x68687a,
};

const BIRTH_DURATION = 0.5;
const DISSOLVE_DURATION = 0.5;
const GHOST_DURATION = 3.0;

function isFinitePosition(x: number, y: number, z: number): boolean {
  return Number.isFinite(x) && Number.isFinite(y) && Number.isFinite(z);
}

function hashToUnit(seed: string): number {
  let hash = 2166136261;
  for (let i = 0; i < seed.length; i += 1) {
    hash ^= seed.charCodeAt(i);
    hash = Math.imul(hash, 16777619);
  }
  return ((hash >>> 0) % 1000) / 1000;
}

export class GraphRenderer {
  scene: THREE.Scene;
  nodes: Map<string, GraphNode3D> = new Map();
  edges: Map<string, GraphEdge3D> = new Map();
  nodeGroup: THREE.Group;
  edgeGroup: THREE.Group;
  visualizationMode: VisualizationMode = 'hero';
  highlightedEdgeIds: Set<string> = new Set();

  constructor(scene: THREE.Scene) {
    this.scene = scene;
    this.nodeGroup = new THREE.Group();
    this.edgeGroup = new THREE.Group();
    this.scene.add(this.nodeGroup);
    this.scene.add(this.edgeGroup);
  }

  setVisualizationMode(mode: VisualizationMode): void {
    this.visualizationMode = mode;
  }

  private getNodePresentation(nodeType: string): { opacity: number; scale: number } {
    if (this.visualizationMode === 'focus') {
      if (nodeType === 'PolicyBoundary') {
        return { opacity: 0.96, scale: 1.02 };
      }
      if (nodeType === 'Resource') {
        return { opacity: 0.98, scale: 0.94 };
      }
      return { opacity: 1, scale: 1.2 };
    }

    if (nodeType === 'Agent') {
      return { opacity: 0.56, scale: 0.76 };
    }
    if (nodeType === 'PolicyBoundary') {
      return { opacity: 0.3, scale: 0.72 };
    }
    return { opacity: 0.24, scale: 0.62 };
  }

  private getEdgeOpacity(edgeType: string, highlighted: boolean): number {
    if (highlighted) {
      return this.visualizationMode === 'focus' ? 1 : 0.86;
    }

    if (this.visualizationMode === 'focus') {
      return edgeType === 'PolicyBoundaryCrossing' || edgeType === 'TaintPropagatesTo'
        ? 0.78
        : 0.6;
    }

    return edgeType === 'PolicyBoundaryCrossing' || edgeType === 'TaintPropagatesTo'
      ? 0.36
      : 0.2;
  }

  private createNodeGeometry(nodeType: string): THREE.BufferGeometry {
    if (nodeType === 'Agent') {
      return new THREE.IcosahedronGeometry(11, 1);
    }
    if (nodeType === 'PolicyBoundary') {
      return new THREE.TorusGeometry(12, 2.8, 12, 32);
    }
    return new THREE.OctahedronGeometry(6.4, 0);
  }

  private createNodeMaterial(nodeType: string): THREE.MeshPhongMaterial {
    const color = nodeType === 'Agent' ? 0x8bc5ff
      : nodeType === 'PolicyBoundary' ? 0xfb7185 : 0x7dd3fc;
    return new THREE.MeshPhongMaterial({
      color,
      emissive: color,
      emissiveIntensity: nodeType === 'PolicyBoundary' ? 1.4 : 1.12,
      transparent: true,
      opacity: 0,
      shininess: nodeType === 'Agent' ? 96 : 78,
      specular: new THREE.Color(nodeType === 'PolicyBoundary' ? 0xffc5d4 : 0xd9f4ff),
      blending: THREE.AdditiveBlending,
      depthWrite: false,
    });
  }

  private getSemanticPosition(nodeId: string, nodeType: string, x: number, y: number, z: number): THREE.Vector3 {
    const point = new THREE.Vector3(x, y, z);
    const magnitude = point.length();
    const unit = magnitude > 0.001 ? point.clone().normalize() : new THREE.Vector3(1, 0, 0);
    const phase = hashToUnit(nodeId);
    const angle = phase * Math.PI * 2;

    if (nodeType === 'Agent') {
      const orbit = new THREE.Vector3(
        Math.cos(angle) * (132 + phase * 28),
        (phase - 0.5) * 64,
        Math.sin(angle) * (116 + phase * 24)
      );
      point.multiplyScalar(0.28);
      point.y *= 0.58;
      point.add(orbit);
      return point;
    }

    if (nodeType === 'Resource') {
      const radial = Math.max(magnitude * 0.96, 234 + phase * 72);
      const orbit = new THREE.Vector3(
        Math.cos(angle + 0.38) * radial,
        Math.sin(angle * 1.4) * 48,
        Math.sin(angle + 0.38) * radial
      );
      point.copy(unit.multiplyScalar(28)).add(orbit);
      point.y *= 0.74;
      return point;
    }

    const boundaryRadial = Math.max(magnitude * 1.1, 360 + phase * 56);
    const orbit = new THREE.Vector3(
      Math.cos(angle - 0.28) * boundaryRadial,
      phase > 0.5 ? 104 : -104,
      Math.sin(angle - 0.28) * boundaryRadial
    );
    point.copy(unit.multiplyScalar(36)).add(orbit);
    point.y *= 0.52;
    return point;
  }

  private updateEdgeGeometry(edge: GraphEdge3D, source: THREE.Vector3, target: THREE.Vector3): void {
    const mid = source.clone().lerp(target, 0.5);
    let bend = source.clone().cross(target);
    if (bend.lengthSq() < 0.001) {
      bend = new THREE.Vector3(0, 1, 0).cross(target.clone().sub(source));
    }
    if (bend.lengthSq() < 0.001) {
      bend = new THREE.Vector3(0, 1, 0);
    }
    bend.normalize();
    const distance = source.distanceTo(target);
    const control = mid
      .clone()
      .add(bend.multiplyScalar(Math.max(distance * 0.18, 18)))
      .add(mid.clone().normalize().multiplyScalar(Math.max(distance * 0.08, 10)));
    const curve = new THREE.QuadraticBezierCurve3(source.clone(), control, target.clone());
    const points = curve.getPoints(26);
    edge.line.geometry.dispose();
    edge.line.geometry = new THREE.BufferGeometry().setFromPoints(points);
  }

  updateGraph(graph: GraphData): void {
    const newNodeIds = new Set(graph.nodes.map((n) => n.node_id));
    const newEdgeIds = new Set(graph.edges.map((e) => e.edge_id));
    const now = performance.now() / 1000;

    // Mark removed nodes for dissolution
    for (const [id, node] of this.nodes) {
      if (!newNodeIds.has(id) && !node.dissolving) {
        node.dissolving = true;
        node.dissolveStart = now;
      }
    }
    for (const [id, edge] of this.edges) {
      if (!newEdgeIds.has(id) && !edge.dissolving) {
        edge.dissolving = true;
        edge.dissolveStart = now;
      }
    }

    // Layout
    const layoutInput = graph.nodes.map((n) => {
      const existing = this.nodes.get(n.node_id);
      return {
        id: n.node_id,
        type: n.node_type,
        x: existing?.mesh.position.x ?? (Math.random() - 0.5) * 50,
        y: existing?.mesh.position.y ?? (Math.random() - 0.5) * 50,
        z: existing?.mesh.position.z ?? (Math.random() - 0.5) * 50,
      };
    });

    const layoutEdges = graph.edges.map((e) => ({
      source: e.source, target: e.target, edge_type: e.edge_type,
    }));

    const laidOut = forceLayout3D(layoutInput, layoutEdges, 100);

    // Create/update nodes
    for (const pos of laidOut) {
      if (!isFinitePosition(pos.x, pos.y, pos.z)) continue;
      const data = graph.nodes.find((n) => n.node_id === pos.id);
      if (!data) continue;
      const semanticPosition = this.getSemanticPosition(data.node_id, data.node_type, pos.x, pos.y, pos.z);

      let node3d = this.nodes.get(pos.id);
      if (!node3d) {
        const geom = this.createNodeGeometry(data.node_type);
        const mat = this.createNodeMaterial(data.node_type);
        const mesh = new THREE.Mesh(geom, mat);
        mesh.position.copy(semanticPosition.clone().multiplyScalar(0.72));

        node3d = {
          mesh,
          data: { node_id: data.node_id, node_type: data.node_type },
          targetPosition: semanticPosition.clone(),
          birthTime: now,
          dissolving: false,
          dissolveStart: 0,
          phase: hashToUnit(data.node_id) * Math.PI * 2,
        };
        this.nodes.set(pos.id, node3d);
        this.nodeGroup.add(mesh);
      } else {
        node3d.targetPosition.copy(semanticPosition);
      }
    }

    // Create/update edges
    for (const e of graph.edges) {
      let edge3d = this.edges.get(e.edge_id);
      const sourceNode = this.nodes.get(e.source);
      const targetNode = this.nodes.get(e.target);
      if (!sourceNode || !targetNode) continue;
      if (!isFinitePosition(
        sourceNode.mesh.position.x,
        sourceNode.mesh.position.y,
        sourceNode.mesh.position.z,
      ) || !isFinitePosition(
        targetNode.mesh.position.x,
        targetNode.mesh.position.y,
        targetNode.mesh.position.z,
      )) {
        continue;
      }

      if (!edge3d) {
        const geom = new THREE.BufferGeometry().setFromPoints([
          sourceNode.mesh.position.clone(),
          targetNode.mesh.position.clone(),
        ]);
        const color = EDGE_COLORS[e.edge_type] ?? 0xffffff;
        const mat = new THREE.LineBasicMaterial({
          color,
          transparent: true,
          opacity: 0,
          blending: THREE.AdditiveBlending,
          depthWrite: false,
        });
        const line = new THREE.Line(geom, mat);

        edge3d = {
          line,
          data: { edge_id: e.edge_id, edge_type: e.edge_type, source: e.source, target: e.target },
          birthTime: now,
          dissolving: false,
          dissolveStart: 0,
          phase: hashToUnit(e.edge_id) * Math.PI * 2,
        };
        this.edges.set(e.edge_id, edge3d);
        this.edgeGroup.add(line);
      }
      this.updateEdgeGeometry(edge3d, sourceNode.mesh.position, targetNode.mesh.position);
    }
  }

  update(delta: number): void {
    const now = performance.now() / 1000;

    for (const [, node] of this.nodes) {
      const presentation = this.getNodePresentation(node.data.node_type);
      node.mesh.position.lerp(node.targetPosition, Math.min(0.12 + delta * 3.4, 0.24));
      node.mesh.rotation.y += delta * (node.data.node_type === 'PolicyBoundary' ? 0.44 : 0.18);
      node.mesh.rotation.x += delta * (node.data.node_type === 'Resource' ? 0.12 : 0.05);
      const pulse = 1 + Math.sin(now * (node.data.node_type === 'Agent' ? 2.4 : 1.8) + node.phase) * 0.08;
      if (node.dissolving) {
        const elapsed = now - node.dissolveStart;
        if (elapsed < DISSOLVE_DURATION) {
          const t = elapsed / DISSOLVE_DURATION;
          (node.mesh.material as THREE.MeshPhongMaterial).opacity = presentation.opacity * (1 - t);
          node.mesh.scale.setScalar(Math.max(presentation.scale * (1 - t), 0.01));
        } else if (elapsed < DISSOLVE_DURATION + GHOST_DURATION) {
          (node.mesh.material as THREE.MeshPhongMaterial).opacity = 0.05;
          node.mesh.scale.setScalar(0);
        } else {
          this.nodeGroup.remove(node.mesh);
          this.nodes.delete(node.data.node_id);
        }
      } else {
        const age = now - node.birthTime;
        if (age < BIRTH_DURATION) {
          const t = age / BIRTH_DURATION;
          const overshoot = presentation.scale * 1.12;
          const scale = t < 0.6
            ? overshoot * (t / 0.6)
            : overshoot - (overshoot - presentation.scale) * ((t - 0.6) / 0.4);
          (node.mesh.material as THREE.MeshPhongMaterial).opacity = Math.min(t * 2, 1) * presentation.opacity;
          node.mesh.scale.setScalar(Math.max(scale * pulse, 0.01));
        } else {
          (node.mesh.material as THREE.MeshPhongMaterial).opacity = presentation.opacity;
          node.mesh.scale.setScalar(presentation.scale * pulse);
        }
      }
    }

    for (const [id, edge] of this.edges) {
      const targetOpacity = this.getEdgeOpacity(edge.data.edge_type, this.highlightedEdgeIds.has(id));
      const sourceNode = this.nodes.get(edge.data.source);
      const targetNode = this.nodes.get(edge.data.target);
      if (sourceNode && targetNode) {
        this.updateEdgeGeometry(edge, sourceNode.mesh.position, targetNode.mesh.position);
      }
      if (edge.dissolving) {
        const elapsed = now - edge.dissolveStart;
        if (elapsed < DISSOLVE_DURATION) {
          (edge.line.material as THREE.LineBasicMaterial).opacity = targetOpacity * (1 - elapsed / DISSOLVE_DURATION);
        } else {
          this.edgeGroup.remove(edge.line);
          this.edges.delete(edge.data.edge_id);
        }
      } else {
        const age = now - edge.birthTime;
        const shimmer = this.highlightedEdgeIds.has(id)
          ? 0.08 + Math.sin(now * 4.6 + edge.phase) * 0.1
          : 0.02 + Math.sin(now * 1.4 + edge.phase) * 0.02;
        (edge.line.material as THREE.LineBasicMaterial).opacity = age < BIRTH_DURATION
          ? (age / BIRTH_DURATION) * targetOpacity
          : Math.max(targetOpacity + shimmer, 0.09);
      }
    }
  }

  highlightPath(edgeIds: string[]): void {
    this.highlightedEdgeIds = new Set(edgeIds);
    for (const [id, edge] of this.edges) {
      (edge.line.material as THREE.LineBasicMaterial).color.setHex(
        edgeIds.includes(id)
          ? edge.data.edge_type === 'PolicyBoundaryCrossing' || edge.data.edge_type === 'TaintPropagatesTo'
            ? 0xff6b87
            : 0x9ce9ff
          : EDGE_COLORS[edge.data.edge_type] ?? 0xffffff
      );
    }
  }

  clearHighlights(): void {
    this.highlightedEdgeIds.clear();
    for (const [, edge] of this.edges) {
      (edge.line.material as THREE.LineBasicMaterial).color.setHex(
        EDGE_COLORS[edge.data.edge_type] ?? 0xffffff
      );
    }
  }

  dispose(): void {
    this.nodeGroup.traverse((child) => {
      if (child instanceof THREE.Mesh) {
        child.geometry.dispose();
        (child.material as THREE.Material).dispose();
      }
    });
    this.edgeGroup.traverse((child) => {
      if (child instanceof THREE.Line) {
        child.geometry.dispose();
        (child.material as THREE.Material).dispose();
      }
    });
    this.nodeGroup.clear();
    this.edgeGroup.clear();
    this.nodes.clear();
    this.edges.clear();
  }
}
