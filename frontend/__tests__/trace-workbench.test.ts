import { describe, expect, test } from 'vitest';
import { demoAlerts, demoEvidence } from '@/lib/demo-data';
import { buildTraceRunModel, classifyEventKind, summarizeActorEvents } from '@/lib/trace-workbench';

describe('classifyEventKind — real backend event types', () => {
  test('maps process/lifecycle events to agent', () => {
    expect(classifyEventKind('EXEC')).toBe('agent');
    expect(classifyEventKind('PROCESS_EXEC')).toBe('agent');
    expect(classifyEventKind('EXIT')).toBe('agent');
    expect(classifyEventKind('HANDOFF_SPAWN')).toBe('agent');
    expect(classifyEventKind('BASH_READLINE')).toBe('agent');
  });

  test('maps security/anomaly events to boundary', () => {
    expect(classifyEventKind('INJECTION_DETECTED')).toBe('boundary');
    expect(classifyEventKind('IDENTITY_CONFUSION')).toBe('boundary');
    expect(classifyEventKind('CONTEXT_POLLUTION_DETECTED')).toBe('boundary');
    expect(classifyEventKind('CREDENTIAL_LEAK_DETECTED')).toBe('boundary');
    expect(classifyEventKind('ANOMALY')).toBe('boundary');
    expect(classifyEventKind('ALERT')).toBe('boundary');
    expect(classifyEventKind('SUBPROCESS_ALERT')).toBe('boundary');
    expect(classifyEventKind('SIGNOFF_TRANSITION')).toBe('boundary');
    // PascalCase contract types from data_contract.rs
    expect(classifyEventKind('CredentialAccess')).toBe('boundary');
    expect(classifyEventKind('ContextInjection')).toBe('boundary');
  });

  test('maps resource access events to file/network', () => {
    expect(classifyEventKind('FILE_OPEN')).toBe('file');
    expect(classifyEventKind('FILE_WRITE')).toBe('file');
    expect(classifyEventKind('FILE_DELETE')).toBe('file');
    expect(classifyEventKind('NETWORK_CONNECT')).toBe('network');
    expect(classifyEventKind('NETWORK_SEND')).toBe('network');
  });

  test('keeps demo snake_case event types stable', () => {
    expect(classifyEventKind('agent_start')).toBe('agent');
    expect(classifyEventKind('delegation')).toBe('agent');
    expect(classifyEventKind('role_claim')).toBe('agent');
    expect(classifyEventKind('file_read')).toBe('file');
    expect(classifyEventKind('boundary_cross')).toBe('boundary');
    expect(classifyEventKind('file_write')).toBe('file');
  });

  test('maps system/control events to verdict instead of everything falling through', () => {
    expect(classifyEventKind('SUMMARY')).toBe('verdict');
    expect(classifyEventKind('CONTROL')).toBe('verdict');
    expect(classifyEventKind('CLOCK_SYNC')).toBe('verdict');
  });
});

describe('trace workbench model', () => {
  test('turns a responsibility alert into a readable investigation trace', () => {
    const model = buildTraceRunModel(demoAlerts[0], demoEvidence);

    expect(model.title).toContain('权限提升');
    expect(model.primaryAgent).toBe('agent-security');
    expect(model.timeline.length).toBeGreaterThanOrEqual(5);
    expect(model.boundaryEvents).toBeGreaterThan(0);
    expect(model.verdict.evidence).toBe('/etc/shadow');
    expect(model.timeline.map((event) => event.kind)).toContain('boundary');
    expect(model.timeline.map((event) => event.material)).toContain('/etc/shadow');
  });

  test('populates actorRoles and per-event actorRole from the alert agent list', () => {
    const model = buildTraceRunModel(demoAlerts[0], demoEvidence);
    expect(model.actorRoles['agent-security']).toBe('security-auditor');
    expect(model.actorRoles['agent-frontend']).toBe('frontend-developer');
    expect(
      model.timeline.every((e) => e.actorRole === null || typeof e.actorRole === 'string'),
    ).toBe(true);
  });

  test('summarizeActorEvents aggregates spans, evidence count, and highest severity', () => {
    const model = buildTraceRunModel(demoAlerts[0], demoEvidence);
    const firstActor = model.timeline[0].actor;
    const summary = summarizeActorEvents(model.timeline.filter((e) => e.actor === firstActor));
    expect(summary.spans).toBeGreaterThan(0);
    expect(['Normal', 'Info', 'Warning', 'Critical']).toContain(summary.highestSeverity);
  });
});

describe('LLM observer events', () => {
  test('surfaces judged verdicts (llm_agreed set) as llm-kind events', () => {
    const model = buildTraceRunModel(demoAlerts[0], demoEvidence);
    const llmEvents = model.timeline.filter((e) => e.kind === 'llm');
    expect(llmEvents.length).toBeGreaterThan(0);
    expect(model.kindCounts.llm).toBe(llmEvents.length);
  });

  test('a disagreeing verdict carries its observer comment as output', () => {
    const model = buildTraceRunModel(demoAlerts[0], demoEvidence);
    const disagree = model.timeline.find((e) => e.title.includes('credential_pattern'));
    expect(disagree).toBeDefined();
    expect(disagree?.output).toMatch(/巧合|凭据|泄漏/);
  });

  test('observer_failed verdicts (llm_agreed null) produce no llm event', () => {
    const base = demoAlerts[0];
    const withFailed = {
      ...base,
      engine: {
        ...base.engine,
        rule_verdicts: [
          {
            rule_id: 'r-observer-failed',
            description: '观察者失败',
            matched_resource: 'observer-failed',
            llm_agreed: null,
            llm_comment: null,
          },
        ],
        pattern_verdicts: [],
      },
    } as typeof demoAlerts[0];
    const model = buildTraceRunModel(withFailed, demoEvidence);
    expect(model.timeline.filter((e) => e.kind === 'llm')).toHaveLength(0);
  });
});

describe('signal event collapsing', () => {
  test('signal events classify to network, not verdict noise', () => {
    expect(classifyEventKind('signal')).toBe('network');
    expect(classifyEventKind('EXIT')).toBe('agent');
  });

  test('collapses repetitive signal steps into a single network event', () => {
    const base = demoAlerts[0];
    const lastNs = base.timeline.steps[base.timeline.steps.length - 1].timestamp_ns;
    const withSignals = {
      ...base,
      timeline: {
        ...base.timeline,
        steps: [
          ...base.timeline.steps,
          { timestamp_ns: lastNs + 1000, event_type: 'signal', description: 'signal: ssl (pid=111)', resource: null, evidence_ref: null, agent_id: null, operation: null, evidence_level: null, triggering_rule_ids: [], triggering_pattern_id: null, source_event_id: null },
          { timestamp_ns: lastNs + 2000, event_type: 'signal', description: 'signal: ssl (pid=111)', resource: null, evidence_ref: null, agent_id: null, operation: null, evidence_level: null, triggering_rule_ids: [], triggering_pattern_id: null, source_event_id: null },
          { timestamp_ns: lastNs + 3000, event_type: 'signal', description: 'signal: ssl (pid=222)', resource: null, evidence_ref: null, agent_id: null, operation: null, evidence_level: null, triggering_rule_ids: [], triggering_pattern_id: null, source_event_id: null },
        ],
      },
    } as typeof demoAlerts[0];

    const model = buildTraceRunModel(withSignals, demoEvidence);
    // exactly ONE collapsed aggregate (demo also has a native network_connect step)
    const aggregates = model.timeline.filter((e) => e.kind === 'network' && e.title.startsWith('SSL/TLS 连接'));
    expect(aggregates).toHaveLength(1);
    expect(aggregates[0].title).toMatch(/SSL|连接|signal/);
    expect(model.kindCounts.network).toBe(2); // native network_connect + collapsed aggregate
    // verdict must not be flooded by signal noise
    expect(model.kindCounts.verdict).toBe(0);
  });
});
