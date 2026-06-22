import * as THREE from 'three';

function getContainerSize(container: HTMLElement): { width: number; height: number } {
  return {
    width: Math.max(container.clientWidth || window.innerWidth, 1),
    height: Math.max(container.clientHeight || window.innerHeight, 1),
  };
}

export function createScene(): THREE.Scene {
  const scene = new THREE.Scene();
  scene.fog = new THREE.FogExp2(0x08111f, 0.00034);
  return scene;
}

export function createRenderer(container: HTMLElement): THREE.WebGLRenderer {
  const { width, height } = getContainerSize(container);
  const renderer = new THREE.WebGLRenderer({
    antialias: true,
    alpha: true,
    preserveDrawingBuffer: true,
  });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 1.75));
  renderer.setSize(width, height);
  renderer.setClearColor(0x050914, 0);
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  container.appendChild(renderer.domElement);
  return renderer;
}

export function createCamera(container: HTMLElement): THREE.PerspectiveCamera {
  const { width, height } = getContainerSize(container);
  const camera = new THREE.PerspectiveCamera(42, width / height, 0.1, 10000);
  camera.position.set(112, 44, 468);
  return camera;
}

export function onResize(
  renderer: THREE.WebGLRenderer,
  camera: THREE.PerspectiveCamera,
  container: HTMLElement
): () => void {
  const resize = () => {
    const { width, height } = getContainerSize(container);
    renderer.setSize(width, height);
    camera.aspect = width / height;
    camera.updateProjectionMatrix();
  };

  const observer = typeof ResizeObserver !== 'undefined'
    ? new ResizeObserver(resize)
    : null;

  window.addEventListener('resize', resize);
  observer?.observe(container);

  return () => {
    window.removeEventListener('resize', resize);
    observer?.disconnect();
  };
}
