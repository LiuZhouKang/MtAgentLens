// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import CommandCenterPage from '@/components/command-center/command-center-page';
import { demoAlerts, demoGraph } from '@/lib/demo-data';
import { useAppStore } from '@/lib/store';

// Mock the WebGL 3D stage (jsdom has no WebGL) and capture its callbacks so the
// tests can simulate graph interactions.
const stageState: {
  onNodeClick: ((info: { id: string; node_type: string; agent_id: string; desc: string }) => void) | null;
  onBackgroundClick: (() => void) | null;
} = { onNodeClick: null, onBackgroundClick: null };

vi.mock('@/components/ui/mtagentlens-graph-stage', () => ({
  default: function MockGraphStage(props: {
    onNodeClick: (info: { id: string; node_type: string; agent_id: string; desc: string }) => void;
    onBackgroundClick: () => void;
  }) {
    stageState.onNodeClick = props.onNodeClick;
    stageState.onBackgroundClick = props.onBackgroundClick;
    return <div data-testid="mtl-stage" />;
  },
}));

vi.mock('@/components/command-center/node-detail-panel', () => ({
  default: function MockNodeDetailPanel({ nodeId, onClose }: { nodeId: string; onClose: () => void }) {
    return (
      <div data-testid="node-detail" onClick={onClose}>
        detail:{nodeId}
      </div>
    );
  },
}));

vi.mock('@/components/ui/command-palette', () => ({
  default: function MockCommandPalette() {
    return <div data-testid="palette">palette</div>;
  },
}));

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
      },
    },
  });

  return render(
    <QueryClientProvider client={queryClient}>
      <CommandCenterPage />
    </QueryClientProvider>,
  );
}

describe('CommandCenterPage', () => {
  beforeEach(() => {
    stageState.onNodeClick = null;
    stageState.onBackgroundClick = null;
    useAppStore.setState({
      activeRunId: null,
      alerts: demoAlerts,
      graph: demoGraph,
      graphMetrics: demoAlerts[0].graph_snapshot?.metrics ?? null,
    });
  });

  afterEach(() => {
    cleanup();
  });

  it('renders the 3D responsibility theater when data is present', async () => {
    renderPage();
    await waitFor(() => expect(screen.getByTestId('mtl-stage')).toBeDefined());
  });

  it('shows the full-page loader while there is no data', () => {
    useAppStore.setState({ alerts: [], graph: { nodes: [], edges: [] } });
    renderPage();
    expect(screen.getAllByText('MtAgentLens').length).toBeGreaterThan(0);
  });

  it('opens a node detail panel when a graph node is clicked and closes on background', () => {
    renderPage();
    expect(stageState.onNodeClick).not.toBeNull();

    act(() => {
      stageState.onNodeClick!({
        id: demoGraph.nodes[0].node_id,
        node_type: demoGraph.nodes[0].node_type,
        agent_id: demoGraph.nodes[0].agent_id ?? 'unassigned',
        desc: 'test node',
      });
    });

    expect(screen.getByTestId('node-detail')).toBeDefined();

    act(() => {
      stageState.onBackgroundClick!();
    });
    expect(screen.queryByTestId('node-detail')).toBeNull();
  });

  it('opens the command palette with ⌘K and closes with Escape', async () => {
    renderPage();
    expect(screen.queryByTestId('palette')).toBeNull();

    fireEvent.keyDown(window, { key: 'k', ctrlKey: true });
    await waitFor(() => expect(screen.getByTestId('palette')).toBeDefined());

    fireEvent.keyDown(window, { key: 'Escape' });
    await waitFor(() => expect(screen.queryByTestId('palette')).toBeNull());
  });
});
