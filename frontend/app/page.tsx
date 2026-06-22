'use client';

import { useEffect, useRef, useState, useCallback, useMemo } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { useAppStore } from '@/lib/store';
import { connectSSE } from '@/lib/sse';
import { demoGraph, demoAlerts } from '@/lib/demo-data';
import Topbar from '@/components/ui/topbar';
import CommandPalette from '@/components/ui/command-palette';
import TimelineScrubber from '@/components/ui/timeline-scrubber';
import AlertCard from '@/components/ui/alert-card';
import { Zap, X, Play, ArrowLeft } from 'lucide-react';

const queryClient = new QueryClient();

function AppShell() {
  const store = useAppStore();
  const appState = store.appState;
  const alerts = store.alerts;
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [panelLevel, setPanelLevel] = useState(0); // 0=globe, 1=detail, 2=traceback
  const [selectedAlertIdx, setSelectedAlertIdx] = useState<number | null>(null);
  const iframeRef = useRef<HTMLIFrameElement>(null);

  // ── Engine init + SSE ──
  useEffect(() => {
    const timer = setTimeout(() => store.setAppState('idle'), 500);
    return () => clearTimeout(timer);
  }, []);

  const sseRef = useRef<{ close: () => void } | null>(null);
  useEffect(() => {
    if (appState === 'live') {
      sseRef.current = connectSSE(
        '/api/alerts/stream',
        (alert) => store.onAlertReceived(alert),
        (err) => store.setError(err)
      );
    }
    return () => sseRef.current?.close();
  }, [appState]);

  // ── Listen for node clicks from globe iframe ──
  useEffect(() => {
    const handler = (event: MessageEvent) => {
      if (!event.data || !event.data.type) return;
      const msg = event.data;

      // Iframe signals it's ready — re-send current graph if we have one
      if (msg.type === 'agentsight:ready') {
        if (store.graph.nodes.length > 0) {
          const iframe = iframeRef.current;
          if (iframe?.contentWindow) {
            iframe.contentWindow.postMessage({
              type: 'agentsight:loadGraph',
              nodes: store.graph.nodes,
              edges: store.graph.edges,
            }, '*');
            iframe.contentWindow.postMessage({
              type: 'agentsight:setPhase',
              phase: appState === 'live' ? 'provisional' : 'authoritative',
            }, '*');
          }
        }
        return;
      }

      if (msg.type !== 'agentsight:nodeClicked') return;
      // Find alert related to this node and open detail
      const nodeId = msg.nodeId;
      const idx = alerts.findIndex(a =>
        a.primary_agent?.agent_id === nodeId ||
        a.involved_agents?.some(ia => ia.agent_id === nodeId)
      );
      if (idx >= 0) { setSelectedAlertIdx(idx); setPanelLevel(1); }
    };
    window.addEventListener('message', handler);
    return () => window.removeEventListener('message', handler);
  }, [alerts, appState, store.graph]);

  // ── Keyboard ──
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'k') { e.preventDefault(); setPaletteOpen(v => !v); }
      if (e.key === 'Escape') { setPanelLevel(0); setSelectedAlertIdx(null); setPaletteOpen(false); }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, []);

  // ── Post graph to iframe ──
  const postGraph = useCallback((nodes: any[], edges: any[]) => {
    const iframe = iframeRef.current;
    if (!iframe?.contentWindow) return;
    iframe.contentWindow.postMessage({
      type: 'agentsight:loadGraph',
      nodes, edges,
    }, '*');
    iframe.contentWindow.postMessage({
      type: 'agentsight:setPhase',
      phase: appState === 'live' ? 'provisional' : 'authoritative',
    }, '*');
  }, [appState]);

  // ── Demo ──
  const loadDemo = useCallback(() => {
    store.setGraph(demoGraph.nodes, demoGraph.edges);
    store.setAlerts(demoAlerts);
    store.setAppState('replay');
    postGraph(demoGraph.nodes, demoGraph.edges);
    // Auto-open first alert in detail panel
    setTimeout(() => { setSelectedAlertIdx(0); setPanelLevel(1); }, 300);
  }, [store, postGraph]);

  // ── Open detail panel ──
  const openDetail = useCallback((idx: number) => {
    setSelectedAlertIdx(idx);
    setPanelLevel(1);
  }, []);

  const openTraceback = useCallback(() => {
    const alert = alerts[selectedAlertIdx ?? 0];
    if (!alert) return;
    setPanelLevel(2);
    // Post traceback path to globe
    const tracebackEdges = alert.timeline?.steps
      ?.filter((s: any) => s.evidence_ref)
      ?.map((s: any) => s.evidence_ref) ?? [];
    if (iframeRef.current?.contentWindow) {
      iframeRef.current.contentWindow.postMessage({
        type: 'agentsight:highlightPath',
        edgeIds: alert.engine?.pattern_verdicts?.length
          ? demoGraph.edges.filter(e => e.edge_type === 'PolicyBoundaryCrossing').map(e => e.edge_id)
          : [],
      }, '*');
    }
  }, [alerts, selectedAlertIdx]);

  const closePanel = useCallback(() => {
    setPanelLevel(0);
    setSelectedAlertIdx(null);
    if (iframeRef.current?.contentWindow) {
      iframeRef.current.contentWindow.postMessage({ type: 'agentsight:clearHighlights' }, '*');
    }
  }, []);

  // ── Iframe style ──
  const iframeStyle = useMemo<React.CSSProperties>(() => {
    const dur = panelLevel === 0 ? '0.3s' : '0.6s'; // faster close, slower open
    const base: React.CSSProperties = {
      position: 'absolute', top: 0, left: 0, width: '100%', height: '100%', border: 'none',
      transition: `transform ${dur} cubic-bezier(0.16, 1, 0.3, 1), opacity ${dur} cubic-bezier(0.16, 1, 0.3, 1)`,
      pointerEvents: panelLevel > 0 ? 'none' : 'auto',
    };
    if (panelLevel === 0) {
      base.transform = 'scale(1) translateY(0)';
      base.opacity = 1;
    } else if (panelLevel === 1) {
      base.transform = 'scale(0.4) translateY(-30%)';
      base.opacity = 0.3;
    } else {
      base.transform = 'scale(0.25) translateY(-35%)';
      base.opacity = 0.15;
    }
    return base;
  }, [panelLevel]);

  const selectedAlert = selectedAlertIdx != null ? alerts[selectedAlertIdx] : null;

  return (
    <div style={{ width: '100vw', height: '100vh', overflow: 'hidden', background: '#000', position: 'relative' }}>
      {/* ── Globe iframe ── */}
      <iframe ref={iframeRef} src="/globe/index.html" style={iframeStyle} title="AgentSight Globe" />

      {/* ── ERROR state ── */}
      {appState === 'error' && (
        <div style={{
          position: 'absolute', top: 0, left: 0, right: 0, zIndex: 50,
          padding: '10px 16px', background: 'rgba(248,113,113,0.15)',
          borderBottom: '1px solid rgba(248,113,113,0.3)',
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        }}>
          <span style={{ fontSize: 13, color: '#fca5a5' }}>
            {store.errorMessage || 'Connection to AgentSight backend failed'}
          </span>
          <button onClick={() => { store.setError(null); store.setAppState('idle'); }}
            style={{ padding: '4px 12px', borderRadius: 6, fontSize: 12,
              background: 'rgba(248,113,113,0.2)', border: '1px solid rgba(248,113,113,0.3)',
              color: '#fca5a5', cursor: 'pointer' }}>
            Retry
          </button>
        </div>
      )}

      {/* ── Topbar (always visible when not init) ── */}
      {appState !== 'init' && (
        <>
          <Topbar />
        </>
      )}

      {/* ── Level 0: Alert Feed (floating top-right) ── */}
      {appState !== 'init' && panelLevel === 0 && (
        <div style={{
          position: 'absolute', top: 56, right: 16, width: 340, maxHeight: '70vh',
          overflowY: 'auto', zIndex: 20, pointerEvents: 'auto',
        }}>
          {appState === 'idle' && (
            <button onClick={loadDemo} style={{
              width: '100%', marginBottom: 12, padding: '12px 16px', borderRadius: 12,
              fontSize: 14, fontWeight: 500, display: 'flex', alignItems: 'center',
              justifyContent: 'center', gap: 8, cursor: 'pointer',
              background: 'rgba(99,102,241,0.1)', border: '1px solid rgba(99,102,241,0.25)',
              color: '#a5b4fc', transition: 'background 0.2s',
            }}>
              <Zap size={15} color="#a5b4fc" /> Load Demo Data
            </button>
          )}
          {alerts.length === 0 && appState !== 'idle' && (
            <p style={{ fontSize: 13, color: 'rgba(255,255,255,0.25)', textAlign: 'center', padding: 32 }}>
              No alerts detected
            </p>
          )}
          {alerts.length === 0 && appState === 'idle' && (
            <p style={{ fontSize: 13, color: 'rgba(255,255,255,0.25)', textAlign: 'center', padding: 32 }}>
              Select a run to begin monitoring
            </p>
          )}
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {alerts.map((alert, i) => (
              <div key={alert.alert_id} onClick={() => openDetail(i)}
                style={{
                  padding: '10px 12px', borderRadius: 8, cursor: 'pointer',
                  background: 'rgba(12,14,28,0.85)', border: '1px solid rgba(255,255,255,0.06)',
                  transition: 'border-color 0.2s',
                }}
                onMouseEnter={e => e.currentTarget.style.borderColor = 'rgba(99,102,241,0.3)'}
                onMouseLeave={e => e.currentTarget.style.borderColor = 'rgba(255,255,255,0.06)'}
              >
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <span style={{
                    width: 6, height: 6, borderRadius: '50%', flexShrink: 0,
                    background: alert.severity === 'Critical' ? '#f87171' : alert.severity === 'Warning' ? '#fbbf24' : '#818cf8',
                  }} />
                  <span style={{ fontSize: 12, fontWeight: 500, color: 'rgba(255,255,255,0.9)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', flex: 1 }}>
                    {alert.title}
                  </span>
                  <span style={{ fontSize: 10, color: 'rgba(255,255,255,0.3)', fontFamily: 'monospace' }}>
                    {(alert.timestamp_ns / 1e9).toFixed(0)}s
                  </span>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* ── Level 1: Detail Panel ── */}
      {panelLevel === 1 && selectedAlert && (
        <div style={{
          position: 'absolute', bottom: 0, left: '50%', transform: 'translateX(-50%)',
          width: 'min(720px, 90vw)', maxHeight: '70vh', overflowY: 'auto', zIndex: 30,
          background: 'rgba(8,10,22,0.95)', borderRadius: '16px 16px 0 0',
          border: '1px solid rgba(99,102,241,0.15)', borderBottom: 'none',
          padding: '24px 28px',
          animation: 'slideUp 0.4s cubic-bezier(0.16, 1, 0.3, 1)',
        }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
            <button onClick={closePanel} style={{ background: 'none', border: 'none', color: 'rgba(255,255,255,0.4)', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 4 }}>
              <X size={16} /> Close
            </button>
            <button onClick={openTraceback} style={{
              padding: '6px 14px', borderRadius: 8, fontSize: 12, fontWeight: 500,
              background: 'rgba(99,102,241,0.12)', border: '1px solid rgba(99,102,241,0.25)',
              color: '#a5b4fc', cursor: 'pointer',
            }}>
              Trace Root Cause →
            </button>
          </div>
          <AlertCard alert={selectedAlert} />
        </div>
      )}

      {/* ── Level 2: Traceback Panel ── */}
      {panelLevel === 2 && selectedAlert && (
        <div style={{
          position: 'absolute', bottom: 0, left: '50%', transform: 'translateX(-50%)',
          width: 'min(720px, 90vw)', maxHeight: '75vh', overflowY: 'auto', zIndex: 30,
          background: 'rgba(8,10,22,0.95)', borderRadius: '16px 16px 0 0',
          border: '1px solid rgba(99,102,241,0.15)', borderBottom: 'none',
          padding: '24px 28px',
          animation: 'slideUp 0.4s cubic-bezier(0.16, 1, 0.3, 1)',
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 16 }}>
            <button onClick={() => setPanelLevel(1)} style={{ background: 'none', border: 'none', color: 'rgba(255,255,255,0.4)', cursor: 'pointer' }}>
              <ArrowLeft size={16} />
            </button>
            <span style={{ fontSize: 13, fontWeight: 600, color: '#a5b4fc' }}>Traceback Analysis</span>
            <button onClick={closePanel} style={{ background: 'none', border: 'none', color: 'rgba(255,255,255,0.4)', cursor: 'pointer', marginLeft: 'auto' }}>
              <X size={16} />
            </button>
          </div>

          {/* Root cause */}
          <div style={{ marginBottom: 16, padding: '12px 16px', borderRadius: 8, background: 'rgba(99,102,241,0.06)', border: '1px solid rgba(99,102,241,0.12)' }}>
            <span style={{ fontSize: 11, color: '#6366f1', fontWeight: 500, textTransform: 'uppercase', letterSpacing: '0.05em' }}>Root Cause</span>
            <p style={{ margin: '4px 0 0', fontSize: 13, color: 'rgba(255,255,255,0.7)' }}>
              {selectedAlert.action_hint || selectedAlert.reasoning || 'Analyze the causal chain below'}
            </p>
          </div>

          {/* Timeline steps */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: 0, borderLeft: '2px solid rgba(99,102,241,0.2)', paddingLeft: 16 }}>
            {(selectedAlert.timeline?.steps || []).map((step: any, i: number) => (
              <div key={i} style={{ position: 'relative', paddingBottom: i < (selectedAlert.timeline?.steps?.length || 0) - 1 ? 20 : 0 }}>
                <div style={{
                  position: 'absolute', left: -22, top: 4, width: 10, height: 10, borderRadius: '50%',
                  background: step.operation === 'FileRead' ? '#60a5fa' : step.operation === 'Delegation' ? '#a78bfa' : '#f87171',
                  border: '2px solid rgba(8,10,22,0.95)',
                }} />
                <span style={{ fontSize: 11, fontFamily: 'monospace', color: 'rgba(255,255,255,0.3)' }}>
                  T+{(step.timestamp_ns / 1e9).toFixed(1)}s
                </span>
                <p style={{ margin: '2px 0 0', fontSize: 13, color: 'rgba(255,255,255,0.8)' }}>
                  {step.description}
                </p>
                {step.resource && (
                  <code style={{ fontSize: 11, color: 'rgba(255,255,255,0.35)' }}>{step.resource}</code>
                )}
                {step.source_event_id && (
                  <code style={{ fontSize: 10, color: 'rgba(255,255,255,0.2)', marginLeft: 8 }}>
                    [{step.source_event_id.slice(0, 8)}]
                  </code>
                )}
              </div>
            ))}
          </div>

          {/* Controls */}
          <div style={{ marginTop: 16, display: 'flex', alignItems: 'center', gap: 12 }}>
            <button style={{
              padding: '6px 14px', borderRadius: 8, fontSize: 12, fontWeight: 500,
              background: 'rgba(99,102,241,0.12)', border: '1px solid rgba(99,102,241,0.25)',
              color: '#a5b4fc', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 4,
            }}>
              <Play size={14} /> Play
            </button>
            <div style={{ flex: 1, height: 2, background: 'rgba(255,255,255,0.06)', borderRadius: 1, position: 'relative' }}>
              <div style={{ position: 'absolute', left: '30%', top: -4, width: 10, height: 10, borderRadius: '50%', background: '#6366f1' }} />
            </div>
          </div>
        </div>
      )}

      {/* ── INIT overlay ── */}
      {appState === 'init' && (
        <div style={{ position: 'absolute', inset: 0, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', pointerEvents: 'none', zIndex: 10 }}>
          <h1 style={{ fontSize: 46, fontWeight: 600, letterSpacing: '0.15em', color: 'rgba(255,255,255,0.5)', userSelect: 'none', margin: 0 }}>
            AGENTSIGHT
          </h1>
          <p style={{ marginTop: 12, fontSize: 14, letterSpacing: '0.05em', color: 'rgba(255,255,255,0.35)' }}>
            eBPF-Powered LLM Agent Observability
          </p>
        </div>
      )}

      {/* ── Command palette ── */}
      {paletteOpen && <CommandPalette onClose={() => setPaletteOpen(false)} />}

      {/* ── Timeline (replay only) ── */}
      {appState === 'replay' && panelLevel > 0 && <TimelineScrubber />}

      {/* ── Keyframe animation for slideUp ── */}
      <style>{`
        @keyframes slideUp {
          from { transform: translateX(-50%) translateY(30px); opacity: 0; }
          to { transform: translateX(-50%) translateY(0); opacity: 1; }
        }
      `}</style>
    </div>
  );
}

export default function Home() {
  return (
    <QueryClientProvider client={queryClient}>
      <AppShell />
    </QueryClientProvider>
  );
}
