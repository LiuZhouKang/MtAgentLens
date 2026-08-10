'use client';

import dynamic from 'next/dynamic';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useAppStore } from '@/lib/store';
import { useAlertModels } from '@/lib/use-alert-models';
import type { ResponsibilityNode } from '@/lib/types';
import LoadingScreen from '@/components/ui/loading-screen';
import GraphTimeScrubber from '@/components/graph/graph-time-scrubber';
import { LiveAlertFeed } from '@/components/live/live-alert-feed';
import { LiveEventTail } from '@/components/live/live-event-tail';
import NodeDetailPanel from './node-detail-panel';

const MtAgentLensGraphStage = dynamic(() => import('@/components/ui/mtagentlens-graph-stage'), {
  ssr: false,
});

type SelectedNodeData = {
  id: string;
  node_type: string;
  agent_id: string;
  desc: string;
  rawNodes: ResponsibilityNode[];
};

const CommandPalette = dynamic(() => import('@/components/ui/command-palette'), {
  ssr: false,
});

export default function CommandCenterPage() {
  const alerts = useAppStore((state) => state.alerts);
  const graph = useAppStore((state) => state.graph);
  const focusAlert = useAppStore((state) => state.focusAlert);
  const dbAssets = useAppStore((state) => state.dbAssets);
  const snapshotFrameIndex = useAppStore((state) => state.snapshotFrameIndex);
  const liveSnapshotFrames = useAppStore((state) => state.liveSnapshotFrames);
  // 直播优先用实时帧（LiveSession 周期推进）；否则用 db 导入帧
  const snapshotFrames = liveSnapshotFrames.length > 0 ? liveSnapshotFrames : (dbAssets?.snapshotFrames ?? []);
  const appState = useAppStore((state) => state.appState);
  const isLive = appState === 'live';

  const [paletteOpen, setPaletteOpen] = useState(false);
  const [selectedAlertId, setSelectedAlertId] = useState<string | null>(null);
  const [selectedNode, setSelectedNode] = useState<SelectedNodeData | null>(null);
  const [showEventTail, setShowEventTail] = useState(false);

  // 直播自动打开原始事件流（实时滚动即时可见）
  useEffect(() => {
    if (isLive) setShowEventTail(true);
  }, [isLive]);

  const handleNodeClick = useCallback(
    (info: { id: string; node_type: string; agent_id: string; desc: string }) => {
      const rawNodes = graph.nodes.filter(
        (n) =>
          info.id === n.node_id ||
          (n.node_type === 'PolicyBoundary' && info.desc.includes(n.node_id.slice(0, 8))),
      );
      setSelectedNode({ ...info, rawNodes });
    },
    [graph.nodes],
  );

  const handleBackgroundClick = useCallback(() => {
    setSelectedNode(null);
  }, []);

  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault();
        setPaletteOpen(true);
      }
      if (event.key === 'Escape') {
        setPaletteOpen(false);
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, []);

  const alertModels = useAlertModels(alerts, graph);

  const selectedItem = useMemo(() => {
    if (!alertModels.length) return null;
    return alertModels.find((item) => item.alert.alert_id === selectedAlertId) ?? alertModels[0];
  }, [alertModels, selectedAlertId]);

  useEffect(() => {
    if (!selectedAlertId && alertModels[0]) {
      setSelectedAlertId(alertModels[0].alert.alert_id);
    }
  }, [alertModels, selectedAlertId]);

  useEffect(() => {
    if (!selectedItem) return;
    focusAlert(selectedItem.alert);
  }, [focusAlert, selectedItem]);

  // A real collector run often has a full graph but zero alerts (a monitoring
  // session that triggered no rule). "No alerts" must not read as "still
  // loading" — the theater renders as soon as there is any consumable data.
  // 实时运行中即使图/告警尚空也立即渲染（事件流即时可见）。
  const hasData = graph.nodes.length > 0 || alerts.length > 0 || isLive;

  if (!hasData) {
    return (
      <section
        className="command-center"
        aria-busy="true"
        style={{ height: '100%', minHeight: 0, position: 'relative' }}
      >
        <LoadingScreen show />
      </section>
    );
  }

  return (
    <section
      className="command-center command-center--immersive"
      aria-labelledby="command-console-title"
      style={{ height: '100%', minHeight: 0 }}
    >
      {/* M5：自建 sr-only 标题修复 aria-labelledby 悬挂（原 id 依赖 IncidentRail 才存在） */}
      <h1 id="command-console-title" className="sr-only">
        态势
      </h1>
      <div className="command-theater">
        <div className="command-theater__shell">
          <div className="command-theater__canvas">
            <MtAgentLensGraphStage
              active
              onNodeClick={handleNodeClick}
              onBackgroundClick={handleBackgroundClick}
              graphOverride={
                snapshotFrames.length > 1
                  ? (() => {
                      const frame =
                        snapshotFrames[Math.min(snapshotFrameIndex, snapshotFrames.length - 1)];
                      // db 帧（GraphSnapshotFrame）带 .graph；直播帧（GraphSnapshot）节点/边直挂
                      return 'graph' in frame
                        ? frame.graph
                        : { nodes: frame.nodes, edges: frame.edges };
                    })()
                  : undefined
              }
            />
          </div>
        </div>
      </div>

      {/* 图时间演化滑杆——多帧 db 导入时自管显示/隐藏 */}
      <GraphTimeScrubber />

      {/* 实时告警流——态势页的「实时」视觉证据（最新告警实时插入，点击聚焦） */}
      <div style={{ position: 'absolute', right: 16, top: 16, zIndex: 20 }}>
        <LiveAlertFeed />
      </div>

      {/* 原始事件实时滚动——直播自动打开，可手动开合 */}
      {showEventTail && (
        <div
          id="live-event-tail-panel"
          style={{
            position: 'absolute',
            left: 16,
            right: 200,
            bottom: 44,
            height: 200,
            zIndex: 20,
          }}
        >
          <LiveEventTail />
        </div>
      )}
      <button
        type="button"
        data-testid="event-tail-toggle"
        aria-expanded={showEventTail}
        aria-controls="live-event-tail-panel"
        onClick={() => setShowEventTail((v) => !v)}
        style={{
          position: 'absolute',
          bottom: 12,
          left: 16,
          zIndex: 20,
          fontSize: 9,
          letterSpacing: '0.05em',
          textTransform: 'uppercase',
          color: showEventTail ? '#4ade80' : 'rgba(255,255,255,0.5)',
          background: showEventTail ? 'rgba(74,222,128,0.1)' : 'rgba(255,255,255,0.05)',
          border: `1px solid ${showEventTail ? 'rgba(74,222,128,0.3)' : 'rgba(255,255,255,0.12)'}`,
          borderRadius: 999,
          padding: '3px 10px',
          cursor: 'pointer',
        }}
      >
        原始事件流
      </button>

      {/* Node detail panel */}
      {selectedNode && (
        <NodeDetailPanel
          nodeId={selectedNode.id}
          nodeType={selectedNode.node_type}
          agentId={selectedNode.agent_id}
          desc={selectedNode.desc}
          rawNodes={selectedNode.rawNodes}
          onClose={() => setSelectedNode(null)}
        />
      )}

      {paletteOpen ? <CommandPalette onClose={() => setPaletteOpen(false)} /> : null}
    </section>
  );
}
