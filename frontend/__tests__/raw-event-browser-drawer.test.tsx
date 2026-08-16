// @vitest-environment jsdom
import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import RawEventBrowser from '@/components/command-center/raw-event-browser';

// 组件直接收 props（dbAssets 事件数组），不依赖 store——测试最小
const EMPTY = { pollutionEvents: [], identityProcessMap: [] };
describe('RawEventBrowser drawer', () => {
  it('空数据源渲染空态而非报错', () => {
    render(
      <RawEventBrowser
        processEvents={[]}
        tlsSessions={[]}
        intents={[]}
        actions={[]}
        {...EMPTY}
        onClose={() => {}}
      />,
    );
    expect(screen.getByText(/原始事件流/i)).toBeTruthy();
    expect(screen.getByText(/暂无原始事件/i)).toBeTruthy();
  });

  it('渲染 kind 徽章 + 计数；真实数据 0 的 kind 徽章隐藏', () => {
    render(
      <RawEventBrowser
        processEvents={[
          {
            process_event_id: 1,
            pid: 42,
            start_time_ns: 1000,
            parent_pid: null,
            parent_start_time_ns: null,
            comm: 'claude',
            timestamp_ns: 1000,
            agent_id: null,
          },
        ]}
        tlsSessions={[]}
        intents={[]}
        actions={[]}
        pollutionEvents={[]}
        identityProcessMap={[]}
        onClose={() => {}}
      />,
    );
    // process 徽章有、其余 4 类徽章隐藏（诚实）
    expect(screen.getByRole('button', { name: /进程事件/ })).toBeTruthy();
    expect(screen.queryByRole('button', { name: /TLS/ })).toBeNull();
    expect(screen.queryByRole('button', { name: /意图/ })).toBeNull();
    expect(screen.queryByRole('button', { name: /动作/ })).toBeNull();
    expect(screen.queryByRole('button', { name: /上下文污染/ })).toBeNull();
  });
});
