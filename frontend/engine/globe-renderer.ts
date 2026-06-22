import * as THREE from 'three';

const GLOBE_RADIUS = 150;

export function createGlobe(scene: THREE.Scene): THREE.Group {
  const globe = new THREE.Group();

  // Dark inner sphere — pure black, depthWrite: false so transparent particles
  // behind it (sparkles, graph nodes) can render through.
  const innerSphere = new THREE.Mesh(
    new THREE.SphereGeometry(GLOBE_RADIUS - 2, 48, 48),
    new THREE.MeshBasicMaterial({ color: 0x000000, depthWrite: false })
  );
  globe.add(innerSphere);

  // Add a subtle wireframe icosahedron to give the globe structure
  // (matching the original Harvard Globe's commented-out wireframe)
  const wireframe = new THREE.Mesh(
    new THREE.IcosahedronGeometry(GLOBE_RADIUS - 1, 6),
    new THREE.MeshBasicMaterial({ color: 0x080808, wireframe: true })
  );
  globe.add(wireframe);

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
    color: 0x3a3a5a,
    transparent: true,
    opacity: 0.4,
    blending: THREE.AdditiveBlending,
  });

  for (const feature of geoJson.features) {
    if (feature.geometry.type === 'Polygon') {
      for (const ring of feature.geometry.coordinates) {
        const points = ring.map(
          ([lon, lat]: [number, number]) =>
            latLonToVec3(lat, lon, GLOBE_RADIUS * 1.48)
        );
        const geom = new THREE.BufferGeometry().setFromPoints(points);
        const line = new THREE.Line(geom, lineMaterial);
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
  globe.rotation.y += delta * 0.05;
}
