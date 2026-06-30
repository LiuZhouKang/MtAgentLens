// @vitest-environment jsdom
import * as THREE from 'three';
import { render, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import HomeEngineStage, { deriveAlertFocus } from '@/components/ui/home-engine-stage';
import { demoAlerts, demoGraph } from '@/lib/demo-data';

const mockStoreState = {
  graph: demoGraph,
  engineRef: { current: null as unknown },
};

const mockState = vi.hoisted(() => ({
  latestGraphApi: null as ReturnType<typeof createGraphApi> | null,
  constructorCalls: [] as unknown[][],
}));

function createGraphApi() {
  const chargeForce = {
    strength: vi.fn(() => chargeForce),
  };
  const linkForce = {
    distance: vi.fn(() => linkForce),
    strength: vi.fn(() => linkForce),
  };
  const accessors: Record<string, unknown> = {};
  let currentGraphData: { nodes: Array<Record<string, unknown>>; links: Array<Record<string, unknown>> } | null = null;

  const accessor = (key: string) =>
    vi.fn((...args: [unknown?]) => {
      if (args.length === 0) {
        return accessors[key];
      }
      accessors[key] = args[0];
      return api;
    });

  const api = {
    _destructor: vi.fn(),
    pauseAnimation: vi.fn(() => api),
    resumeAnimation: vi.fn(() => api),
    d3AlphaDecay: vi.fn(() => api),
    d3VelocityDecay: vi.fn(() => api),
    cooldownTicks: vi.fn(() => api),
    warmupTicks: vi.fn(() => api),
    d3ReheatSimulation: vi.fn(() => api),
    zoomToFit: vi.fn(() => api),
    cameraPosition: vi.fn(() => api),
    d3Force: vi.fn((name: string) => (name === 'link' ? linkForce : chargeForce)),
    graphData: vi.fn((value?: { nodes: Array<Record<string, unknown>>; links: Array<Record<string, unknown>> }) => {
      if (value === undefined) {
        return currentGraphData;
      }
      currentGraphData = value;
      return api;
    }),
    backgroundColor: vi.fn(() => api),
    showNavInfo: vi.fn(() => api),
    width: vi.fn(() => api),
    height: vi.fn(() => api),
    nodeId: vi.fn(() => api),
    nodeRelSize: vi.fn(() => api),
    nodeResolution: vi.fn(() => api),
    nodeLabel: accessor('nodeLabel'),
    nodeVal: accessor('nodeVal'),
    nodeColor: accessor('nodeColor'),
    nodeThreeObject: accessor('nodeThreeObject'),
    nodeThreeObjectExtend: vi.fn(() => api),
    linkOpacity: vi.fn(() => api),
    linkColor: accessor('linkColor'),
    linkWidth: accessor('linkWidth'),
    linkCurvature: accessor('linkCurvature'),
    linkDirectionalParticles: accessor('linkDirectionalParticles'),
    linkDirectionalParticleWidth: accessor('linkDirectionalParticleWidth'),
    linkDirectionalParticleSpeed: accessor('linkDirectionalParticleSpeed'),
    linkDirectionalParticleColor: accessor('linkDirectionalParticleColor'),
    linkDirectionalParticleOffset: accessor('linkDirectionalParticleOffset'),
    linkDirectionalParticleThreeObject: accessor('linkDirectionalParticleThreeObject'),
    linkDirectionalArrowLength: vi.fn(() => api),
    linkDirectionalArrowRelPos: vi.fn(() => api),
    linkDirectionalArrowColor: vi.fn(() => api),
    enableNodeDrag: vi.fn(() => api),
    enableNavigationControls: vi.fn(() => api),
    showPointerCursor: vi.fn(() => api),
    onNodeClick: vi.fn((cb: unknown) => {
      accessors.onNodeClick = cb;
      return api;
    }),
    onNodeHover: vi.fn((cb: unknown) => {
      accessors.onNodeHover = cb;
      return api;
    }),
    onLinkHover: vi.fn((cb: unknown) => {
      accessors.onLinkHover = cb;
      return api;
    }),
    onBackgroundClick: vi.fn((cb: unknown) => {
      accessors.onBackgroundClick = cb;
      return api;
    }),
    onNodeDrag: vi.fn((cb: unknown) => {
      accessors.onNodeDrag = cb;
      return api;
    }),
    onNodeDragEnd: vi.fn((cb: unknown) => {
      accessors.onNodeDragEnd = cb;
      return api;
    }),
    scene: vi.fn(() => new THREE.Scene()),
    camera: vi.fn(() => new THREE.PerspectiveCamera()),
    renderer: vi.fn(() => ({ setPixelRatio: vi.fn() })),
    postProcessingComposer: vi.fn(() => ({ addPass: vi.fn() })),
    lights: vi.fn(() => api),
    controls: vi.fn(() => ({
      minDistance: 0,
      maxDistance: 0,
      rotateSpeed: 0,
      zoomSpeed: 0,
      panSpeed: 0,
      update: vi.fn(),
    })),
  };

  return api;
}

vi.mock('@/lib/store', () => ({
  useAppStore: Object.assign((selector: (state: typeof mockStoreState) => unknown) => selector(mockStoreState), {
    getState: () => mockStoreState,
  }),
}));

vi.mock('@/engine/alert-pulse', () => ({
  AlertPulseOverlay: class AlertPulseOverlay {
    update() {}
    showPulse() {}
    clear() {}
  },
}));

vi.mock('3d-force-graph', () => {
  function MockForceGraph3D(...args: unknown[]) {
    mockState.constructorCalls.push(args);
    mockState.latestGraphApi = createGraphApi();
    return mockState.latestGraphApi;
  }

  return {
    __esModule: true,
    default: MockForceGraph3D,
  };
});

describe('HomeEngineStage', () => {
  beforeEach(() => {
    mockState.latestGraphApi = null;
    mockState.constructorCalls = [];

    vi.stubGlobal(
      'ResizeObserver',
      class ResizeObserver {
        observe() {}
        disconnect() {}
      },
    );
    vi.stubGlobal('requestAnimationFrame', vi.fn(() => 1));
    vi.stubGlobal('cancelAnimationFrame', vi.fn());
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('enables node dragging and pins dragged node positions', async () => {
    const onReady = vi.fn();
    render(
      <HomeEngineStage
        active
        featuredAlert={demoAlerts[0]}
        theme="dark"
        panelLevel={0}
        dataMode="semantic"
        onReady={onReady}
      />,
    );

    await waitFor(() => {
      expect(onReady).toHaveBeenCalled();
      expect(mockState.latestGraphApi).not.toBeNull();
    });

    const graphApi = mockState.latestGraphApi as ReturnType<typeof createGraphApi>;
    expect(graphApi.enableNodeDrag).toHaveBeenCalledWith(true);
    expect(graphApi.onNodeDrag).toHaveBeenCalled();
    expect(graphApi.linkDirectionalParticles).toHaveBeenCalled();
    expect(graphApi.linkDirectionalParticleOffset).toHaveBeenCalled();
    expect(graphApi.linkDirectionalParticleThreeObject).toHaveBeenCalled();

    const particleAccessor = graphApi.linkDirectionalParticles.mock.calls.at(-1)?.[0] as ((link: Record<string, unknown>) => number) | undefined;
    expect(particleAccessor?.({ kind: 'real', edgeType: 'PolicyBoundaryCrossing' })).toBe(0);

    const dragHandler = graphApi.onNodeDragEnd.mock.calls.at(-1)?.[0] as ((node: Record<string, unknown>) => void) | undefined;

    const draggedNode = {
      id: 'agent-security',
      x: 128,
      y: -36,
      z: 214,
    };

    dragHandler?.(draggedNode);

    expect(draggedNode).toMatchObject({
      fx: 128,
      fy: -36,
      fz: 214,
    });

    expect(graphApi.d3ReheatSimulation).toHaveBeenCalled();
    expect(graphApi.resumeAnimation).toHaveBeenCalled();
  });

  it('derives default alert focus from primary agent and risky edges', () => {
    const focus = deriveAlertFocus(demoAlerts[0]);

    expect(focus.nodeIds.has('agent-security')).toBe(true);
    expect(focus.nodeIds.has('boundary-system')).toBe(true);
    expect(focus.nodeIds.has('res-shadow')).toBe(true);
    expect(focus.edgeIds.has('e-boundary')).toBe(true);
    expect(focus.edgeIds.has('e-taint')).toBe(true);
    expect(focus.edgeIds.has('e-delegate')).toBe(true);
  });
});
