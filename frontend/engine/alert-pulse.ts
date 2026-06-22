import * as THREE from 'three';
import type { ResponsibilityAlert } from '@/lib/types';

interface PulseInstance {
  ring: THREE.Mesh;
  birthTime: number;
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
    const geom = new THREE.RingGeometry(15, 18, 64);
    const color = severityColors[_alert.severity] ?? 0xffffff;
    const mat = new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.8, side: THREE.DoubleSide, blending: THREE.AdditiveBlending });
    const ring = new THREE.Mesh(geom, mat);
    const theta = Math.random() * Math.PI * 2;
    const phi = Math.random() * Math.PI;
    ring.position.set(250 * Math.sin(phi) * Math.cos(theta), 250 * Math.cos(phi), 250 * Math.sin(phi) * Math.sin(theta));
    ring.lookAt(0, 0, 0);
    this.pulses.push({ ring, birthTime: performance.now() / 1000 });
    this.group.add(ring);
  }

  update(_delta: number): void {
    const now = performance.now() / 1000;
    for (let i = this.pulses.length - 1; i >= 0; i--) {
      const pulse = this.pulses[i];
      const age = now - pulse.birthTime;
      if (age > PULSE_DURATION) {
        this.group.remove(pulse.ring);
        pulse.ring.geometry.dispose();
        (pulse.ring.material as THREE.Material).dispose();
        this.pulses.splice(i, 1);
      } else {
        const t = age / PULSE_DURATION;
        pulse.ring.scale.setScalar(1 + t * 3);
        (pulse.ring.material as THREE.MeshBasicMaterial).opacity = 0.8 * (1 - t);
      }
    }
  }

  clear(): void {
    for (const p of this.pulses) {
      this.group.remove(p.ring);
      p.ring.geometry.dispose();
      (p.ring.material as THREE.Material).dispose();
    }
    this.pulses = [];
  }
}
