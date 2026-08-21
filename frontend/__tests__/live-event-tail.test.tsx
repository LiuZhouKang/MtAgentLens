// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { LiveEventTail, isNearBottom } from '@/components/live/live-event-tail';
import { pushRawEvent, resetRawEvents } from '@/lib/live/live-raw-events';
import type { LiveRawEvent } from '@/lib/live/live-types';

// 虚拟化交给共享 VirtualList 自身职责；本测试聚焦 LiveEventTail 逻辑，mock 为全量渲染。
vi.mock('@/components/ui/virtual-list', () => ({
  default: ({ items, renderRow, onScroll, scrollRef, emptyLabel }: any) => (
    <div ref={scrollRef} onScroll={onScroll} data-testid="mock-virtual-list">
      {items.length === 0 ? (
        <span>{emptyLabel}</span>
      ) : (
        items.map((item: LiveRawEvent, i: number) => (
          <div key={item.id} data-testid="event-row">
            {renderRow(item, i)}
          </div>
        ))
      )}
    </div>
  ),
}));

function ev(id: string, source: string, pid = 8120): LiveRawEvent {
  return {
    id,
    timestampMs: 1000 + Number(id),
    timestampNs: 0,
    source,
    pid,
    comm: 'claude',
    data: { len: 1200 },
  };
}

beforeEach(() => {
  resetRawEvents();
});

afterEach(() => {
  cleanup();
});

describe('LiveEventTail —— 原始事件实时滚动', () => {
  it('渲染实时事件（source + comm + pid 可见）', () => {
    pushRawEvent(ev('1', 'tls_capture'));
    render(<LiveEventTail />);
    // source 同时出现在过滤下拉 option 与行内 → 用 getAllByText
    expect(screen.getAllByText('tls_capture').length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText('claude')).toBeTruthy();
    expect(screen.getByText('pid 8120')).toBeTruthy();
  });

  it('空态提示', () => {
    render(<LiveEventTail />);
    expect(screen.getByText('等待实时事件……')).toBeTruthy();
  });

  it('按 source 过滤', () => {
    pushRawEvent(ev('1', 'tls_capture', 8120));
    pushRawEvent(ev('2', 'process', 9999));
    render(<LiveEventTail />);
    expect(screen.getByText('pid 9999')).toBeTruthy();

    fireEvent.change(screen.getByTestId('live-source-filter'), { target: { value: 'tls_capture' } });
    expect(screen.getAllByText('tls_capture').length).toBeGreaterThanOrEqual(1);
    expect(screen.queryByText('pid 9999')).toBeNull(); // process 行被过滤
  });

  it('点击行展开 data 载荷', () => {
    pushRawEvent(ev('1', 'tls_capture'));
    const { container } = render(<LiveEventTail />);
    expect(container.querySelector('pre')).toBeNull(); // 载荷默认折叠
    fireEvent.click(screen.getByText('claude')); // 点击行内唯一文本（避免与 option 歧义）
    const pre = container.querySelector('pre');
    expect(pre).not.toBeNull();
    expect(pre?.textContent).toContain('1200');
  });

  it('跟随开关可切换', () => {
    render(<LiveEventTail />);
    const toggle = screen.getByTestId('live-follow-toggle');
    expect(toggle.getAttribute('aria-pressed')).toBe('true');
    fireEvent.click(toggle);
    expect(toggle.getAttribute('aria-pressed')).toBe('false');
  });

  it('isNearBottom：滚动到底判定', () => {
    expect(isNearBottom({ scrollHeight: 1000, scrollTop: 900, clientHeight: 100 })).toBe(true);
    expect(isNearBottom({ scrollHeight: 1000, scrollTop: 100, clientHeight: 100 })).toBe(false);
  });
});
