import * as THREE from 'three';

const SPARKLE_COUNT = 100000;
// Place sparkles slightly outside the dark inner sphere (radius 148)
// so they're visible in front of it.
const SPHERE_RADIUS = 155;

export function createGlobeSparkles(
  scene: THREE.Scene,
  material: THREE.ShaderMaterial
): THREE.Points {
  const positions = new Float32Array(SPARKLE_COUNT * 3);
  const colors = new Float32Array(SPARKLE_COUNT * 3);
  const sizes = new Float32Array(SPARKLE_COUNT);

  for (let i = 0; i < SPARKLE_COUNT; i++) {
    const theta = Math.random() * Math.PI * 2;
    const phi = Math.acos(2 * Math.random() - 1);
    positions[i * 3] = SPHERE_RADIUS * Math.sin(phi) * Math.cos(theta);
    positions[i * 3 + 1] = SPHERE_RADIUS * Math.sin(phi) * Math.sin(theta);
    positions[i * 3 + 2] = SPHERE_RADIUS * Math.cos(phi);

    // Blue-white tinted sparkles
    const brightness = 0.5 + Math.random() * 0.4;
    colors[i * 3] = 0.5 * brightness;
    colors[i * 3 + 1] = 0.6 * brightness;
    colors[i * 3 + 2] = 1.0 * brightness;

    sizes[i] = 2.0 + Math.random() * 3.0;
  }

  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
  geometry.setAttribute('customColor', new THREE.BufferAttribute(colors, 3));
  geometry.setAttribute('size', new THREE.BufferAttribute(sizes, 1));

  const sparkles = new THREE.Points(geometry, material);
  sparkles.frustumCulled = false;
  sparkles.renderOrder = 1;
  scene.add(sparkles);
  return sparkles;
}
