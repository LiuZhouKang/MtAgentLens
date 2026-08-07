import * as THREE from 'three';

export const SPARKLE_COUNT = 22000;
export const SPHERE_RADIUS = 154;
export const INTRO_DURATION = 4.6;

interface GlobeSparkleCloud {
  positions: Float32Array;
  targets: Float32Array;
  colors: Float32Array;
  sizes: Float32Array;
  introOffsets: Float32Array;
  shimmerPhase: Float32Array;
}

export interface GlobeSparklesController {
  points: THREE.Points;
  update(delta: number): void;
  dispose(): void;
}

export function generateGlobeSparkleCloud(
  count = SPARKLE_COUNT,
  radius = SPHERE_RADIUS,
  random: () => number = Math.random,
): GlobeSparkleCloud {
  const positions = new Float32Array(count * 3);
  const targets = new Float32Array(count * 3);
  const colors = new Float32Array(count * 3);
  const sizes = new Float32Array(count);
  const introOffsets = new Float32Array(count);
  const shimmerPhase = new Float32Array(count);

  for (let i = 0; i < count; i++) {
    const theta = random() * Math.PI * 2;
    const phi = Math.acos(2 * random() - 1);
    const settledRadius = radius + (random() - 0.5) * 8;
    const targetX = settledRadius * Math.sin(phi) * Math.cos(theta);
    const targetY = settledRadius * Math.cos(phi);
    const targetZ = settledRadius * Math.sin(phi) * Math.sin(theta);

    targets[i * 3] = targetX;
    targets[i * 3 + 1] = targetY;
    targets[i * 3 + 2] = targetZ;

    // Start above and in front of the globe so the intro keeps the old vertical
    // particle-fall feeling instead of appearing as a flat dissolve.
    positions[i * 3] = targetX * (0.22 + random() * 0.24) + (random() - 0.5) * 96;
    positions[i * 3 + 1] = targetY + 280 + random() * 540;
    positions[i * 3 + 2] = targetZ + 240 + random() * 420;

    const brightness = 0.26 + random() * 0.3;
    const warmth = 0.92 + random() * 0.18;
    colors[i * 3] = 0.28 * brightness * warmth;
    colors[i * 3 + 1] = 0.56 * brightness;
    colors[i * 3 + 2] = 0.98 * brightness;

    sizes[i] = 0.8 + random() * 1.4;
    introOffsets[i] = random() * 1.35;
    shimmerPhase[i] = random() * Math.PI * 2;
  }

  return { positions, targets, colors, sizes, introOffsets, shimmerPhase };
}

function easeOutCubic(value: number): number {
  return 1 - (1 - value) ** 3;
}

export function createGlobeSparkles(
  scene: THREE.Scene,
  material: THREE.ShaderMaterial,
): GlobeSparklesController {
  const cloud = generateGlobeSparkleCloud();
  const runtimePositions = new Float32Array(cloud.positions);

  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.BufferAttribute(runtimePositions, 3));
  geometry.setAttribute('customColor', new THREE.BufferAttribute(cloud.colors, 3));
  geometry.setAttribute('size', new THREE.BufferAttribute(cloud.sizes, 1));

  const sparkles = new THREE.Points(geometry, material);
  sparkles.frustumCulled = false;
  sparkles.renderOrder = 1;
  scene.add(sparkles);

  let elapsed = 0;
  const positionAttribute = geometry.getAttribute('position') as THREE.BufferAttribute;

  return {
    points: sparkles,
    update(delta: number) {
      elapsed += delta;
      const introSettled = elapsed >= INTRO_DURATION + 1.2;

      for (let i = 0; i < SPARKLE_COUNT; i++) {
        const offset = cloud.introOffsets[i];
        const introT = THREE.MathUtils.clamp(
          (elapsed - offset) / (INTRO_DURATION - offset * 0.35),
          0,
          1,
        );
        const settleT = easeOutCubic(introT);
        const startIndex = i * 3;

        runtimePositions[startIndex] = THREE.MathUtils.lerp(
          cloud.positions[startIndex],
          cloud.targets[startIndex],
          settleT,
        );
        runtimePositions[startIndex + 1] = THREE.MathUtils.lerp(
          cloud.positions[startIndex + 1],
          cloud.targets[startIndex + 1],
          settleT,
        );
        runtimePositions[startIndex + 2] = THREE.MathUtils.lerp(
          cloud.positions[startIndex + 2],
          cloud.targets[startIndex + 2],
          settleT,
        );

        if (introSettled) {
          const drift = Math.sin(elapsed * 0.36 + cloud.shimmerPhase[i]) * 1.4;
          runtimePositions[startIndex] += Math.cos(cloud.shimmerPhase[i]) * drift * 0.14;
          runtimePositions[startIndex + 1] += drift * 0.2;
          runtimePositions[startIndex + 2] += Math.sin(cloud.shimmerPhase[i] * 0.7) * drift * 0.16;
        }
      }

      positionAttribute.needsUpdate = true;
      if (material.uniforms.opacity) {
        material.uniforms.opacity.value = introSettled
          ? 0.56
          : 0.44 + Math.min(elapsed / INTRO_DURATION, 1) * 0.08;
      }
    },
    dispose() {
      scene.remove(sparkles);
      geometry.dispose();
    },
  };
}
