import * as THREE from 'three';

interface SpiralPoint {
  mesh: THREE.Mesh;
}

export class TimelineRing {
  scene: THREE.Scene;
  points: SpiralPoint[] = [];
  group: THREE.Group;

  constructor(scene: THREE.Scene) {
    this.scene = scene;
    this.group = new THREE.Group();
    this.scene.add(this.group);
  }

  setEvents(events: { timestamp_ns: number; operation?: string }[]): void {
    this.clear();
    const startRadius = 180;
    const endRadius = 220;
    const turns = 4;

    for (let i = 0; i < events.length; i++) {
      const t = events.length > 1 ? i / (events.length - 1) : 0.5;
      const radius = startRadius + (endRadius - startRadius) * t;
      const angle = t * Math.PI * 2 * turns;
      const geom = new THREE.SphereGeometry(2, 8, 8);
      const mat = new THREE.MeshBasicMaterial({
        color: t < 0.3 ? 0xffffff : 0x68687a,
        transparent: true,
        opacity: t < 0.3 ? 0.9 : 0.3,
      });
      const mesh = new THREE.Mesh(geom, mat);
      mesh.position.set(radius * Math.cos(angle), (t - 0.5) * 40, radius * Math.sin(angle));
      this.points.push({ mesh });
      this.group.add(mesh);
    }
  }

  clear(): void {
    for (const p of this.points) {
      this.group.remove(p.mesh);
      p.mesh.geometry.dispose();
      (p.mesh.material as THREE.Material).dispose();
    }
    this.points = [];
  }

  update(_delta: number): void {
    this.group.rotation.y += 0.002;
  }
}
