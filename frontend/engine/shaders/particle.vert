// GLSL3 vertex shader — for ShaderMaterial with glslVersion: THREE.GLSL3.
// modelViewMatrix, projectionMatrix, position are built-in; do not redeclare.
in float size;
in vec3 customColor;
out vec3 vColor;

void main() {
  vColor = customColor;
  vec4 mvPosition = modelViewMatrix * vec4(position, 1.0);
  gl_PointSize = size * (300.0 / length(mvPosition.xyz));
  gl_Position = projectionMatrix * mvPosition;
}
