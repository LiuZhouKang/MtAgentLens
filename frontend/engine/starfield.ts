import * as THREE from 'three';

export function createStarfield(scene: THREE.Scene, count = 1200): THREE.Points {
  const positions = new Float32Array(count * 3);
  const radius = 2000;

  for (let i = 0; i < count; i++) {
    const theta = Math.random() * Math.PI * 2;
    const phi = Math.acos(2 * Math.random() - 1);
    positions[i * 3] = radius * Math.sin(phi) * Math.cos(theta);
    positions[i * 3 + 1] = radius * Math.sin(phi) * Math.sin(theta);
    positions[i * 3 + 2] = radius * Math.cos(phi);
  }

  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));

  const material = new THREE.PointsMaterial({
    color: 0xffffff,
    size: 4.0,
    transparent: true,
    opacity: 0.5,
    blending: THREE.AdditiveBlending,
    depthTest: false,
    depthWrite: false,
  });

  const stars = new THREE.Points(geometry, material);
  scene.add(stars);
  return stars;
}

export function animateStarfield(stars: THREE.Points, delta: number): void {
  stars.rotation.y += delta * 0.005;
  stars.rotation.x += delta * 0.002;
}
