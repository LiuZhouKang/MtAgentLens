import * as THREE from 'three';

export class CameraController {
  camera: THREE.PerspectiveCamera;
  spherical: { theta: number; phi: number; radius: number };
  sphericalTarget: { theta: number; phi: number; radius: number };
  private isFlying: boolean = false;
  private flyTarget: THREE.Vector3 | null = null;
  private flyLookAt: THREE.Vector3 = new THREE.Vector3(0, 0, 0);
  private flyStartPos: THREE.Vector3 = new THREE.Vector3();
  private flyStartLookAt: THREE.Vector3 = new THREE.Vector3();
  private flyElapsed: number = 0;
  private flyDuration: number = 0;

  constructor(camera: THREE.PerspectiveCamera) {
    this.camera = camera;
    const pos = camera.position;
    this.spherical = {
      theta: Math.atan2(pos.x, pos.z),
      phi: Math.acos(pos.y / pos.length()),
      radius: pos.length(),
    };
    this.sphericalTarget = { ...this.spherical };
  }

  flyTo(target: THREE.Vector3, lookAt: THREE.Vector3, duration: number = 1.5): void {
    this.flyStartPos.copy(this.camera.position);
    this.flyStartLookAt.copy(this.flyLookAt);
    this.flyTarget = target.clone();
    this.flyLookAt = lookAt.clone();
    this.flyDuration = duration;
    this.flyElapsed = 0;
    this.isFlying = true;
  }

  flyToLevel(level: 0 | 1 | 2): void {
    const targets: Record<number, { pos: THREE.Vector3; lookAt: THREE.Vector3 }> = {
      0: { pos: new THREE.Vector3(0, 0, 450), lookAt: new THREE.Vector3(0, 0, 0) },
      1: { pos: new THREE.Vector3(0, 0, 250), lookAt: new THREE.Vector3(0, 0, 0) },
      2: { pos: new THREE.Vector3(0, 100, 150), lookAt: new THREE.Vector3(0, 0, 0) },
    };
    const t = targets[level];
    this.flyTo(t.pos, t.lookAt, level === 0 ? 2.0 : 1.5);
  }

  reset(duration: number = 2.0): void {
    this.flyToLevel(0);
    this.flyDuration = duration;
  }

  orbit(deltaX: number, deltaY: number): void {
    if (this.isFlying) return;
    this.sphericalTarget.theta -= deltaX * 0.005;
    this.sphericalTarget.phi -= deltaY * 0.005;
    this.sphericalTarget.phi = Math.max(1.7, Math.min(Math.PI * 1.5, this.sphericalTarget.phi));
  }

  zoom(delta: number): void {
    if (this.isFlying) return;
    this.sphericalTarget.radius += delta * 0.5;
    this.sphericalTarget.radius = Math.max(175, Math.min(600, this.sphericalTarget.radius));
  }

  update(delta: number): void {
    if (this.isFlying && this.flyTarget) {
      this.flyElapsed += delta;
      const t = Math.min(this.flyElapsed / this.flyDuration, 1.0);
      const ease = t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2;
      this.camera.position.lerpVectors(this.flyStartPos, this.flyTarget, ease);
      const currentLookAt = new THREE.Vector3().lerpVectors(
        this.flyStartLookAt,
        this.flyLookAt,
        ease,
      );
      this.camera.lookAt(currentLookAt);
      if (t >= 1.0) {
        this.isFlying = false;
        this.flyTarget = null;
      }
      return;
    }

    const ease = 0.1;
    this.spherical.theta += (this.sphericalTarget.theta - this.spherical.theta) * ease;
    this.spherical.phi += (this.sphericalTarget.phi - this.spherical.phi) * ease;
    this.spherical.radius += (this.sphericalTarget.radius - this.spherical.radius) * ease;

    const { theta, phi, radius } = this.spherical;
    this.camera.position.set(
      radius * Math.sin(phi) * Math.sin(theta),
      radius * Math.cos(phi),
      radius * Math.sin(phi) * Math.cos(theta),
    );
    this.camera.lookAt(0, 0, 0);
  }
}
