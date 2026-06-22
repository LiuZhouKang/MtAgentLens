'use client';

import { useEffect, useRef, useCallback } from 'react';
import { HandoffDashboardShell } from '@/components/ui/handoff-dashboard-shell';
import { useHandoffStore } from '@/lib/handoff-store';

export default function HandoffPage() {
  const store = useHandoffStore();
  const initialized = useRef(false);

  // ── Initialize: try API, fall back to demo data ──
  useEffect(() => {
    if (initialized.current) return;
    initialized.current = true;
    store.fetchFromApi();
  }, []);

  // ── Connect SSE when backend is available ──
  useEffect(() => {
    if (!store.useBackend) return;

    let es: EventSource | null = null;
    let retries = 0;

    function connect() {
      es = new EventSource('/api/events');

      es.onmessage = (event) => {
        try {
          const envelope = JSON.parse(event.data);
          if (envelope.stream_type === 'handoff_observability' ||
              envelope.stream_type === 'delegation_tree') {
            store.handleSseEvent(envelope);
            retries = 0;
          }
        } catch {
          // Skip malformed events
        }
      };

      es.onerror = () => {
        es?.close();
        if (retries >= 5) return; // Stop reconnecting after 5 retries
        retries++;
        setTimeout(connect, Math.min(Math.pow(2, retries) * 1000, 15000));
      };
    }

    connect();
    return () => es?.close();
  }, [store.useBackend]);

  const handleSelectNode = useCallback((nodeId: string) => {
    store.selectNode(nodeId);
  }, []);

  // ── Render ──
  return (
    <div
      style={{
        width: '100vw',
        height: '100vh',
        overflow: 'hidden',
        background: '#0a0a1a',
        color: '#e8e8ed',
        fontFamily: 'Inter, system-ui, sans-serif',
      }}
    >
      {/* Back button */}
      <div
        style={{
          position: 'absolute',
          top: 16,
          left: 16,
          zIndex: 40,
        }}
      >
        <a
          href="/"
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: 6,
            padding: '6px 14px',
            borderRadius: 8,
            fontSize: 13,
            fontWeight: 500,
            textDecoration: 'none',
            color: 'rgba(255,255,255,0.5)',
            background: 'rgba(255,255,255,0.04)',
            border: '1px solid rgba(255,255,255,0.08)',
            transition: 'all 0.2s',
          }}
          onMouseOver={(e) => {
            e.currentTarget.style.background = 'rgba(255,255,255,0.08)';
            e.currentTarget.style.color = 'rgba(255,255,255,0.8)';
          }}
          onMouseOut={(e) => {
            e.currentTarget.style.background = 'rgba(255,255,255,0.04)';
            e.currentTarget.style.color = 'rgba(255,255,255,0.5)';
          }}
        >
          ← Back to Dashboard
        </a>
      </div>

      {/* Connection status */}
      <div
        style={{
          position: 'absolute',
          top: 16,
          right: 16,
          zIndex: 40,
          display: 'flex',
          alignItems: 'center',
          gap: 6,
          padding: '4px 10px',
          borderRadius: 6,
          fontSize: 11,
          background: store.useBackend
            ? 'rgba(34,197,94,0.1)'
            : 'rgba(234,179,8,0.1)',
          border: `1px solid ${
            store.useBackend
              ? 'rgba(34,197,94,0.2)'
              : 'rgba(234,179,8,0.2)'
          }`,
          color: store.useBackend ? '#22c55e' : '#eab308',
        }}
      >
        <span
          style={{
            width: 6,
            height: 6,
            borderRadius: '50%',
            background: store.useBackend ? '#22c55e' : '#eab308',
          }}
        />
        {store.useBackend ? 'Backend Connected' : 'Demo Data'}
      </div>

      {/* Loading indicator */}
      {store.loading && (
        <div
          style={{
            position: 'absolute',
            top: 56,
            right: 16,
            zIndex: 40,
            fontSize: 12,
            color: 'rgba(255,255,255,0.4)',
          }}
        >
          Loading...
        </div>
      )}

      {/* Main dashboard */}
      <HandoffDashboardShell
        delegationTree={store.delegationTree}
        metrics={store.metrics}
        recentEvents={store.recentEvents}
        selectedNodeId={store.selectedNodeId}
        dependencyEdges={store.dependencyEdges}
        onSelectNode={handleSelectNode}
      />
    </div>
  );
}
