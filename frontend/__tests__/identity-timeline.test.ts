import { describe, expect, it } from 'vitest';
import { buildAgentProcessTimeline } from '@/lib/identity-lineage';
import type { IdentityProcessMapEntry, ProcessEvent } from '@/lib/types';

/** 固定 start_time_ns（100 为基准）——父子靠 pid + 统一 start 精确匹配。 */
const START_NS = 8648099209154;
function ev(
  id: number,
  pid: number,
  parentPid: number | null,
  tsNs: number,
  comm: string,
  agentId: string | null,
): ProcessEvent {
  return {
    process_event_id: id,
    pid,
    start_time_ns: START_NS + pid, // 每 pid 唯一 start（父用 pid 区分）
    parent_pid: parentPid,
    parent_start_time_ns: parentPid != null ? START_NS + parentPid : null,
    comm,
    timestamp_ns: tsNs,
    agent_id: agentId,
  };
}

describe('buildAgentProcessTimeline —— 进程时间瀑布', () => {
  const map: IdentityProcessMapEntry[] = [100, 101, 102].map((pid) => ({
    agent_id: 'a1',
    pid,
    start_time_ns: START_NS + pid,
  }));

  it('按真实时间顺序构建瀑布根与层级', () => {
    const T0 = 1_700_000_000_000_000_000;
    const events = [
      ev(1, 100, null, T0, 'sudo', 'a1'),
      ev(2, 101, 100, T0 + 1_000_000, 'bash', 'a1'),
      ev(3, 102, 101, T0 + 2_000_000, 'git', 'a1'),
    ];
    const t = buildAgentProcessTimeline({ processEvents: events, identityProcessMap: map, agentId: 'a1' });
    expect(t).not.toBeNull();
    expect(t!.roots.length).toBe(1);
    expect(t!.roots[0]!.comm).toBe('sudo');
    expect(t!.roots[0]!.children[0]!.comm).toBe('bash');
    expect(t!.roots[0]!.children[0]!.children[0]!.comm).toBe('git');
    // 时间轴：startMs 递增（1ms / 2ms）
    expect(t!.roots[0]!.startMs).toBeCloseTo(0, 3);
    expect(t!.roots[0]!.children[0]!.startMs).toBeCloseTo(1, 2);
    expect(t!.roots[0]!.children[0]!.children[0]!.startMs).toBeCloseTo(2, 2);
    // 生命周期终点 = 子树最大 ts
    expect(t!.roots[0]!.endMs).toBeCloseTo(2, 2);
  });

  it('多个无父进程的根按时间排序', () => {
    const T0 = 1_700_000_000_000_000_000;
    const events = [
      ev(1, 200, null, T0 + 5_000_000, 'node', 'a1'),
      ev(2, 201, null, T0 + 1_000_000, 'sh', 'a1'),
    ];
    const t = buildAgentProcessTimeline({ processEvents: events, identityProcessMap: map, agentId: 'a1' });
    expect(t!.roots.map((r) => r.comm)).toEqual(['sh', 'node']); // 时间升序
  });

  it('无该 agent 进程事件 → null', () => {
    const t = buildAgentProcessTimeline({
      processEvents: [ev(1, 999, null, 1000, 'x', 'other')],
      identityProcessMap: map,
      agentId: 'a1',
    });
    expect(t).toBeNull();
  });

  it('跨 agent 进程不作为子进程（无归属）', () => {
    const events = [
      ev(1, 100, null, 1000, 'sudo', 'a1'),
      ev(2, 300, 100, 2000, 'node', 'a2'), // a2 的进程，pid 不在 a1 map
    ];
    const t = buildAgentProcessTimeline({ processEvents: events, identityProcessMap: map, agentId: 'a1' });
    expect(t!.roots[0]!.children.length).toBe(0);
  });
});
