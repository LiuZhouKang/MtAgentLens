import * as THREE from 'three';
import { EffectComposer } from 'three/examples/jsm/postprocessing/EffectComposer.js';
import { RenderPass } from 'three/examples/jsm/postprocessing/RenderPass.js';
import { UnrealBloomPass } from 'three/examples/jsm/postprocessing/UnrealBloomPass.js';
import { createScene, createRenderer, createCamera, onResize } from './scene-manager';
import { createStarfield, animateStarfield } from './starfield';
import { createGlobe, addGlobeOutlines, animateGlobe } from './globe-renderer';
import { createGlobeSparkles } from './globe-sparkles';
import { createParticleMaterial } from './particle-shaders';
import { CameraController } from './camera-controller';
import { GraphRenderer } from './graph-renderer';
import { AlertPulseOverlay } from './alert-pulse';
import { TimelineRing } from './timeline-ring';
import type { GraphData, ResponsibilityAlert } from '@/lib/types';

export interface EngineAPI {
  setVisualizationMode(mode: string): void;
  flyToAgent(agentId: string, duration?: number): void;
  flyToNode(nodeId: string, duration?: number): void;
  flyToLevel(level: number, duration?: number): void;
  resetCamera(duration?: number): void;
  updateGraph(graph: GraphData): void;
  highlightPath(edgeIds: string[]): void;
  clearHighlights(): void;
  showAlertPulse(alert: ResponsibilityAlert): void;
  clearAlertPulses(): void;
  orbit(deltaX: number, deltaY: number): void;
  zoom(delta: number): void;
  onNodeClicked(cb: (nodeId: string) => void): void;
  onZoomLevelChanged(cb: (level: number) => void): void;
  dispose(): void;
}

export async function initEngine(container: HTMLElement): Promise<EngineAPI> {
  const scene = createScene();
  const renderer = createRenderer(container);
  const camera = createCamera();
  onResize(renderer, camera);

  // Ambient lighting for scene depth
  scene.add(new THREE.AmbientLight(0x111122, 0.3));

  const starfield = createStarfield(scene, 500);
  const globe = createGlobe(scene);
  await addGlobeOutlines(globe, '/data/world.json');

  const particleMat = createParticleMaterial('/images/block.png');
  const sparkles = createGlobeSparkles(scene, particleMat);

  // Post-processing: Bloom for sci-fi glow effect
  const composer = new EffectComposer(renderer);
  composer.addPass(new RenderPass(scene, camera));
  const bloomPass = new UnrealBloomPass(
    new THREE.Vector2(window.innerWidth, window.innerHeight),
    0.3,   // strength  (subtle glow, not overwhelming)
    0.5,   // radius    (soft spread)
    0.6    // threshold (only brightest areas bloom)
  );
  composer.addPass(bloomPass);

  const graphRenderer = new GraphRenderer(scene);
  const alertPulse = new AlertPulseOverlay(scene);
  const timelineRing = new TimelineRing(scene);

  const cameraCtrl = new CameraController(camera);

  // Debug: expose scene for browser inspection
  (window as any).__agentsight_scene = scene;
  (window as any).__agentsight_camera = camera;
  console.log('[AgentSight] Engine initialized. Scene children:', scene.children.length,
    'Globe children:', globe.children.length,
    'Starfield particles:', (starfield as THREE.Points).geometry.attributes.position.count);

  // Raycaster for 3D object interaction
  const raycaster = new THREE.Raycaster();
  raycaster.params.Points.threshold = 0.5;
  const mouse = new THREE.Vector2();

  function handleClick(event: MouseEvent) {
    mouse.x = (event.clientX / window.innerWidth) * 2 - 1;
    mouse.y = -(event.clientY / window.innerHeight) * 2 + 1;

    raycaster.setFromCamera(mouse, camera);
    // Check intersections with sparkles and graph nodes
    const sparkleIntersects = raycaster.intersectObject(sparkles);
    if (sparkleIntersects.length > 0 && nodeClickCb) {
      // Sparkle clicked — treat as background click for now
      return;
    }
    // Check graph nodes
    const nodeMeshes: THREE.Object3D[] = [];
    graphRenderer.nodeGroup.traverse((c) => { if (c instanceof THREE.Mesh) nodeMeshes.push(c); });
    const nodeIntersects = raycaster.intersectObjects(nodeMeshes);
    if (nodeIntersects.length > 0 && nodeClickCb) {
      // Find which node was clicked by matching mesh
      for (const [id, node] of graphRenderer.nodes) {
        if (node.mesh === nodeIntersects[0].object) {
          nodeClickCb(id);
          break;
        }
      }
    }
  }
  container.addEventListener('click', handleClick);

  let prevTime = performance.now();
  let nodeClickCb: ((id: string) => void) | null = null;
  let zoomCb: ((level: number) => void) | null = null;

  let rafId: number;
  function animate(): void {
    rafId = requestAnimationFrame(animate);
    const now = performance.now();
    const delta = Math.min((now - prevTime) / 1000, 0.1);
    prevTime = now;

    animateStarfield(starfield as THREE.Points, delta);
    animateGlobe(globe, delta);
    cameraCtrl.update(delta);
    graphRenderer.update(delta);
    alertPulse.update(delta);
    timelineRing.update(delta);

    composer.render();
  }
  animate();

  // Update bloom on resize
  window.addEventListener('resize', () => {
    bloomPass.setSize(window.innerWidth, window.innerHeight);
  });

  return {
    setVisualizationMode(_mode: string) {},
    flyToAgent(agentId: string, duration?: number) {
      const node = graphRenderer.nodes.get(agentId);
      if (node) {
        cameraCtrl.flyTo(
          node.mesh.position.clone().add(new THREE.Vector3(0, 0, 100)),
          node.mesh.position.clone(),
          duration ?? 1.2
        );
      }
    },
    flyToNode(nodeId: string, duration?: number) {
      const node = graphRenderer.nodes.get(nodeId);
      if (node) {
        cameraCtrl.flyTo(
          node.mesh.position.clone().add(new THREE.Vector3(0, 0, 80)),
          node.mesh.position.clone(),
          duration ?? 0.8
        );
      }
    },
    flyToLevel(level: number, duration?: number) { cameraCtrl.flyToLevel(level as 0|1|2); },
    resetCamera(duration?: number) { cameraCtrl.reset(duration); },
    updateGraph(graph: GraphData) { graphRenderer.updateGraph(graph); },
    highlightPath(edgeIds: string[]) { graphRenderer.highlightPath(edgeIds); },
    clearHighlights() { graphRenderer.clearHighlights(); },
    showAlertPulse(alert: ResponsibilityAlert) { alertPulse.showPulse(alert); },
    clearAlertPulses() { alertPulse.clear(); },
    orbit(deltaX: number, deltaY: number) { cameraCtrl.orbit(deltaX, deltaY); },
    zoom(delta: number) { cameraCtrl.zoom(delta); },
    onNodeClicked(cb: (nodeId: string) => void) { nodeClickCb = cb; },
    onZoomLevelChanged(cb: (level: number) => void) { zoomCb = cb; },
    dispose() {
      cancelAnimationFrame(rafId);
      container.removeEventListener('click', handleClick);
      composer.dispose();
      graphRenderer.dispose();
      renderer.dispose();
    },
  };
}
