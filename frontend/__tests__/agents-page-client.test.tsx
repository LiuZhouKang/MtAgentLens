// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import AgentsPageClient from '@/components/agent/agents-page-client';
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

describe('AgentsPageClient', () => {
  beforeEach(() => {
    useAppStore.setState({
      activeRunId: null,
      alerts: demoAlerts,
      graph: demoGraph,
    });
    window.history.replaceState({}, '', '/agents');
  });

  afterEach(() => {
    cleanup();
  });

  it('defaults to a selected agent and exposes cluster and investigation hops', () => {
    render(<AgentsPageClient />, { wrapper: createWrapper() });

    expect(screen.getByRole('heading', { name: 'agent-security' })).toBeDefined();
    expect(screen.getByText('Agent 责任队列')).toBeDefined();
    expect(screen.getByRole('link', { name: '回到所属 Cluster' }).getAttribute('href')).toBe(
      '/clusters#clusterId=agent-security',
    );
    expect(screen.getByRole('link', { name: '进入主调查' }).getAttribute('href')).toBe(
      '/investigations#alertId=demo-alert-1',
    );
  });
});
