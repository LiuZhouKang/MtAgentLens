// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import InvestigationContextBar from '@/components/system/investigation-context-bar';
import { useAppStore } from '@/lib/store';
import { DEFAULT_INVESTIGATION_CONTEXT } from '@/lib/investigation-context';

beforeEach(() => {
  useAppStore.setState({
    investigationContext: DEFAULT_INVESTIGATION_CONTEXT,
  });
});

afterEach(() => {
  cleanup();
});

describe('InvestigationContextBar', () => {
  it('renders the live overview state by default', () => {
    render(<InvestigationContextBar />);

    expect(screen.getByText('LIVE')).toBeDefined();
    expect(screen.getByText('Live responsibility field')).toBeDefined();
    expect(screen.getByText('overview')).toBeDefined();
  });

  it('renders incident, agent, evidence and path anchors when focused', () => {
    useAppStore.getState().setInvestigationContext({
      activeIncidentId: 'demo-alert-1',
      activeAgentId: 'agent-security',
      activeEvidenceId: 'ev-1',
      highlightedPathEdgeIds: ['edge-1', 'edge-2'],
      graphFocus: {
        reason: 'incident',
        label: 'Agent attempted privilege escalation',
        nodeId: 'agent-security',
        edgeId: 'edge-1',
        agentId: 'agent-security',
      },
    });

    render(<InvestigationContextBar />);

    expect(screen.getByText('Agent attempted privilege escalation')).toBeDefined();
    expect(screen.getByRole('link', { name: /demo-alert-1/ }).getAttribute('href')).toBe(
      '/investigations#alertId=demo-alert-1',
    );
    expect(screen.getByRole('link', { name: /agent-security/ }).getAttribute('href')).toBe(
      '/overview#agentId=agent-security',
    );
    expect(screen.getByText('2 path edges')).toBeDefined();
    expect(screen.getByText('ev-1')).toBeDefined();
  });
});
