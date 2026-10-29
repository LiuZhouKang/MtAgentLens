import { describe, expect, it } from 'vitest';
import {
  availableKindsForAgent,
  buildRawEventRows,
  buildTimeRanges,
  filterRawEventRows,
  summarizeRawEvents,
} from '@/lib/raw-event-browser';
import type {
  Action,
  ContextPollutionEvent,
  IdentityProcessMapEntry,
  Intent,
  ProcessEvent,
  TlsSession,
} from '@/lib/types';

const processEvents: ProcessEvent[] = [
  {
    process_event_id: 1,
    pid: 42,
    start_time_ns: 1000,
    parent_pid: null,
    parent_start_time_ns: null,
    comm: 'claude',
    timestamp_ns: 1000,
    agent_id: 'agent-a',
  },
  {
    process_event_id: 2,
    pid: 300,
    start_time_ns: 4000,
    parent_pid: null,
    parent_start_time_ns: null,
    comm: 'orphan-tool',
    timestamp_ns: 4000,
    agent_id: null,
  },
];
const tlsSessions: TlsSession[] = [
  { tls_session_id: 's-1', pid: 42, start_time_ns: 1000, timestamp_ns: 1000, agent_id: 'agent-a' },
  { tls_session_id: 's-5', pid: 44, start_time_ns: 1000, timestamp_ns: 1200, agent_id: null },
];
const intents: Intent[] = [
  {
    intent_id: 'int-1',
    process_id: { pid: 42, start_time_ns: 1000 },
    message_id: 'msg-1',
    tool_name: 'Bash',
    tool_args: [{ Command: 'ls -la' }],
    intent_class: 'FileSystem',
    parse_state: 'Eligible',
    primary_matching_eligible: true,
    authority_source: 'SseResponseToolUse',
    timestamp_ns: 1000,
  },
];
const actions: Action[] = [
  {
    action_id: 'act-1',
    process_id: { pid: 42, start_time_ns: 1000 },
    action_type: 'FileWrite',
    normalized_args: [{ Path: '/tmp/x.sh' }],
    derivation_mode: 'Direct',
    signal_class: 'HighSignal',
    timestamp_ns: 1002,
  },
];
const pollutionEvents: ContextPollutionEvent[] = [
  {
    pollution_id: 1,
    source_agent_id: 'agent-a',
    contaminated_artifact: 'cwd',
    propagation_path: ['agent-a', 'agent-b'],
    detected_at_ns: 900,
    run_id: null,
  },
];
// pid 42@1000 → agent-a（真实 identity_process_map 语义）；其余 pid 无映射。
const identityProcessMap: IdentityProcessMapEntry[] = [
  { agent_id: 'agent-a', pid: 42, start_time_ns: 1000 },
];

const input = { processEvents, tlsSessions, intents, actions, pollutionEvents, identityProcessMap };

describe('buildRawEventRows', () => {
  it('五类归一化并按 timestampNs 升序（稳定序保留同刻插入序）', () => {
    const rows = buildRawEventRows(input);
    // ts 900: pollution；1000: process(42) → tls(s-1) → intent(int-1)；1002 action；1200 tls(s-5)；4000 process(300)
    expect(rows.map((r) => r.kind)).toEqual([
      'pollution',
      'process',
      'tls',
      'intent',
      'action',
      'tls',
      'process',
    ]);
    expect(rows[1].id).toBe('process:42:1000');
    expect(rows[1].title).toBe('claude');
    expect(rows[1].agentId).toBe('agent-a');
    expect(rows[3].title).toBe('Bash');
    // 未识别进程 agentId null（p2 与 s-5 均 null）
    expect(rows[5].agentId).toBeNull();
    expect(rows[6].agentId).toBeNull();
  });

  it('intent/action 经 identity_process_map 解析真实 agent 归属', () => {
    const rows = buildRawEventRows(input);
    const intent = rows.find((r) => r.kind === 'intent');
    const action = rows.find((r) => r.kind === 'action');
    expect(intent?.agentId).toBe('agent-a');
    expect(action?.agentId).toBe('agent-a');
  });

  it('pollution 行标题 = 污染工件、detail = 传播路径、agent = source_agent_id', () => {
    const rows = buildRawEventRows(input);
    const p = rows.find((r) => r.kind === 'pollution');
    expect(p?.title).toBe('cwd');
    expect(p?.detail).toContain('agent-a → agent-b');
    expect(p?.agentId).toBe('agent-a');
  });

  it('summarizeRawEvents 统计各 kind 数量', () => {
    const rows = buildRawEventRows(input);
    expect(summarizeRawEvents(rows)).toEqual({
      process: 2,
      tls: 2,
      intent: 1,
      action: 1,
      pollution: 1,
    });
  });
});

describe('filterRawEventRows', () => {
  const rows = buildRawEventRows(input);

  it('按 kind 过滤（空 = 全部）', () => {
    expect(
      filterRawEventRows(rows, { kinds: [], agentId: null, text: '', sinceNs: null }),
    ).toHaveLength(7);
    expect(
      filterRawEventRows(rows, { kinds: ['tls'], agentId: null, text: '', sinceNs: null }),
    ).toHaveLength(2);
  });

  it('按 agentId 过滤（intent/action 也已解析归属）', () => {
    const out = filterRawEventRows(rows, {
      kinds: [],
      agentId: 'agent-a',
      text: '',
      sinceNs: null,
    });
    // process(42) + tls(s-1) + intent(int-1) + action(act-1) + pollution(1)
    expect(out).toHaveLength(5);
    expect(out.every((r) => r.agentId === 'agent-a')).toBe(true);
  });

  it('按文本过滤（title/detail/args/agentId/载荷命中）', () => {
    expect(
      filterRawEventRows(rows, { kinds: [], agentId: null, text: 'x.sh', sinceNs: null }),
    ).toHaveLength(1); // act-1 detail 含 /tmp/x.sh
    expect(
      filterRawEventRows(rows, { kinds: [], agentId: null, text: 'claude', sinceNs: null }),
    ).toHaveLength(1);
    // 搜索完整载荷：int-1 的 tool_args 含 Command
    expect(
      filterRawEventRows(rows, { kinds: [], agentId: null, text: 'Command', sinceNs: null }),
    ).toHaveLength(1);
  });

  it('按时间范围过滤（sinceNs）', () => {
    expect(
      filterRawEventRows(rows, { kinds: [], agentId: null, text: '', sinceNs: 1200 }),
    ).toHaveLength(2); // ts 1200 + 4000
  });

  it('多维组合过滤', () => {
    const out = filterRawEventRows(rows, {
      kinds: ['process'],
      agentId: 'agent-a',
      text: '',
      sinceNs: 1000,
    });
    expect(out).toHaveLength(1);
    expect(out[0].kind).toBe('process');
  });
});

describe('buildTimeRanges', () => {
  it('短 run（146s）只给能切中数据的档位，全部非空', () => {
    const base = 1784173169527394406;
    const ranges = buildTimeRanges(base, base + 146_800_000_000);
    // 每个非「全部时间」档位 window < span（必然切中数据）
    for (const r of ranges.slice(1)) expect(r.window).toBeLessThan(146_800_000_000);
    expect(ranges[0]).toEqual({ label: '全部时间', window: null });
    expect(ranges.length).toBeGreaterThan(1);
  });

  it('长 run（3 小时）给小时级档位', () => {
    const base = 1784173169527394406;
    const ranges = buildTimeRanges(base, base + 3 * 3600 * 1e9);
    const labels = ranges.map((r) => r.label);
    expect(labels).toContain('最近 1 小时');
    expect(labels).toContain('最近 2 小时');
    for (const r of ranges.slice(1)) expect(r.window).toBeLessThan(3 * 3600 * 1e9);
  });

  it('跨度过短（< 10 秒）用比例档位', () => {
    const base = 1784173169527394406;
    const ranges = buildTimeRanges(base, base + 5_000_000_000);
    expect(ranges[1].label).toBe('最近 1/2');
    expect(ranges[1].window).toBe(2_500_000_000);
  });
});

describe('availableKindsForAgent', () => {
  it('级联收窄：选 agent 后仅返回该 agent 实际有的 kind', () => {
    const rows = buildRawEventRows(input);
    // agent-a 拥有 process/tls/intent/action/pollution 全部五类
    expect(availableKindsForAgent(rows, 'agent-a')).toEqual([
      'process',
      'tls',
      'intent',
      'action',
      'pollution',
    ]);
    expect(availableKindsForAgent(rows, null)).toEqual([
      'process',
      'tls',
      'intent',
      'action',
      'pollution',
    ]);
    expect(availableKindsForAgent(rows, 'agent-b')).toEqual([]); // agent-b 无事件
  });
});
