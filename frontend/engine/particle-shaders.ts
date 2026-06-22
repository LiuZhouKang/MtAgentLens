import * as THREE from 'three';

// GLSL3 shaders for ShaderMaterial with glslVersion: THREE.GLSL3.
// Three.js auto-adds #version 300 es, precision, and built-in uniforms
// (modelViewMatrix, projectionMatrix, position). Only declare custom
// attributes, varyings, and uniforms.

const vertexShader = /* glsl */ `
in float size;
in vec3 customColor;
out vec3 vColor;

void main() {
  vColor = customColor;
  vec4 mvPosition = modelViewMatrix * vec4(position, 1.0);
  gl_PointSize = size * (300.0 / length(mvPosition.xyz));
  gl_Position = projectionMatrix * mvPosition;
}`;

const fragmentShader = /* glsl */ `
uniform vec3 color;
uniform sampler2D map;
uniform float opacity;
in vec3 vColor;
out vec4 pc_fragColor;

void main() {
  vec4 mask = texture(map, gl_PointCoord);
  pc_fragColor = vec4(color * vColor, opacity) * mask;
}`;

export function createParticleMaterial(textureUrl: string): THREE.ShaderMaterial {
  const texture = new THREE.TextureLoader().load(textureUrl);
  return new THREE.ShaderMaterial({
    uniforms: {
      color: { value: new THREE.Color(0xffffff) },
      map: { value: texture },
      opacity: { value: 0.48 },
    },
    vertexShader,
    fragmentShader,
    glslVersion: THREE.GLSL3,
    transparent: true,
    depthTest: false,
    depthWrite: false,
    blending: THREE.NormalBlending,
  });
}
