import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  RAW_EVENT_BUFFER_CAP,
  getLiveRawEvents,
  pushRawEvent,
  pushRawEvents,
  resetRawEvents,
  subscribeLiveRawEvents,
} from '@/lib/live/live-raw-events';

function ev(id: string): Parameters<typeof pushRawEvent>[0] {
  return { id, timestampMs: Number(id), timestampNs: Number(id), source: 'tls', pid: 1, comm: 'c', data: {} };
}

beforeEach(() => {
  resetRawEvents();
});

describe('rawEvents 环形缓冲 + uSES', () => {
  it('push 后 get 可见，订阅者收到通知', () => {
    const cb = vi.fn();
    const unsub = subscribeLiveRawEvents(cb);
    pushRawEvent(ev('1'));
    expect(cb).toHaveBeenCalled();
    expect(getLiveRawEvents().length).toBe(1);
    expect(getLiveRawEvents()[0].id).toBe('1');
    unsub();
  });

  it('退订后不再收到通知', () => {
    const cb = vi.fn();
    const unsub = subscribeLiveRawEvents(cb);
    unsub();
    pushRawEvent(ev('1'));
    expect(cb).not.toHaveBeenCalled();
  });

  it('未变时 getSnapshot 引用稳定（uSES 不重复渲染）', () => {
    pushRawEvent(ev('1'));
    const a = getLiveRawEvents();
    const b = getLiveRawEvents();
    expect(a).toBe(b);
  });

  it('变更时 getSnapshot 引用更新（uSES 感知）', () => {
    pushRawEvent(ev('1'));
    const a = getLiveRawEvents();
    pushRawEvent(ev('2'));
    const b = getLiveRawEvents();
    expect(a).not.toBe(b);
    expect(b.length).toBe(2);
  });

  it('环形缓冲封顶：超过 cap 淘汰最旧', () => {
    const events = Array.from({ length: RAW_EVENT_BUFFER_CAP + 10 }, (_, i) => ev(String(i)));
    pushRawEvents(events);
    const buf = getLiveRawEvents();
    expect(buf.length).toBe(RAW_EVENT_BUFFER_CAP);
    // 最旧的 10 条被淘汰，保留的是后 CAP 条
    expect(buf[0].id).toBe(String(10));
    expect(buf[buf.length - 1].id).toBe(String(RAW_EVENT_BUFFER_CAP + 9));
  });

  it('resetRawEvents 清空并通知', () => {
    const cb = vi.fn();
    subscribeLiveRawEvents(cb);
    pushRawEvent(ev('1'));
    cb.mockClear();
    resetRawEvents();
    expect(getLiveRawEvents().length).toBe(0);
    expect(cb).toHaveBeenCalled();
  });
});
