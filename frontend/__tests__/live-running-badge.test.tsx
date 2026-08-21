// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render } from '@testing-library/react';
import { LiveRunningBadge } from '@/components/live/live-running-badge';
import { useAppStore } from '@/lib/store';

vi.mock('@/lib/api', () => ({
  api: { getRuns: vi.fn().mockResolvedValue([]) },
}));

describe('LiveRunningBadge —— 实时运行徽章', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useAppStore.setState({ appState: 'replay' });
  });

  afterEach(() => {
    cleanup();
  });

  it('live 态显示「实时运行」', () => {
    useAppStore.setState({ appState: 'live' });
    const { getByTestId } = render(<LiveRunningBadge />);
    expect(getByTestId('live-running-badge')).toBeTruthy();
  });

  it('replay 态渲染为空', () => {
    const { container } = render(<LiveRunningBadge />);
    expect(container.firstChild).toBeNull();
  });
});
