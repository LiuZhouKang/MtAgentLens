'use client';

import dynamic from 'next/dynamic';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useAppStore } from '@/lib/store';
import { buildInvestigationModel } from '@/lib/investigation';
import { useAlertModels } from '@/lib/use-alert-models';
import type { ResponsibilityNode } from '@/lib/types';
import LoadingScreen from '@/components/ui/loading-screen';
import NodeDetailPanel from './node-detail-panel';

const MtAgentLensGraphStage = dynamic(
  () => import('@/components/ui/mtagentlens-graph-stage'),
  { ssr: false },
);

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

  const [paletteOpen, setPaletteOpen] = useState(false);
  const [selectedAlertId, setSelectedAlertId] = useState<string | null>(null);
  const [selectedNode, setSelectedNode] = useState<SelectedNodeData | null>(null);

  const handleNodeClick = useCallback((info: { id: string; node_type: string; agent_id: string; desc: string }) => {
    const rawNodes = graph.nodes.filter((n) =>
      info.id === n.node_id ||
      (n.node_type === 'PolicyBoundary' && info.desc.includes(n.node_id.slice(0, 8)))
    );
    setSelectedNode({ ...info, rawNodes });
  }, [graph.nodes]);

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

  const selectedAlert = selectedItem?.alert ?? null;
  // Selected alert's model is computed synchronously so the theater renders
  // immediately; the rest of the list populates lazily in idle time.
  const selectedModel =
    selectedItem?.model ??
    (selectedAlert ? buildInvestigationModel(selectedAlert, graph, []) : null);

  if (!selectedAlert || !selectedModel) {
    return (
      <section className="command-center" aria-busy="true" style={{ height: '100%', minHeight: 0, position: 'relative' }}>
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
      <div className="command-theater">
        <div className="command-theater__shell">
          <div className="command-theater__canvas">
            <MtAgentLensGraphStage
              active
              onNodeClick={handleNodeClick}
              onBackgroundClick={handleBackgroundClick}
            />
          </div>
        </div>
      </div>

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
