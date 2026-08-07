import { describe, expect, it } from 'vitest';
import { availableKindsForAgent, buildRawEventRows, filterRawEventRows, summarizeRawEvents } from '@/lib/raw-event-browser';
import type { Action, Intent, ProcessEvent, TlsSession } from '@/lib/types';

const processEvents: ProcessEvent[] = [
  { process_event_id: 1, pid: 42, start_time_ns: 1000, parent_pid: null, parent_start_time_ns: null, comm: 'claude', timestamp_ns: 1000, agent_id: 'agent-a' },
  { process_event_id: 2, pid: 300, start_time_ns: 4000, parent_pid: null, parent_start_time_ns: null, comm: 'orphan-tool', timestamp_ns: 4000, agent_id: null },
];
const tlsSessions: TlsSession[] = [
  { tls_session_id: 's-1', pid: 42, start_time_ns: 1000, timestamp_ns: 1000, agent_id: 'agent-a' },
  { tls_session_id: 's-5', pid: 44, start_time_ns: 1000, timestamp_ns: 1200, agent_id: null },
];
const intents: Intent[] = [
  { intent_id: 'int-1', process_id: { pid: 42, start_time_ns: 1000 }, message_id: 'msg-1', tool_name: 'Bash', tool_args: [{ Command: 'ls -la' }], intent_class: 'FileSystem', parse_state: 'Eligible', primary_matching_eligible: true, authority_source: 'SseResponseToolUse', timestamp_ns: 1000 },
];
const actions: Action[] = [
  { action_id: 'act-1', process_id: { pid: 42, start_time_ns: 1000 }, action_type: 'FileWrite', normalized_args: [{ Path: '/tmp/x.sh' }], derivation_mode: 'Direct', signal_class: 'HighSignal', timestamp_ns: 1002 },
];

describe('buildRawEventRows', () => {
  it('4 kind 归一化并按 timestampNs 升序（稳定序保留同刻插入序）', () => {
    const rows = buildRawEventRows({ processEvents, tlsSessions, intents, actions });
    // ts 1000: process(42) → tls(s-1) → intent(int-1)；随后 action(1002) → tls(s-5)(1200) → process(300)(4000)
    expect(rows.map((r) => r.kind)).toEqual(['process', 'tls', 'intent', 'action', 'tls', 'process']);
    expect(rows[0].id).toBe('process:42:1000');
    expect(rows[0].title).toBe('claude');
    expect(rows[0].agentId).toBe('agent-a');
    // tls 行 title = tls_session_id
    expect(rows[1].title).toBe('s-1');
    // intent 行 title = tool_name
    expect(rows[2].title).toBe('Bash');
    // 未识别进程 agentId null（p2 与 s-5 均 null）
    expect(rows[4].agentId).toBeNull();
    expect(rows[5].agentId).toBeNull();
  });

  it('summarizeRawEvents 统计各 kind 数量', () => {
    const rows = buildRawEventRows({ processEvents, tlsSessions, intents, actions });
    expect(summarizeRawEvents(rows)).toEqual({ process: 2, tls: 2, intent: 1, action: 1 });
  });
});

describe('filterRawEventRows', () => {
  const rows = buildRawEventRows({ processEvents, tlsSessions, intents, actions });

  it('按 kind 过滤（空 = 全部）', () => {
    expect(filterRawEventRows(rows, { kinds: [], agentId: null, text: '', sinceNs: null })).toHaveLength(6);
    expect(filterRawEventRows(rows, { kinds: ['tls'], agentId: null, text: '', sinceNs: null })).toHaveLength(2);
  });

  it('按 agentId 过滤', () => {
    const out = filterRawEventRows(rows, { kinds: [], agentId: 'agent-a', text: '', sinceNs: null });
    expect(out).toHaveLength(2); // process(42) + tls(s-1)
    expect(out.every((r) => r.agentId === 'agent-a')).toBe(true);
  });

  it('按文本过滤（title/detail/comm/args 命中）', () => {
    expect(filterRawEventRows(rows, { kinds: [], agentId: null, text: 'x.sh', sinceNs: null })).toHaveLength(1); // act-1 detail 含 /tmp/x.sh
    expect(filterRawEventRows(rows, { kinds: [], agentId: null, text: 'claude', sinceNs: null })).toHaveLength(1);
  });

  it('按时间范围过滤（sinceNs）', () => {
    expect(filterRawEventRows(rows, { kinds: [], agentId: null, text: '', sinceNs: 1200 })).toHaveLength(2); // ts 1200 + 4000
  });

  it('多维组合过滤', () => {
    const out = filterRawEventRows(rows, { kinds: ['process'], agentId: 'agent-a', text: '', sinceNs: 1000 });
    expect(out).toHaveLength(1);
    expect(out[0].kind).toBe('process');
  });
});

describe('availableKindsForAgent', () => {
  it('级联收窄：选 agent 后仅返回该 agent 实际有的 kind', () => {
    const rows = buildRawEventRows({ processEvents, tlsSessions, intents, actions });
    // agent-a 仅 process(42) + tls(s-1)；intent/action 无 agent 标注不计入
    expect(availableKindsForAgent(rows, 'agent-a')).toEqual(['process', 'tls']);
    expect(availableKindsForAgent(rows, null)).toEqual(['process', 'tls', 'intent', 'action']);
    expect(availableKindsForAgent(rows, 'agent-b')).toEqual([]); // agent-b 无事件
  });
});
