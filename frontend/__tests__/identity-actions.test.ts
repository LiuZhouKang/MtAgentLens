import { describe, expect, it } from 'vitest';
import { buildProcessActions } from '@/lib/identity-lineage';
import type { Action } from '@/lib/types';

function action(
  id: string,
  pid: number,
  actionType: string,
  args: unknown[],
  signalClass = 'HighSignal',
): Action {
  return {
    action_id: id,
    process_id: { pid, start_time_ns: 0 },
    action_type: actionType,
    normalized_args: args,
    derivation_mode: 'Direct',
    signal_class: signalClass,
    timestamp_ns: 0,
  };
}

describe('buildProcessActions —— 进程-动作关联', () => {
  it('按 pid 聚合动作，提取文件路径', () => {
    const map = buildProcessActions([
      action('a1', 100, 'FileRead', [{ Path: '/etc/passwd' }]),
      action('a2', 100, 'FileWrite', [{ Path: '/tmp/out.log' }]),
      action('a3', 200, 'ProcessExec', [{ command: 'ls -la' }]),
    ]);
    expect(map.get(100)?.length).toBe(2);
    expect(map.get(100)![0]).toMatchObject({
      actionType: 'FileRead',
      target: '/etc/passwd',
    });
    expect(map.get(100)![1]!.target).toBe('/tmp/out.log');
    expect(map.get(200)![0]!.target).toBe('ls -la');
  });

  it('无路径/命令参数的动作为 null（诚实）', () => {
    const map = buildProcessActions([action('a1', 100, 'FileRead', [{ Other: 'x' }])]);
    expect(map.get(100)![0]!.target).toBeNull();
  });

  it('缺 pid 的动作跳过', () => {
    const noPid = { ...action('a1', 0, 'FileRead', []), process_id: { pid: undefined as unknown as number, start_time_ns: 0 } };
    const map = buildProcessActions([noPid as unknown as Action]);
    expect(map.size).toBe(0);
  });

  it('不同 pid 互不干扰', () => {
    const map = buildProcessActions([
      action('a1', 100, 'FileRead', [{ Path: '/a' }]),
      action('a2', 200, 'FileWrite', [{ Path: '/b' }]),
    ]);
    expect(map.get(100)![0]!.target).toBe('/a');
    expect(map.get(200)![0]!.target).toBe('/b');
  });
});
