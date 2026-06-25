// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import ClustersPageClient from '@/components/cluster/clusters-page-client';
import { useAppStore } from '@/lib/store';
import { demoAlerts, demoGraph } from '@/lib/demo-data';

vi.mock('next/navigation', () => ({
  useRouter: () => ({
    replace: vi.fn(),
  }),
  useSearchParams: () => new URLSearchParams(),
}));

vi.mock('@/components/ui/theme-toggle', () => ({
  default: function MockThemeToggle() {
    return <button type="button">主题切换</button>;
  },
}));

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

describe('ClustersPageClient', () => {
  beforeEach(() => {
    useAppStore.setState({
      activeRunId: null,
      alerts: demoAlerts,
      graph: demoGraph,
    });
    window.history.replaceState({}, '', '/clusters');
  });

  afterEach(() => {
    cleanup();
  });

  it('defaults to the highest-pressure cluster and exposes its lead agent hop', () => {
    render(<ClustersPageClient />, { wrapper: createWrapper() });

    expect(screen.getByRole('heading', { name: 'agent-security' })).toBeDefined();
    expect(screen.getAllByText('lead agent: agent-security').length).toBeGreaterThan(0);
    expect(screen.getByRole('link', { name: '打开主 Agent' }).getAttribute('href')).toBe(
      '/agents#agentId=agent-security',
    );
  });

  it('renders the cluster observatory stage and decision deck', () => {
    render(<ClustersPageClient />, { wrapper: createWrapper() });

    expect(screen.getByText('Cluster Control Strip')).toBeDefined();
    expect(screen.getByRole('heading', { name: 'Cluster Watchlist' })).toBeDefined();
    expect(screen.getByText('Cluster Observatory Space')).toBeDefined();
    expect(screen.getByText('Responsibility field')).toBeDefined();
    expect(screen.getByRole('heading', { name: 'Decision Deck' })).toBeDefined();
    expect(screen.getByRole('heading', { name: 'Recommended Next Steps' })).toBeDefined();
    expect(screen.getByRole('link', { name: 'Review handoff lanes' })).toBeDefined();
  });
});
