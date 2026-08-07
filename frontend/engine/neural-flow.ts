import * as THREE from 'three';
import {
  BatchedRenderer,
  ConeEmitter,
  ConstantColor,
  ConstantValue,
  IntervalValue,
  ParticleSystem,
  RenderMode,
  Vector4,
} from 'three.quarks';

export type NeuralFlowEdgeTone = 'neutral' | 'warning' | 'critical';

export type NeuralFlowEdgeFrame = {
  id: string;
  startX: number;
  startY: number;
  startZ: number;
  endX: number;
  endY: number;
  endZ: number;
  tone: NeuralFlowEdgeTone;
  intensity: number;
};

// Security-semantic palette — matches home-engine-stage link colors
const EDGE_TONE_COLOR: Record<NeuralFlowEdgeTone, THREE.Color> = {
  neutral: new THREE.Color('#5e6ad2'),
  warning: new THREE.Color('#f59e0b'),
  critical: new THREE.Color('#f97316'),
};

function clamp(value: number, min: number, max: number) {
  return Math.min(Math.max(value, min), max);
}

function lerp(a: number, b: number, t: number) {
  return a + (b - a) * t;
}

export class NeuralFlowRenderer {
  private readonly batch: BatchedRenderer;
  private readonly texture: THREE.Texture;
  private readonly systems = new Map<string, ParticleSystem>();
  private readonly systemParams = new Map<
    string,
    { speed: ConstantValue; life: IntervalValue; rate: ConstantValue }
  >();
  private readonly scratchStart = new THREE.Vector3();
  private readonly scratchEnd = new THREE.Vector3();
  private readonly scratchDir = new THREE.Vector3();
  private readonly scratchQuat = new THREE.Quaternion();
  private attachedScene: THREE.Scene | null = null;

  constructor(textureUrl = '/globe/images/dot7.png') {
    this.batch = new BatchedRenderer();
    this.texture = new THREE.TextureLoader().load(textureUrl);
  }

  attach(scene: THREE.Scene) {
    if (this.attachedScene === scene) return;
    this.detach();
    this.attachedScene = scene;
    scene.add(this.batch);
  }

  detach() {
    if (!this.attachedScene) return;
    this.attachedScene.remove(this.batch);
    this.attachedScene = null;
  }

  update(deltaSeconds: number) {
    this.batch.update(deltaSeconds);
  }

  sync(frameEdges: NeuralFlowEdgeFrame[]) {
    const seen = new Set<string>();

    frameEdges.forEach((edge) => {
      seen.add(edge.id);
      const existing = this.systems.get(edge.id);
      if (existing) {
        this.updateSystem(existing, edge);
        return;
      }

      const system = this.createSystem(edge);
      this.systems.set(edge.id, system);
      this.batch.addSystem(system);
      if (this.attachedScene) {
        this.attachedScene.add(system.emitter);
      }
      this.updateSystem(system, edge);
    });

    Array.from(this.systems.entries()).forEach(([id, system]) => {
      if (seen.has(id)) return;
      this.batch.deleteSystem(system);
      if (this.attachedScene) {
        this.attachedScene.remove(system.emitter);
      }
      system.dispose();
      this.systems.delete(id);
      this.systemParams.delete(id);
    });
  }

  dispose() {
    this.detach();
    Array.from(this.systems.values()).forEach((system) => {
      this.batch.deleteSystem(system);
      system.dispose();
    });
    this.systems.clear();
    this.systemParams.clear();
    this.texture.dispose();
  }

  private createSystem(edge: NeuralFlowEdgeFrame) {
    const speed = new ConstantValue(80);
    const life = new IntervalValue(0.55, 0.95);
    const rate = new ConstantValue(12);
    const material = new THREE.MeshBasicMaterial({
      map: this.texture,
      transparent: true,
      blending: THREE.AdditiveBlending,
      depthWrite: false,
      depthTest: false,
      color: EDGE_TONE_COLOR[edge.tone],
      opacity: 1,
      toneMapped: false,
    });

    const system = new ParticleSystem({
      duration: 2,
      looping: true,
      prewarm: true,
      worldSpace: true,
      startLife: life,
      startSpeed: speed,
      startSize: new IntervalValue(0.7, 1.4),
      startColor: new ConstantColor(new Vector4(1, 1, 1, 1)),
      emissionOverTime: rate,
      shape: new ConeEmitter({ angle: 0.16, radius: 0.035, thickness: 1 }),
      renderMode: RenderMode.Trail,
      material,
      renderOrder: 22,
    });

    this.systemParams.set(edge.id, { speed, life, rate });
    return system;
  }

  private updateSystem(system: ParticleSystem, edge: NeuralFlowEdgeFrame) {
    const intensity = clamp(edge.intensity, 0, 1);
    const params = this.systemParams.get(edge.id);
    if (!params) return;

    this.scratchStart.set(edge.startX, edge.startY, edge.startZ);
    this.scratchEnd.set(edge.endX, edge.endY, edge.endZ);
    this.scratchDir.subVectors(this.scratchEnd, this.scratchStart);

    const length = this.scratchDir.length();
    if (!Number.isFinite(length) || length < 1e-3) return;

    this.scratchDir.multiplyScalar(1 / length);

    const travelSeconds = clamp(length / lerp(74, 220, intensity), 0.28, 1.4);
    const speed = length / travelSeconds;

    params.speed.value = speed;
    params.life.a = travelSeconds * 0.78;
    params.life.b = travelSeconds * 1.12;
    params.rate.value = lerp(10, 64, intensity);

    (system.material as THREE.MeshBasicMaterial).color.copy(EDGE_TONE_COLOR[edge.tone]);

    system.emitter.position.copy(this.scratchStart);
    this.scratchQuat.setFromUnitVectors(new THREE.Vector3(0, 1, 0), this.scratchDir);
    system.emitter.quaternion.copy(this.scratchQuat);
    system.emitter.updateMatrixWorld();
    system.play();
  }
}
