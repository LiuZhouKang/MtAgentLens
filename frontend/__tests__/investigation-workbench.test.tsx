// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import InvestigationWorkbench from '@/components/investigation/investigation-workbench';
import { buildInvestigationModel } from '@/lib/investigation';
import { demoAlerts, demoEvidence, demoGraph } from '@/lib/demo-data';
import { useAppStore } from '@/lib/store';
import type { ResponsibilityClaim } from '@/lib/types';

function claim(id: string): ResponsibilityClaim {
  return {
    claim_id: id,
    claim_type: 'TaintedContextUse',
    subject_agent_id: 'agent-a',
    policy_id: 'POLICY-1',
    boundary_id: 'BOUNDARY-1',
    claim_state: 'Inferred',
    severity: 'Critical',
    confidence: 0.9,
    evidence_refs: [],
    competing_explanations: [],
    traceback_ref: null,
    phase: { Provisional: { timestamp_ns: 1000 } },
    timestamp_ns: 1000,
  };
}

afterEach(() => {
  cleanup();
});

describe('InvestigationWorkbench', () => {
  it('renders the workbench with alert queue, timeline and evidence chain', () => {
    const model = buildInvestigationModel(demoAlerts[0], demoGraph, demoEvidence);

    render(
      <InvestigationWorkbench
        alert={demoAlerts[0]}
        model={model}
        availableAlerts={demoAlerts}
        onSelectAlert={vi.fn()}
      />,
    );

    // Left: alert queue
    expect(screen.getByText('告警队列')).toBeDefined();
    expect(screen.getAllByText(demoAlerts[0].title).length).toBeGreaterThan(0);
    // Center: timeline + evidence chain
    expect(screen.getByText('事件时间线')).toBeDefined();
    expect(screen.getByText(`${model.timelineSteps.length} 步`)).toBeDefined();
    expect(screen.getAllByText('证据链').length).toBeGreaterThan(0);
    // Right: verdict + subject + actions
    expect(screen.getByText('判决')).toBeDefined();
    expect(screen.getByText('主体信息')).toBeDefined();
    expect(screen.getByText('后续步骤')).toBeDefined();
  });

  it('scopes the responsibility panel to the alert claim_ids', () => {
    useAppStore.setState({ claims: [claim('claim-1'), claim('claim-2')] });
    const alert = {
      ...demoAlerts[0],
      engine: { ...demoAlerts[0].engine, claim_ids: ['claim-1'] },
    };
    const model = buildInvestigationModel(alert, demoGraph, demoEvidence);

    render(
      <InvestigationWorkbench
        alert={alert}
        model={model}
        availableAlerts={demoAlerts}
        onSelectAlert={vi.fn()}
      />,
    );

    expect(screen.getByText('1 条 · 本告警')).toBeDefined();
    expect(screen.queryByText('2 条')).toBeNull();
  });

  it('lists the run claims when an older alert has no exact claim ids', () => {
    useAppStore.setState({ claims: [claim('claim-1'), claim('claim-2')] });
    const model = buildInvestigationModel(demoAlerts[0], demoGraph, demoEvidence);

    render(
      <InvestigationWorkbench
        alert={demoAlerts[0]}
        model={model}
        availableAlerts={demoAlerts}
        onSelectAlert={vi.fn()}
      />,
    );

    expect(screen.getByText('2 条')).toBeDefined();
  });

  it('does not attach unrelated run claims to an explicit empty claim_ids list', () => {
    useAppStore.setState({ claims: [claim('unrelated-claim')] });
    const alert = { ...demoAlerts[0], engine: { ...demoAlerts[0].engine, claim_ids: [] } };
    render(
      <InvestigationWorkbench
        alert={alert}
        model={buildInvestigationModel(alert, demoGraph, demoEvidence)}
        availableAlerts={[alert]}
        onSelectAlert={vi.fn()}
      />,
    );
    expect(screen.getByText('0 条 · 本告警')).toBeDefined();
    expect(screen.getByText('暂无责任声明')).toBeDefined();
    expect(screen.queryByText('unrelated-claim')).toBeNull();
  });

  it('shows an empty state when the timeline has no steps', () => {
    const model = {
      ...buildInvestigationModel(demoAlerts[0], demoGraph, demoEvidence),
      timelineSteps: [],
    };

    render(
      <InvestigationWorkbench
        alert={demoAlerts[0]}
        model={model}
        availableAlerts={demoAlerts}
        onSelectAlert={vi.fn()}
      />,
    );

    expect(screen.getByText('暂无时间线数据')).toBeDefined();
  });
});
