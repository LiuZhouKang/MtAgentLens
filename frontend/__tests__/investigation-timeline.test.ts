const MS = 1_000_000; // 1 毫秒 = 1e6 ns

import { describe, expect, it } from 'vitest';
import { buildInvestigationTimeline } from '@/lib/investigation-timeline';
import type {
  Action,
  BehaviorTimelineEntry,
  CorrelationEvidence,
  ProcessEvent,
} from '@/lib/types';

function step(over: Partial<BehaviorTimelineEntry> & { timestamp_ns: number }): BehaviorTimelineEntry {
  return {
    event_type: '',
    description: '',
    resource: null,
    evidence_ref: null,
    agent_id: null,
    operation: null,
    evidence_level: null,
    triggering_rule_ids: [],
    triggering_pattern_id: null,
    source_event_id: null,
    ...over,
  };
}

const evidence: CorrelationEvidence[] = [
  {
    evidence_id: 'ev-1',
    intent_id: 'i',
    action_id: 'a',
    match_level: 'L1Exact',
    confidence: 0.9,
    matched_fields: {
      canonical_path: '/tmp/leak.txt',
      host: null,
      port: null,
      command: 'sudo cat /tmp/leak.txt',
      raw_args: ['sudo', 'cat', '/tmp/leak.txt'],
    },
    derivation_mode: 'Direct',
    phase: { Provisional: { timestamp_ns: 0 } },
    timestamp_ns: 0,
  },
];

const processEvents: ProcessEvent[] = [
  {
    process_event_id: 1,
    pid: 250,
    start_time_ns: 0,
    comm: 'claude',
    parent_pid: null,
    parent_start_time_ns: null,
    timestamp_ns: 0,
    agent_id: null,
  },
];

const actions: Action[] = [
  {
    action_id: 'a-1',
    process_id: { pid: 250, start_time_ns: 0 },
    action_type: 'FileRead',
    normalized_args: [{ Path: '/etc/passwd' }],
    derivation_mode: 'Direct',
    signal_class: 'HighSignal',
    timestamp_ns: 500 * MS,
  },
  {
    action_id: 'a-2',
    process_id: { pid: 250, start_time_ns: 0 },
    action_type: 'ProcessExec',
    normalized_args: [{ command: 'ls -la' }],
    derivation_mode: 'Direct',
    signal_class: 'HighSignal',
    timestamp_ns: 600 * MS,
  },
];

const T = 1_000_000;

describe('buildInvestigationTimeline —— 告警时间线富化', () => {
  it('折叠重复 SSL 信号为一条聚合事件，rawCount/foldedSignals 正确', () => {
    const tl = buildInvestigationTimeline({
      steps: [
        step({ timestamp_ns: 0, event_type: 'EXEC', description: 'process exec: sudo (pid=100)' }),
        step({ timestamp_ns: T, event_type: 'signal', description: 'signal: ssl (pid=100)' }),
        step({ timestamp_ns: T * 2, event_type: 'signal', description: 'signal: ssl (pid=100)' }),
        step({ timestamp_ns: T * 3, event_type: 'signal', description: 'signal: ssl (pid=100)' }),
      ],
      evidence,
      totalDurationMs: 4000,
      alertSeverity: 'Warning',
    });
    expect(tl.rawCount).toBe(4);
    expect(tl.foldedSignals).toBe(3);
    // EXEC + 1 条聚合信号
    expect(tl.events).toHaveLength(2);
    const sig = tl.events.find((e) => e.aggregated > 0)!;
    expect(sig.label).toBe('SSL/TLS 连接');
    expect(sig.kind).toBe('network');
    expect(sig.aggregated).toBe(3);
  });

  it('从 description 解析 comm / pid / exitCode', () => {
    const tl = buildInvestigationTimeline({
      steps: [
        step({ timestamp_ns: 0, event_type: 'EXEC', description: 'process exec: claude (pid=250)' }),
        step({
          timestamp_ns: T,
          event_type: 'EXIT',
          description: 'process exit: curl (pid=149, code=0)',
        }),
      ],
      evidence,
      totalDurationMs: 2000,
      alertSeverity: 'Info',
    });
    expect(tl.events[0]).toMatchObject({ comm: 'claude', pid: 250, exitCode: null });
    expect(tl.events[1]).toMatchObject({ comm: 'curl', pid: 149, exitCode: 0 });
  });

  it('关联证据 → matched 字段 + 置信度', () => {
    const tl = buildInvestigationTimeline({
      steps: [
        step({
          timestamp_ns: 0,
          event_type: 'FILE_READ',
          description: 'file read: /tmp/leak.txt',
          evidence_ref: 'ev-1',
          evidence_level: 'L1Exact',
        }),
      ],
      evidence,
      totalDurationMs: 1000,
      alertSeverity: 'Critical',
    });
    const ev = tl.events[0];
    expect(ev.matchedPath).toBe('/tmp/leak.txt');
    expect(ev.matchedCommand).toBe('sudo cat /tmp/leak.txt');
    expect(ev.confidence).toBe(0.9);
    expect(ev.rawArgs).toEqual(['sudo', 'cat', '/tmp/leak.txt']);
    expect(ev.severity).toBe('Warning'); // L1Exact → 强证据 Warning
  });

  it('offset/duration 按真实时间间隔推断；末条用总耗时余量', () => {
    const tl = buildInvestigationTimeline({
      steps: [
        step({ timestamp_ns: 0, event_type: 'EXEC', description: 'process exec: a (pid=1)' }),
        step({ timestamp_ns: 2000 * MS, event_type: 'EXEC', description: 'process exec: b (pid=2)' }),
        step({ timestamp_ns: 5000 * MS, event_type: 'EXIT', description: 'process exit: b (pid=2)' }),
      ],
      evidence,
      totalDurationMs: 8000,
      alertSeverity: 'Info',
    });
    expect(tl.events[0].offsetMs).toBe(0);
    expect(tl.events[0].durationMs).toBe(2000);
    expect(tl.events[1].offsetMs).toBe(2000);
    expect(tl.events[1].durationMs).toBe(3000);
    // 末条：总耗时 8000 - offset 5000 = 3000
    expect(tl.events[2].offsetMs).toBe(5000);
    expect(tl.events[2].durationMs).toBe(3000);
  });

  it('中文标签 + boundary 高亮 + 触发规则', () => {
    const tl = buildInvestigationTimeline({
      steps: [
        step({
          timestamp_ns: 0,
          event_type: 'INJECTION_DETECTED',
          description: 'injection detected',
          triggering_rule_ids: ['R-1'],
        }),
        step({
          timestamp_ns: T,
          event_type: 'FILE_WRITE',
          description: 'file write',
          triggering_pattern_id: 'PT-1',
        }),
      ],
      evidence,
      totalDurationMs: 2000,
      alertSeverity: 'Critical',
    });
    expect(tl.events[0].label).toBe('注入检测');
    expect(tl.events[0].isBoundary).toBe(true);
    expect(tl.events[0].isCritical).toBe(true);
    expect(tl.events[0].hasRules).toBe(true);
    expect(tl.events[0].ruleIds).toEqual(['R-1']);
    expect(tl.events[1].label).toBe('文件写入');
    expect(tl.events[1].hasRules).toBe(true);
    expect(tl.events[1].patternId).toBe('PT-1');
  });

  it('空步骤 → 空结果（诚实空态）', () => {
    const tl = buildInvestigationTimeline({
      steps: [],
      evidence,
      totalDurationMs: 0,
      alertSeverity: 'Info',
    });
    expect(tl.events).toEqual([]);
    expect(tl.rawCount).toBe(0);
  });

  it('按 pid 精确关联 process_events → 真实进程名（不编造）', () => {
    const tl = buildInvestigationTimeline({
      steps: [
        // description 只有 pid，真实 comm 来自 process_events 表
        step({ timestamp_ns: 0, event_type: 'EXEC', description: 'process exec: sh (pid=250)' }),
        // 无 process_events 匹配 → 回落 description 解析
        step({ timestamp_ns: MS, event_type: 'EXEC', description: 'process exec: git (pid=999)' }),
      ],
      evidence,
      totalDurationMs: 2000,
      alertSeverity: 'Info',
      processEvents,
    });
    expect(tl.events[0].realComm).toBe('claude'); // process_events 权威覆盖
    expect(tl.events[1].realComm).toBe('git'); // 无匹配回落 description
  });

  it('按 pid 精确关联 actions → 真实行为（时间升序 + 中文标签）', () => {
    const tl = buildInvestigationTimeline({
      steps: [step({ timestamp_ns: 0, event_type: 'EXEC', description: 'process exec: claude (pid=250)' })],
      evidence,
      totalDurationMs: 1000,
      alertSeverity: 'Info',
      processEvents,
      actions,
    });
    const ev = tl.events[0];
    expect(ev.actions).toHaveLength(2);
    expect(ev.actions[0]).toMatchObject({
      actionLabel: '文件读取',
      target: '/etc/passwd',
    });
    expect(ev.actions[1]).toMatchObject({
      actionLabel: '进程执行',
      target: 'ls -la',
    });
    // 时间升序
    expect(ev.actions[0].tsNs).toBeLessThan(ev.actions[1].tsNs);
  });

  it('无 pid 的步骤不关联行为（诚实，不模糊匹配）', () => {
    const tl = buildInvestigationTimeline({
      steps: [
        step({ timestamp_ns: 0, event_type: 'EXEC', description: 'event without pid' }),
      ],
      evidence,
      totalDurationMs: 1000,
      alertSeverity: 'Info',
      actions,
    });
    expect(tl.events[0].actions).toEqual([]);
  });
});
