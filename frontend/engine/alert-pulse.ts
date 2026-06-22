import * as THREE from 'three';
import type { ResponsibilityAlert } from '@/lib/types';

interface PulseInstance {
  group: THREE.Group;
  beacon: THREE.Mesh;
  shell: THREE.Mesh;
  shards: THREE.LineSegments;
  birthTime: number;
  normal: THREE.Vector3;
}

const PULSE_DURATION = 3.0;
const severityColors: Record<string, number> = { Critical: 0xf43f5e, Warning: 0xf59e0b, Info: 0x60a5fa };

export class AlertPulseOverlay {
  scene: THREE.Scene;
  pulses: PulseInstance[] = [];
  group: THREE.Group;

  constructor(scene: THREE.Scene) {
    this.scene = scene;
    this.group = new THREE.Group();
    this.scene.add(this.group);
  }

  showPulse(_alert: ResponsibilityAlert): void {
    const color = severityColors[_alert.severity] ?? 0xffffff;
    const theta = Math.random() * Math.PI * 2;
    const phi = Math.random() * Math.PI;
    const anchor = new THREE.Vector3(
      164 * Math.sin(phi) * Math.cos(theta),
      164 * Math.cos(phi),
      164 * Math.sin(phi) * Math.sin(theta)
    );
    const normal = anchor.clone().normalize();
    const tangent = new THREE.Vector3(0, 1, 0).cross(normal);
    if (tangent.lengthSq() < 0.001) {
      tangent.set(1, 0, 0);
    }
    tangent.normalize();
    const bitangent = normal.clone().cross(tangent).normalize();

    const beacon = new THREE.Mesh(
      new THREE.SphereGeometry(3.2, 16, 16),
      new THREE.MeshBasicMaterial({
        color,
        transparent: true,
        opacity: 0.92,
        blending: THREE.AdditiveBlending,
        depthWrite: false,
      })
    );

    const shell = new THREE.Mesh(
      new THREE.SphereGeometry(7.6, 20, 20),
      new THREE.MeshBasicMaterial({
        color,
        transparent: true,
        opacity: 0.24,
        blending: THREE.AdditiveBlending,
        side: THREE.BackSide,
        depthWrite: false,
      })
    );

    const segmentCount = 12;
    const shardPositions = new Float32Array(segmentCount * 2 * 3);
    for (let i = 0; i < segmentCount; i++) {
      const angle = (i / segmentCount) * Math.PI * 2;
      const spread = 5 + (i % 3) * 2.4;
      const length = 9 + (i % 4) * 3.2;
      const radial = tangent.clone().multiplyScalar(Math.cos(angle) * spread)
        .add(bitangent.clone().multiplyScalar(Math.sin(angle) * spread));
      const elevated = radial.clone().add(normal.clone().multiplyScalar(1.6));
      const tip = radial.clone().multiplyScalar(1.55).add(normal.clone().multiplyScalar(length));
      shardPositions.set([elevated.x, elevated.y, elevated.z, tip.x, tip.y, tip.z], i * 6);
    }

    const shards = new THREE.LineSegments(
      new THREE.BufferGeometry(),
      new THREE.LineBasicMaterial({
        color,
        transparent: true,
        opacity: 0.46,
        blending: THREE.AdditiveBlending,
        depthWrite: false,
      })
    );
    shards.geometry.setAttribute('position', new THREE.BufferAttribute(shardPositions, 3));

    const pulseGroup = new THREE.Group();
    pulseGroup.position.copy(anchor);
    pulseGroup.lookAt(anchor.clone().add(normal));
    pulseGroup.add(shell);
    pulseGroup.add(shards);
    pulseGroup.add(beacon);

    this.pulses.push({
      group: pulseGroup,
      beacon,
      shell,
      shards,
      birthTime: performance.now() / 1000,
      normal,
    });
    this.group.add(pulseGroup);
  }

  update(_delta: number): void {
    const now = performance.now() / 1000;
    for (let i = this.pulses.length - 1; i >= 0; i--) {
      const pulse = this.pulses[i];
      const age = now - pulse.birthTime;
      if (age > PULSE_DURATION) {
        this.group.remove(pulse.group);
        pulse.beacon.geometry.dispose();
        pulse.shell.geometry.dispose();
        pulse.shards.geometry.dispose();
        (pulse.beacon.material as THREE.Material).dispose();
        (pulse.shell.material as THREE.Material).dispose();
        (pulse.shards.material as THREE.Material).dispose();
        this.pulses.splice(i, 1);
      } else {
        const t = age / PULSE_DURATION;
        pulse.group.scale.setScalar(1 + t * 1.9);
        pulse.group.position.addScaledVector(pulse.normal, _delta * 1.2);
        (pulse.beacon.material as THREE.MeshBasicMaterial).opacity = 0.92 * (1 - t * 0.72);
        (pulse.shell.material as THREE.MeshBasicMaterial).opacity = 0.24 * (1 - t);
        (pulse.shards.material as THREE.LineBasicMaterial).opacity = 0.46 * (1 - t);
      }
    }
  }

  clear(): void {
    for (const p of this.pulses) {
      this.group.remove(p.group);
      p.beacon.geometry.dispose();
      p.shell.geometry.dispose();
      p.shards.geometry.dispose();
      (p.beacon.material as THREE.Material).dispose();
      (p.shell.material as THREE.Material).dispose();
      (p.shards.material as THREE.Material).dispose();
    }
    this.pulses = [];
  }
}
