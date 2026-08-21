// 原始实时事件环形缓冲 + uSES 订阅。
//
// 高频实时数据（原始事件流）刻意不进入 zustand（selector 引用稳定性约束）；
// 本模块持有一个写时复制的环形缓冲，组件用 `useSyncExternalStore(subscribe,
// getLiveRawEvents)` 细粒度订阅。快照引用：缓冲变更时整体替换（uSES 能感知），
// 未变更时引用稳定（uSES 跳过重渲染）。

import type { LiveRawEvent } from './live-types';

/** 环形缓冲容量——封顶淘汰最旧，防止高吞吐下内存无限增长。 */
export const RAW_EVENT_BUFFER_CAP = 5000;

let buffer: readonly LiveRawEvent[] = [];
const subscribers = new Set<() => void>();

function notify(): void {
  subscribers.forEach((cb) => cb());
}

/** 批量入缓冲（LiveSession rAF 合帧调用，避免逐条 O(n) 复制）。 */
export function pushRawEvents(events: LiveRawEvent[]): void {
  if (events.length === 0) return;
  const next =
    buffer.length + events.length > RAW_EVENT_BUFFER_CAP
      ? [...buffer, ...events].slice(-RAW_EVENT_BUFFER_CAP)
      : [...buffer, ...events];
  buffer = next;
  notify();
}

/** 单条入缓冲（便捷入口，等价于 pushRawEvents([ev])）。 */
export function pushRawEvent(ev: LiveRawEvent): void {
  pushRawEvents([ev]);
}

/** 清空缓冲（新 run 开始 / 断开重连时调用）。 */
export function resetRawEvents(): void {
  if (buffer.length === 0) return;
  buffer = [];
  notify();
}

/** uSES getSnapshot：返回当前缓冲引用（变更时新引用，未变时稳定）。 */
export function getLiveRawEvents(): readonly LiveRawEvent[] {
  return buffer;
}

/** uSES subscribe：注册变更回调，返回退订函数。 */
export function subscribeLiveRawEvents(cb: () => void): () => void {
  subscribers.add(cb);
  return () => {
    subscribers.delete(cb);
  };
}
