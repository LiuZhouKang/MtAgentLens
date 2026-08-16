// @vitest-environment jsdom
import * as React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import CommandCenterPage from '@/components/command-center/command-center-page';
import { demoAlerts, demoGraph } from '@/lib/demo-data';
import { useAppStore } from '@/lib/store';
import type { DbAssets } from '@/lib/db-import';
import type { GraphMetrics, GraphSnapshotFrame } from '@/lib/types';

// Mock the WebGL 3D stage (jsdom has no WebGL) and capture its callbacks so the
// tests can simulate graph interactions.
// readyDelay>0 → 延迟触发 onReady（模拟「图渲染中」阶段，验证全屏 Orbit 持续覆盖）；
// readyDelay=0 → 挂载即触发（默认，图就绪后全屏 LoadingScreen 淡出）。
let readyDelay = 0;
const stageState: {
  onNodeClick:
    ((info: { id: string; node_type: string; agent_id: string; desc: string }) => void) | null;
  onBackgroundClick: (() => void) | null;
  onReady: (() => void) | null;
} = { onNodeClick: null, onBackgroundClick: null, onReady: null };

vi.mock('@/components/ui/mtagentlens-graph-stage', () => ({
  default: function MockGraphStage(props: {
    onNodeClick: (info: { id: string; node_type: string; agent_id: string; desc: string }) => void;
    onBackgroundClick: () => void;
    onReady?: () => void;
  }) {
    stageState.onNodeClick = props.onNodeClick;
    stageState.onBackgroundClick = props.onBackgroundClick;
    stageState.onReady = props.onReady ?? null;
    // 模拟图渲染就绪：readyDelay=0 立即触发；>0 延迟（覆盖图渲染中阶段）。
    // onReady 随每次渲染更新、readyDelay 为测试旋钮 → 依赖数组有意留空。
    React.useEffect(() => {
      if (readyDelay > 0) {
        const t = setTimeout(() => props.onReady?.(), readyDelay);
        return () => clearTimeout(t);
      }
      props.onReady?.();
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);
    return <div data-testid="mtl-stage" />;
  },
}));

vi.mock('@/components/command-center/node-detail-panel', () => ({
  default: function MockNodeDetailPanel({
    nodeId,
    onClose,
  }: {
    nodeId: string;
    onClose: () => void;
  }) {
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

// M4 时间演化：构造 2 帧 dbAssets 供 scrubber 测试（其余字段置空即可）。
const snapMetrics: GraphMetrics = {
  node_counts: {},
  edge_counts: {},
  agent_count: 0,
  cross_agent_edges: 0,
  delegation_depth: 0,
  taint_chains: 0,
  avg_confidence: 0,
};

const snapFrame = (id: string, nodeId: string, ts: number): GraphSnapshotFrame => ({
  snapshot_id: id,
  timestamp_ns: ts,
  graph: {
    nodes: [
      {
        node_id: nodeId,
        node_type: 'Agent',
        agent_id: nodeId,
        resource: null,
        evidence_refs: [],
        timestamp_ns: ts,
        phase: { Provisional: { timestamp_ns: ts } },
      },
    ],
    edges: [],
  },
  metrics: snapMetrics,
});

const twoFrameAssets: DbAssets = {
  pollutionEvents: [],
  policyEvaluations: [],
  nonLinkDiagnostics: [],
  agentIdentities: [],
  provisionalIdentities: [],
  identityProcessMap: [],
  processEvents: [],
  processEventCount: 0,
  processEventTruncated: false,
  tlsSessions: [],
  tlsSessionCount: 0,
  tlsSessionTruncated: false,
  subagentBoundaries: [],
  snapshotFrames: [snapFrame('f0', 'n1', 1), snapFrame('f1', 'n1', 2)],
};

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
    readyDelay = 0;
    stageState.onNodeClick = null;
    stageState.onBackgroundClick = null;
    stageState.onReady = null;
    useAppStore.setState({
      activeRunId: null,
      alerts: demoAlerts,
      graph: demoGraph,
      graphMetrics: demoAlerts[0].graph_snapshot?.metrics ?? null,
      dbAssets: null,
      snapshotFrameIndex: 0,
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

  it('renders the theater when the graph has data but alerts are empty (real db run with no violations)', async () => {
    // Real collector dbs frequently carry a full graph with zero alerts
    // (a monitoring session that triggered no rule). The theater must render —
    // "no alerts" is not "loading".
    useAppStore.setState({ alerts: [], graph: demoGraph });
    renderPage();
    await waitFor(() => expect(screen.getByTestId('mtl-stage')).toBeDefined());
    // 数据就绪 + 图渲染就绪（onReady 约 1200ms）后全屏 LoadingScreen 平滑淡出并卸载
    await waitFor(() => expect(screen.queryByText('MtAgentLens')).toBeNull(), { timeout: 4000 });
  });

  it('数据就绪但图渲染未就绪时保持单一全屏 Orbit，图就绪后平滑淡出（无第二加载态）', async () => {
    // 图渲染中（onReady 延迟 300ms）：态势页唯一加载态 = 全屏 Observation Orbit
    // （Image 8，与全站统一）。图内不再自渲染迷你 Orbit → 无 Image 9 第二动画。
    readyDelay = 300;
    useAppStore.setState({ alerts: [], graph: demoGraph });
    renderPage();
    await waitFor(() => expect(screen.getByTestId('mtl-stage')).toBeDefined());
    // 图渲染就绪前：全屏 Orbit 持续在场（品牌文字可测）
    expect(screen.getByText('MtAgentLens')).toBeDefined();
    // 图渲染就绪后：AnimatePresence exit 平滑淡出并卸载 → 只剩态势图
    await waitFor(() => expect(screen.queryByText('MtAgentLens')).toBeNull(), { timeout: 4000 });
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

  it('snapshotFrames > 1 时渲染时间 scrubber', async () => {
    useAppStore.setState({ dbAssets: twoFrameAssets, snapshotFrameIndex: 1 });
    renderPage();
    expect(await screen.findByRole('group', { name: /时间演化/i })).toBeTruthy();
    expect(screen.getByText(/2 \/ 2/)).toBeTruthy(); // 帧索引「最新 / 总数」
  });

  it('snapshotFrames ≤ 1 时隐藏 scrubber', async () => {
    renderPage();
    expect(screen.queryByRole('group', { name: /时间演化/i })).toBeNull();
  });
});
