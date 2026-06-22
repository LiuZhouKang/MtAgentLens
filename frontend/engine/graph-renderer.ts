import * as THREE from 'three';
import type { GraphData } from '@/lib/types';
import { forceLayout3D } from './graph-layout';

interface GraphNode3D {
  mesh: THREE.Mesh;
  data: { node_id: string; node_type: string };
  birthTime: number;
  dissolving: boolean;
  dissolveStart: number;
}

interface GraphEdge3D {
  line: THREE.Line;
  data: { edge_id: string; edge_type: string };
  birthTime: number;
  dissolving: boolean;
  dissolveStart: number;
}

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

export class GraphRenderer {
  scene: THREE.Scene;
  nodes: Map<string, GraphNode3D> = new Map();
  edges: Map<string, GraphEdge3D> = new Map();
  nodeGroup: THREE.Group;
  edgeGroup: THREE.Group;

  constructor(scene: THREE.Scene) {
    this.scene = scene;
    this.nodeGroup = new THREE.Group();
    this.edgeGroup = new THREE.Group();
    this.scene.add(this.nodeGroup);
    this.scene.add(this.edgeGroup);
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
      const data = graph.nodes.find((n) => n.node_id === pos.id);
      if (!data) continue;

      let node3d = this.nodes.get(pos.id);
      if (!node3d) {
        const geom = data.node_type === 'Agent'
          ? new THREE.SphereGeometry(8, 16, 16)
          : data.node_type === 'PolicyBoundary'
            ? new THREE.CircleGeometry(12, 32)
            : new THREE.BoxGeometry(6, 6, 6);

        const color = data.node_type === 'Agent' ? 0x818cf8
          : data.node_type === 'PolicyBoundary' ? 0xf43f5e : 0x60a5fa;

        const mat = new THREE.MeshBasicMaterial({
          color, transparent: true, opacity: 0, blending: THREE.AdditiveBlending,
        });
        const mesh = new THREE.Mesh(geom, mat);
        mesh.position.set(pos.x, pos.y, pos.z);

        node3d = { mesh, data: { node_id: data.node_id, node_type: data.node_type }, birthTime: now, dissolving: false, dissolveStart: 0 };
        this.nodes.set(pos.id, node3d);
        this.nodeGroup.add(mesh);
      } else {
        node3d.mesh.position.lerp(new THREE.Vector3(pos.x, pos.y, pos.z), 0.3);
      }
    }

    // Create/update edges
    for (const e of graph.edges) {
      let edge3d = this.edges.get(e.edge_id);
      const sourceNode = this.nodes.get(e.source);
      const targetNode = this.nodes.get(e.target);
      if (!sourceNode || !targetNode) continue;

      if (!edge3d) {
        const geom = new THREE.BufferGeometry().setFromPoints([
          sourceNode.mesh.position, targetNode.mesh.position,
        ]);
        const color = EDGE_COLORS[e.edge_type] ?? 0xffffff;
        const mat = new THREE.LineBasicMaterial({ color, transparent: true, opacity: 0, blending: THREE.AdditiveBlending });
        const line = new THREE.Line(geom, mat);

        edge3d = { line, data: { edge_id: e.edge_id, edge_type: e.edge_type }, birthTime: now, dissolving: false, dissolveStart: 0 };
        this.edges.set(e.edge_id, edge3d);
        this.edgeGroup.add(line);
      } else {
        const positions = new Float32Array([
          sourceNode.mesh.position.x, sourceNode.mesh.position.y, sourceNode.mesh.position.z,
          targetNode.mesh.position.x, targetNode.mesh.position.y, targetNode.mesh.position.z,
        ]);
        edge3d.line.geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
      }
    }
  }

  update(_delta: number): void {
    const now = performance.now() / 1000;

    for (const [, node] of this.nodes) {
      if (node.dissolving) {
        const elapsed = now - node.dissolveStart;
        if (elapsed < DISSOLVE_DURATION) {
          const t = elapsed / DISSOLVE_DURATION;
          (node.mesh.material as THREE.MeshBasicMaterial).opacity = 1 - t;
          node.mesh.scale.setScalar(1 - t);
        } else if (elapsed < DISSOLVE_DURATION + GHOST_DURATION) {
          (node.mesh.material as THREE.MeshBasicMaterial).opacity = 0.1;
          node.mesh.scale.setScalar(0);
        } else {
          this.nodeGroup.remove(node.mesh);
          this.nodes.delete(node.data.node_id);
        }
      } else {
        const age = now - node.birthTime;
        if (age < BIRTH_DURATION) {
          const t = age / BIRTH_DURATION;
          const scale = t < 0.6 ? 1.2 * (t / 0.6) : 1.2 - 0.2 * ((t - 0.6) / 0.4);
          (node.mesh.material as THREE.MeshBasicMaterial).opacity = Math.min(t * 2, 1);
          node.mesh.scale.setScalar(Math.max(scale, 0.01));
        } else {
          (node.mesh.material as THREE.MeshBasicMaterial).opacity = 1;
          node.mesh.scale.setScalar(1);
        }
      }
    }

    for (const [, edge] of this.edges) {
      if (edge.dissolving) {
        const elapsed = now - edge.dissolveStart;
        if (elapsed < DISSOLVE_DURATION) {
          (edge.line.material as THREE.LineBasicMaterial).opacity = 1 - elapsed / DISSOLVE_DURATION;
        } else {
          this.edgeGroup.remove(edge.line);
          this.edges.delete(edge.data.edge_id);
        }
      } else {
        const age = now - edge.birthTime;
        (edge.line.material as THREE.LineBasicMaterial).opacity = age < BIRTH_DURATION ? age / BIRTH_DURATION : 1;
      }
    }
  }

  highlightPath(edgeIds: string[]): void {
    for (const [id, edge] of this.edges) {
      (edge.line.material as THREE.LineBasicMaterial).color.setHex(
        edgeIds.includes(id) ? 0xff4444 : EDGE_COLORS[edge.data.edge_type] ?? 0xffffff
      );
    }
  }

  clearHighlights(): void {
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
