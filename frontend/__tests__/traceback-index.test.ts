import { describe, expect, it } from 'vitest';
import type { ResponsibilityTraceback } from '@/lib/types';
import { buildTracebacksByClaimId } from '@/lib/traceback-index';

describe('buildTracebacksByClaimId — 反向索引', () => {
  it('indexes tracebacks by claim_id (the real FK)', () => {
    const tracebacks: ResponsibilityTraceback[] = [
      { traceback_id: 'tb-1', claim_id: 'c1', chain_node_ids: [], chain_edge_ids: [], root_cause_node_id: '', explanation_summary: '', confidence: 0.9 },
    ];
    const index = buildTracebacksByClaimId(tracebacks);
    expect(index.get('c1')?.traceback_id).toBe('tb-1');
    expect(index.has('nope')).toBe(false);
  });

  it('last-write-wins when the same claim_id is re-traced', () => {
    const tracebacks: ResponsibilityTraceback[] = [
      { traceback_id: 'tb-a', claim_id: 'c1', chain_node_ids: [], chain_edge_ids: [], root_cause_node_id: '', explanation_summary: 'first', confidence: 0.8 },
      { traceback_id: 'tb-b', claim_id: 'c1', chain_node_ids: [], chain_edge_ids: [], root_cause_node_id: '', explanation_summary: 'second', confidence: 0.9 },
    ];
    const index = buildTracebacksByClaimId(tracebacks);
    expect(index.get('c1')?.explanation_summary).toBe('second');
  });
});
