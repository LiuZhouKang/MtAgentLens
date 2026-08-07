// @vitest-environment jsdom
import { afterEach, beforeAll, describe, expect, it } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import TracesPageClient from '@/components/traces/traces-page-client';

beforeAll(() => {
  // jsdom 不做布局——virtualizer 用 offsetWidth/offsetHeight 量容器（得到 0）。
  // 与 virtual-list.test.tsx 相同的桩：固定尺寸让责任链虚拟行真正渲染。
  for (const prop of ['offsetHeight', 'offsetWidth', 'clientHeight', 'clientWidth']) {
    Object.defineProperty(HTMLElement.prototype, prop, {
      configurable: true,
      get() {
        return prop.endsWith('Height') ? 300 : 800;
      },
    });
  }
});

afterEach(() => {
  cleanup();
  // 重置 URL hash——测试 3 切换 run 会写 hash，残留会污染下一个测试的初始 run 选择
  if (typeof window !== 'undefined') {
    window.history.replaceState({}, '', window.location.pathname);
  }
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
    // Filter buttons (aria-label "筛选 <kind>" disambiguates from event rows)
    expect(screen.getByRole('button', { name: /筛选 全部/ })).toBeDefined();
    expect(screen.getByRole('button', { name: /筛选 Tool/ })).toBeDefined();
  });

  it('filters timeline events by kind', async () => {
    const user = userEvent.setup();
    render(<TracesPageClient />, { wrapper: createWrapper() });

    await user.click(screen.getByRole('button', { name: /筛选 文件/ }));
    // The responsibility-chain panel still renders with a filtered event count
    expect(screen.getByText(/个事件/)).toBeDefined();

    await user.click(screen.getByRole('button', { name: /筛选 全部/ }));
    expect(screen.getByText(/个事件/)).toBeDefined();
  });

  it('shows live kind counts on every filter button in the enriched demo trace', () => {
    render(<TracesPageClient />, { wrapper: createWrapper() });

    // 全部 button carries the total span count (demo timeline is non-empty)
    const allBtn = screen.getByRole('button', { name: /筛选 全部/ });
    expect(allBtn.textContent).toMatch(/全部\s*[1-9]/);

    // every responsibility kind is populated by the enriched demo data
    // (tool + network added; llm comes from the observer verdicts)
    for (const label of ['Agent', 'LLM', 'Tool', '文件', '网络', '边界']) {
      const btn = screen.getByRole('button', { name: new RegExp(`筛选 ${label}\\s*[1-9]`) });
      expect(btn).toBeDefined();
      expect(btn.hasAttribute('disabled')).toBe(false);
    }

    // 判定 stays empty in the demo story — the disabled empty-state UI still shows it as 0
    const verdictBtn = screen.getByRole('button', { name: /筛选 判定 0/ });
    expect(verdictBtn).toBeDefined();
    expect(verdictBtn.hasAttribute('disabled')).toBe(true);
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

  it('renders agent identity (role badge + avatar) in the responsibility chain', () => {
    render(<TracesPageClient />, { wrapper: createWrapper() });

    // role badge derived from the alert's claimed_role (may appear in several places)
    expect(screen.getAllByText('security-auditor').length).toBeGreaterThan(0);
    // at least one agent avatar (aria-label = the agent id)
    expect(screen.getAllByLabelText(/agent-/).length).toBeGreaterThan(0);
  });

  it('renders per-actor kind distribution badges in group headers', () => {
    render(<TracesPageClient />, { wrapper: createWrapper() });

    // agent-security group spans agent + file + boundary events
    expect(screen.getByTitle(/agent-security · 文件 × 1/)).toBeDefined();
    expect(screen.getByTitle(/agent-security · 边界 × 1/)).toBeDefined();
    // agent-frontend group only has agent events — no resource kind badges
    expect(screen.queryByTitle(/agent-frontend · 文件/)).toBeNull();
  });
});
