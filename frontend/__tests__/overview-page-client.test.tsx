// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import OverviewPageClient from '@/components/overview/overview-page-client';
import { useAppStore } from '@/lib/store';
import { demoAlerts, demoGraph } from '@/lib/demo-data';

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

  it('renders the runtime observatory shell with probe matrix, causal stage, and responsibility graph', () => {
    render(<OverviewPageClient />, { wrapper: createWrapper() });

    expect(screen.getByRole('heading', { name: 'Probe Matrix' })).toBeDefined();
    expect(screen.getByRole('heading', { name: 'Runtime Causal Stage' })).toBeDefined();
    expect(screen.getByRole('heading', { name: 'Responsibility Graph' })).toBeDefined();
    expect(screen.getByRole('heading', { name: 'Priority Investigation Queue' })).toBeDefined();
  });
});
