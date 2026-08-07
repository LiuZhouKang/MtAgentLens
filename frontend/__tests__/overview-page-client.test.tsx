// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import OverviewPageClient from '@/components/overview/overview-page-client';
import { useAppStore } from '@/lib/store';
import { demoAlerts, demoGraph } from '@/lib/demo-data';


function createWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
        gcTime: 0,
      },
    },
  });

  return function Wrapper({ children }: { children: React.ReactNode }) {
    return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
  };
}

describe('OverviewPageClient', () => {
  beforeEach(() => {
    useAppStore.setState({
      activeRunId: null,
      alerts: demoAlerts,
      graph: demoGraph,
    });
  });

  afterEach(() => {
    cleanup();
  });

  it('renders the overview dashboard with KPI cards and the responsibility network', () => {
    render(<OverviewPageClient />, { wrapper: createWrapper() });

    // KPI strip
    expect(screen.getByText('Active Agents')).toBeDefined();
    expect(screen.getByText('Critical Alerts')).toBeDefined();
    // Responsibility network stage
    expect(screen.getByText('实时责任网络')).toBeDefined();
    expect(screen.getByText('Collector Ready')).toBeDefined();
    // Rail panels
    expect(screen.getByText('集群压力')).toBeDefined();
    expect(screen.getByText('责任告警')).toBeDefined();
    expect(screen.getByText('事件流')).toBeDefined();
  });

  it('shows the lead cluster focus panel', () => {
    render(<OverviewPageClient />, { wrapper: createWrapper() });

    expect(screen.getByText('Lead Cluster')).toBeDefined();
  });

  it('shows the full-page loader when there is no data yet', () => {
    useAppStore.setState({ alerts: [], graph: { nodes: [], edges: [] } });
    render(<OverviewPageClient />, { wrapper: createWrapper() });

    expect(screen.getAllByText('MtAgentLens').length).toBeGreaterThan(0);
  });
});
