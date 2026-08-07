// @vitest-environment jsdom
import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import type { Action, CorrelationEvidence, Intent } from '@/lib/types';
import IntentActionChain from '@/components/traces/intent-action-chain';

const intents: Intent[] = [
  { intent_id: 'int-1', process_id: { pid: 42, start_time_ns: 1000 }, message_id: 'm1', tool_name: 'Bash', tool_args: [{ Command: 'ls -la /tmp' }], intent_class: 'FileSystem', parse_state: 'Eligible', primary_matching_eligible: true, authority_source: 'SseResponseToolUse', timestamp_ns: 1000 },
  { intent_id: 'int-2', process_id: { pid: 42, start_time_ns: 1000 }, message_id: 'm2', tool_name: 'Read', tool_args: [{ Path: '/tmp/x.sh' }], intent_class: 'FileSystem', parse_state: 'Eligible', primary_matching_eligible: true, authority_source: 'TextFallback', timestamp_ns: 1050 },
  { intent_id: 'int-3', process_id: { pid: 7, start_time_ns: 2000 }, message_id: 'm3', tool_name: 'Glob', tool_args: [{ Path: '/tmp/*.sh' }], intent_class: 'FileSystem', parse_state: 'Ineligible', primary_matching_eligible: false, authority_source: 'TextFallback', timestamp_ns: 1100 },
];

const actions: Action[] = [
  { action_id: 'act-1', process_id: { pid: 42, start_time_ns: 1000 }, action_type: 'FileWrite', normalized_args: [{ Path: '/tmp/x.sh' }], derivation_mode: 'Direct', signal_class: 'HighSignal', timestamp_ns: 1000 },
  { action_id: 'act-2', process_id: { pid: 42, start_time_ns: 1000 }, action_type: 'FileRead', normalized_args: [{ Path: '/tmp/x.sh' }], derivation_mode: 'Direct', signal_class: 'LowSignal', timestamp_ns: 1002 },
  { action_id: 'act-3', process_id: { pid: 7, start_time_ns: 2000 }, action_type: 'ProcessExec', normalized_args: [{ Path: '/bin/bash', Command: 'bash' }], derivation_mode: 'Derived', signal_class: 'HighSignal', timestamp_ns: 1100 },
];

const evidence: CorrelationEvidence[] = [
  { evidence_id: 'ev-1', intent_id: 'int-1', action_id: 'act-1', match_level: 'L1Exact', confidence: 0.95, matched_fields: { canonical_path: '/tmp/x.sh', host: null, port: null, command: null, raw_args: [] }, derivation_mode: 'Direct', phase: { Provisional: { timestamp_ns: 1000 } }, timestamp_ns: 1000 },
  { evidence_id: 'ev-2', intent_id: 'int-2', action_id: 'act-1', match_level: 'L3Containment', confidence: 0.65, matched_fields: { canonical_path: null, host: null, port: null, command: null, raw_args: [] }, derivation_mode: 'Direct', phase: { Provisional: { timestamp_ns: 1050 } }, timestamp_ns: 1050 },
  { evidence_id: 'ev-3', intent_id: 'int-1', action_id: 'act-2', match_level: 'L5Substring', confidence: 0.5, matched_fields: { canonical_path: null, host: null, port: null, command: null, raw_args: ['x'] }, derivation_mode: 'Direct', phase: { Provisional: { timestamp_ns: 1200 } }, timestamp_ns: 1200 },
];

describe('IntentActionChain — OTel execute_tool 意图动作链', () => {
  it('groups by intent and aggregates multiple evidence onto one action', () => {
    render(<IntentActionChain intents={intents} actions={actions} evidence={evidence} />);
    // int-1 (Bash) 触发了 act-1 + act-2
    expect(screen.getAllByText('Bash').length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText('FileWrite').length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText('FileRead')).toBeDefined();
    // act-1 聚合 ev-1 + ev-2
    expect(screen.getAllByText('L1Exact')).toBeDefined();
    expect(screen.getByText('L3Containment')).toBeDefined();
  });

  it('renders orphan intents/actions greyed but visible', () => {
    render(<IntentActionChain intents={intents} actions={actions} evidence={evidence} />);
    // int-3 (Glob) 与 act-3 (ProcessExec) 无 evidence 关联
    expect(screen.getByText('Glob')).toBeDefined();
    expect(screen.getByText('ProcessExec')).toBeDefined();
  });

  it('shows an empty state when there is no intent/action data', () => {
    render(<IntentActionChain intents={[]} actions={[]} evidence={[]} />);
    expect(screen.getByText(/未提取到意图\/动作关联/)).toBeDefined();
  });
});
