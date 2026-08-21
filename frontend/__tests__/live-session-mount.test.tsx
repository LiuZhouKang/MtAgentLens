// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render } from '@testing-library/react';
import { LiveSessionMount } from '@/components/live/live-session-mount';

const startLiveSession = vi.fn();
const stopLiveSession = vi.fn();

vi.mock('@/lib/live/live-session', () => ({
  startLiveSession: () => startLiveSession(),
  stopLiveSession: () => stopLiveSession(),
}));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('LiveSessionMount —— 实时会话全局挂载', () => {
  it('挂载时启动实时会话', () => {
    render(<LiveSessionMount />);
    expect(startLiveSession).toHaveBeenCalledTimes(1);
  });

  it('卸载时停止实时会话（清理 SSE 与轮询）', () => {
    const { unmount } = render(<LiveSessionMount />);
    unmount();
    expect(stopLiveSession).toHaveBeenCalledTimes(1);
  });

  it('渲染为空（无视觉副作用）', () => {
    const { container } = render(<LiveSessionMount />);
    expect(container.firstChild).toBeNull();
  });
});
