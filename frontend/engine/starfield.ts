import * as THREE from 'three';

interface AnimatedLayer {
  object: THREE.Object3D;
  update: (elapsed: number, delta: number) => void;
}

export interface StarfieldController {
  root: THREE.Group;
  particleCount: number;
  animate: (delta: number) => void;
  dispose: () => void;
}

const CLUSTER_CENTERS = [
  new THREE.Vector3(-196, 82, -140),
  new THREE.Vector3(-64, -126, -78),
  new THREE.Vector3(154, 44, -116),
  new THREE.Vector3(192, -92, 34),
  new THREE.Vector3(58, 126, 118),
  new THREE.Vector3(-172, -18, 124),
];

const STAR_PALETTE = [
  new THREE.Color('#e9f6ff'),
  new THREE.Color('#8ddfff'),
  new THREE.Color('#8f92ff'),
  new THREE.Color('#f9d68d'),
];

const NEBULA_PALETTE = ['#4dc7ff', '#846dff', '#ffb65f'];

function randomBetween(min: number, max: number): number {
  return min + Math.random() * (max - min);
}

function pickColor(palette: THREE.Color[]): THREE.Color {
  return palette[Math.floor(Math.random() * palette.length)].clone();
}

function sphericalPoint(radiusMin: number, radiusMax: number, squash = 1): THREE.Vector3 {
  const theta = Math.random() * Math.PI * 2;
  const phi = Math.acos(2 * Math.random() - 1);
  const radius = randomBetween(radiusMin, radiusMax);
  return new THREE.Vector3(
    radius * Math.sin(phi) * Math.cos(theta),
    radius * Math.sin(phi) * Math.sin(theta) * squash,
    radius * Math.cos(phi)
  );
}

function discPoint(radiusMax: number, thickness: number): THREE.Vector3 {
  const radius = Math.pow(Math.random(), 0.62) * radiusMax;
  const angle = Math.random() * Math.PI * 2;
  return new THREE.Vector3(
    Math.cos(angle) * radius,
    randomBetween(-thickness, thickness),
    Math.sin(angle) * radius
  );
}

function createGlowTexture(inner: string, outer: string): THREE.CanvasTexture {
  const canvas = document.createElement('canvas');
  canvas.width = 128;
  canvas.height = 128;
  const ctx = canvas.getContext('2d');
  if (!ctx) {
    const fallback = new THREE.CanvasTexture(canvas);
    fallback.needsUpdate = true;
    return fallback;
  }

  const gradient = ctx.createRadialGradient(64, 64, 0, 64, 64, 64);
  gradient.addColorStop(0, inner);
  gradient.addColorStop(0.35, outer);
  gradient.addColorStop(1, 'rgba(0,0,0,0)');
  ctx.fillStyle = gradient;
  ctx.fillRect(0, 0, 128, 128);

  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  return texture;
}

function createStarLayer(options: {
  count: number;
  pointFactory: () => THREE.Vector3;
  size: number;
  opacity: number;
  texture: THREE.Texture;
  depthTest?: boolean;
  sizeAttenuation?: boolean;
}): THREE.Points {
  const positions = new Float32Array(options.count * 3);
  const colors = new Float32Array(options.count * 3);

  for (let i = 0; i < options.count; i += 1) {
    const point = options.pointFactory();
    const color = pickColor(STAR_PALETTE);
    positions[i * 3] = point.x;
    positions[i * 3 + 1] = point.y;
    positions[i * 3 + 2] = point.z;
    colors[i * 3] = color.r;
    colors[i * 3 + 1] = color.g;
    colors[i * 3 + 2] = color.b;
  }

  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
  geometry.setAttribute('color', new THREE.BufferAttribute(colors, 3));

  const material = new THREE.PointsMaterial({
    size: options.size,
    transparent: true,
    opacity: options.opacity,
    vertexColors: true,
    blending: THREE.AdditiveBlending,
    map: options.texture,
    alphaMap: options.texture,
    depthTest: options.depthTest ?? false,
    depthWrite: false,
    sizeAttenuation: options.sizeAttenuation ?? true,
  });

  return new THREE.Points(geometry, material);
}

function createOrbitRing(radiusX: number, radiusY: number, color: string, rotation: THREE.Euler): THREE.Line {
  const curve = new THREE.EllipseCurve(0, 0, radiusX, radiusY, 0, Math.PI * 2, false, 0);
  const points = curve.getPoints(220).map((point) => new THREE.Vector3(point.x, point.y, 0));
  const geometry = new THREE.BufferGeometry().setFromPoints(points);
  const material = new THREE.LineBasicMaterial({
    color,
    transparent: true,
    opacity: 0.18,
    blending: THREE.AdditiveBlending,
  });
  const ring = new THREE.LineLoop(geometry, material);
  ring.rotation.copy(rotation);
  return ring;
}

function createFractureLine(
  start: THREE.Vector3,
  control: THREE.Vector3,
  end: THREE.Vector3,
  color: string
): THREE.Line {
  const curve = new THREE.QuadraticBezierCurve3(start, control, end);
  const geometry = new THREE.BufferGeometry().setFromPoints(curve.getPoints(68));
  const material = new THREE.LineBasicMaterial({
    color,
    transparent: true,
    opacity: 0.26,
    blending: THREE.AdditiveBlending,
  });
  return new THREE.Line(geometry, material);
}

function disposeObject(root: THREE.Object3D): void {
  root.traverse((child) => {
    const geometry = (child as THREE.Mesh).geometry;
    if (geometry instanceof THREE.BufferGeometry) {
      geometry.dispose();
    }

    const material = (child as THREE.Mesh).material;
    const materials = Array.isArray(material) ? material : material ? [material] : [];
    for (const entry of materials) {
      const texture = (entry as THREE.Material & { map?: THREE.Texture; alphaMap?: THREE.Texture }).map;
      const alphaTexture = (entry as THREE.Material & { alphaMap?: THREE.Texture }).alphaMap;
      texture?.dispose();
      if (alphaTexture && alphaTexture !== texture) {
        alphaTexture.dispose();
      }
      entry.dispose();
    }
  });
}

export function createStarfield(scene: THREE.Scene, count = 1200): StarfieldController {
  const root = new THREE.Group();
  root.position.set(110, 20, -64);

  const starTexture = createGlowTexture('rgba(255,255,255,1)', 'rgba(158,216,255,0.65)');
  const nebulaTexture = createGlowTexture('rgba(103,178,255,0.86)', 'rgba(76,120,255,0.12)');
  const warmNebulaTexture = createGlowTexture('rgba(255,214,158,0.86)', 'rgba(255,128,60,0.12)');

  const animatedLayers: AnimatedLayer[] = [];
  const baseCount = Math.max(count, 120);

  const farStars = createStarLayer({
    count: baseCount * 10,
    pointFactory: () => sphericalPoint(1200, 2400, 0.72),
    size: 2.2,
    opacity: 0.2,
    texture: starTexture,
  });
  const midStars = createStarLayer({
    count: baseCount * 8,
    pointFactory: () => discPoint(980, 160),
    size: 3.2,
    opacity: 0.26,
    texture: starTexture,
  });
  const nearStars = createStarLayer({
    count: baseCount * 3,
    pointFactory: () => discPoint(460, 72),
    size: 5.2,
    opacity: 0.42,
    texture: starTexture,
  });
  const coreStars = createStarLayer({
    count: baseCount * 2,
    pointFactory: () => discPoint(210, 28),
    size: 7.8,
    opacity: 0.36,
    texture: nebulaTexture,
    depthTest: false,
  });

  root.add(farStars, midStars, nearStars, coreStars);

  animatedLayers.push(
    {
      object: farStars,
      update: (_elapsed, delta) => {
        farStars.rotation.y += delta * 0.005;
        farStars.rotation.x += delta * 0.0018;
      },
    },
    {
      object: midStars,
      update: (_elapsed, delta) => {
        midStars.rotation.y -= delta * 0.007;
        midStars.rotation.z += delta * 0.0015;
      },
    },
    {
      object: nearStars,
      update: (elapsed, delta) => {
        nearStars.rotation.y += delta * 0.012;
        nearStars.position.y = Math.sin(elapsed * 0.18) * 10;
      },
    }
    ,
    {
      object: coreStars,
      update: (elapsed, delta) => {
        coreStars.rotation.y -= delta * 0.02;
        const material = coreStars.material as THREE.PointsMaterial;
        material.opacity = 0.28 + Math.sin(elapsed * 0.9) * 0.08;
      },
    }
  );

  const orbitGroup = new THREE.Group();
  const orbitRings = [
    createOrbitRing(280, 138, '#7ad7ff', new THREE.Euler(Math.PI * 0.54, 0.12, Math.PI * 0.08)),
    createOrbitRing(338, 186, '#8c82ff', new THREE.Euler(Math.PI * 0.42, 0.08, -Math.PI * 0.1)),
    createOrbitRing(232, 110, '#f2b65f', new THREE.Euler(Math.PI * 0.6, -0.18, Math.PI * 0.18)),
  ];
  orbitRings.forEach((ring) => orbitGroup.add(ring));
  root.add(orbitGroup);
  animatedLayers.push({
    object: orbitGroup,
    update: (elapsed, delta) => {
      orbitGroup.rotation.y -= delta * 0.03;
      orbitRings.forEach((ring, index) => {
        const material = ring.material as THREE.LineBasicMaterial;
        material.opacity = 0.12 + Math.sin(elapsed * (0.8 + index * 0.18) + index) * 0.04;
      });
    },
  });

  const clusterGroup = new THREE.Group();
  CLUSTER_CENTERS.forEach((center, index) => {
    const localTexture = index % 2 === 0 ? nebulaTexture : warmNebulaTexture;
    const cluster = createStarLayer({
      count: Math.round(baseCount * 0.85),
      pointFactory: () => center.clone().add(sphericalPoint(18, 74, 0.66)),
      size: 6.6,
      opacity: 0.34,
      texture: localTexture,
      depthTest: false,
    });

    const halo = new THREE.Sprite(
      new THREE.SpriteMaterial({
        map: localTexture,
        color: NEBULA_PALETTE[index % NEBULA_PALETTE.length],
        transparent: true,
        opacity: 0.24,
        blending: THREE.AdditiveBlending,
        depthWrite: false,
      })
    );
    halo.position.copy(center);
    halo.scale.setScalar(index % 2 === 0 ? 188 : 144);

    const orbit = createOrbitRing(
      randomBetween(34, 54),
      randomBetween(18, 26),
      index % 3 === 0 ? '#5ed7ff' : index % 3 === 1 ? '#9884ff' : '#f4b565',
      new THREE.Euler(randomBetween(0.1, 1.2), randomBetween(-0.6, 0.6), randomBetween(-0.8, 0.8))
    );
    orbit.position.copy(center);

    clusterGroup.add(cluster, halo, orbit);
  });
  root.add(clusterGroup);
  animatedLayers.push({
    object: clusterGroup,
    update: (elapsed, delta) => {
      clusterGroup.rotation.y += delta * 0.014;
      clusterGroup.position.y = Math.sin(elapsed * 0.12) * 4;
    },
  });

  const nebulaGroup = new THREE.Group();
  for (let i = 0; i < 4; i += 1) {
    const sprite = new THREE.Sprite(
      new THREE.SpriteMaterial({
        map: i % 2 === 0 ? nebulaTexture : warmNebulaTexture,
        color: i % 2 === 0 ? '#66b6ff' : '#fdc978',
        transparent: true,
        opacity: i % 2 === 0 ? 0.11 : 0.08,
        blending: THREE.AdditiveBlending,
        depthWrite: false,
      })
    );
    sprite.position.copy(CLUSTER_CENTERS[(i + 1) % CLUSTER_CENTERS.length].clone().multiplyScalar(0.86));
    sprite.position.y += i % 2 === 0 ? 56 : -42;
    sprite.scale.set(320 + i * 44, 220 + i * 30, 1);
    nebulaGroup.add(sprite);
  }
  root.add(nebulaGroup);
  animatedLayers.push({
    object: nebulaGroup,
    update: (elapsed) => {
      nebulaGroup.children.forEach((child, index) => {
        const sprite = child as THREE.Sprite;
        sprite.material.opacity = 0.08 + Math.sin(elapsed * (0.44 + index * 0.08) + index) * 0.03;
        const scaleBase = 1 + Math.sin(elapsed * 0.24 + index * 0.9) * 0.035;
        sprite.scale.set((320 + index * 44) * scaleBase, (220 + index * 30) * scaleBase, 1);
      });
    },
  });

  const fractureGroup = new THREE.Group();
  const fractures = [
    createFractureLine(
      new THREE.Vector3(-210, -118, 122),
      new THREE.Vector3(-12, -14, 8),
      new THREE.Vector3(264, 136, -62),
      '#ff5b86'
    ),
    createFractureLine(
      new THREE.Vector3(-138, 152, 94),
      new THREE.Vector3(58, 24, -34),
      new THREE.Vector3(242, -168, -104),
      '#ff9b63'
    ),
  ];
  fractures.forEach((line) => fractureGroup.add(line));
  root.add(fractureGroup);
  animatedLayers.push({
    object: fractureGroup,
    update: (elapsed) => {
      fractureGroup.children.forEach((child, index) => {
        const material = (child as THREE.Line).material as THREE.LineBasicMaterial;
        material.opacity = 0.18 + Math.sin(elapsed * (1.4 + index * 0.18)) * 0.08;
      });
    },
  });

  const coreGlow = new THREE.Sprite(
    new THREE.SpriteMaterial({
      map: nebulaTexture,
      color: '#7cd7ff',
      transparent: true,
      opacity: 0.18,
      blending: THREE.AdditiveBlending,
      depthWrite: false,
    })
  );
  coreGlow.scale.set(280, 280, 1);
  root.add(coreGlow);
  animatedLayers.push({
    object: coreGlow,
    update: (elapsed) => {
      coreGlow.material.opacity = 0.12 + Math.sin(elapsed * 0.9) * 0.035;
      const scale = 1 + Math.sin(elapsed * 0.5) * 0.06;
      coreGlow.scale.set(280 * scale, 280 * scale, 1);
    },
  });


  scene.add(root);

  let elapsed = 0;
  return {
    root,
    particleCount: baseCount * 23 + Math.round(baseCount * 0.85) * CLUSTER_CENTERS.length,
    animate(delta: number) {
      elapsed += delta;
      root.rotation.y += delta * 0.01;
      root.rotation.z = Math.sin(elapsed * 0.06) * 0.05;
      animatedLayers.forEach((layer) => layer.update(elapsed, delta));
    },
    dispose() {
      scene.remove(root);
      disposeObject(root);
      root.clear();
    },
  };
}

export function animateStarfield(starfield: StarfieldController, delta: number): void {
  starfield.animate(delta);
}
