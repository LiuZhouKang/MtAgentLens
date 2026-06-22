'use client';

import { useAppStore } from '@/lib/store';
import AlertCard from './alert-card';
import { useEffect, useState, useCallback } from 'react';
import { demoGraph, demoAlerts } from '@/lib/demo-data';
import { Zap } from 'lucide-react';

export default function SidePanel() {
  const store = useAppStore();
  const [onboardingStep, setOnboardingStep] = useState(-1);

  const loadDemo = useCallback(() => {
    store.setGraph(demoGraph.nodes, demoGraph.edges);
    store.setAlerts(demoAlerts);
    store.setAppState('replay');
  }, [store]);

  useEffect(() => {
    if (typeof window === 'undefined') return;
    if (localStorage.getItem('onboarding_complete')) return;
    const steps = [
      'Drag to rotate the globe, scroll to zoom',
      'Click nodes for details, double-click to expand',
      'Cmd+K opens the command palette',
      'Run agentsight record to start monitoring',
    ];
    let i = 0;
    const timer = setInterval(() => {
      if (i >= steps.length) {
        clearInterval(timer);
        localStorage.setItem('onboarding_complete', 'true');
        setOnboardingStep(-1);
        return;
      }
      setOnboardingStep(i);
      i++;
    }, 4000);
    return () => clearInterval(timer);
  }, []);

  const panelStyle: React.CSSProperties = {
    position: 'absolute',
    top: 0,
    right: 0,
    bottom: 0,
    width: 380,
    zIndex: 20,
    overflowY: 'auto',
    pointerEvents: 'auto',
    background: 'rgba(6, 8, 18, 0.92)',
    borderLeft: '1px solid rgba(99, 102, 241, 0.12)',
  };

  const demoBtnStyle: React.CSSProperties = {
    width: '100%',
    marginBottom: 16,
    padding: '12px 16px',
    borderRadius: 12,
    fontSize: 14,
    fontWeight: 500,
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 8,
    cursor: 'pointer',
    background: 'rgba(99, 102, 241, 0.08)',
    border: '1px solid rgba(99, 102, 241, 0.2)',
    color: '#a5b4fc',
    transition: 'background 0.2s, border-color 0.2s',
  };

  const sectionLabel: React.CSSProperties = {
    fontSize: 11,
    fontWeight: 500,
    textTransform: 'uppercase',
    letterSpacing: '0.08em',
    color: '#6366f1',
    marginBottom: 12,
  };

  const emptyStyle: React.CSSProperties = {
    fontSize: 13,
    color: 'rgba(255,255,255,0.25)',
    textAlign: 'center',
    paddingTop: 32,
    paddingBottom: 32,
  };

  return (
    <>
      <div style={panelStyle}>
        <div style={{ padding: 16 }}>
          {store.appState === 'idle' && (
            <button
              onClick={loadDemo}
              style={demoBtnStyle}
              onMouseEnter={(e) => {
                e.currentTarget.style.background = 'rgba(99, 102, 241, 0.14)';
                e.currentTarget.style.borderColor = 'rgba(99, 102, 241, 0.35)';
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.background = 'rgba(99, 102, 241, 0.08)';
                e.currentTarget.style.borderColor = 'rgba(99, 102, 241, 0.2)';
              }}
            >
              <Zap size={15} color="#a5b4fc" />
              <span>Load Demo Data</span>
            </button>
          )}

          <h3 style={sectionLabel}>
            Alerts ({store.alerts.length})
          </h3>

          {store.alerts.length === 0 && (
            <p style={emptyStyle}>
              {store.appState === 'idle'
                ? 'Select a run to begin monitoring'
                : 'No alerts detected'}
            </p>
          )}

          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {store.alerts.map((alert) => (
              <AlertCard key={alert.alert_id} alert={alert} />
            ))}
          </div>
        </div>
      </div>

      {onboardingStep >= 0 && (
        <div
          style={{
            position: 'absolute',
            bottom: 96,
            left: '50%',
            transform: 'translateX(-50%)',
            zIndex: 50,
            padding: '12px 24px',
            borderRadius: 12,
            fontSize: 14,
            color: 'rgba(255,255,255,0.85)',
            background: 'rgba(14, 18, 36, 0.94)',
            border: '1px solid rgba(99, 102, 241, 0.15)',
          }}
        >
          {[
            'Drag to rotate the globe, scroll to zoom',
            'Click nodes for details, double-click to expand',
            'Cmd+K opens the command palette',
            'Run agentsight record to start monitoring',
          ][onboardingStep]}
        </div>
      )}
    </>
  );
}
