// @vitest-environment jsdom
import * as THREE from 'three';
import React from 'react';
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
  latestReactGraphProps: null as Record<string, unknown> | null,
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
  let currentGraphData: {
    nodes: Array<Record<string, unknown>>;
    links: Array<Record<string, unknown>>;
  } | null = null;

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
    graphData: vi.fn(
      (value?: {
        nodes: Array<Record<string, unknown>>;
        links: Array<Record<string, unknown>>;
      }) => {
        if (value === undefined) {
          return currentGraphData;
        }
        currentGraphData = value;
        return api;
      },
    ),
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
  useAppStore: Object.assign(
    (selector: (state: typeof mockStoreState) => unknown) => selector(mockStoreState),
    {
      getState: () => mockStoreState,
    },
  ),
}));

vi.mock('@/engine/alert-pulse', () => ({
  AlertPulseOverlay: class AlertPulseOverlay {
    update() {}
    showPulse() {}
    clear() {}
  },
}));

vi.mock('react-force-graph-3d', () => {
  const MockReactForceGraph3D = React.forwardRef((props: Record<string, unknown>, ref) => {
    const api = createGraphApi();
    mockState.latestGraphApi = api;
    mockState.latestReactGraphProps = props;
    React.useImperativeHandle(ref, () => api);
    return <div data-testid="force-graph-mock" />;
  });
  MockReactForceGraph3D.displayName = 'MockReactForceGraph3D';

  return {
    __esModule: true,
    default: MockReactForceGraph3D,
  };
});

describe('HomeEngineStage', () => {
  beforeEach(() => {
    mockState.latestGraphApi = null;
    mockState.latestReactGraphProps = null;

    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({
        ok: true,
        json: async () => ({
          nodes: [
            {
              id: 'oss-node-1',
              user: 'frontend-expert',
              description: 'demo node',
            },
          ],
          links: [],
        }),
      })),
    );

    vi.stubGlobal(
      'ResizeObserver',
      class ResizeObserver {
        observe() {}
        disconnect() {}
      },
    );
    vi.stubGlobal(
      'requestAnimationFrame',
      vi.fn(() => 1),
    );
    vi.stubGlobal('cancelAnimationFrame', vi.fn());
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('keeps the oss demo graph from re-centering during interaction', async () => {
    render(
      <HomeEngineStage
        active
        featuredAlert={demoAlerts[0]}
        theme="dark"
        panelLevel={0}
        dataMode="oss-demo"
      />,
    );

    await waitFor(() => {
      expect(mockState.latestGraphApi).not.toBeNull();
      expect(mockState.latestReactGraphProps).not.toBeNull();
    });

    const graphApi = mockState.latestGraphApi as ReturnType<typeof createGraphApi>;
    const props = mockState.latestReactGraphProps as Record<string, unknown>;

    expect(props.enableNodeDrag).toBe(false);
    await waitFor(() => {
      expect(graphApi.zoomToFit).toHaveBeenCalled();
    });
    expect(graphApi.cameraPosition).not.toHaveBeenCalled();
  });

  it('renders the semantic stage with stable 3D props and selection callbacks', async () => {
    const onReady = vi.fn();
    const onNodeSelected = vi.fn();
    render(
      <HomeEngineStage
        active
        featuredAlert={demoAlerts[0]}
        theme="dark"
        panelLevel={0}
        dataMode="semantic"
        onReady={onReady}
        onNodeSelected={onNodeSelected}
      />,
    );

    await waitFor(() => {
      expect(onReady).toHaveBeenCalled();
      expect(mockState.latestGraphApi).not.toBeNull();
      expect(mockState.latestReactGraphProps).not.toBeNull();
    });

    const graphApi = mockState.latestGraphApi as ReturnType<typeof createGraphApi>;
    expect(graphApi.zoomToFit).toHaveBeenCalled();

    const props = mockState.latestReactGraphProps as Record<string, unknown>;
    expect(props.enableNodeDrag).toBe(false);
    expect(typeof props.onNodeClick).toBe('function');
    expect(typeof props.linkDirectionalParticles).toBe('function');

    const graphData = props.graphData as {
      nodes: Array<Record<string, unknown>>;
      links: Array<Record<string, unknown>>;
    };
    const realNode = graphData.nodes.find(
      (node) => node.kind === 'core' && node.realNodeId === 'agent-security',
    );
    expect(realNode).toBeTruthy();

    const clickHandler = props.onNodeClick as (node: Record<string, unknown>) => void;
    clickHandler(realNode as Record<string, unknown>);

    expect(onNodeSelected).toHaveBeenCalledWith(
      expect.objectContaining({
        nodeId: 'agent-security',
        nodeType: 'Agent',
      }),
    );
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
