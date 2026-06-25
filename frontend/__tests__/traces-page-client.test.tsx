// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import TracesPageClient from '@/components/traces/traces-page-client';

vi.mock('@/components/ui/theme-toggle', () => ({
  default: function MockThemeToggle() {
    return <button type="button">主题切换</button>;
  },
}));

afterEach(() => {
  cleanup();
});

describe('TracesPageClient', () => {
  it('renders the trace control strip and risk dashboard for the active run', () => {
    render(<TracesPageClient />);

    expect(screen.getByText('Trace Control Strip')).toBeDefined();
    expect(screen.getByText('Signal Density')).toBeDefined();
    expect(screen.getByText('Live Trace Feed')).toBeDefined();
    expect(screen.getByText('Risk Verdict Board')).toBeDefined();
    expect(screen.getByText('Key Risk Drivers')).toBeDefined();
  });

  it('lets the operator filter timeline events and inspect a selected step', async () => {
    const user = userEvent.setup();

    render(<TracesPageClient />);

    expect(screen.getByRole('heading', { name: 'Agent "security-auditor" 尝试权限提升' })).toBeDefined();
    expect(screen.getByRole('heading', { name: 'security-auditor 读取 /etc/shadow' })).toBeDefined();

    await user.click(screen.getByRole('button', { name: 'Boundary 1' }));

    expect(screen.getByRole('heading', { name: '策略边界突破' })).toBeDefined();
    expect(screen.queryByText('frontend-expert 委派任务给 security-auditor')).toBeNull();

    await user.click(screen.getByRole('button', { name: '策略边界突破' }));

    expect(screen.getByText('Selected Event')).toBeDefined();
    expect(screen.getAllByText('boundary-system').length).toBeGreaterThan(0);
    expect(screen.getByText('unsafe.shadow')).toBeDefined();
    expect(screen.getByText('privilege_escalation')).toBeDefined();
  });

  it('switches active run and refreshes the verdict summary', async () => {
    const user = userEvent.setup();

    render(<TracesPageClient />);

    await user.click(screen.getByRole('button', { name: /Agent "frontend-expert" 正常操作/i }));

    expect(screen.getByRole('heading', { name: 'Agent "frontend-expert" 正常操作' })).toBeDefined();
    expect(screen.getByText('Info Verdict')).toBeDefined();
    expect(screen.getByText('当前 trace 没有边界级风险，适合继续查看工具参数和上下文流向。')).toBeDefined();
  });
});
