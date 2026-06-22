import * as THREE from 'three';

export const GLOBE_RADIUS = 150;
export const GLOBE_OUTLINE_RADIUS = GLOBE_RADIUS * 1.018;

export function createGlobe(scene: THREE.Scene): THREE.Group {
  const globe = new THREE.Group();
  globe.rotation.z = THREE.MathUtils.degToRad(-18);

  const body = new THREE.Mesh(
    new THREE.SphereGeometry(GLOBE_RADIUS, 64, 64),
    new THREE.MeshBasicMaterial({
      color: 0x0a1a2d,
      transparent: true,
      opacity: 0.92,
      depthWrite: false,
    })
  );
  globe.add(body);

  const atmosphere = new THREE.Mesh(
    new THREE.SphereGeometry(GLOBE_RADIUS * 1.06, 40, 40),
    new THREE.MeshBasicMaterial({
      color: 0x67b8ff,
      transparent: true,
      opacity: 0.12,
      side: THREE.BackSide,
      depthWrite: false,
      blending: THREE.AdditiveBlending,
    })
  );
  globe.add(atmosphere);

  const halo = new THREE.Mesh(
    new THREE.SphereGeometry(GLOBE_RADIUS * 1.13, 32, 32),
    new THREE.MeshBasicMaterial({
      color: 0x90ddff,
      transparent: true,
      opacity: 0.045,
      side: THREE.BackSide,
      depthWrite: false,
      blending: THREE.AdditiveBlending,
    })
  );
  globe.add(halo);

  scene.add(globe);
  return globe;
}

export async function addGlobeOutlines(
  globe: THREE.Group,
  geoJsonPath: string
): Promise<void> {
  const resp = await fetch(geoJsonPath);
  const geoJson = await resp.json();

  const outlineGroup = new THREE.Group();
  const lineMaterial = new THREE.LineBasicMaterial({
    color: 0x6eb8ff,
    transparent: true,
    opacity: 0.08,
    depthWrite: false,
    blending: THREE.NormalBlending,
  });

  for (const feature of geoJson.features) {
    const coordinateSets =
      feature.geometry.type === 'Polygon'
        ? [feature.geometry.coordinates]
        : feature.geometry.type === 'MultiPolygon'
          ? feature.geometry.coordinates
          : [];

    for (const polygon of coordinateSets) {
      for (const ring of polygon) {
        const points = ring.map(([lon, lat]: [number, number]) =>
          latLonToVec3(lat, lon, GLOBE_OUTLINE_RADIUS)
        );
        const geom = new THREE.BufferGeometry().setFromPoints(points);
        const line = new THREE.Line(geom, lineMaterial);
        line.renderOrder = 2;
        outlineGroup.add(line);
      }
    }
  }

  globe.add(outlineGroup);
}

function latLonToVec3(lat: number, lon: number, radius: number): THREE.Vector3 {
  const phi = (90 - lat) * (Math.PI / 180);
  const theta = (lon + 180) * (Math.PI / 180);
  return new THREE.Vector3(
    -radius * Math.sin(phi) * Math.cos(theta),
    radius * Math.cos(phi),
    radius * Math.sin(phi) * Math.sin(theta)
  );
}

export function animateGlobe(globe: THREE.Group, delta: number): void {
  globe.rotation.y += delta * 0.018;
}
