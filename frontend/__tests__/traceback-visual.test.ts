import { describe, expect, it } from 'vitest';
import { parseTracebackSummary } from '@/lib/traceback-visual';

const SIMPLE_SUMMARY = `Traceback for claim claim:f7709126adcd03b3: Tainted Context Use (confidence 65%)
Causal chain:
  [start] Agent(99e353da-d6e)
  crossed boundary→ Resource(File { canonical_path: ".." })
Root cause: Agent(99e353da-d6e)
Supporting evidence: 6 record(s)`;

const COMPLEX_SUMMARY = `Traceback for claim claim:e2b97abe338fba05: Tainted Context Use (confidence 68%)
Causal chain:
  [start] Agent(9982971f-50e)
  caused→ Resource(File { canonical_path: "/" })
  caused→ Resource(Process { process_id: ProcessId { pid: 588250, start_time_ns: 39091549374357 } })
  caused→ Agent(9982971f-50e)
Root cause: Agent(9982971f-50e)
Supporting evidence: 24 record(s)`;

describe('parseTracebackSummary —— 回溯链可读化', () => {
  it('解析简单链：Agent → 边界穿越 File + 根因 + 证据数', () => {
    const v = parseTracebackSummary(SIMPLE_SUMMARY)!;
    expect(v.steps).toHaveLength(2);
    // agent 保留完整 id（深度详细）
    expect(v.steps[0]).toMatchObject({ kind: 'agent', label: '99e353da-d6e' });
    expect(v.steps[1]).toMatchObject({ kind: 'file', label: '..', isBoundary: true });
    expect(v.rootCause).toMatchObject({ kind: 'agent' });
    expect(v.evidenceCount).toBe(6);
  });

  it('解析复杂链：File / Process / Agent 混合', () => {
    const v = parseTracebackSummary(COMPLEX_SUMMARY)!;
    expect(v.steps).toHaveLength(4);
    expect(v.steps[1].kind).toBe('file');
    expect(v.steps[1].label).toBe('/');
    expect(v.steps[2].kind).toBe('process');
    expect(v.steps[2].label).toBe('进程 pid 588250');
    expect(v.steps[3].kind).toBe('agent');
    expect(v.evidenceCount).toBe(24);
  });

  it('无法识别的文本 → null（诚实回退）', () => {
    expect(parseTracebackSummary('')).toBeNull();
    expect(parseTracebackSummary('完全无关文本')).toBeNull();
  });

  it('无根因但只有证据数的文本 → steps 空但 evidenceCount 保留', () => {
    const v = parseTracebackSummary('Causal chain:\n  [start] Agent(aaa)\nSupporting evidence: 3 record(s)');
    expect(v).not.toBeNull();
    expect(v!.steps).toHaveLength(1);
    expect(v!.evidenceCount).toBe(3);
  });
});
