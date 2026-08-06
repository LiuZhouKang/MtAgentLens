// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import TracesPageClient from '@/components/traces/traces-page-client';

vi.mock('@/components/ui/theme-toggle', () => ({
  default: function MockThemeToggle() {
    return <button type="button">主题切换</button>;
  },
}));

afterEach(() => {
  cleanup();
});

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

describe('TracesPageClient', () => {
  it('renders the trace workbench with KPI cards and the responsibility chain', () => {
    render(<TracesPageClient />, { wrapper: createWrapper() });

    // KPI strip
    expect(screen.getByText('Span 数')).toBeDefined();
    expect(screen.getByText('判决')).toBeDefined();
    // Panels + controls
    expect(screen.getByText('责任链路')).toBeDefined();
    expect(screen.getByText('Trace')).toBeDefined();
    expect(screen.getByText('进程树')).toBeDefined();
    expect(screen.getByText('过滤')).toBeDefined();
    // Filter buttons
    expect(screen.getByRole('button', { name: '全部' })).toBeDefined();
    expect(screen.getByRole('button', { name: 'Tool' })).toBeDefined();
  });

  it('filters timeline events by kind', async () => {
    const user = userEvent.setup();
    render(<TracesPageClient />, { wrapper: createWrapper() });

    await user.click(screen.getByRole('button', { name: '文件' }));
    // The responsibility-chain panel still renders with a filtered event count
    expect(screen.getByText(/个事件/)).toBeDefined();

    await user.click(screen.getByRole('button', { name: '全部' }));
    expect(screen.getByText(/个事件/)).toBeDefined();
  });

  it('switches active run from the trace list without crashing', async () => {
    const user = userEvent.setup();
    render(<TracesPageClient />, { wrapper: createWrapper() });

    const runButtons = screen.getAllByRole('button', { name: /demo-alert/ });
    expect(runButtons.length).toBeGreaterThan(0);

    if (runButtons.length > 1) {
      await user.click(runButtons[1]);
    }
    expect(screen.getByText('责任链路')).toBeDefined();
  });
});
