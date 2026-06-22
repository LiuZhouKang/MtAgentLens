// GLSL3 fragment shader — for ShaderMaterial with glslVersion: THREE.GLSL3.
// Declare only custom uniforms; Three.js provides built-ins automatically.
uniform vec3 color;
uniform sampler2D map;
in vec3 vColor;
out vec4 pc_fragColor;

void main() {
  pc_fragColor = vec4(color * vColor, 1.0);
  pc_fragColor = pc_fragColor * texture(map, gl_PointCoord);
}
